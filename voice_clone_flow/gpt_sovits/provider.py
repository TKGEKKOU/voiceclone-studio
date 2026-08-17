from __future__ import annotations

PROVIDER_ID = "voice_clone_flow_gsv"


def build_gsv_provider_config(asset, api_base: str, provider_id: str = PROVIDER_ID) -> dict:
    lang = str(getattr(asset, "reference_language", "zh") or "zh")
    return {
        "id": provider_id, "type": "gsv_tts_selfhost", "provider": "gpt_sovits_inference",
        "provider_type": "text_to_speech", "enable": True, "api_base": api_base,
        "gpt_weights_path": str(getattr(asset, "gpt_weights_path", "")),
        "sovits_weights_path": str(getattr(asset, "sovits_weights_path", "")), "timeout": 120,
        "gsv_default_parms": {
            "gsv_ref_audio_path": str(getattr(asset, "refer_audio_path", "")),
            "gsv_prompt_text": str(getattr(asset, "reference_text", "")), "gsv_prompt_lang": lang,
            "gsv_text_lang": lang, "gsv_text_split_method": "cut5" if lang == "ja" else "cut3",
            "gsv_aux_ref_audio_paths": "", "gsv_top_k": 5, "gsv_top_p": 1.0,
            "gsv_temperature": 1.0, "gsv_batch_size": 1, "gsv_batch_threshold": 0.75,
            "gsv_split_bucket": True, "gsv_speed_factor": 1, "gsv_fragment_interval": 0.3,
            "gsv_streaming_mode": False, "gsv_seed": -1, "gsv_parallel_infer": True,
            "gsv_repetition_penalty": 1.35, "gsv_media_type": "wav",
        },
    }


async def apply_gsv_provider(context, config: dict) -> str:
    manager = getattr(context, "provider_manager", None)
    if manager is None:
        raise RuntimeError("当前 AstrBot Context 未提供 ProviderManager")
    configured = {str(item.get("id")) for item in getattr(manager, "providers_config", []) if isinstance(item, dict)}
    if config["id"] in getattr(manager, "inst_map", {}) or config["id"] in configured:
        await manager.update_provider(config["id"], config)
    else:
        await manager.create_provider(config)
    return config["id"]
