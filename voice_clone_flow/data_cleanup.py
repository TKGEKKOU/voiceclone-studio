from dataclasses import dataclass
from pathlib import Path
import shutil
import time


@dataclass(frozen=True)
class CleanupItem:
    relative_path: str
    path: Path
    file_count: int
    bytes: int


@dataclass(frozen=True)
class CleanupPreview:
    items: tuple[CleanupItem, ...]
    total_bytes: int
    file_count: int


def build_cleanup_preview(data_dir: Path, protected_task_ids: set[str] | None = None, minimum_age_days: int = 0) -> CleanupPreview:
    protected = protected_task_ids or set()
    items = []
    for root_name in ("sessions", "datasets"):
        root = Path(data_dir) / root_name
        if not root.is_dir():
            continue
        for child in root.iterdir():
            age_seconds = time.time() - child.stat().st_mtime
            if not child.is_dir() or child.name in protected or age_seconds < max(0, minimum_age_days) * 86400:
                continue
            files = [p for p in child.rglob("*") if p.is_file()]
            items.append(CleanupItem(f"{root_name}/{child.name}", child, len(files), sum(p.stat().st_size for p in files)))
    return CleanupPreview(tuple(items), sum(item.bytes for item in items), sum(item.file_count for item in items))


def remove_cleanup_items(preview: CleanupPreview, relative_paths: set[str]) -> int:
    removed = 0
    for item in preview.items:
        if item.relative_path in relative_paths:
            shutil.rmtree(item.path)
            removed += 1
    return removed
