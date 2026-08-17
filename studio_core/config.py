from __future__ import annotations

import json
import os
import secrets
from pathlib import Path


DEFAULTS = {
    "name": "VoiceClone Studio",
    "api_token": "",
    "frp_server_addr": "47.103.73.128",
    "frp_server_port": 7001,
    "frp_token": "",
    "frp_local_port": 9090,
    "frp_remote_port": 19090,
    "frp_proxy_name": "voiceclone-studio",
    "frp_stop_with_studio": True,
    "asr_base_url": "https://api.openai.com/v1",
    "asr_api_key": "",
    "asr_model": "whisper-1",
    "asr_timeout_seconds": 300,
    "llm_base_url": "https://api.openai.com/v1",
    "llm_api_key": "",
    "llm_model": "gpt-4o-mini",
    "llm_timeout_seconds": 60,
}


class StudioConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict:
        data: dict = {}
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = raw
            except (OSError, ValueError):
                data = {}
        if data.get("token") and not data.get("api_token"):
            data["api_token"] = data.pop("token")
        values = {**DEFAULTS, **data}
        if not values["api_token"]:
            values["api_token"] = secrets.token_urlsafe(24)
        return self._validate(values)

    def save(self, changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise ValueError("配置格式无效")
        values = self.load()
        allowed = set(DEFAULTS)
        values.update({key: value for key, value in changes.items() if key in allowed})
        values = self._validate(values)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)
        return values

    def ensure(self) -> dict:
        values = self.load()
        if not self.path.is_file():
            self.save(values)
        return values

    def public(self) -> dict:
        values = self.load()
        return {
            "name": values["name"],
            "api_token_configured": bool(values["api_token"]),
            "frp_server_addr": values["frp_server_addr"],
            "frp_server_port": values["frp_server_port"],
            "frp_token_configured": bool(values["frp_token"]),
            "frp_local_port": values["frp_local_port"],
            "frp_remote_port": values["frp_remote_port"],
            "frp_proxy_name": values["frp_proxy_name"],
            "frp_stop_with_studio": values["frp_stop_with_studio"],
            "asr_base_url": values["asr_base_url"],
            "asr_api_key_configured": bool(values["asr_api_key"]),
            "asr_model": values["asr_model"],
            "asr_timeout_seconds": values["asr_timeout_seconds"],
            "llm_base_url": values["llm_base_url"],
            "llm_api_key_configured": bool(values["llm_api_key"]),
            "llm_model": values["llm_model"],
            "llm_timeout_seconds": values["llm_timeout_seconds"],
        }

    @staticmethod
    def _validate(values: dict) -> dict:
        result = dict(values)
        for key in ("frp_server_port", "frp_local_port", "frp_remote_port"):
            try:
                result[key] = int(result[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 端口必须是整数") from exc
            if not 1 <= result[key] <= 65535:
                raise ValueError(f"{key} 端口必须在 1 到 65535 之间")
        result["asr_timeout_seconds"] = max(10, int(result["asr_timeout_seconds"]))
        result["llm_timeout_seconds"] = max(10, int(result["llm_timeout_seconds"]))
        result["name"] = str(result["name"]).strip() or DEFAULTS["name"]
        result["api_token"] = str(result["api_token"]).strip()
        if len(result["api_token"]) < 8:
            raise ValueError("Studio Token 至少需要 8 个字符")
        result["frp_server_addr"] = str(result["frp_server_addr"]).strip()
        result["frp_token"] = str(result["frp_token"]).strip()
        result["frp_proxy_name"] = str(result["frp_proxy_name"]).strip() or DEFAULTS["frp_proxy_name"]
        result["frp_stop_with_studio"] = bool(result["frp_stop_with_studio"])
        result["asr_base_url"] = str(result["asr_base_url"]).strip().rstrip("/")
        result["asr_api_key"] = str(result["asr_api_key"]).strip()
        result["asr_model"] = str(result["asr_model"]).strip() or DEFAULTS["asr_model"]
        result["llm_base_url"] = str(result["llm_base_url"]).strip().rstrip("/")
        result["llm_api_key"] = str(result["llm_api_key"]).strip()
        result["llm_model"] = str(result["llm_model"]).strip() or DEFAULTS["llm_model"]
        return result
