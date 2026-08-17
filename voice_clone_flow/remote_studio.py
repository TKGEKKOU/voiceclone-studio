from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .remote_config import RemoteStudioConfig


class RemoteStudioError(RuntimeError):
    pass


class RemoteStudioAuthError(RemoteStudioError):
    pass


class RemoteStudioConnectionError(RemoteStudioError):
    pass


class RemoteStudioBusyError(RemoteStudioError):
    pass


class RemoteStudioProtocolError(RemoteStudioError):
    pass


class RemoteStudioClient:
    def __init__(self, config: RemoteStudioConfig, opener: Callable = urlopen):
        self.config = config.validate()
        self.opener = opener

    def _request(self, path: str, payload: dict | None = None):
        url = urljoin(self.config.base_url + "/", path.lstrip("/"))
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=body,
            method="POST" if body is not None else "GET",
            headers={
                "Accept": "application/json, audio/wav",
                "Authorization": f"Bearer {self.config.token}",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            return self.opener(request, timeout=self.config.timeout_seconds)
        except HTTPError as exc:
            if exc.code == 401 or exc.code == 403:
                raise RemoteStudioAuthError("Studio Token 无效") from exc
            if exc.code == 429:
                raise RemoteStudioBusyError("Studio 当前繁忙") from exc
            raise RemoteStudioProtocolError(f"Studio 返回 HTTP {exc.code}") from exc
        except (OSError, URLError, TimeoutError) as exc:
            raise RemoteStudioConnectionError("无法连接 VoiceClone Studio") from exc

    def health(self) -> dict:
        try:
            with self._request("/api/health") as response:
                value = json.loads(response.read().decode("utf-8"))
        except RemoteStudioError:
            raise
        except (ValueError, TypeError, UnicodeError) as exc:
            raise RemoteStudioProtocolError("Studio 健康检查返回格式无效") from exc
        if not isinstance(value, dict):
            raise RemoteStudioProtocolError("Studio 健康检查返回格式无效")
        return value

    def list_voices(self) -> list[dict]:
        try:
            with self._request("/api/voices") as response:
                value = json.loads(response.read().decode("utf-8"))
        except RemoteStudioError:
            raise
        except (ValueError, TypeError, UnicodeError) as exc:
            raise RemoteStudioProtocolError("Studio 音色列表返回格式无效") from exc
        voices = value.get("voices", value) if isinstance(value, dict) else value
        if not isinstance(voices, list) or not all(isinstance(item, dict) for item in voices):
            raise RemoteStudioProtocolError("Studio 音色列表返回格式无效")
        return voices

    def synthesize(self, payload: dict, destination: Path) -> Path:
        try:
            with self._request("/api/tts", payload) as response:
                destination = Path(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as target:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        target.write(chunk)
        except RemoteStudioError:
            raise
        except OSError as exc:
            raise RemoteStudioConnectionError("远程音频写入失败") from exc
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RemoteStudioProtocolError("Studio 未返回音频数据")
        return destination
