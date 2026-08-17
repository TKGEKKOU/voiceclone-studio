"""AstrBot STT Provider adapter for the framework-independent core."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .transcription import EmptyTranscript, TranscriptionUnavailable


class AstrBotSTTBackend:
    def __init__(
        self,
        context: Any,
        provider_id: str = "",
        umo: str | None = None,
    ) -> None:
        self._context = context
        self._provider_id = provider_id.strip()
        self._umo = umo

    async def _resolve_provider(self) -> Any:
        if self._provider_id:
            provider = self._context.get_provider_by_id(self._provider_id)
            if provider is None or not callable(getattr(provider, "get_text", None)):
                raise TranscriptionUnavailable(
                    f"未找到可用的 ASR Provider：{self._provider_id}"
                )
            return provider

        provider = await self._context.get_using_stt_provider_async(umo=self._umo)
        if provider is None or not callable(getattr(provider, "get_text", None)):
            raise TranscriptionUnavailable("尚未配置可用的 ASR Provider")
        return provider

    async def transcribe(self, audio_path: Path, language: str) -> str:
        del language  # AstrBot's public STT contract currently selects language provider-side.
        path = Path(audio_path)
        if not path.is_file():
            raise FileNotFoundError(f"音频文件不存在：{path}")
        provider = await self._resolve_provider()
        text = str(await provider.get_text(str(path))).strip()
        if not text:
            raise EmptyTranscript(f"未识别到有效文本：{path.name}")
        return text
