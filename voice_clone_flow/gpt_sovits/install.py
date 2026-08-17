"""GPT-SoVITS distribution installer.

VoiceClone Flow does not bundle the 14 GB+ distribution. A user-provided archive URL
(zip of an integrated package, e.g. the nvidia50 build) is downloaded with
progress/resume, extracted into ``runtime/gpt_sovits``, and registered in the
GPT-SoVITS config. Optional project-bundled patches under
``runtime/gpt_sovits_patches`` are copied over the extracted tree afterwards
(used for files that have no public download source).
"""

import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from .config import GPTSoVITSConfig, probe_installation
from ..platform_runtime import PlatformProfile, detect_platform_profile
from .linux_runtime import LinuxPythonEnvironment


LINUX_SOURCE_REVISION = "d523079fc05d9a8028d6085bffe4a2757c32abb6"
LINUX_SOURCE_URL = (
    f"https://github.com/RVC-Boss/GPT-SoVITS/archive/{LINUX_SOURCE_REVISION}.zip"
)
LINUX_PRETRAINED_URLS = (
    "https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/pretrained_models.zip",
    "https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/pretrained_models.zip",
    "https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/pretrained_models.zip",
)
LINUX_G2PW_URLS = (
    "https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/G2PWModel.zip",
    "https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/G2PWModel.zip",
    "https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/G2PWModel.zip",
)
LINUX_REQUIRED_MODEL_PATHS = (
    "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large/config.json",
    "GPT_SoVITS/pretrained_models/chinese-hubert-base/config.json",
    "GPT_SoVITS/pretrained_models/s1v3.ckpt",
    "GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth",
    "GPT_SoVITS/pretrained_models/v2Pro/s2Dv2Pro.pth",
    "GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt",
)
MINIMUM_FREE_BYTES = {"windows": 24 * 1024**3, "linux": 12 * 1024**3}


# Content inside the integrated package that the project never uses. The
# previous hand-trimmed build (10.1 GB) worked without all of these, so they
# are removed after extraction to keep a fresh install at ~10 GB:
#   - tools/asr + tools/uvr5: the pack's own ASR/vocal-separation, replaced by
#     the app's Qwen3-ASR and HT-Demucs separator;
#   - v1/v2/v3/v4 pretrained variants: only used when switching the engine to
#     those versions, which VoiceClone Flow never does (it uses v2Pro + trained assets).
_SLIM_PATHS = (
    "tools/asr",
    "tools/uvr5",
    "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained",
    "GPT_SoVITS/pretrained_models/gsv-v4-pretrained",
    "GPT_SoVITS/pretrained_models/s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt",
    "GPT_SoVITS/pretrained_models/s2D488k.pth",
    "GPT_SoVITS/pretrained_models/s2G488k.pth",
    "GPT_SoVITS/pretrained_models/s2Gv3.pth",
)


class GPTSoVITSInstallCancelled(RuntimeError):
    pass


class _InstallState:
    def __init__(self) -> None:
        self.installing = False
        self.cancel_requested = threading.Event()
        self.phase = "idle"
        self.current_file = ""
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.detail = ""
        self.error = ""
        self.started_at: float | None = None
        self.source = ""
        self.overall_percent = 0
        self.last_output = ""

    def set_progress(
        self,
        phase: str,
        current_file: str = "",
        downloaded: int = 0,
        total: int = 0,
        detail: str = "",
    ) -> None:
        self.phase = phase
        self.current_file = current_file
        self.downloaded_bytes = downloaded
        self.total_bytes = total
        self.detail = detail

    def set_stage(self, phase: str, detail: str, *, progress_percent: int) -> None:
        self.phase = phase
        self.current_file = ""
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.detail = detail
        self.overall_percent = max(0, min(100, progress_percent))

    def set_output(self, output: str) -> None:
        self.last_output = output[-1000:]
        if output:
            self.detail = output[-300:]

    def snapshot(self) -> dict:
        elapsed = time.monotonic() - self.started_at if self.started_at else 0
        transferring = self.phase in ("download", "extracting")
        speed = (
            self.downloaded_bytes / elapsed
            if transferring and elapsed > 0 and self.downloaded_bytes
            else 0
        )
        remaining = self.total_bytes - self.downloaded_bytes
        return {
            "installing": self.installing,
            "cancelling": self.installing and self.cancel_requested.is_set(),
            "phase": self.phase,
            "current_file": self.current_file,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "detail": self.detail,
            "progress_percent": self.overall_percent,
            "phase_progress_percent": round(self.downloaded_bytes * 100 / self.total_bytes)
            if self.total_bytes
            else None,
            "download_speed_bytes": round(speed),
            "eta_seconds": round(remaining / speed)
            if speed > 0 and remaining > 0
            else 0
            if self.total_bytes and remaining <= 0
            else None,
            "elapsed_seconds": round(elapsed),
            "error": self.error,
            "source": self.source,
            "last_output": self.last_output,
        }


class GPTSoVITSInstallManager:
    def __init__(
        self,
        project_root: Path,
        config: GPTSoVITSConfig,
        platform_profile: PlatformProfile | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.config = config
        self.install_dir = self.project_root / "runtime" / "gpt_sovits"
        self.patches_dir = Path(__file__).resolve().parents[2] / "runtime_patches"
        self.bundled_7zr = self.patches_dir / "tools" / "7zr.exe"
        self.platform_profile = platform_profile or detect_platform_profile()
        self._profile_injected = platform_profile is not None
        self.download_dir = self.project_root / "downloads" / "gpt_sovits"
        self.state = _InstallState()
        self._lock = threading.Lock()
        self._device_profile: tuple[str, bool] | None = None

    def status(self) -> dict:
        probe = probe_installation(
            self.install_dir if self.install_dir.is_dir() else None,
            system=self.platform_profile.system,
        )
        snapshot = self.state.snapshot()
        disk_free = shutil.disk_usage(self.project_root).free
        device, is_half = self._runtime_device()
        missing_models = (
            self._missing_linux_models()
            if self.platform_profile.system == "linux" and self.install_dir.is_dir()
            else []
        )
        return {
            **snapshot,
            "installed": probe.ok and not missing_models,
            "present": self.install_dir.is_dir(),
            "install_dir": str(self.install_dir),
            "patches_dir": str(self.patches_dir) if self.patches_dir.is_dir() else "",
            "external_configured": bool(self.config.values()["install_dir"]),
            "download_url": self.config.values().get("download_url"),
            "platform": self.platform_profile.system,
            "architecture": self.platform_profile.architecture,
            "install_strategy": "linux_source"
            if self.platform_profile.system == "linux"
            else "windows_bundle",
            "source_revision": LINUX_SOURCE_REVISION
            if self.platform_profile.system == "linux"
            else "v2pro-20250604",
            "disk_free_bytes": disk_free,
            "minimum_free_bytes": MINIMUM_FREE_BYTES.get(self.platform_profile.system, 0),
            "missing_models": missing_models,
            "runtime_device": device,
            "half_precision": is_half,
        }

    def remove_install(self) -> dict:
        """Delete the project-bundled GPT-SoVITS engine and clear the config."""

        if self.state.installing:
            raise RuntimeError("请先取消正在进行的下载")
        if self.install_dir.is_dir():
            shutil.rmtree(self.install_dir)
        if self.config.values()["install_dir"] == str(self.install_dir):
            self.config.save(install_dir=None)
        return self.status()

    def start_install(self, url: str | None) -> bool:
        with self._lock:
            if self.state.installing:
                return False
            if not self._profile_injected:
                self.platform_profile = detect_platform_profile()
            self._device_profile = None
            if self.platform_profile.system not in {"windows", "linux"}:
                raise RuntimeError("GPT-SoVITS 一键安装目前仅支持 Windows 和 Linux")
            if self.platform_profile.architecture not in {"x86_64", "arm64"}:
                raise RuntimeError(
                    f"当前 CPU 架构不支持一键安装：{self.platform_profile.architecture}"
                )
            if (
                self.platform_profile.system == "windows"
                and self.platform_profile.architecture != "x86_64"
            ):
                raise RuntimeError("Windows 一键安装目前仅支持 x86_64 架构")
            required = MINIMUM_FREE_BYTES[self.platform_profile.system]
            available = shutil.disk_usage(self.project_root).free
            if available < required:
                raise RuntimeError(
                    f"磁盘空间不足：至少需要 {required / 1024**3:.0f} GB，"
                    f"当前可用 {available / 1024**3:.1f} GB"
                )
            url = self._effective_download_url((url or "").strip())
            if not url:
                raise ValueError("请提供 GPT-SoVITS 整合包下载地址（7z 或 zip）")
            self.state.installing = True
            self.state.cancel_requested.clear()
            self.state.error = ""
            self.state.last_output = ""
            self.state.started_at = time.monotonic()
            self.state.set_stage("preparing", "检测系统并准备下载", progress_percent=0)
        threading.Thread(
            target=self._install,
            args=(url,),
            name="gpt-sovits-install",
            daemon=True,
        ).start()
        return True

    def _effective_download_url(self, configured_url: str) -> str:
        if self.platform_profile.system == "linux":
            return LINUX_SOURCE_URL
        return configured_url

    def cancel_install(self) -> bool:
        if not self.state.installing:
            return False
        self.state.cancel_requested.set()
        self.state.phase = "cancelling"
        return True

    def _install(self, url: str) -> None:
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
            archive = self._obtain_archive(url)
            self.state.set_stage("download", "源码或整合包下载完成", progress_percent=8)
            if self.state.cancel_requested.is_set():
                raise GPTSoVITSInstallCancelled()
            if self.platform_profile.system == "linux" and self._has_linux_source_tree():
                self.state.set_stage("extracting", "复用已解压的官方源码", progress_percent=12)
            else:
                self.state.set_progress("extracting", archive.name, 0, 0, detail="准备解压…")
                self._extract(archive)
                self.state.set_stage("extracting", "源码或整合包解压完成", progress_percent=15)
            if self.platform_profile.system == "linux":
                self._prepare_linux_runtime()
            self.state.set_stage("patching", "准备应用项目补丁", progress_percent=92)
            self._apply_patches()
            self._configure_v2pro_default()
            self.state.set_stage("cleaning", "清理冗余文件", progress_percent=96)
            self._slim_install()
            self.state.set_stage("verifying", "校验安装完整性", progress_percent=99)
            if self.platform_profile.system == "linux":
                missing = self._missing_linux_models()
                if missing:
                    raise RuntimeError("预训练模型安装不完整：" + "、".join(missing))
            probe = probe_installation(
                self.install_dir, system=self.platform_profile.system
            )
            if not probe.ok:
                raise RuntimeError(probe.error or "解压后未找到可用的 GPT-SoVITS 安装")
            self.config.save(install_dir=str(self.install_dir))
            # 安装成功后删除下载缓存；失败时保留以便断点续传/重试。
            archive.unlink(missing_ok=True)
            archive.with_suffix(archive.suffix + ".part").unlink(missing_ok=True)
            self.state.set_stage("complete", "安装完成，可以启动 TTS 服务", progress_percent=100)
        except GPTSoVITSInstallCancelled:
            self.state.error = ""
            self.state.set_progress("idle", "", 0, 0)
        except Exception as exc:
            if self.state.cancel_requested.is_set():
                self.state.error = ""
                self.state.set_progress("idle", "", 0, 0)
            else:
                self.state.error = str(exc)
                self.state.phase = "error"
        finally:
            self.state.installing = False
            self.state.cancel_requested.clear()
            self.state.started_at = None

    def _prepare_linux_runtime(self) -> None:
        self.state.set_stage("python", "创建独立 Python 环境", progress_percent=15)
        environment = LinuxPythonEnvironment(
            self.install_dir,
            progress=self.state.set_output,
            cancelled=self.state.cancel_requested.is_set,
        )
        environment.ensure()
        self.state.set_stage("dependencies", "安装 GPT-SoVITS Python 依赖", progress_percent=25)
        environment.install_requirements(self.install_dir / "requirements.txt")
        self.state.set_stage("models", "下载 GPT-SoVITS v2Pro 预训练模型", progress_percent=55)
        self._install_linux_model_archive(
            "pretrained_models.zip", LINUX_PRETRAINED_URLS, self.install_dir / "GPT_SoVITS"
        )
        self.state.set_stage("models", "下载中文前端模型", progress_percent=82)
        self._install_linux_model_archive(
            "G2PWModel.zip", LINUX_G2PW_URLS, self.install_dir / "GPT_SoVITS" / "text"
        )
        missing = self._missing_linux_models()
        if missing:
            raise RuntimeError("预训练模型包缺少必需文件：" + "、".join(missing))
        self.state.set_stage("models", "Linux 运行环境准备完成", progress_percent=90)

    def _has_linux_source_tree(self) -> bool:
        return all(
            path.is_file()
            for path in (
                self.install_dir / "api_v2.py",
                self.install_dir / "requirements.txt",
            )
        ) and (self.install_dir / "GPT_SoVITS").is_dir()

    def _missing_linux_models(self) -> list[str]:
        return [relative for relative in LINUX_REQUIRED_MODEL_PATHS if not (self.install_dir / relative).is_file()]

    def _install_linux_model_archive(
        self, filename: str, urls: tuple[str, ...], destination: Path
    ) -> None:
        if filename == "pretrained_models.zip" and not self._missing_linux_models():
            return
        if filename == "G2PWModel.zip" and (destination / "G2PWModel").is_dir():
            return
        archive = self.download_dir / filename
        last_error: Exception | None = None
        for url in urls:
            try:
                self._download(url, archive, phase="models")
                self._extract_zip_into(archive, destination)
                archive.unlink(missing_ok=True)
                return
            except GPTSoVITSInstallCancelled:
                raise
            except Exception as exc:
                last_error = exc
                archive.unlink(missing_ok=True)
                archive.with_suffix(archive.suffix + ".part").unlink(missing_ok=True)
        raise RuntimeError(f"下载或解压 {filename} 失败：{last_error}")

    def _extract_zip_into(self, archive: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            total = sum(item.file_size for item in members if not item.is_dir())
            done = 0
            for member in members:
                if self.state.cancel_requested.is_set():
                    raise GPTSoVITSInstallCancelled()
                target = self._safe_member_path(destination, member.filename)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, target.open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
                        done += len(chunk)
                        self.state.set_progress(
                            "models", member.filename, done, total, detail=f"解压 {member.filename}"
                        )

    def _obtain_archive(self, url: str) -> Path:
        filename = Path(urlsplit(url).path).name or "gpt-sovits.zip"
        archive = self.download_dir / filename
        if archive.is_file() and archive.stat().st_size > 0:
            archive.with_suffix(archive.suffix + ".part").unlink(missing_ok=True)
            self.state.set_progress(
                "download",
                filename,
                archive.stat().st_size,
                archive.stat().st_size,
                detail="复用已下载的整合包",
            )
            return archive
        self.state.set_progress("download", filename, 0, 0, detail="准备下载…")
        self._download(url, archive)
        return archive

    def _download(self, url: str, destination: Path, *, phase: str = "download") -> None:
        opener = urllib.request.build_opener()
        attempt = 0
        while True:
            attempt += 1
            if self.state.cancel_requested.is_set():
                raise GPTSoVITSInstallCancelled()
            resume = (
                destination.with_suffix(destination.suffix + ".part").stat().st_size
                if destination.with_suffix(destination.suffix + ".part").is_file()
                else 0
            )
            headers = {"User-Agent": "VoiceCloneFlow/0.1"}
            if resume > 0:
                headers["Range"] = f"bytes={resume}-"
            request = urllib.request.Request(url, headers=headers)
            partial = destination.with_suffix(destination.suffix + ".part")
            self.state.source = urlsplit(url).hostname or "下载源"
            try:
                with (
                    opener.open(request, timeout=60) as response,
                    partial.open(
                        "ab" if resume and getattr(response, "status", None) == 206 else "wb"
                    ) as target,
                ):
                    resumable = resume > 0 and getattr(response, "status", None) == 206
                    downloaded = resume if resumable else 0
                    total = downloaded + int(response.headers.get("Content-Length") or 0)
                    self.state.set_progress(
                        phase,
                        destination.name,
                        downloaded,
                        total,
                        detail=f"来源：{self.state.source}",
                    )
                    while chunk := response.read(1024 * 1024):
                        if self.state.cancel_requested.is_set():
                            raise GPTSoVITSInstallCancelled()
                        target.write(chunk)
                        downloaded += len(chunk)
                        self.state.set_progress(
                            phase,
                            destination.name,
                            downloaded,
                            total,
                            detail=f"来源：{self.state.source}",
                        )
                os.replace(partial, destination)
                return
            except GPTSoVITSInstallCancelled:
                raise
            except (OSError, urllib.error.URLError) as exc:
                if attempt > 2:
                    raise RuntimeError(
                        f"下载 {destination.name} 失败（{self.state.source}）：{exc}"
                    ) from exc
                time.sleep(2)

    def _extract(self, archive: Path) -> None:
        if archive.suffix.lower() == ".7z":
            self._extract_7z(archive)
            return
        self._extract_zip(archive)

    @staticmethod
    def _safe_member_path(root: Path, name: str) -> Path:
        """Resolve an archive member under ``root``, rejecting traversal."""

        parts = [
            part for part in name.replace("\\", "/").split("/") if part not in ("", ".")
        ]
        if any(part == ".." or ":" in part for part in parts):
            raise RuntimeError(f"整合包包含不安全的文件路径：{name}")
        return root.joinpath(*parts)

    def _extract_zip(self, archive: Path) -> None:
        temporary = self.install_dir.with_name(self.install_dir.name + ".tmp")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                members = bundle.infolist()
                base = None
                for member in members:
                    if member.is_dir():
                        continue
                    first = member.filename.split("/", 1)[0]
                    if first:
                        base = first
                        break
                total = sum(
                    member.file_size for member in members if not member.is_dir()
                )
                done = 0
                for member in members:
                    if self.state.cancel_requested.is_set():
                        raise GPTSoVITSInstallCancelled()
                    target = self._safe_member_path(temporary, member.filename)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with (
                        bundle.open(member) as source,
                        target.open("wb") as destination,
                    ):
                        while chunk := source.read(1024 * 1024):
                            if self.state.cancel_requested.is_set():
                                raise GPTSoVITSInstallCancelled()
                            destination.write(chunk)
                            done += len(chunk)
                            self.state.set_progress(
                                "extracting",
                                member.filename,
                                done,
                                total,
                                detail=f"解压 {member.filename}",
                            )
                if base and all(
                    member.filename == base or member.filename.startswith(f"{base}/")
                    for member in members
                ):
                    extracted = temporary / base
                    if extracted.is_dir():
                        if self.install_dir.exists():
                            shutil.rmtree(self.install_dir)
                        shutil.move(str(extracted), str(self.install_dir))
                        return
                if self.install_dir.exists():
                    shutil.rmtree(self.install_dir)
                self.install_dir.mkdir(parents=True, exist_ok=True)
                for child in temporary.iterdir():
                    shutil.move(str(child), str(self.install_dir))
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _extract_7z(self, archive: Path) -> None:
        """Extract a .7z integrated package with live progress via 7-Zip."""

        # Project-bundled extractor first so a fresh install works even after
        # the previous engine directory (which also contained 7zr.exe) was
        # moved away or deleted; fall back to the engine's own copy, then to
        # a system 7-Zip.
        seven_zip = self._resolve_7zip()
        temporary = self.install_dir.with_name(self.install_dir.name + ".tmp")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True, exist_ok=True)
        total = archive.stat().st_size if archive.is_file() else 0
        try:
            process = subprocess.Popen(
                [seven_zip, "x", str(archive), f"-o{temporary}", "-y", "-bb1", "-bsp1"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            pending_name = ""
            for line in process.stdout:
                if self.state.cancel_requested.is_set():
                    process.kill()
                    raise GPTSoVITSInstallCancelled()
                match = re.match(r"\s*(\d{1,3})\s*%", line)
                if match:
                    percent = int(match.group(1))
                    done = int(total * percent / 100) if total else 0
                    self.state.set_progress(
                        "extracting",
                        pending_name or Path(archive).name,
                        done,
                        total,
                        detail=f"解压 {percent}%",
                    )
                    pending_name = ""
                    continue
                stripped = line.strip()
                if stripped.startswith("- "):
                    pending_name = stripped[2:].strip()
            returncode = process.wait()
            if returncode != 0:
                raise RuntimeError(f"7-Zip 解压失败（退出码 {returncode}）")
            children = list(temporary.iterdir())
            if self.install_dir.exists():
                shutil.rmtree(self.install_dir)
            if len(children) == 1 and children[0].is_dir():
                shutil.move(str(children[0]), str(self.install_dir))
            else:
                self.install_dir.mkdir(parents=True, exist_ok=True)
                for child in children:
                    shutil.move(str(child), str(self.install_dir))
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _resolve_7zip(self) -> str:
        if self.platform_profile.system == "linux":
            seven_zip = (
                self.platform_profile.archive_tool
                or shutil.which("7z")
                or shutil.which("7za")
            )
            if seven_zip:
                return str(seven_zip)
            raise RuntimeError(
                "Linux 缺少 7-Zip，请在服务器执行："
                + self.platform_profile.install_hints.get(
                    "7zip", "sudo apt install -y p7zip-full"
                )
            )
        bundled = (self.bundled_7zr, self.install_dir / "tools" / "7zr.exe")
        seven_zip = next((str(path) for path in bundled if path.is_file()), None)
        seven_zip = seven_zip or shutil.which("7z") or shutil.which("7za")
        if not seven_zip:
            raise RuntimeError("未找到 7-Zip，请安装 7-Zip 后重试。")
        return str(seven_zip)

    def _apply_patches(self) -> None:
        if not self.patches_dir.is_dir():
            self.state.set_progress("patching", "", 1, 1, detail="无需应用补丁")
            return
        files = [source for source in self.patches_dir.rglob("*") if source.is_file()]
        if self.platform_profile.system == "linux":
            files = [
                source
                for source in files
                if source.suffix.lower() != ".exe"
                and "runtime" not in source.relative_to(self.patches_dir).parts
            ]
        for index, source in enumerate(files, start=1):
            if self.state.cancel_requested.is_set():
                raise GPTSoVITSInstallCancelled()
            relative = source.relative_to(self.patches_dir)
            target = self.install_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            self.state.set_progress(
                "patching",
                str(relative),
                index,
                len(files),
                detail=f"补丁 {index}/{len(files)}",
            )
        # The integrated runtime ships a users.pth pointing at the packager's
        # absolute path; rewrite it to this installation so a downloaded copy
        # is self-contained.
        users_pth = self.install_dir / "runtime" / "Lib" / "site-packages" / "users.pth"
        if users_pth.parent.is_dir():
            base = str(self.install_dir)
            users_pth.write_text(
                "\n".join(
                    [
                        base,
                        f"{base}/GPT_SoVITS/BigVGAN",
                        f"{base}/tools",
                        f"{base}/tools/asr",
                        f"{base}/GPT_SoVITS",
                        f"{base}/tools/uvr5",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

    def _configure_v2pro_default(self) -> None:
        config = self.install_dir / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
        if not config.is_file():
            raise RuntimeError(
                "缺少 GPT-SoVITS 推理配置：GPT_SoVITS/configs/tts_infer.yaml"
            )
        text = config.read_text(encoding="utf-8")
        marker = "\nv1:"
        if marker not in text:
            raise RuntimeError("GPT-SoVITS 推理配置格式不兼容，未找到 v1 配置段")
        _, remainder = text.split(marker, 1)
        device, is_half = self._runtime_device()
        custom = "\n".join(
            [
                "custom:",
                "  version: v2Pro",
                f"  device: {device}",
                f"  is_half: {str(is_half).lower()}",
                "  bert_base_path: GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large",
                "  cnhuhbert_base_path: GPT_SoVITS/pretrained_models/chinese-hubert-base",
                "  t2s_weights_path: GPT_SoVITS/pretrained_models/s1v3.ckpt",
                "  vits_weights_path: GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth",
            ]
        )
        config.write_text(f"{custom}{marker}{remainder}", encoding="utf-8")

    def _runtime_device(self) -> tuple[str, bool]:
        if self._device_profile is not None:
            return self._device_profile
        if self.platform_profile.system == "windows":
            self._device_profile = ("cuda", True)
            return self._device_profile
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            try:
                result = subprocess.run(
                    [nvidia_smi, "-L"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                if result.returncode == 0:
                    self._device_profile = ("cuda", True)
                    return self._device_profile
            except (OSError, subprocess.SubprocessError):
                pass
        self._device_profile = ("cpu", False)
        return self._device_profile

    def _slim_install(self) -> None:
        """Remove package content the project never uses (see _SLIM_PATHS).

        Runs after patches are applied. Missing targets are ignored so the
        list stays compatible with future pack versions.
        """

        targets = [self.install_dir / rel for rel in _SLIM_PATHS]
        for index, target in enumerate(targets, start=1):
            if self.state.cancel_requested.is_set():
                raise GPTSoVITSInstallCancelled()
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.is_file():
                target.unlink(missing_ok=True)
            self.state.set_progress(
                "cleaning",
                str(target.relative_to(self.install_dir)),
                index,
                len(targets),
                detail=f"清理冗余文件 {index}/{len(targets)}",
            )
