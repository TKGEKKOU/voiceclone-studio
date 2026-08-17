from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import uuid4


TaskState = Literal[
    "queued",
    "extracting",
    "separating",
    "segmenting",
    "review",
    "preparing",
    "training",
    "registering",
    "ready",
    "failed",
    "cancelled",
]

TERMINAL_STATES = frozenset({"ready", "failed", "cancelled"})
INTERRUPTIBLE_STATES = frozenset(
    {
        "extracting",
        "separating",
        "segmenting",
        "preparing",
        "training",
        "registering",
    }
)
_NEXT_STATE: dict[TaskState, TaskState] = {
    "queued": "extracting",
    "extracting": "separating",
    "separating": "segmenting",
    "segmenting": "review",
    "review": "preparing",
    "preparing": "training",
    "training": "registering",
    "registering": "ready",
}


class InvalidTaskTransition(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class TaskRecord:
    id: str
    name: str
    source_name: str
    state: TaskState
    created_at: str
    updated_at: str
    error: str | None = None

    @classmethod
    def create(cls, name: str, source_name: str) -> "TaskRecord":
        timestamp = _now()
        return cls(
            id=uuid4().hex,
            name=name.strip(),
            source_name=source_name,
            state="queued",
            created_at=timestamp,
            updated_at=timestamp,
        )

    def transition(self, state: TaskState, error: str | None = None) -> "TaskRecord":
        allowed = state in {"failed", "cancelled"} and self.state not in TERMINAL_STATES
        allowed = allowed or _NEXT_STATE.get(self.state) == state
        if not allowed:
            raise InvalidTaskTransition(f"不能从 {self.state} 迁移到 {state}")
        return replace(self, state=state, error=error, updated_at=_now())

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "TaskRecord":
        return cls(
            id=str(payload["id"]),
            name=str(payload["name"]),
            source_name=str(payload["source_name"]),
            state=cast(TaskState, payload["state"]),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            error=str(payload["error"]) if payload.get("error") is not None else None,
        )
