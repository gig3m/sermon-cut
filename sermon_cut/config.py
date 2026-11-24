from __future__ import annotations
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED = ("church_name", "youtube_channel_id", "default_speaker", "canva_template_id")


@dataclass
class ShortsConfig:
    caption_font_size: int = 84
    cap_y: int = 1400
    amber: tuple = (255, 201, 60)
    deadzone: int = 95
    maxjump: int = 200
    max_vel: float = 5.0
    pre_alpha: float = 0.20
    post_alpha: float = 0.08
    min_fg: int = 3060
    # tracking ROI (source-px y band): track the UPPER BODY (head+torso), not the
    # whole figure, so an arm gesture doesn't pull the frame off the speaker.
    # Camera-specific, like the crop geometry.
    roi_top: int = 400
    roi_bottom: int = 680
    crop_w: int = 430
    crop_h: int = 764
    crop_y: int = 290
    fade_up: float = 0.4
    dissolve: float = 0.6
    outro_hold: float = 2.4
    fade_out: float = 0.5
    target_min: int = 20
    target_max: int = 60
    hard_min: int = 15
    hard_max: int = 180


@dataclass
class Config:
    church_name: str
    youtube_channel_id: str
    default_speaker: str
    canva_template_id: str
    intro_seconds: int = 5
    outro_seconds: int = 5
    front_padding_seconds: int = 1
    back_padding_seconds: int = 4
    dissolve_seconds: float = 0.8
    fade_seconds: float = 0.6
    whisper_model: str = "medium"
    description_footer: str = ""
    notify_to: str = ""
    notify_from: str = ""
    shorts: ShortsConfig = field(default_factory=ShortsConfig)


def load_config(path: Path) -> Config:
    data = tomllib.loads(Path(path).read_text())
    for key in REQUIRED:
        if key not in data:
            raise KeyError(f"config.toml missing required field: {key}")
    shorts_data = data.pop("shorts", {})
    data["shorts"] = ShortsConfig(**shorts_data)
    return Config(**data)
