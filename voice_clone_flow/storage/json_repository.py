from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..models import TaskRecord


_VALID_ID = re.compile(r"^[a-f0-9]{32}$")


class JsonTaskRepository:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, task_id: str) -> Path:
        if not _VALID_ID.fullmatch(task_id):
            raise ValueError("任务 ID 格式无效")
        return self.directory / f"{task_id}.json"

    def save(self, task: TaskRecord) -> TaskRecord:
        target = self._path(task.id)
        temporary = target.with_suffix(".json.tmp")
        payload = json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
        return task

    def get(self, task_id: str) -> TaskRecord:
        payload = json.loads(self._path(task_id).read_text(encoding="utf-8"))
        return TaskRecord.from_dict(payload)

    def list(self) -> list[TaskRecord]:
        tasks = [
            TaskRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in self.directory.glob("*.json")
        ]
        return sorted(tasks, key=lambda task: task.created_at)
