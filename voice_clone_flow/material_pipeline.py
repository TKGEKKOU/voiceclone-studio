from __future__ import annotations

import asyncio
from pathlib import Path

from .astrbot_stt import AstrBotSTTBackend
from .pipeline.audio import convert_audio, extract_audio, resolve_ffmpeg
from .pipeline.separator import HtdemucsSeparator
from .pipeline.vad import EnergyVAD
from .review_store import ReviewStore
from .workflow import prepare_tracks_for_review


class MaterialPipeline:
    def __init__(self, context, provider_id: str, ffmpeg_path: str, model_path: Path, sessions_dir: Path, backend=None) -> None:
        self.context = context
        self.provider_id = provider_id
        self.ffmpeg_path = ffmpeg_path
        self.model_path = Path(model_path)
        self.sessions_dir = Path(sessions_dir)
        self.reviews = ReviewStore(self.sessions_dir)
        self.backend = backend

    async def run(self, task_id: str, source: Path, language: str, transition) -> list[dict[str, object]]:
        root = self.sessions_dir / task_id
        tracks = root / "tracks"
        segments_dir = root / "segments"
        ffmpeg = resolve_ffmpeg(self.ffmpeg_path)
        mix = tracks / "mix_44k.wav"
        vocals = tracks / "vocals_44k.wav"
        vad_track = tracks / "vocals_16k.wav"
        reference = tracks / "vocals_24k.wav"

        transition("extracting")
        await asyncio.to_thread(extract_audio, ffmpeg, source, mix)
        transition("separating")
        separator = HtdemucsSeparator(self.model_path)
        await asyncio.to_thread(separator.separate, mix, vocals)
        transition("segmenting")
        await asyncio.to_thread(convert_audio, ffmpeg, vocals, vad_track, 16000, 1)
        await asyncio.to_thread(convert_audio, ffmpeg, vocals, reference, 24000, 1)
        backend = self.backend or AstrBotSTTBackend(self.context, self.provider_id)
        rows = await prepare_tracks_for_review(
            vad_track,
            reference,
            segments_dir,
            EnergyVAD(),
            backend,
            language,
        )
        payload = [
            {
                "index": row.index,
                "audio_name": row.audio_path.name,
                "start_seconds": round(row.start_seconds, 3),
                "end_seconds": round(row.end_seconds, 3),
                "speech_seconds": round(row.speech_seconds, 3),
                "language": row.language,
                "text": row.text,
                "approved": True,
            }
            for row in rows
        ]
        self.reviews.save(task_id, payload)
        transition("review")
        return payload
