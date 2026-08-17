from __future__ import annotations

import io
import wave


def trim_wav(payload: bytes, max_seconds: float) -> bytes:
    if max_seconds <= 0:
        raise ValueError("max_seconds 必须大于 0")

    source_buffer = io.BytesIO(payload)
    with wave.open(source_buffer, "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        frame_rate = source.getframerate()
        compression_type = source.getcomptype()
        compression_name = source.getcompname()
        frame_count = min(source.getnframes(), int(frame_rate * max_seconds))
        frames = source.readframes(frame_count)

    target_buffer = io.BytesIO()
    with wave.open(target_buffer, "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(sample_width)
        target.setframerate(frame_rate)
        target.setcomptype(compression_type, compression_name)
        target.writeframes(frames)
    return target_buffer.getvalue()
