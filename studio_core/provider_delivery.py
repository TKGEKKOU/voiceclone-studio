from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


STAGES = ("pending", "delivered", "registered", "verified", "failed")
_STAGE_ORDER = {name: index for index, name in enumerate(STAGES[:-1])}
_MASK = "********"


class ProviderDeliveryError(ValueError):
    """A provider delivery task is invalid or cannot move to the requested stage."""


def _stable_task_id(provider_config: dict[str, Any]) -> str:
    encoded = json.dumps(provider_config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "delivery_" + hashlib.sha256(encoded).hexdigest()[:24]


class ProviderDeliveryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, rows: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    @staticmethod
    def _validate_provider(provider_config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(provider_config, dict):
            raise ProviderDeliveryError("Provider 配置必须是对象")
        required = ("id", "type", "provider", "provider_type", "api_base", "character", "text_lang")
        missing = [key for key in required if not str(provider_config.get(key, "")).strip()]
        if missing:
            raise ProviderDeliveryError("Provider 缺少字段：" + ", ".join(missing))
        if provider_config.get("type") != "gsvi_tts_api":
            raise ProviderDeliveryError("Provider type 必须是 gsvi_tts_api")
        if provider_config.get("provider") != "gpt_sovits_inference":
            raise ProviderDeliveryError("Provider provider 必须是 gpt_sovits_inference")
        if provider_config.get("provider_type") != "text_to_speech":
            raise ProviderDeliveryError("Provider provider_type 必须是 text_to_speech")
        if "default_params" in provider_config or "gsv_default_parms" in provider_config:
            raise ProviderDeliveryError("Provider 不应包含 default_params；请使用 GSVI 原生字段")
        clean = dict(provider_config)
        clean["id"] = str(clean["id"]).strip()
        clean["api_base"] = str(clean["api_base"]).strip().rstrip("/")
        clean["character"] = str(clean["character"]).strip()
        clean["text_lang"] = str(clean["text_lang"]).strip()
        clean["enable"] = bool(clean.get("enable", True))
        return clean

    @staticmethod
    def _public(task: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in task.items() if key != "provider_config"}
        config = dict(task.get("provider_config") or {})
        if "api_key" in config:
            config["api_key"] = _MASK
        result["provider"] = config
        result.pop("provider_config", None)
        return result

    def create(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        clean = self._validate_provider(provider_config)
        task_id = _stable_task_id(clean)
        rows = self._read()
        existing = rows.get(task_id)
        if existing:
            return dict(existing)
        now = time.time()
        task = {
            "id": task_id,
            "stage": "pending",
            "message": "等待服务器插件领取",
            "error": "",
            "created_at": now,
            "updated_at": now,
            "claimed_at": None,
            "provider_config": clean,
        }
        rows[task_id] = task
        self._write(rows)
        return task

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._read().get(str(task_id))

    def public(self, task_id: str) -> dict[str, Any] | None:
        task = self.get(task_id)
        return self._public(task) if task else None

    def claim(self, lease_seconds: int = 30) -> dict[str, Any] | None:
        rows = self._read()
        now = time.time()
        for task in rows.values():
            if task.get("stage") == "pending" or (
                task.get("stage") == "delivered"
                and task.get("claimed_at")
                and now - float(task["claimed_at"]) >= lease_seconds
            ):
                task["stage"] = "delivered"
                task["claimed_at"] = now
                task["updated_at"] = now
                task["message"] = "服务器插件已领取"
                self._write(rows)
                return dict(task)
        return None

    def report(self, task_id: str, stage: str, message: str = "", error: str = "") -> dict[str, Any]:
        if stage not in STAGES:
            raise ProviderDeliveryError("未知投递阶段")
        rows = self._read()
        task = rows.get(str(task_id))
        if not task:
            raise ProviderDeliveryError("投递任务不存在")
        current = str(task.get("stage"))
        if current == "verified":
            if stage != current:
                raise ProviderDeliveryError("已完成的投递任务不能回退")
        elif stage != "failed" and current != "failed":
            if stage not in _STAGE_ORDER or current not in _STAGE_ORDER or _STAGE_ORDER[stage] < _STAGE_ORDER[current]:
                raise ProviderDeliveryError("投递阶段不能回退")
        task["stage"] = stage
        task["message"] = str(message).strip()
        task["error"] = str(error).strip()
        task["updated_at"] = time.time()
        self._write(rows)
        return dict(task)
