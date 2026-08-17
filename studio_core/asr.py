from __future__ import annotations

import asyncio
import json
import mimetypes
import secrets
import urllib.error
import urllib.request
from pathlib import Path

from voice_clone_flow.transcription import EmptyTranscript, TranscriptionUnavailable


class OpenAICompatibleASRBackend:
    """Minimal OpenAI-compatible /audio/transcriptions client."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 300) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout = timeout

    async def transcribe(self, audio_path: Path, language: str) -> str:
        if not self.base_url or not self.api_key or not self.model:
            raise TranscriptionUnavailable("请先配置 ASR API 地址、API Key 和模型")
        return await asyncio.to_thread(self._request, Path(audio_path), language)

    def _request(self, path: Path, language: str) -> str:
        boundary = "----VoiceClone" + secrets.token_hex(12)
        parts: list[bytes] = []
        for name, value in {"model": self.model, "language": language}.items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        mime = mimetypes.guess_type(path.name)[0] or "audio/wav"
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
        parts.extend((path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()))
        request = urllib.request.Request(
            self.base_url + "/audio/transcriptions",
            data=b"".join(parts),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError) as exc:
            raise TranscriptionUnavailable(f"ASR 请求失败：{exc}") from exc
        text = str(payload.get("text", "")).strip() if isinstance(payload, dict) else ""
        if not text:
            raise EmptyTranscript(f"未识别到有效文本：{path.name}")
        return text
