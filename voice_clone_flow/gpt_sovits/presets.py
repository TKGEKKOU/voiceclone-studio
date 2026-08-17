from dataclasses import dataclass


@dataclass(frozen=True)
class TrainingPreset:
    id: str
    label: str
    gpt_epochs: int
    sovits_epochs: int


PRESETS = (
    TrainingPreset("light", "轻量训练", 5, 10),
    TrainingPreset("quick", "快速训练", 10, 20),
    TrainingPreset("standard", "标准训练", 15, 30),
    TrainingPreset("enhanced", "增强训练", 20, 50),
    TrainingPreset("fine", "精细训练", 30, 100),
)

GPT_EPOCH_RANGE = range(3, 51)
SOVITS_EPOCH_RANGE = range(5, 151)


def validate_training_epochs(gpt_epochs: int, sovits_epochs: int) -> tuple[int, int]:
    if gpt_epochs not in GPT_EPOCH_RANGE:
        raise ValueError("GPT Epoch 必须在 3 到 50 之间")
    if sovits_epochs not in SOVITS_EPOCH_RANGE:
        raise ValueError("SoVITS Epoch 必须在 5 到 150 之间")
    return gpt_epochs, sovits_epochs


def get_training_preset(preset_id: str) -> TrainingPreset:
    for preset in PRESETS:
        if preset.id == preset_id:
            return preset
    raise ValueError(f"未知训练预设: {preset_id}")
