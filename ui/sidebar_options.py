from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApiPreset:
    base_url: str
    model: str


AUDIO_TYPE_OPTIONS = [
    "Live 现场演唱 (常规/摇滚)",
    "Live 高语速/窄画幅短字幕",
    "Live 现场演唱 (极柔气声/清唱)",
    "访谈 / 电台播客",
]
DEFAULT_AUDIO_TYPE = AUDIO_TYPE_OPTIONS[0]

HARDWARE_MODE_OPTIONS = [
    "极限防爆 (4G~6G显存/默认)",
    "均衡提速 (8G显存)",
    "满血狂飙 (12G+显存)",
]
DEFAULT_HARDWARE_MODE = HARDWARE_MODE_OPTIONS[0]

DEMUCS_OPTIONS = [
    "先跳过 Demucs (极速/推荐先出轴)",
    "开启 Demucs 人声分离 (高质量/很慢)",
]
DEFAULT_DEMUCS_OPTION = DEMUCS_OPTIONS[0]

WHISPER_MODEL_OPTIONS = [
    "large-v2 (战神级/推荐)",
    "large-v3-turbo (极速高精)",
    "large-v3",
    "medium (省显存)",
    "small",
    "base",
]
DEFAULT_WHISPER_MODEL = WHISPER_MODEL_OPTIONS[0]

OUTPUT_MODE_OPTIONS = ["中日双语字幕", "纯中文字幕"]
DEFAULT_OUTPUT_MODE = OUTPUT_MODE_OPTIONS[0]

ASS_DISABLED_OPTION = "不生成 (仅SRT)"
DEFAULT_ASS_PRESET = "默认双语 (纯白+浅灰)"

FONT_SIZE_OPTIONS = ["跟随预设", "30", "35", "40", "45", "50", "55", "60", "65", "70", "75", "80", "90"]

ASPECT_RATIO_OPTIONS = [
    "16:9 (常规宽屏/默认)",
    "自动侦测 (跟随原视频源)",
    "1:1 (正方形/电台封面)",
    "4:3 (复古老番/传统屏幕)",
    "9:16 (竖屏短视频)",
]
DEFAULT_ASPECT_RATIO = ASPECT_RATIO_OPTIONS[0]

FADE_MODE_OPTIONS = [
    "智能动态呼吸 (默认/推荐)",
    "全局硬切无延时 (极速弹出)",
    "强制全局淡入淡出 (旧版)",
]
DEFAULT_FADE_MODE = FADE_MODE_OPTIONS[0]

TRANSLATION_API_PRESETS = {
    "SiliconFlow (DeepSeek-V3)": ApiPreset("https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3"),
    "Kimi (月之暗面)": ApiPreset("https://api.moonshot.cn/v1", "moonshot-v1-32k"),
    "Google Gemini": ApiPreset("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash"),
    "完全自定义": ApiPreset("", ""),
}
DEFAULT_API_PRESET = "SiliconFlow (DeepSeek-V3)"

DESIGN_API_PRESETS = {
    "与翻译引擎一致 (默认)": None,
    "SiliconFlow (DeepSeek-V3)": ApiPreset("https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3"),
    "Kimi (月之暗面)": ApiPreset("https://api.moonshot.cn/v1", "moonshot-v1-32k"),
    "Google Gemini": ApiPreset("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-pro"),
    "完全自定义": ApiPreset("", ""),
}
DEFAULT_DESIGN_API_PRESET = "与翻译引擎一致 (默认)"
