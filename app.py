from __future__ import annotations

import base64
import asyncio
from contextlib import asynccontextmanager
import json
import os
import secrets
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from studio_core.config import StudioConfigStore
from studio_core.frpc import FrpcManager, managed_frpc_lifespan
from studio_core.audio_probe import trim_wav
from studio_core.provider_delivery import ProviderDeliveryError, ProviderDeliveryStore
from studio_core.request_monitor import RequestMonitorStore
from voice_clone_flow.gpt_sovits.voices import VoiceRegistry

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CONFIG = DATA / "config" / "studio.json"
DATA.mkdir(parents=True, exist_ok=True)
CONFIG.parent.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    if CONFIG.is_file():
        try:
            value = json.loads(CONFIG.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError):
            pass
    value = {"token": secrets.token_urlsafe(24), "name": "VoiceClone Studio"}
    CONFIG.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return value


cfg = load_config()
registry = VoiceRegistry(DATA)
app = FastAPI(title="VoiceClone Studio", version="0.1.0")


def check_token(authorization: str | None) -> None:
    expected = str(cfg.get("token", ""))
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Studio Token 无效")


class TTSRequest(BaseModel):
    voice_id: str
    text: str
    text_language: str = "zh"
    prompt_text: str = ""
    prompt_language: str = "zh"
    stream: bool = True


class TokenUpdate(BaseModel):
    token: str


@app.get("/api/health")
async def health(authorization: str | None = Header(default=None)):
    check_token(authorization)
    return {"status": "ok", "studio_version": app.version, "name": cfg.get("name", "VoiceClone Studio")}


@app.get("/api/config")
async def config(authorization: str | None = Header(default=None)):
    check_token(authorization)
    return {"token": str(cfg.get("token", "")), "name": cfg.get("name", "VoiceClone Studio")}


@app.post("/api/config/token")
async def update_token(body: TokenUpdate, authorization: str | None = Header(default=None)):
    check_token(authorization)
    token = body.token.strip()
    if len(token) < 8:
        raise HTTPException(status_code=400, detail="Token 至少需要 8 个字符")
    cfg["token"] = token
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"saved": True}


@app.get("/api/voices")
async def voices(authorization: str | None = Header(default=None)):
    check_token(authorization)
    result = []
    for item in registry.list():
        if item.source == "remote":
            continue
        result.append({
            "id": item.id,
            "name": item.name,
            "language": item.reference_language or "zh",
            "reference_text": item.reference_text,
            "gpt_weights_path": item.gpt_weights_path,
            "sovits_weights_path": item.sovits_weights_path,
            "reference_audio_path": item.refer_audio_path,
            "status": item.status,
        })
    return {"voices": result}


@app.post("/api/tts")
async def tts(body: TTSRequest, authorization: str | None = Header(default=None)):
    check_token(authorization)
    voice = registry.get(body.voice_id)
    if voice is None:
        raise HTTPException(status_code=404, detail="音色不存在")
    raise HTTPException(status_code=503, detail="GPT-SoVITS 推理服务尚未启动，请先在 Studio 页面启动运行环境")


@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse(ROOT / "pages" / "studio.html")


# 0.2 runtime bridge. The original skeleton above is kept for compatibility;
# this factory is the single app instance used by the Studio launcher.
class _AudioTickets:
    def __init__(self, root: Path, ttl_seconds: int = 900) -> None:
        self.root = Path(root) / "audio_cache"
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, tuple[float, Path, str]] = {}

    def create(self, payload: bytes, request_id: str = "") -> str:
        ticket = secrets.token_urlsafe(24)
        path = self.root / f"{ticket}.wav"
        path.write_bytes(payload)
        self._items[ticket] = (time.time() + self.ttl_seconds, path, request_id)
        return ticket

    def request_id(self, ticket: str) -> str:
        item = self._items.get(ticket)
        return item[2] if item else ""

    def consume(self, ticket: str) -> bytes | None:
        item = self._items.pop(ticket, None)
        if item is None:
            return None
        expires, path, _request_id = item
        try:
            return path.read_bytes() if expires >= time.time() else None
        finally:
            path.unlink(missing_ok=True)


class _StudioRuntime:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.data = self.root / "data"
        from voice_clone_flow.gpt_sovits.adapter import GPTSoVITSAdapter
        from voice_clone_flow.gpt_sovits.config import GPTSoVITSConfig
        from voice_clone_flow.gpt_sovits.synthesis import GPTSoVITSSynthesisService
        from voice_clone_flow.gpt_sovits.voices import VoiceRegistry

        self.registry = VoiceRegistry(self.data)
        self.gpt_config = GPTSoVITSConfig(self.data)
        self.adapter = GPTSoVITSAdapter(self.gpt_config, self.data)
        self.synthesis = GPTSoVITSSynthesisService(self.adapter)
        from voice_clone_flow.gpt_sovits.install import GPTSoVITSInstallManager
        from voice_clone_flow.runtime_resources import FFmpegResourceManager
        from voice_clone_flow.separator_resources import SeparatorResourceManager

        self.install = GPTSoVITSInstallManager(self.data, self.gpt_config)
        self.ffmpeg = FFmpegResourceManager(self.data, "", None)
        self.separator = SeparatorResourceManager(self.data)
        from voice_clone_flow.services.studio import VoiceCloneStudio
        from voice_clone_flow.storage.json_repository import JsonTaskRepository
        from voice_clone_flow.gpt_sovits.training import TrainingService
        self.tasks = VoiceCloneStudio(JsonTaskRepository(self.data / "tasks"))
        self.training = TrainingService(self.data, self.gpt_config, self.registry)

    def list_voices(self) -> list[dict]:
        self.registry.discover_external()
        return [
            {
                "id": item.id,
                "name": item.name,
                "language": item.reference_language or "zh",
                "reference_text": item.reference_text,
                "gpt_weights_path": item.gpt_weights_path,
                "sovits_weights_path": item.sovits_weights_path,
                "reference_audio_path": item.refer_audio_path,
                "status": item.status,
            }
            for item in self.registry.list()
            if item.source != "remote"
        ]

    def health(self) -> dict:
        return {"gpt_sovits": self.adapter.status(), "voices": len(self.registry.list())}

    def runtime_status(self) -> dict:
        return {
            "gpt_sovits": self.install.status(),
            "service": self.adapter.status(),
            "ffmpeg": self.ffmpeg.status(),
            "separator": self.separator.status(),
        }

    def start_gpt(self) -> dict:
        self.adapter.ensure_service()
        return self.adapter.status()

    def stop_gpt(self) -> dict:
        self.adapter.stop_service()
        return self.adapter.status()

    def install_ffmpeg(self) -> dict:
        started = self.ffmpeg.start_install()
        return {**self.ffmpeg.status(), "started": started}

    def install_separator(self) -> dict:
        started = self.separator.start_install()
        return {**self.separator.status(), "started": started}

    def install_gpt(self, url: str = "") -> dict:
        source = url.strip() or str(self.gpt_config.values().get("download_url") or "")
        started = self.install.start_install(source)
        return {**self.install.status(), "started": started}

    def create_voice(self, payload: dict) -> dict:
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("音色名称不能为空")
        return self.registry.create(name, **{key: str(value) for key, value in payload.items() if key != "name"})

    def get_voice(self, voice_id: str):
        return self.registry.get(voice_id)

    def update_reference(self, voice_id: str, audio_path: str, text: str):
        return self.registry.update_reference(voice_id, audio_path, text)

    def synthesize_chunks(self, voice_id: str, text: str, language: str):
        asset = self.registry.get(voice_id)
        if asset is None:
            raise KeyError(voice_id)
        if asset.status != "ready":
            raise RuntimeError(f"音色状态不可推理：{asset.status}")
        if not bool(self.adapter.status().get("service_running")):
            raise RuntimeError("请先手动启动 GPT-SoVITS 推理服务")
        yield self.synthesis.synthesize(asset, text, language)

    def list_tasks(self) -> list[dict]:
        return [item.to_dict() for item in self.tasks.repository.list()]

    def task_detail(self, task_id: str) -> dict:
        from voice_clone_flow.review_store import ReviewStore
        task = self.tasks.repository.get(task_id)
        rows = ReviewStore(self.data / "sessions").load(task_id)
        return {"task": task.to_dict(), "segments": rows}

    def create_task(self, filename: str):
        return self.tasks.create_task(Path(filename).stem or "voice", filename)

    async def process_task(self, task_id: str, source: Path, language: str, backend) -> None:
        from voice_clone_flow.material_pipeline import MaterialPipeline
        ffmpeg = self.ffmpeg.resolve()
        separator = self.separator.model_path if self.separator.status().get("ready") else None
        if ffmpeg is None:
            raise RuntimeError("FFmpeg 尚未安装")
        if separator is None:
            raise RuntimeError("人声分离模型尚未安装")
        pipeline = MaterialPipeline(None, "", str(ffmpeg), separator, self.data / "sessions", backend=backend)
        def transition(state):
            current = self.tasks.repository.get(task_id)
            self.tasks.repository.save(current.transition(state))
        try:
            await pipeline.run(task_id, source, language, transition)
        except Exception as exc:
            current = self.tasks.repository.get(task_id)
            if current.state not in {"failed", "cancelled", "ready"}:
                self.tasks.repository.save(current.transition("failed", str(exc)))

    def export_dataset(self, task_id: str, rows: list[dict]) -> dict:
        from voice_clone_flow.dataset import DatasetRow, export_gpt_sovits_dataset
        from voice_clone_flow.review_store import ReviewStore
        store = ReviewStore(self.data / "sessions")
        current = {str(row.get("audio_name")): row for row in store.load(task_id)}
        normalized = []
        for row in rows:
            name = str(row.get("audio_name", ""))
            if name in current and Path(name).name == name:
                normalized.append({**current[name], "text": str(row.get("text", "")).strip(), "approved": bool(row.get("approved"))})
        store.save(task_id, normalized)
        dataset_rows = [DatasetRow(self.data / "sessions" / task_id / "segments" / row["audio_name"], row["text"], row["language"], row["approved"]) for row in normalized]
        result = export_gpt_sovits_dataset(dataset_rows, self.data / "datasets" / task_id)
        return {"count": result.count, "skipped_count": result.skipped_count, "manifest": str(result.manifest), "dataset_dir": str(result.root)}

    def train_task(self, task_id: str, name: str, language: str, gpt_epochs: int, sovits_epochs: int) -> dict:
        dataset = self.data / "datasets" / task_id
        if not (dataset / "train.list").is_file():
            raise RuntimeError("请先生成训练数据")
        asset = self.registry.create(name, status="created", reference_language=language, gpt_epochs=gpt_epochs, sovits_epochs=sovits_epochs)
        target = self.training.dataset_dir(asset.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(dataset, target, dirs_exist_ok=True)
        if not self.training.start_training(asset.id, expected_language=language, gpt_epochs=gpt_epochs, sovits_epochs=sovits_epochs):
            raise RuntimeError("已有训练任务正在进行")
        return self.registry.get(asset.id).__dict__


def create_app(*, root: Path | None = None, config_store=None, runtime=None, frpc_manager=None) -> FastAPI:
    project_root = Path(root or ROOT).resolve()
    store = config_store or StudioConfigStore(project_root / "data" / "config" / "studio.json")
    store.ensure()
    engine = runtime or _StudioRuntime(project_root)
    tickets = _AudioTickets(project_root / "data")
    frpc = frpc_manager or FrpcManager(project_root / "data")
    deliveries = ProviderDeliveryStore(project_root / "data" / "provider_deliveries.json")
    monitor = RequestMonitorStore(project_root / "data" / "monitor")
    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        stop_on_exit = bool(store.load().get("frp_stop_with_studio", True))
        async with managed_frpc_lifespan(frpc, stop_on_exit=stop_on_exit):
            yield

    application = FastAPI(title="VoiceClone Studio", version="0.2.0", lifespan=lifespan)

    def current() -> dict:
        return store.load()

    def auth(authorization: str | None) -> None:
        expected = str(current().get("api_token", current().get("token", "")))
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not expected or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="Studio Token 无效")

    def monitor_begin(source: str, voice_id: str, text: str, language: str) -> str:
        try:
            voice = engine.get_voice(voice_id) if callable(getattr(engine, "get_voice", None)) else None
            row = monitor.create_request(source=source, original_text=text, voice_id=voice_id, voice_name=str(getattr(voice, "name", voice_id)), requested_language=language)
            return row["id"]
        except Exception:
            return ""

    def monitor_note(request_id: str, stage: str, **changes) -> None:
        if request_id:
            try:
                monitor.update(request_id, stage, **changes)
            except Exception:
                pass

    @application.get("/api/health")
    async def api_health(authorization: str | None = Header(default=None)):
        auth(authorization)
        return {"status": "ok", "studio_version": application.version, "name": current().get("name", "VoiceClone Studio"), **engine.health()}

    @application.get("/api/config")
    async def api_config(authorization: str | None = Header(default=None)):
        auth(authorization)
        value = current()
        return {"token": value.get("api_token", value.get("token", "")), "name": value.get("name", "VoiceClone Studio")}

    @application.post("/api/config/token")
    async def api_update_token(body: TokenUpdate):
        if len(body.token.strip()) < 8:
            raise HTTPException(status_code=400, detail="Token 至少需要 8 个字符")
        store.save({"api_token": body.token.strip()})
        return {"saved": True}

    @application.get("/api/voices")
    async def api_voices(authorization: str | None = Header(default=None)):
        auth(authorization)
        return {"voices": engine.list_voices()}

    def provider_for_voice(voice_id: str) -> dict:
        asset = engine.get_voice(voice_id) if callable(getattr(engine, "get_voice", None)) else None
        if asset is None:
            raise HTTPException(status_code=404, detail="音色不存在")
        values = current()
        language = str(getattr(asset, "reference_language", "zh") or "zh").lower()
        labels = {"zh": "中文", "ja": "日文", "en": "英文", "ko": "韩文", "yue": "粤语"}
        voice_key = str(getattr(asset, "remote_voice_id", "") or getattr(asset, "id", voice_id))
        provider_id = "voice_clone_flow_remote_" + "".join(char if char.isalnum() or char in "-_" else "_" for char in voice_key)
        return {
            "id": provider_id,
            "type": "gsvi_tts_api",
            "provider": "gpt_sovits_inference",
            "provider_type": "text_to_speech",
            "enable": True,
            "display_name": str(getattr(asset, "name", voice_key)),
            "api_key": str(values.get("api_token", "")),
            "api_base": f"http://127.0.0.1:{int(values.get('frp_remote_port', 19090))}",
            "version": "v2Pro",
            "character": voice_key,
            "prompt_text_lang": labels.get(language, "中文"),
            "emotion": "默认",
            "text_lang": labels.get(language, "中文"),
            "timeout": 300,
        }

    @application.post("/api/provider-deliveries/preview")
    async def api_provider_preview(payload: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        provider = provider_for_voice(str(payload.get("voice_id", "")).strip())
        public = dict(provider)
        public["api_key"] = "********"
        return {"provider": public, "valid": True}

    @application.post("/api/provider-deliveries", status_code=201)
    async def api_provider_delivery(payload: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            provider = provider_for_voice(str(payload.get("voice_id", "")).strip())
            task = deliveries.create(provider)
            return {"delivery": deliveries._public(task)}
        except ProviderDeliveryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/provider-deliveries/claim")
    async def api_provider_claim(authorization: str | None = Header(default=None)):
        auth(authorization)
        task = deliveries.claim()
        return {"delivery": task} if task else {"delivery": None}

    @application.get("/api/provider-deliveries/{task_id}")
    async def api_provider_delivery_status(task_id: str, authorization: str | None = Header(default=None)):
        auth(authorization)
        task = deliveries.public(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="投递任务不存在")
        return {"delivery": task}

    @application.post("/api/provider-deliveries/{task_id}/report")
    async def api_provider_delivery_report(task_id: str, payload: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            task = deliveries.report(task_id, str(payload.get("stage", "")), str(payload.get("message", "")), str(payload.get("error", "")))
            return {"delivery": deliveries._public(task)}
        except ProviderDeliveryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/voices/open-folder")
    async def api_open_voices_folder(authorization: str | None = Header(default=None)):
        auth(authorization)
        from voice_clone_flow.path_actions import open_voices_root
        try:
            target = open_voices_root(project_root / "data")
            return {"opened": True, "path": str(target)}
        except (OSError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/voices")
    async def api_create_voice(payload: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        if not callable(getattr(engine, "create_voice", None)):
            raise HTTPException(status_code=503, detail="运行时不支持音色登记")
        try:
            asset = engine.create_voice(payload)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"voice": asset.__dict__}

    @application.get("/api/voices/{voice_id}")
    async def api_voice(voice_id: str, authorization: str | None = Header(default=None)):
        auth(authorization)
        asset = engine.get_voice(voice_id) if callable(getattr(engine, "get_voice", None)) else None
        if asset is None:
            raise HTTPException(status_code=404, detail="音色不存在")
        return {"voice": asset.__dict__}

    @application.post("/api/voices/{voice_id}/reference")
    async def api_reference(voice_id: str, payload: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            asset = engine.update_reference(voice_id, str(payload.get("reference_audio_path", "")), str(payload.get("reference_text", "")))
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"voice": asset.__dict__}

    @application.get("/api/runtime/status")
    async def api_runtime_status(authorization: str | None = Header(default=None)):
        auth(authorization)
        return engine.runtime_status() if callable(getattr(engine, "runtime_status", None)) else {}

    @application.post("/api/config/asr")
    async def api_asr_config(payload: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        values = store.save({
            "asr_base_url": str(payload.get("base_url", "")),
            "asr_api_key": str(payload.get("api_key", "")),
            "asr_model": str(payload.get("model", "")),
            "asr_timeout_seconds": int(payload.get("timeout_seconds", 300)),
        })
        return {"saved": True, "asr_base_url": values["asr_base_url"], "asr_model": values["asr_model"], "asr_api_key_configured": bool(values["asr_api_key"])}

    @application.post("/api/config/llm")
    async def api_llm_config(payload: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        values = store.save({
            "llm_base_url": str(payload.get("base_url", "")),
            "llm_api_key": str(payload.get("api_key", "")),
            "llm_model": str(payload.get("model", "")),
            "llm_timeout_seconds": int(payload.get("timeout_seconds", 60)),
        })
        return {"saved": True, "llm_base_url": values["llm_base_url"], "llm_model": values["llm_model"], "llm_api_key_configured": bool(values["llm_api_key"])}

    @application.get("/api/tasks")
    async def api_tasks(authorization: str | None = Header(default=None)):
        auth(authorization)
        return {"tasks": engine.list_tasks()}

    @application.post("/api/tasks")
    async def api_create_task(request: Request, authorization: str | None = Header(default=None)):
        auth(authorization)
        form = await request.form(max_part_size=512 * 1024 * 1024)
        upload = form.get("file")
        if upload is None or not getattr(upload, "filename", ""):
            raise HTTPException(status_code=400, detail="请选择视频或音频素材")
        filename = str(upload.filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".mp4", ".mpeg", ".webm", ".mov", ".mkv", ".flac", ".ogg"}:
            raise HTTPException(status_code=415, detail="不支持该素材格式")
        task = engine.create_task(filename)
        root = project_root / "data" / "sessions" / task.id
        root.mkdir(parents=True, exist_ok=True)
        source = root / f"source{suffix}"
        payload = await upload.read()
        if len(payload) > 400 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="素材不能超过 400 MB")
        source.write_bytes(payload)
        values = current()
        from studio_core.asr import OpenAICompatibleASRBackend
        backend = OpenAICompatibleASRBackend(values["asr_base_url"], values["asr_api_key"], values["asr_model"], values["asr_timeout_seconds"])
        asyncio.create_task(engine.process_task(task.id, source, str(form.get("language", "zh")), backend))
        return JSONResponse({"task": task.to_dict()}, status_code=202)

    @application.get("/api/tasks/{task_id}")
    async def api_task(task_id: str, authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            return engine.task_detail(task_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="任务不存在") from None

    @application.get("/api/tasks/{task_id}/audio/{audio_name}")
    async def api_task_audio(task_id: str, audio_name: str, authorization: str | None = Header(default=None)):
        auth(authorization)
        if Path(audio_name).name != audio_name or not audio_name.startswith("segment_"):
            raise HTTPException(status_code=400, detail="音频路径无效")
        path = project_root / "data" / "sessions" / task_id / "segments" / audio_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="音频不存在")
        return FileResponse(path, media_type="audio/wav")

    @application.post("/api/tasks/{task_id}/dataset")
    async def api_task_dataset(task_id: str, payload: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            return engine.export_dataset(task_id, list(payload.get("segments", [])))
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/tasks/{task_id}/train", status_code=202)
    async def api_task_train(task_id: str, payload: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            return {"voice": engine.train_task(task_id, str(payload.get("name", "")).strip(), str(payload.get("language", "zh")), int(payload.get("gpt_epochs", 15)), int(payload.get("sovits_epochs", 30)))}
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/runtime/ffmpeg/install")
    async def api_ffmpeg_install(authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            return engine.install_ffmpeg()
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/runtime/separator/install")
    async def api_separator_install(authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            return engine.install_separator()
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/gpt-sovits/install")
    async def api_gpt_install(payload: dict | None = None, authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            return engine.install_gpt(str((payload or {}).get("url", "")))
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/gpt-sovits/start")
    async def api_gpt_start(authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            return engine.start_gpt()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @application.post("/api/gpt-sovits/stop")
    async def api_gpt_stop(authorization: str | None = Header(default=None)):
        auth(authorization)
        return engine.stop_gpt()

    @application.get("/api/frp/status")
    async def api_frp_status(authorization: str | None = Header(default=None)):
        auth(authorization)
        return {"frp": frpc.status(), "config": store.public()}

    def voice_service_status() -> dict:
        runtime_status = engine.runtime_status()
        service = runtime_status.get("service", {}) if isinstance(runtime_status, dict) else {}
        frp = frpc.status()
        gpt_running = bool(service.get("service_running"))
        enabled = gpt_running and bool(frp.get("connected"))
        if enabled:
            phase, message = "ready", "语音服务已开启，AstrBot 可以访问本机推理。"
        elif bool(frp.get("conflict")):
            phase, message = "conflict", str(frp.get("message") or "FRPC 发生实例冲突。")
        elif gpt_running or bool(frp.get("running")):
            phase, message = "starting", "语音服务正在启动，请等待 GPT-SoVITS 与 FRPC 都就绪。"
        else:
            phase, message = "stopped", "语音服务已关闭，Studio 页面仍保持在线。"
        return {"enabled": enabled, "phase": phase, "message": message, "gpt": service, "frp": frp, "config": store.public()}

    @application.get("/api/voice-service/status")
    async def api_voice_service_status(authorization: str | None = Header(default=None)):
        auth(authorization)
        return voice_service_status()

    @application.post("/api/voice-service/start")
    async def api_voice_service_start(authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            engine.start_gpt()
            frpc.prepare(current())
            frpc.start()
            return voice_service_status()
        except Exception as exc:
            try:
                engine.stop_gpt()
            except Exception:
                pass
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/voice-service/stop")
    async def api_voice_service_stop(authorization: str | None = Header(default=None)):
        auth(authorization)
        errors = []
        try:
            frpc.stop()
        except Exception as exc:
            errors.append(f"FRPC：{exc}")
        try:
            engine.stop_gpt()
        except Exception as exc:
            errors.append(f"GPT-SoVITS：{exc}")
        result = voice_service_status()
        if errors:
            result["phase"] = "error"
            result["message"] = "；".join(errors)
        return result

    @application.post("/api/frp/prepare")
    async def api_frp_prepare(payload: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            store.save(payload)
            return {"frp": frpc.prepare(store.load())}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/frp/start")
    async def api_frp_start(authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            return {"frp": frpc.start()}
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/frp/stop")
    async def api_frp_stop(authorization: str | None = Header(default=None)):
        auth(authorization)
        return {"frp": frpc.stop()}

    @application.get("/api/monitor/requests")
    async def api_monitor_requests(limit: int = 100, authorization: str | None = Header(default=None)):
        auth(authorization)
        return {"requests": monitor.snapshot(limit)}

    @application.get("/api/monitor/requests/{request_id}")
    async def api_monitor_request(request_id: str, authorization: str | None = Header(default=None)):
        auth(authorization)
        row = monitor.get(request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="监控记录不存在")
        return {"request": monitor.public(row)}

    @application.get("/api/monitor/audio/{request_id}")
    async def api_monitor_audio(request_id: str, authorization: str | None = Header(default=None)):
        auth(authorization)
        path = monitor.audio_path(request_id)
        if path is None:
            raise HTTPException(status_code=404, detail="监控音频已过期或不存在")
        return FileResponse(path, media_type="audio/wav", filename=f"{request_id}.wav")

    @application.delete("/api/monitor/requests")
    async def api_monitor_clear(authorization: str | None = Header(default=None)):
        auth(authorization)
        monitor.clear()
        return {"cleared": True}

    @application.get("/api/monitor/events")
    async def api_monitor_events(authorization: str | None = Header(default=None)):
        auth(authorization)
        queue = monitor.subscribe()
        async def events():
            try:
                yield "event: ready\ndata: {}\n\n"
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=20)
                        yield "event: update\ndata: " + json.dumps(item, ensure_ascii=False) + "\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                monitor.unsubscribe(queue)
        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @application.post("/api/tts")
    async def api_tts(body: TTSRequest, authorization: str | None = Header(default=None)):
        auth(authorization)
        request_id = monitor_begin("studio_preview", body.voice_id, body.text, body.text_language)
        try:
            text, language = await prepare_tts_text(body.voice_id, body.text, body.text_language, request_id)
            monitor_note(request_id, "synthesizing", processed_text=text, synthesis_language=language)
            chunks = engine.synthesize_chunks(body.voice_id, text, language)
        except KeyError:
            monitor_note(request_id, "failed", error="音色不存在")
            raise HTTPException(status_code=404, detail="音色不存在") from None
        except RuntimeError as exc:
            monitor_note(request_id, "failed", error=str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        def monitored_chunks():
            collected = bytearray()
            try:
                for chunk in chunks:
                    collected.extend(chunk)
                    yield chunk
                if request_id:
                    monitor.save_audio(request_id, bytes(collected))
            except Exception as exc:
                monitor_note(request_id, "failed", error=str(exc))
                raise
        return StreamingResponse(monitored_chunks(), media_type="audio/wav", headers={"X-VoiceClone-Request-ID": request_id})

    async def prepare_tts_text(voice_id: str, text: str, requested_language: str, request_id: str = "") -> tuple[str, str]:
        voice = engine.get_voice(voice_id) if callable(getattr(engine, "get_voice", None)) else None
        if voice is None:
            raise KeyError(voice_id)
        voice_language = str(getattr(voice, "reference_language", "zh") or "zh").lower()
        if voice_language != "ja":
            return text, requested_language
        has_kana = any("\u3040" <= char <= "\u30ff" for char in text)
        if has_kana:
            return text, "ja"
        monitor_note(request_id, "translating")
        values = current()
        from studio_core.llm import OpenAICompatibleTranslator
        translator = OpenAICompatibleTranslator(
            values["llm_base_url"],
            values["llm_api_key"],
            values["llm_model"],
            values["llm_timeout_seconds"],
        )
        translated = await asyncio.to_thread(translator.translate_ja, text)
        monitor_note(request_id, "translated", processed_text=translated, synthesis_language="ja")
        return translated, "ja"

    @application.post("/infer_single")
    async def infer_single(payload: dict, authorization: str | None = Header(default=None)):
        auth(authorization)
        voice_id, text = str(payload.get("model_name", "")).strip(), str(payload.get("text", "")).strip()
        dl_url = str(payload.get("dl_url", "")).rstrip("/")
        if not voice_id or not text or not dl_url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="model_name、text 和有效 dl_url 不能为空")
        requested_language = str(payload.get("text_lang", "中文"))
        request_id = monitor_begin("gsvi", voice_id, text, requested_language)
        try:
            text, language = await prepare_tts_text(voice_id, text, requested_language, request_id)
            monitor_note(request_id, "synthesizing", processed_text=text, synthesis_language=language)
            audio = b"".join(engine.synthesize_chunks(voice_id, text, language))
            if payload.get("provider_verification") is True:
                audio = trim_wav(audio, 1.5)
        except KeyError:
            monitor_note(request_id, "failed", error="音色不存在")
            raise HTTPException(status_code=404, detail="音色不存在") from None
        except RuntimeError as exc:
            monitor_note(request_id, "failed", error=str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if request_id:
            monitor.save_audio(request_id, audio)
        ticket = tickets.create(audio, request_id)
        return {"msg": "合成成功", "audio_url": f"{dl_url}/api/audio/{ticket}", "request_id": request_id}

    @application.get("/api/audio/{ticket}")
    async def api_audio(ticket: str):
        request_id = tickets.request_id(ticket)
        payload = tickets.consume(ticket)
        if payload is None:
            raise HTTPException(status_code=404, detail="音频下载凭据无效或已使用")
        if request_id:
            try:
                monitor.mark_downloaded(request_id)
            except Exception:
                pass
        return StreamingResponse(iter((payload,)), media_type="audio/wav")

    @application.get("/", response_class=FileResponse)
    async def api_index():
        return FileResponse(project_root / "pages" / "studio-v2.html")

    return application


app = create_app()
