from __future__ import annotations

import os
import platform
import shutil
from dataclasses import asdict, dataclass
from typing import Callable, Mapping


INSTALL_HINTS = {
    "linux": {
        "ffmpeg": "sudo apt update && sudo apt install -y ffmpeg",
        "7zip": "sudo apt update && sudo apt install -y p7zip-full",
        "git": "sudo apt update && sudo apt install -y git",
        "python_venv": "sudo apt update && sudo apt install -y python3-venv",
    }
}


def normalize_system_name(value: str) -> str:
    name = str(value or "").strip().lower()
    return name if name in {"windows", "linux"} else "unsupported"


def normalize_architecture(value: str) -> str:
    name = str(value or "").strip().lower()
    if name in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if name in {"arm64", "aarch64"}:
        return "arm64"
    return name or "unknown"


@dataclass(frozen=True)
class PlatformProfile:
    system: str
    architecture: str
    executable_suffix: str
    ffmpeg_path: str
    ffprobe_path: str
    archive_tool: str
    git_path: str
    can_use_bundled_7zr: bool
    can_download_managed_ffmpeg: bool
    can_open_directory: bool
    install_hints: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def detect_platform_profile(
    *,
    system: str | None = None,
    machine: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
) -> PlatformProfile:
    system_name = normalize_system_name(system or platform.system())
    architecture = normalize_architecture(machine or platform.machine())
    env = os.environ if environ is None else environ
    is_windows = system_name == "windows"
    is_linux = system_name == "linux"
    archive_tool = ""
    if is_linux:
        archive_tool = which("7z") or which("7za") or ""
    elif is_windows:
        archive_tool = which("7z") or which("7za") or ""
    has_display = bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    can_open_directory = is_windows or bool(
        is_linux and has_display and which("xdg-open")
    )
    return PlatformProfile(
        system=system_name,
        architecture=architecture,
        executable_suffix=".exe" if is_windows else "",
        ffmpeg_path=which("ffmpeg") or "",
        ffprobe_path=which("ffprobe") or "",
        archive_tool=archive_tool,
        git_path=which("git") or "",
        can_use_bundled_7zr=is_windows,
        can_download_managed_ffmpeg=(is_windows and architecture == "x86_64")
        or (is_linux and architecture in {"x86_64", "arm64"}),
        can_open_directory=can_open_directory,
        install_hints=dict(INSTALL_HINTS.get(system_name, {})),
    )
