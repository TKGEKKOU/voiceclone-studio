from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


class RemoteStudioConfigError(ValueError):
    pass


@dataclass
class RemoteStudioConfig:
    mode: str = "local"
    base_url: str = ""
    token: str = ""
    timeout_seconds: float = 300.0

    def validate(self) -> "RemoteStudioConfig":
        self.mode = str(self.mode or "local").strip().lower()
        if self.mode not in {"local", "remote"}:
            raise RemoteStudioConfigError("运行模式必须是 local 或 remote")
        self.base_url = str(self.base_url or "").strip().rstrip("/")
        if self.base_url:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RemoteStudioConfigError("Studio 地址必须是有效的 http:// 或 https:// 地址")
        self.token = str(self.token or "")
        try:
            self.timeout_seconds = max(1.0, float(self.timeout_seconds))
        except (TypeError, ValueError) as exc:
            raise RemoteStudioConfigError("远程请求超时必须是数字") from exc
        if self.mode == "remote" and (not self.base_url or not self.token):
            raise RemoteStudioConfigError("远程模式需要填写 Studio 地址和 Token")
        return self

    def masked(self) -> dict:
        return {**asdict(self), "token": "****" if self.token else ""}

    def visible(self) -> dict:
        return asdict(self)


class RemoteStudioConfigStore:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "remote_studio.json"

    def load(self) -> RemoteStudioConfig:
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(values, dict):
                values = {}
        except (OSError, ValueError, TypeError):
            values = {}
        return RemoteStudioConfig(**{key: values[key] for key in RemoteStudioConfig.__dataclass_fields__ if key in values}).validate()

    def save(self, config: RemoteStudioConfig) -> RemoteStudioConfig:
        config.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)
        return config
