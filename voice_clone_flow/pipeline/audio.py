from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from ..errors import MediaProcessingError, PipelineConfigurationError


def resolve_ffmpeg(
    configured_path: str | Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path:
    if configured_path:
        explicit = Path(configured_path).expanduser()
        if not explicit.is_file():
            raise PipelineConfigurationError(f"FFmpeg 文件不存在：{explicit}")
        return explicit.resolve()
    located = which("ffmpeg")
    if not located:
        raise PipelineConfigurationError("未找到 FFmpeg，请配置可执行文件路径或按页面提示安装系统 FFmpeg")
    return Path(located).resolve()


def _run_ffmpeg(
    ffmpeg: Path,
    args: list[str],
    runner: Callable[..., object] = subprocess.run,
) -> None:
    result = runner(
        [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        check=False,
    )
    if int(getattr(result, "returncode", 1)) != 0:
        stderr = getattr(result, "stderr", b"") or b""
        if isinstance(stderr, bytes):
            detail = stderr.decode("utf-8", errors="replace")[-1000:].strip()
        else:
            detail = str(stderr)[-1000:].strip()
        raise MediaProcessingError(f"FFmpeg 处理失败：{detail or '未知错误'}")


def extract_audio(
    ffmpeg: Path,
    source: Path,
    target: Path,
    runner: Callable[..., object] = subprocess.run,
) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        ffmpeg,
        ["-i", str(source), "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(target)],
        runner,
    )
    return target


def convert_audio(
    ffmpeg: Path,
    source: Path,
    target: Path,
    sample_rate: int,
    channels: int = 1,
    runner: Callable[..., object] = subprocess.run,
) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        ffmpeg,
        ["-i", str(source), "-ac", str(channels), "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(target)],
        runner,
    )
    return target
