"""Framework-independent speech transcription contracts and orchestration."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class TranscriptionError(RuntimeError):
    """Base error for transcription failures."""


class TranscriptionUnavailable(TranscriptionError):
    """Raised when no usable transcription backend is configured."""


class EmptyTranscript(TranscriptionError):
    """Raised when a backend returns no usable text."""


class TranscriptionCancelled(TranscriptionError):
    """Raised when a batch is cancelled between files."""


class TranscriptionBackend(Protocol):
    async def transcribe(self, audio_path: Path, language: str) -> str:
        """Return normalized text for one local audio file."""


@dataclass(frozen=True)
class Transcript:
    audio_path: Path
    language: str
    text: str


async def transcribe_files(
    backend: TranscriptionBackend,
    audio_paths: Sequence[Path],
    language: str,
    on_progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[Transcript]:
    """Transcribe local audio files in input order with cooperative cancellation."""

    paths = [Path(path) for path in audio_paths]
    rows: list[Transcript] = []
    total = len(paths)
    for index, path in enumerate(paths, start=1):
        if is_cancelled is not None and is_cancelled():
            raise TranscriptionCancelled("语音标注任务已取消")
        text = (await backend.transcribe(path, language)).strip()
        if not text:
            raise EmptyTranscript(f"未识别到有效文本：{path.name}")
        rows.append(Transcript(path, language, text))
        if on_progress is not None:
            on_progress(index, total)
    return rows
