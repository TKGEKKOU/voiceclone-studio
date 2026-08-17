from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SeparatorModelStatus:
    configured_path: str
    exists: bool
    usable: bool
    message: str


def inspect_separator_model(path: str | Path | None) -> SeparatorModelStatus:
    raw = str(path or "").strip()
    if not raw:
        return SeparatorModelStatus("", False, False, "未配置人声分离模型，无法执行人声分离")
    model = Path(raw).expanduser()
    if model.suffix.lower() != ".onnx":
        return SeparatorModelStatus(raw, model.is_file(), False, "人声分离模型必须是 .onnx 文件")
    if not model.is_file():
        return SeparatorModelStatus(raw, False, False, "人声分离模型文件不存在")
    return SeparatorModelStatus(raw, True, True, "人声分离模型可用")
