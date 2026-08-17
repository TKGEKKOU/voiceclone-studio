from __future__ import annotations

import shutil
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetRow:
    audio_path: Path
    text: str
    language: str
    approved: bool


@dataclass(frozen=True)
class DatasetExport:
    root: Path
    audio_dir: Path
    manifest: Path
    count: int
    skipped_count: int = 0


_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_CONTENT_RE = re.compile(r"[\w\u3040-\u30ff\u3400-\u9fff]", re.UNICODE)


def _is_low_information_row(text: str, language: str) -> bool:
    content_length = len(_CONTENT_RE.findall(text))
    return language.strip().lower() == "ja" and not _KANA_RE.search(text) and content_length <= 2


def export_gpt_sovits_dataset(rows: list[DatasetRow], target: Path) -> DatasetExport:
    target = Path(target)
    audio_dir = target / "wavs"
    audio_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    skipped_count = 0
    for row in rows:
        text = row.text.strip().replace("\n", " ").replace("|", " ")
        if not row.approved or not text or not row.audio_path.is_file():
            continue
        if _is_low_information_row(text, row.language):
            skipped_count += 1
            continue
        destination = audio_dir / row.audio_path.name
        shutil.copy2(row.audio_path, destination)
        lines.append(f"{destination}|voice|{row.language.upper()}|{text}")
    if not lines:
        raise ValueError("没有可导出的已审核片段")
    manifest = target / "train.list"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return DatasetExport(target, audio_dir, manifest, len(lines), skipped_count)
