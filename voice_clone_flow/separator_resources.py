from __future__ import annotations

import hashlib
import os
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

MODEL_FILENAME = "htdemucs_ft_vocals_fp16weights.onnx"
MODEL_SIZE = 165_612_636
MODEL_SHA256 = "0cbe651f535415c9d26a7bb614f7d322dd5a080fa0298f2e50f478030a994dce"
MODEL_URLS = (
    f"https://hf-mirror.com/StemSplitio/htdemucs-ft-vocals-onnx/resolve/main/{MODEL_FILENAME}",
    f"https://huggingface.co/StemSplitio/htdemucs-ft-vocals-onnx/resolve/main/{MODEL_FILENAME}",
)


class SeparatorResourceManager:
    def __init__(self, plugin_data_dir: Path, *, expected_size: int = MODEL_SIZE,
                 expected_sha256: str = MODEL_SHA256, download_urls: tuple[str, ...] = MODEL_URLS) -> None:
        self.model_path = Path(plugin_data_dir) / "models" / "separator" / MODEL_FILENAME
        self.expected_size = expected_size
        self.expected_sha256 = expected_sha256.lower()
        self.download_urls = tuple(url for url in download_urls if url)
        self.installing = False
        self.error = ""
        self.downloaded_bytes = 0
        self._lock = threading.Lock()

    def _valid(self, path: Path) -> bool:
        if not path.is_file() or path.stat().st_size != self.expected_size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest() == self.expected_sha256

    def status(self) -> dict[str, object]:
        ready = self._valid(self.model_path)
        return {"ready": ready, "installed": self.model_path.is_file(), "installing": self.installing,
                "error": self.error, "downloaded_bytes": self.downloaded_bytes, "total_bytes": self.expected_size,
                "progress_percent": round(self.downloaded_bytes * 100 / self.expected_size) if self.expected_size else 0,
                "resolved_model": str(self.model_path), "model_size_bytes": self.expected_size,
                "sha256": self.expected_sha256, "sources": list(self.download_urls)}

    def start_install(self, local_source: str = "") -> bool:
        with self._lock:
            if self.installing:
                return False
            self.installing = True
            self.error = ""
            part = self.model_path.with_suffix(".onnx.part")
            self.downloaded_bytes = part.stat().st_size if part.is_file() else 0
        threading.Thread(target=self._install, args=(local_source,), daemon=True,
                         name="voice-separator-install").start()
        return True

    def _install(self, local_source: str) -> None:
        temporary = self.model_path.with_suffix(".onnx.part")
        try:
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            source = Path(local_source).expanduser() if local_source.strip() else None
            if source and source.is_file():
                temporary.unlink(missing_ok=True)
                shutil.copyfile(source, temporary)
                self.downloaded_bytes = temporary.stat().st_size
            else:
                self._download(temporary)
            if not self._valid(temporary):
                raise RuntimeError("模型大小或 SHA-256 校验失败")
            os.replace(temporary, self.model_path)
            self.error = ""
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            self.error = str(exc)
        finally:
            self.installing = False

    def _download(self, temporary: Path) -> None:
        last_error: Exception | None = None
        for url in self.download_urls:
            try:
                existing = temporary.stat().st_size if temporary.is_file() else 0
                headers = {"User-Agent": "VoiceCloneFlow/0.1"}
                if existing:
                    headers["Range"] = f"bytes={existing}-"
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=180) as response:
                    resumable = existing > 0 and getattr(response, "status", None) == 206
                    mode = "ab" if resumable else "wb"
                    self.downloaded_bytes = existing if resumable else 0
                    with temporary.open(mode) as target:
                        while block := response.read(1024 * 1024):
                            target.write(block)
                            self.downloaded_bytes += len(block)
                return
            except (OSError, urllib.error.URLError) as exc:
                last_error = exc
                self.downloaded_bytes = temporary.stat().st_size if temporary.is_file() else 0
        raise RuntimeError(f"模型下载失败：{last_error}")

    def delete_managed(self) -> bool:
        removed = False
        for path in (self.model_path, self.model_path.with_suffix(".onnx.part")):
            if path.is_file():
                path.unlink()
                removed = True
        self.error = ""
        self.downloaded_bytes = 0
        return removed
