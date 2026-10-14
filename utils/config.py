from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine.cache_env import app_root

CONFIG_FILE = app_root() / "user_settings.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "ui_variant": "next",
    "media_path": "",
    "cached_lyrics": "",
    "audio_type": "Live 现场演唱 (常规/摇滚)",
    "hardware_mode": "自动匹配显存 (推荐)",
    "use_demucs": "先跳过 Demucs (极速/推荐先出轴)",
    "model_size": "large-v2 (战神级/推荐)",
    "output_mode": "中日双语字幕",
    "ass_preset": "默认双语 (纯白+浅灰)",
    "ch_font_override": "跟随预设",
    "jp_font_override": "跟随预设",
    "ch_size_override": "跟随预设",
    "jp_size_override": "跟随预设",
    "aspect_ratio": "16:9 (常规宽屏/默认)",
    "fade_mode": "智能动态呼吸 (默认/推荐)",
    "hires_export": False,
    "api_preset": "SiliconFlow (DeepSeek-V3)",
    "api_provider": "SiliconFlow",
    "api_url": "https://api.siliconflow.cn/v1",
    "api_model": "deepseek-ai/DeepSeek-V3",
    "api_key": "",
    "proxy_url": "",
    "model_download_proxy": "",
    "api_preset_design": "与翻译引擎一致 (默认)",
    "api_url_design": "",
    "api_model_design": "",
    "api_key_design": "",
}


def load_settings() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return DEFAULT_SETTINGS.copy()

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return DEFAULT_SETTINGS.copy()

    settings = DEFAULT_SETTINGS.copy()
    if isinstance(data, dict):
        settings.update(data)
    return _normalize_settings(settings)


def save_settings(settings: dict[str, Any]) -> None:
    settings = _normalize_settings(dict(settings))
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open("w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)


def _normalize_settings(settings: dict[str, Any]) -> dict[str, Any]:
    raw_settings = dict(settings)
    settings = DEFAULT_SETTINGS.copy()
    settings.update(raw_settings)
    if str(settings.get("ui_variant", "classic")).lower() not in {"classic", "next"}:
        settings["ui_variant"] = "classic"
    audio_aliases = {
        "Live": "Live 现场演唱 (常规/摇滚)",
        "Live Soft": "Live 现场演唱 (极柔气声/清唱)",
        "Speech": "访谈 / 电台播客",
        "现场演唱": "Live 现场演唱 (常规/摇滚)",
        "访谈 / 电台播客": "访谈 / 电台播客",
    }
    output_aliases = {
        "Bilingual": "中日双语字幕",
        "Chinese Only": "纯中文字幕",
        "双语字幕": "中日双语字幕",
        "纯中文字幕": "纯中文字幕",
    }
    demucs_aliases = {
        "Enabled": "开启 Demucs 人声分离 (高质量/很慢)",
        "Skip": "先跳过 Demucs (极速/推荐先出轴)",
        "强制跳过": "先跳过 Demucs (极速/推荐先出轴)",
        "开启分离 (推荐)": "开启 Demucs 人声分离 (高质量/很慢)",
        "开启分离 (Live音乐必选)": "开启 Demucs 人声分离 (高质量/很慢)",
    }
    hardware_aliases = {
        "极限防爆 (4G~6G显存/默认)": "自动匹配显存 (推荐)",
        "均衡提速 (8G显存)": "均衡分离 (8G显存)",
        "满血狂飙 (12G+显存)": "快速分离 (12G+显存)",
    }

    settings["audio_type"] = audio_aliases.get(str(settings.get("audio_type", "")), settings["audio_type"])
    settings["output_mode"] = output_aliases.get(str(settings.get("output_mode", "")), settings["output_mode"])
    settings["hardware_mode"] = hardware_aliases.get(
        str(settings.get("hardware_mode", "")),
        str(settings.get("hardware_mode") or DEFAULT_SETTINGS["hardware_mode"]),
    )

    if "use_demucs" not in raw_settings and "demucs_mode" in raw_settings:
        settings["use_demucs"] = raw_settings["demucs_mode"]
    settings["use_demucs"] = demucs_aliases.get(str(settings.get("use_demucs", "")), settings["use_demucs"])

    model_aliases = {
        "large-v2": "large-v2 (战神级/推荐)",
        "large-v3-turbo": "large-v3-turbo (极速/演唱慎用)",
        # The turbo option used to read "极速高精". A model comparison on
        # the same sung material showed turbo drops lines and can swallow
        # the opening, so the label now warns about singing. The old label
        # still maps here, otherwise a saved user_settings.json would
        # silently fall back to large-v2.
        "large-v3-turbo (极速高精)": "large-v3-turbo (极速/演唱慎用)",
        "medium": "medium (省显存)",
    }
    settings["model_size"] = model_aliases.get(str(settings.get("model_size", "")), settings["model_size"])

    legacy_key_map = {
        "ch_font": "ch_font_override",
        "jp_font": "jp_font_override",
        "ch_size": "ch_size_override",
        "jp_size": "jp_size_override",
        "visual_api_url": "api_url_design",
        "visual_api_model": "api_model_design",
        "visual_api_key": "api_key_design",
    }
    for legacy_key, current_key in legacy_key_map.items():
        if legacy_key in settings and settings.get(legacy_key) and not settings.get(current_key):
            settings[current_key] = settings[legacy_key]

    if str(settings.get("visual_api_provider", "")) == "与翻译引擎一致":
        settings["api_preset_design"] = "与翻译引擎一致 (默认)"

    global_proxy = str(settings.get("proxy_url") or settings.get("model_download_proxy") or "").strip()
    settings["proxy_url"] = global_proxy
    settings["model_download_proxy"] = global_proxy

    return settings
