from __future__ import annotations

import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100
SEGMENT_SAMPLES = int(7.8 * SAMPLE_RATE)
SOURCES = ("drums", "bass", "other", "vocals")


def _transition_window(segment: int, overlap_fraction: float = 0.25) -> np.ndarray:
    transition = int(segment * overlap_fraction)
    window = np.ones(segment, dtype=np.float32)
    fade = np.linspace(0, 1, transition, dtype=np.float32)
    window[:transition], window[-transition:] = fade, fade[::-1]
    return window


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
        raw = source.readframes(source.getnframes())
    if channels not in (1, 2) or width != 2:
        raise ValueError("expected mono/stereo 16-bit WAV")
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32).reshape(-1, channels).T / 32768.0
    return (np.tile(samples, (2, 1)) if channels == 1 else samples), rate


def _write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    pcm = (np.clip(samples, -1.0, 1.0).T * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(pcm.shape[1])
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(pcm.tobytes())


class HtdemucsSeparator:
    def __init__(self, model_path: Path, providers: list[str] | None = None, session_factory: Callable[[Path], object] | None = None) -> None:
        self.model_path = Path(model_path)
        self.providers = providers or ["CPUExecutionProvider"]
        self.session_factory = session_factory
        self._session = None

    def _get_session(self):
        if self._session is None:
            if self.session_factory:
                self._session = self.session_factory(self.model_path)
            else:
                import onnxruntime as ort
                self._session = ort.InferenceSession(str(self.model_path), providers=self.providers)
        return self._session

    def separate(self, source: Path, target: Path, progress: Callable[[int, int], None] | None = None) -> Path:
        mix, rate = _read_wav(source)
        if rate != SAMPLE_RATE:
            raise ValueError(f"separator requires {SAMPLE_RATE} Hz, got {rate}")
        overlap, total = SEGMENT_SAMPLES // 4, mix.shape[1]
        stride = SEGMENT_SAMPLES - overlap
        chunks = max(1, (total + stride - 1) // stride)
        window = _transition_window(SEGMENT_SAMPLES)
        output = np.zeros((len(SOURCES), 2, total), dtype=np.float32)
        weight = np.zeros(total, dtype=np.float32)
        for index in range(chunks):
            start, end = index * stride, min(index * stride + SEGMENT_SAMPLES, total)
            chunk = mix[:, start:end]
            chunk = np.pad(chunk, ((0, 0), (0, SEGMENT_SAMPLES - chunk.shape[1])))
            stems = self._get_session().run(["stems"], {"mix": chunk[np.newaxis].astype(np.float32)})[0][0]
            length = end - start
            output[:, :, start:end] += stems[:, :, :length] * window[:length]
            weight[start:end] += window[:length]
            if progress:
                progress(index + 1, chunks)
        output /= np.maximum(weight, 1e-8)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(target, output[SOURCES.index("vocals")], rate)
        return target
