from __future__ import annotations

from ..models import INTERRUPTIBLE_STATES, TERMINAL_STATES, TaskRecord
from ..storage.json_repository import JsonTaskRepository


class VoiceCloneStudio:
    def __init__(self, repository: JsonTaskRepository) -> None:
        self.repository = repository

    def create_task(self, name: str, source_name: str) -> TaskRecord:
        return self.repository.save(TaskRecord.create(name, source_name))

    def cancel(self, task_id: str) -> TaskRecord:
        task = self.repository.get(task_id)
        if task.state in TERMINAL_STATES:
            return task
        return self.repository.save(task.transition("cancelled"))

    def recover_interrupted(self) -> list[TaskRecord]:
        recovered: list[TaskRecord] = []
        for task in self.repository.list():
            if task.state in INTERRUPTIBLE_STATES:
                task = task.transition("failed", "插件重载时任务仍在运行，请重新开始")
                self.repository.save(task)
                recovered.append(task)
        return recovered
