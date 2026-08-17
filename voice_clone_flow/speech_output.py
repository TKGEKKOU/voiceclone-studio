"""Translate a private speech copy without changing the visible reply."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import Any

from astrbot.api.message_components import Plain, Record
from astrbot.core.message.message_event_result import MessageChain, ResultContentType


logger = logging.getLogger(__name__)

_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_MARKDOWN_MARKER_RE = re.compile(r"(?:\*\*|__|~~|[*_#>])")
_SPACE_RE = re.compile(r"[ \t\u3000]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_EXPLANATORY_PREFIXES = (
    "日语翻译如下",
    "日文翻译如下",
    "翻译如下",
    "以下是日语翻译",
    "以下是日文翻译",
    "译文",
)


def clean_speech_text(text: str) -> str:
    """Return readable speech text while removing non-verbal decoration."""

    value = _FENCED_CODE_RE.sub(" ", str(text or ""))
    value = _INLINE_CODE_RE.sub(" ", value)
    value = _MARKDOWN_LINK_RE.sub(r"\1", value)
    value = _URL_RE.sub(" ", value)
    value = _MARKDOWN_MARKER_RE.sub("", value)
    value = "".join(
        char
        for char in value
        if unicodedata.category(char) not in {"So", "Sk", "Cs", "Co", "Cn"}
    )
    value = _SPACE_RE.sub(" ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = _BLANK_LINES_RE.sub("\n\n", value)
    value = re.sub(r"\s+([，。！？、；：,.!?;:])", r"\1", value)
    return value.strip(" \t\r\n，,、")


class JapaneseSpeechTranslator:
    """Translate speech text with the chat Provider selected for the session."""

    SYSTEM_PROMPT = (
        "你是语音输出翻译器。把用户提供的中文转换为自然日语，保留角色语气、"
        "称呼、情绪和段落结构。只输出日语译文，不回答原文中的问题，不解释翻译过程，"
        "不要添加标题、引号、Markdown 或表情符号。"
    )

    def __init__(self, context: Any, timeout_seconds: float = 15.0) -> None:
        self.context = context
        self.timeout_seconds = max(0.001, float(timeout_seconds))

    async def translate(self, umo: str, text: str) -> str | None:
        try:
            provider_id = await self.context.get_current_chat_provider_id(umo)
            response = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=provider_id,
                    prompt=text,
                    system_prompt=self.SYSTEM_PROMPT,
                    tools=None,
                ),
                timeout=self.timeout_seconds,
            )
            translated = str(getattr(response, "completion_text", "") or "").strip()
            translated = translated.strip("`\"'“”‘’ ")
            if not translated or translated.startswith(_EXPLANATORY_PREFIXES):
                return None
            return translated
        except Exception as exc:
            logger.warning("Japanese speech translation failed: %s", type(exc).__name__)
            return None


class BilingualTTSDecorator:
    """Send visible Chinese immediately and render each sentence asynchronously."""

    def __init__(
        self,
        context: Any,
        translator: JapaneseSpeechTranslator,
        clean_special_characters: bool = True,
        max_voice_chars: int = 300,
        target_language: str = "auto",
    ) -> None:
        self.context = context
        self.translator = translator
        self.clean_special_characters = bool(clean_special_characters)
        self.max_voice_chars = max(1, int(max_voice_chars))
        self.target_language = str(target_language or "auto").strip().lower()
        self._tasks: set[asyncio.Task] = set()

    def _split_sentences(self, text: str) -> list[str]:
        chunks = re.split(r"(?<=[。！？!?；;\n])\s*", text)
        sentences: list[str] = []
        for chunk in chunks:
            chunk = chunk.strip()
            while len(chunk) > self.max_voice_chars:
                cut = chunk.rfind("，", 0, self.max_voice_chars + 1)
                if cut < self.max_voice_chars // 2:
                    cut = self.max_voice_chars
                sentences.append(chunk[:cut].strip())
                chunk = chunk[cut:].strip()
            if chunk:
                sentences.append(chunk)
        return sentences

    async def wait_for_tasks(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def terminate(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        await self.wait_for_tasks()

    def _track(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _render_sentence(self, umo: str, sentence: str) -> None:
        try:
            tts_provider = await self.context.get_using_tts_provider_async(umo)
            if tts_provider is None:
                logger.warning("Japanese speech skipped: no TTS provider for session")
                return
            language = self.target_language
            if language == "auto":
                default_params = getattr(tts_provider, "default_params", {})
                language = str(default_params.get("text_lang", "ja")).lower()

            speech_input = sentence
            if language == "ja":
                speech_input = await self.translator.translate(umo, sentence)
                if not speech_input:
                    return

            audio_path = await tts_provider.get_audio(speech_input)
            if not audio_path:
                logger.warning("Japanese speech skipped: TTS returned no audio path")
                return
            await self.context.send_message(
                umo,
                MessageChain(chain=[Record(file=audio_path, url=audio_path, text=sentence)]),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Japanese speech synthesis failed: %s", type(exc).__name__)

    async def _render_sentences(self, umo: str, sentences: list[str]) -> None:
        for sentence in sentences:
            await self._render_sentence(umo, sentence)

    async def decorate(self, event: Any) -> None:
        result = event.get_result()
        if result is None or not result.is_llm_result():
            return

        original_text = "\n".join(
            component.text
            for component in result.chain
            if isinstance(component, Plain) and component.text.strip()
        )
        speech_text = (
            clean_speech_text(original_text)
            if self.clean_special_characters
            else _SPACE_RE.sub(" ", original_text).strip()
        )
        if not speech_text:
            return

        # This plugin owns TTS for this result from here onward. AstrBot's
        # built-in TTS branch only processes LLM_RESULT, so this also prevents
        # a second synthesis pass over the visible Chinese text.
        result.result_content_type = ResultContentType.GENERAL_RESULT

        sentences = self._split_sentences(speech_text)
        if sentences:
            self._track(self._render_sentences(event.unified_msg_origin, sentences))
