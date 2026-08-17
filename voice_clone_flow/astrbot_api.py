"""AstrBot-facing services used by the plugin Page API."""

from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4


ALLOWED_AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".mp4", ".mpeg", ".webm"})
MAX_TEST_AUDIO_BYTES = 20 * 1024 * 1024


class ASRTestError(ValueError):
    pass


def _provider_id(provider: object) -> str:
    config = getattr(provider, "provider_config", {})
    return str(config.get("id", "")).strip() if isinstance(config, dict) else ""


def list_stt_providers(context: object, selected_provider_id: str = "") -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for provider in context.get_all_stt_providers():
        config = getattr(provider, "provider_config", {})
        if not isinstance(config, dict):
            continue
        provider_id = _provider_id(provider)
        if not provider_id:
            continue
        rows.append(
            {
                "id": provider_id,
                "model": str(getattr(provider, "model_name", "") or ""),
                "type": str(config.get("type", "")),
                "selected": provider_id == selected_provider_id,
            }
        )
    return sorted(rows, key=lambda item: str(item["id"]))


async def run_stt_test(
    context: object,
    provider_id: str,
    filename: str,
    content: bytes,
    temporary_dir: Path,
) -> dict[str, object]:
    provider_id = provider_id.strip()
    provider = context.get_provider_by_id(provider_id)
    available_ids = {_provider_id(item) for item in context.get_all_stt_providers()}
    if provider is None or provider_id not in available_ids:
        raise ASRTestError(f"未找到可用的 STT Provider：{provider_id or '未选择'}")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_AUDIO_SUFFIXES:
        raise ASRTestError("仅支持 WAV、MP3、M4A、MP4、MPEG 或 WEBM 音频格式")
    if not content:
        raise ASRTestError("测试音频不能为空")
    if len(content) > MAX_TEST_AUDIO_BYTES:
        raise ASRTestError("测试音频不能超过 20 MB")

    temporary_dir = Path(temporary_dir)
    temporary_dir.mkdir(parents=True, exist_ok=True)
    audio_path = temporary_dir / f"stt-test-{uuid4().hex}{suffix}"
    audio_path.write_bytes(content)
    started = time.perf_counter()
    try:
        text = str(await provider.get_text(str(audio_path))).strip()
        if not text:
            raise ASRTestError("Provider 未返回识别文本")
        return {
            "provider_id": provider_id,
            "text": text,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
    finally:
        audio_path.unlink(missing_ok=True)
