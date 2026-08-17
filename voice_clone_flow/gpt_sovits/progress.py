from __future__ import annotations

import re
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class TrainingProgress:
    stage: str = ""
    epoch: int = 0
    epoch_total: int = 0
    step: int = 0
    step_total: int = 0
    steps_per_second: float = 0.0
    percent: int = 0
    eta_seconds: int | None = None


def parse_training_line(line: str, current: TrainingProgress) -> TrainingProgress:
    result = current
    epoch = re.search(r"(?:Train\s+)?Epoch\s*:?\s*(\d+)", line)
    if epoch:
        result = replace(result, epoch=int(epoch.group(1)))
    step = re.search(r"(\d+)\s*/\s*(\d+).*?(\d+(?:\.\d+)?)it/s", line)
    if step:
        value, total, speed = int(step.group(1)), int(step.group(2)), float(step.group(3))
        percent = round(((result.epoch - 1) * total + value) / (result.epoch_total * total) * 100) if result.epoch_total else 0
        remaining = max(0, (result.epoch_total * total) - ((result.epoch - 1) * total + value))
        result = replace(result, step=value, step_total=total, steps_per_second=speed, percent=percent, eta_seconds=round(remaining / speed) if speed else None)
    return result
