from __future__ import annotations

import json
import os
import re
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5


@dataclass
class VoiceAsset:
    id: str
    name: str
    status: str = "ready"
    engine: str = "gpt_sovits"
    gpt_weights_path: str = ""
    sovits_weights_path: str = ""
    refer_audio_path: str = ""
    reference_language: str = "zh"
    reference_text: str = ""
    dataset_dir: str = ""
    training_stage: str = ""
    error_message: str = ""
    training_preset: str = "standard"
    gpt_epochs: int = 15
    sovits_epochs: int = 30
    training_progress: dict | None = None
    dir_name: str = ""
    source: str = "local"
    remote_voice_id: str = ""
    remote_metadata: dict | None = None
    remote_provider_id: str = ""


@dataclass(frozen=True)
class VoiceDiscovery:
    imported: tuple[str, ...] = ()
    skipped: tuple[dict, ...] = ()


LANGUAGE_ALIASES = {
    "zh": "zh", "cn": "zh", "chinese": "zh",
    "ja": "ja", "jp": "ja", "japanese": "ja",
    "en": "en", "english": "en",
    "ko": "ko", "kr": "ko", "korean": "ko",
    "yue": "yue", "cantonese": "yue",
}


def _audio_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as source:
            return source.getnframes() / source.getframerate()
    except (OSError, EOFError, wave.Error, ZeroDivisionError):
        return None


def readable_voice_dir_name(name: str, asset_id: str, existing: set[str] | None = None) -> str:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", str(name).strip()).strip(" ._") or "voice"
    suffix = asset_id[:8]
    candidate = f"{safe}--{suffix}"
    existing = existing or set()
    return candidate if candidate not in existing else f"{safe}--{asset_id[:12]}"


class VoiceRegistry:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "voices" / "registry.json"

    def list(self) -> list[VoiceAsset]:
        if not self.path.is_file():
            return []
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
            fields = VoiceAsset.__dataclass_fields__
            return [VoiceAsset(**{key: value for key, value in row.items() if key in fields}) for row in rows]
        except (OSError, TypeError, ValueError):
            return []

    def get(self, asset_id: str) -> VoiceAsset | None:
        return next((asset for asset in self.list() if asset.id == asset_id), None)

    def save(self, asset: VoiceAsset) -> VoiceAsset:
        rows = [item for item in self.list() if item.id != asset.id]
        rows.append(asset)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps([asdict(item) for item in rows], ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
        return asset

    def create(self, name: str, **values) -> VoiceAsset:
        asset_id = uuid4().hex
        existing = {item.dir_name for item in self.list() if item.dir_name}
        values.setdefault("dir_name", readable_voice_dir_name(name, asset_id, existing))
        return self.save(VoiceAsset(id=asset_id, name=name.strip(), **values))

    def upsert_remote_voice(self, metadata: dict) -> VoiceAsset:
        remote_id = str(metadata.get("id", "")).strip()
        if not remote_id:
            raise ValueError("远程音色缺少稳定 id")
        asset = next((item for item in self.list() if item.source == "remote" and item.remote_voice_id == remote_id), None)
        if asset is None:
            asset = VoiceAsset(
                id=f"remote_{remote_id}",
                name=str(metadata.get("name") or remote_id),
                source="remote",
                remote_voice_id=remote_id,
                remote_provider_id=f"voice_clone_flow_remote_{remote_id}",
            )
        asset.name = str(metadata.get("name") or asset.name or remote_id)
        asset.status = str(metadata.get("status") or "ready")
        asset.reference_language = str(metadata.get("language") or metadata.get("reference_language") or "zh")
        asset.reference_text = str(metadata.get("reference_text") or "")
        asset.gpt_weights_path = str(metadata.get("gpt_weights_path") or "")
        asset.sovits_weights_path = str(metadata.get("sovits_weights_path") or "")
        asset.refer_audio_path = str(metadata.get("reference_audio_path") or metadata.get("refer_audio_path") or "")
        asset.remote_metadata = dict(metadata)
        return self.save(asset)

    def disable_missing_remote_voice_ids(self, remote_ids: set[str]) -> list[VoiceAsset]:
        changed: list[VoiceAsset] = []
        for asset in self.list():
            if asset.source == "remote" and asset.remote_voice_id not in remote_ids and asset.status != "disabled":
                asset.status = "disabled"
                changed.append(self.save(asset))
        return changed

    def update_reference(
        self,
        asset_id: str,
        audio_path: str,
        reference_text: str,
    ) -> VoiceAsset:
        asset = self.get(asset_id)
        if asset is None:
            raise KeyError(asset_id)
        source = Path(str(audio_path).strip()).expanduser()
        if not source.is_absolute():
            raise ValueError("参考音频必须使用绝对路径")
        if source.suffix.lower() != ".wav":
            raise ValueError("参考音频必须是 WAV 文件")
        if not source.is_file():
            raise FileNotFoundError(f"参考音频不存在：{source}")
        text = str(reference_text).strip()
        if not text:
            raise ValueError("参考音频文本不能为空")
        asset.refer_audio_path = str(source.resolve())
        asset.reference_text = text
        return self.save(asset)

    def migrate_readable_directories(self) -> list[tuple[Path, Path]]:
        root = self.path.parent
        moved: list[tuple[Path, Path]] = []
        for asset in self.list():
            if asset.status == "processing":
                continue
            old_name = asset.dir_name or asset.id
            new_name = asset.dir_name or readable_voice_dir_name(asset.name, asset.id)
            source, target = root / old_name, root / new_name
            if source != target and source.is_dir() and not target.exists():
                source.rename(target)
                moved.append((source, target))
            asset.dir_name = new_name
            if target.is_dir():
                old_prefix, new_prefix = str(source), str(target)
                for field in ("gpt_weights_path", "sovits_weights_path", "refer_audio_path", "dataset_dir"):
                    value = getattr(asset, field)
                    if value and value.lower().startswith(old_prefix.lower()):
                        setattr(asset, field, new_prefix + value[len(old_prefix):])
                self.save(asset)
        return moved

    def discover_external(self) -> VoiceDiscovery:
        root = self.path.parent
        root.mkdir(parents=True, exist_ok=True)
        registered = {item.dir_name or item.id for item in self.list()}
        imported: list[str] = []
        skipped: list[dict] = []
        for directory in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name.lower()):
            if directory.name in registered:
                continue
            gpt, sovits = directory / "gpt.ckpt", directory / "sovits.pth"
            missing = [path.name for path in (gpt, sovits) if not path.is_file()]
            if missing:
                skipped.append({"dir_name": directory.name, "reason": f"缺少 {', '.join(missing)}"})
                continue
            audio, language, text, dataset = self._external_reference(directory)
            asset_id = uuid5(NAMESPACE_URL, f"astrbot-plugin-voice-clone-flow:{directory.name}").hex
            self.save(
                VoiceAsset(
                    id=asset_id,
                    name=directory.name,
                    status="ready" if language else "needs_configuration",
                    gpt_weights_path=str(gpt.resolve()),
                    sovits_weights_path=str(sovits.resolve()),
                    refer_audio_path=str(audio.resolve()) if audio else "",
                    reference_language=language,
                    reference_text=text,
                    dataset_dir=str(dataset.resolve()) if dataset else "",
                    training_stage="外部音色已导入",
                    dir_name=directory.name,
                )
            )
            imported.append(directory.name)
        return VoiceDiscovery(tuple(imported), tuple(skipped))

    def _external_reference(self, directory: Path) -> tuple[Path | None, str, str, Path | None]:
        root = directory.resolve()
        wavs = [path for path in directory.rglob("*.wav") if root in path.resolve().parents]
        by_name: dict[str, list[Path]] = {}
        for path in wavs:
            by_name.setdefault(path.name.lower(), []).append(path)
        manifest_rows: list[tuple[Path, str, str, Path]] = []
        for manifest in sorted(directory.rglob("*.list")):
            for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split("|", 3)
                if len(parts) != 4:
                    continue
                candidates = by_name.get(Path(parts[0]).name.lower(), [])
                if not candidates:
                    continue
                language = LANGUAGE_ALIASES.get(parts[2].strip().lower(), "")
                manifest_rows.append((candidates[0], language, parts[3].strip(), manifest.parent))
        preferred = [row for row in manifest_rows if (duration := _audio_seconds(row[0])) is not None and 3 <= duration <= 10]
        if preferred or manifest_rows:
            return (preferred or manifest_rows)[0]
        preferred_wavs = [path for path in wavs if (duration := _audio_seconds(path)) is not None and 3 <= duration <= 10]
        audio = (preferred_wavs or wavs or [None])[0]
        return audio, "", "", audio.parent if audio else None
