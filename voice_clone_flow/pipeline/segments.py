from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..errors import MediaProcessingError
from .vad import VAD

VAD_RATE = 16000
REFERENCE_RATE = 24000


@dataclass(frozen=True)
class Segment:
    start_sample: int
    end_sample: int
    seconds: float
    speech_seconds: float
    rms: float


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise MediaProcessingError("切片输入必须是单声道 16-bit WAV")
        rate = source.getframerate()
        raw = source.readframes(source.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, rate


def _normalize(samples: np.ndarray, target_rms: float = 0.15, max_gain: float = 10.0) -> np.ndarray:
    rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
    if rms <= 0:
        return samples
    return np.clip(samples * min(max_gain, target_rms / rms), -1.0, 1.0).astype(np.float32)


def segment_speech(
    wav_path: Path,
    vad: VAD,
    target_seconds: float = 600.0,
    min_segment: float = 1.0,
    max_segment: float = 12.0,
    min_total: float = 5.0,
    merge_gap_seconds: float = 1.5,
) -> list[Segment]:
    samples, rate = _read_mono(wav_path)
    if rate != VAD_RATE:
        raise MediaProcessingError(f"切片输入应为 {VAD_RATE} Hz，实际为 {rate} Hz")
    samples = _normalize(samples)
    vad.reset()
    boundaries = []
    frame = int(rate * 0.25)
    for start in range(0, len(samples), frame):
        boundaries.extend(vad.process(samples[start : start + frame]))

    raw: list[tuple[int, int, int]] = []
    opened = None
    for event in boundaries:
        if event.kind == "speech_start" and opened is None:
            opened = event.sample_index
        elif event.kind == "speech_stop" and opened is not None:
            raw.append((opened, event.sample_index, event.sample_index - opened))
            opened = None
    if opened is not None:
        raw.append((opened, len(samples), len(samples) - opened))

    merged: list[tuple[int, int, int]] = []
    for start, stop, speech_samples in raw:
        if merged and (start - merged[-1][1]) / rate <= merge_gap_seconds and (stop - merged[-1][0]) / rate <= max_segment:
            old_start, _, old_speech = merged[-1]
            merged[-1] = (old_start, stop, old_speech + speech_samples)
        else:
            merged.append((start, stop, speech_samples))

    candidates = []
    for start, stop, speech_samples in merged:
        seconds = (stop - start) / rate
        if min_segment <= seconds <= max_segment:
            chunk = samples[start:stop]
            candidates.append(Segment(start, stop, seconds, speech_samples / rate, float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))))

    chosen, total = [], 0.0
    for segment in sorted(candidates, key=lambda item: item.rms, reverse=True):
        remaining = target_seconds - total
        if remaining <= 0:
            break
        if segment.speech_seconds <= remaining:
            chosen.append(segment)
            total += segment.speech_seconds
        elif remaining / segment.speech_seconds > 0.05:
            fraction = remaining / segment.speech_seconds
            end = segment.start_sample + int((segment.end_sample - segment.start_sample) * fraction)
            chosen.append(Segment(segment.start_sample, end, (end - segment.start_sample) / rate, remaining, segment.rms))
            total = target_seconds
    if total < min_total:
        raise MediaProcessingError(f"可用的干净语音不足 {min_total:.0f} 秒（当前 {total:.1f} 秒）")
    return sorted(chosen, key=lambda item: item.start_sample)
