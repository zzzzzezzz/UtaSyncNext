from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


DEFAULT_ASS_PRESET = "默认双语 (纯白+浅灰)"
FOLLOW_PRESET = "跟随预设"
DEFAULT_FADE_MODE = "智能动态呼吸 (默认/推荐)"


def ensure_hex(value: object, default: str = "#FFFFFF") -> str:
    text = str(value).strip()
    if not text or text.lower() == "none" or len(text) < 3:
        return default
    return text if text.startswith("#") else "#" + text


def build_ass_config(
    ass_presets: Mapping[str, Mapping[str, Any]],
    settings: Mapping[str, Any],
    preset_name: str | None = None,
) -> dict[str, Any]:
    selected_preset = str(preset_name or settings.get("ass_preset") or DEFAULT_ASS_PRESET)
    base_config = ass_presets.get(selected_preset) or ass_presets.get(DEFAULT_ASS_PRESET) or {}
    config = copy.deepcopy(dict(base_config))

    ch_font = settings.get("ch_font_override")
    if ch_font and ch_font != FOLLOW_PRESET:
        config["ch_font"] = ch_font
    jp_font = settings.get("jp_font_override")
    if jp_font and jp_font != FOLLOW_PRESET:
        config["jp_font"] = jp_font

    if settings.get("ch_size_override") != FOLLOW_PRESET:
        _set_int(config, "ch_size", settings.get("ch_size_override"))
    if settings.get("jp_size_override") != FOLLOW_PRESET:
        _set_int(config, "jp_size", settings.get("jp_size_override"))

    config["ch_color"] = ensure_hex(config.get("ch_color"), "#FFFFFF")
    config["jp_color"] = ensure_hex(config.get("jp_color"), "#CCCCCC")
    config["ch_outline"] = ensure_hex(config.get("ch_outline", config.get("outline_color")), "#18181B")
    config["jp_outline"] = ensure_hex(config.get("jp_outline", config.get("outline_color")), "#18181B")
    config["fade_mode"] = settings.get("fade_mode", DEFAULT_FADE_MODE)
    return config


def _set_int(config: dict[str, Any], key: str, value: object) -> None:
    try:
        config[key] = int(str(value))
    except Exception:
        pass
