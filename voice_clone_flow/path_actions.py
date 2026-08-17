from pathlib import Path
import os
import subprocess

from .platform_runtime import PlatformProfile, detect_platform_profile


def validate_voice_directory(data_dir: Path, voice_id: str) -> Path:
    root = (Path(data_dir) / "voices").resolve()
    if Path(voice_id).name != voice_id:
        raise ValueError("音色标识无效")
    target = (root / voice_id).resolve()
    if root not in target.parents:
        raise ValueError("音色目录越界")
    if not target.is_dir():
        raise FileNotFoundError("音色目录不存在")
    return target


def _open_directory(target: Path, profile: PlatformProfile) -> None:
    if not profile.can_open_directory:
        return
    if profile.system == "windows" and hasattr(os, "startfile"):
        os.startfile(str(target))
    elif profile.system == "linux":
        subprocess.Popen(["xdg-open", str(target)])


def open_voice_directory(data_dir: Path, voice_id: str,
                         platform_profile: PlatformProfile | None = None) -> Path:
    target = validate_voice_directory(data_dir, voice_id)
    _open_directory(target, platform_profile or detect_platform_profile())
    return target


def ensure_voices_root(data_dir: Path) -> Path:
    root = (Path(data_dir) / "voices").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def open_voices_root(data_dir: Path, platform_profile: PlatformProfile | None = None) -> Path:
    target = ensure_voices_root(data_dir)
    _open_directory(target, platform_profile or detect_platform_profile())
    return target
