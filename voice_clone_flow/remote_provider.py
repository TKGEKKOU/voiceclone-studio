from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Callable

from .gpt_sovits.voices import VoiceAsset
from .remote_studio import RemoteStudioClient

logger = logging.getLogger(__name__)


def remote_provider_id(voice_id: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in str(voice_id))
    return f"voice_clone_flow_remote_{safe}"


def build_remote_provider_config(asset: VoiceAsset) -> dict:
    voice_id = asset.remote_voice_id or asset.id
    return {
        "id": remote_provider_id(voice_id),
        "type": "voice_clone_flow_remote",
        "provider": "voice_clone_flow_remote",
        "provider_type": "text_to_speech",
        "enable": asset.status != "disabled",
        "voice_id": voice_id,
        "display_name": asset.name,
        "default_params": {
            "text_lang": asset.reference_language or "zh",
            "prompt_text": asset.reference_text,
        },
    }


class RemoteTTSProvider:
    def __init__(self, client_factory: Callable[[], RemoteStudioClient], voice: VoiceAsset, temp_dir: Path | None = None):
        self.client_factory = client_factory
        self.voice = voice
        self.default_params = {"text_lang": voice.reference_language or "zh"}
        self.temp_dir = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir()) / "voice_clone_flow_remote"

    async def get_audio(self, text: str) -> str:
        import asyncio
        target = self.temp_dir / f"{self.voice.remote_voice_id or self.voice.id}_{next(tempfile._get_candidate_names())}.wav"
        payload = {
            "voice_id": self.voice.remote_voice_id or self.voice.id,
            "text": str(text),
            "text_language": self.voice.reference_language or "zh",
            "prompt_text": self.voice.reference_text,
            "prompt_language": self.voice.reference_language or "zh",
            "stream": True,
        }
        try:
            await asyncio.to_thread(self.client_factory().synthesize, payload, target)
        except Exception as exc:
            logger.warning("远程 Studio 合成失败：%s", type(exc).__name__)
            raise
        return str(target)


async def apply_remote_provider(context, config: dict) -> str:
    manager = getattr(context, "provider_manager", None)
    if manager is None:
        raise RuntimeError("当前 AstrBot Context 未提供 ProviderManager")
    configured = {str(item.get("id")): item for item in getattr(manager, "providers_config", []) if isinstance(item, dict)}
    if config["id"] in getattr(manager, "inst_map", {}) or config["id"] in configured:
        await manager.update_provider(config["id"], config)
    else:
        await manager.create_provider(config)
    return config["id"]
