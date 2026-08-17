from __future__ import annotations

import os
import shutil
import tarfile
import urllib.request
from pathlib import Path
from typing import Callable


def ffmpeg_download_url(architecture: str) -> str:
    asset = "arm64" if architecture == "arm64" else "amd64"
    return f"https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{asset}-static.tar.xz"


def uv_download_url(architecture: str) -> str:
    asset = "aarch64" if architecture == "arm64" else "x86_64"
    return f"https://github.com/astral-sh/uv/releases/latest/download/uv-{asset}-unknown-linux-gnu.tar.gz"


class LinuxToolInstaller:
    def __init__(
        self, target_dir: Path, opener: Callable = urllib.request.urlopen
    ) -> None:
        self.target_dir = Path(target_dir)
        self.opener = opener

    def download(
        self,
        url: str,
        destination: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        request = urllib.request.Request(
            url, headers={"User-Agent": "VoiceCloneFlow/0.2"}
        )
        existing = temporary.stat().st_size if temporary.is_file() else 0
        if existing:
            request.add_header("Range", f"bytes={existing}-")
        with self.opener(request, timeout=300) as response:
            resumable = existing > 0 and getattr(response, "status", None) == 206
            downloaded = existing if resumable else 0
            total = downloaded + int(response.headers.get("Content-Length", 0))
            with temporary.open("ab" if resumable else "wb") as output:
                while block := response.read(1024 * 1024):
                    output.write(block)
                    downloaded += len(block)
                    if progress:
                        progress(downloaded, total)
        os.replace(temporary, destination)
        return destination

    def install_ffmpeg_archive(self, archive: Path) -> tuple[Path, Path]:
        return self._install_archive_members(archive, ("ffmpeg", "ffprobe"))

    def install_uv_archive(self, archive: Path) -> tuple[Path, Path]:
        return self._install_archive_members(archive, ("uv", "uvx"))

    def _install_archive_members(
        self, archive: Path, names: tuple[str, str]
    ) -> tuple[Path, Path]:
        self.target_dir.mkdir(parents=True, exist_ok=True)
        installed: list[Path] = []
        with tarfile.open(archive, "r:*") as package:
            members = {
                Path(member.name).name: member
                for member in package.getmembers()
                if member.isfile()
            }
            for name in names:
                member = members.get(name)
                if member is None:
                    raise RuntimeError(f"下载包中缺少 {name}")
                source = package.extractfile(member)
                if source is None:
                    raise RuntimeError(f"无法读取下载包中的 {name}")
                target = self.target_dir / name
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755)
                installed.append(target)
        return installed[0], installed[1]
