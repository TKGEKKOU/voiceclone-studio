"""Framework-independent preparation and transcription workflow."""

from __future__ import annotations

import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .errors import MediaProcessingError
from .pipeline.segments import (
    REFERENCE_RATE,
    VAD_RATE,
    Segment,
    segment_speech,
)
from .pipeline.vad import VAD
from .transcription import TranscriptionBackend, transcribe_files


@dataclass(frozen=True)
class PreparedSegment:
    index: int
    audio_path: Path
    start_seconds: float
    end_seconds: float
    speech_seconds: float
    rms: float


@dataclass(frozen=True)
class ReviewTranscript:
    index: int
    audio_path: Path
    start_seconds: float
    end_seconds: float
    speech_seconds: float
    rms: float
    language: str
    text: str
    approved: bool = False


def _read_reference_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels() != 1
            or source.getsampwidth() != 2
            or source.getframerate() != REFERENCE_RATE
        ):
            raise MediaProcessingError(
                f"片段源必须是 {REFERENCE_RATE} Hz 单声道 16-bit WAV"
            )
        return np.frombuffer(
            source.readframes(source.getnframes()), dtype=np.int16
        ).copy()


def _write_pcm(path: Path, pcm: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(REFERENCE_RATE)
        target.writeframes(pcm.astype(np.int16, copy=False).tobytes())


def write_segment_files(
    reference_wav: Path,
    segments: Sequence[Segment],
    output_dir: Path,
) -> list[PreparedSegment]:
    """Persist VAD segments on the 24 kHz training/audio-review timeline."""

    samples = _read_reference_wav(Path(reference_wav))
    output_dir = Path(output_dir)
    rows: list[PreparedSegment] = []
    for index, segment in enumerate(segments):
        start_seconds = segment.start_sample / VAD_RATE
        end_seconds = segment.end_sample / VAD_RATE
        start = max(0, min(round(start_seconds * REFERENCE_RATE), len(samples)))
        end = max(start, min(round(end_seconds * REFERENCE_RATE), len(samples)))
        if end <= start:
            raise MediaProcessingError(f"语音片段 {index + 1} 为空")
        target = output_dir / f"segment_{index + 1:03d}.wav"
        _write_pcm(target, samples[start:end])
        rows.append(
            PreparedSegment(
                index=index,
                audio_path=target,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                speech_seconds=segment.speech_seconds,
                rms=segment.rms,
            )
        )
    return rows


async def transcribe_segment_files(
    backend: TranscriptionBackend,
    segments: Sequence[PreparedSegment],
    language: str,
    on_progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[ReviewTranscript]:
    """Transcribe prepared segments and return rows ready for human review."""

    prepared = list(segments)
    transcripts = await transcribe_files(
        backend,
        [item.audio_path for item in prepared],
        language,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
    )
    return [
        ReviewTranscript(
            index=item.index,
            audio_path=item.audio_path,
            start_seconds=item.start_seconds,
            end_seconds=item.end_seconds,
            speech_seconds=item.speech_seconds,
            rms=item.rms,
            language=transcript.language,
            text=transcript.text,
        )
        for item, transcript in zip(prepared, transcripts, strict=True)
    ]


async def prepare_tracks_for_review(
    vad_track: Path,
    reference_track: Path,
    output_dir: Path,
    vad: VAD,
    backend: TranscriptionBackend,
    language: str,
    *,
    target_seconds: float = 600.0,
    min_segment: float = 1.0,
    max_segment: float = 12.0,
    min_total: float = 5.0,
    on_progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[ReviewTranscript]:
    """Create reviewable segments from prepared tracks and transcribe them."""

    segments = segment_speech(
        Path(vad_track),
        vad,
        target_seconds=target_seconds,
        min_segment=min_segment,
        max_segment=max_segment,
        min_total=min_total,
    )
    prepared = write_segment_files(Path(reference_track), segments, Path(output_dir))
    return await transcribe_segment_files(
        backend,
        prepared,
        language,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
    )
