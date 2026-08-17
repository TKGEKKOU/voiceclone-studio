from __future__ import annotations

import json
import os
import re
from pathlib import Path


_VALID_ID = re.compile(r"^[a-f0-9]{32}$")


class ReviewStore:
    def __init__(self, sessions_dir: Path) -> None:
        self.sessions_dir = Path(sessions_dir)

    def _path(self, task_id: str) -> Path:
        if not _VALID_ID.fullmatch(task_id):
            raise ValueError("任务 ID 格式无效")
        return self.sessions_dir / task_id / "review.json"

    def save(self, task_id: str, rows: list[dict[str, object]]) -> list[dict[str, object]]:
        target = self._path(task_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
        return rows

    def load(self, task_id: str) -> list[dict[str, object]]:
        target = self._path(task_id)
        if not target.is_file():
            return []
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
