from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.font_names import resolve_render_family
from core.subtitle_policy import wrap_ass_text


MICRO_OVERLAP_LIMIT_MS = 20


ASS_PRESETS: dict[str, dict[str, Any]] = {
    "默认双语 (纯白+浅灰)": {
        "ch_font": "Source Han Sans CN",
        "ch_size": 55,
        "ch_color": "#FFFFFF",
        "ch_outline": "#18181B",
        "jp_font": "Source Han Sans JP",
        "jp_size": 35,
        "jp_color": "#CCCCCC",
        "jp_outline": "#18181B",
        "outline_size": 3,
        "shadow_size": 1,
        "description": "通用最稳",
    },
    "yama专属 (克莱因蓝/低饱和)": {
        "ch_font": "Source Han Sans CN",
        "ch_size": 55,
        "ch_color": "#F0F8FF",
        "ch_outline": "#0C4A6E",
        "jp_font": "Zen Kaku Gothic New",
        "jp_size": 35,
        "jp_color": "#BAE6FD",
        "jp_outline": "#0C4A6E",
        "outline_size": 3,
        "shadow_size": 2,
        "description": "冷调低饱和",
    },
    "majiko专属 (柠檬碎冰/低饱和)": {
        "ch_font": "Source Han Sans CN",
        "ch_size": 55,
        "ch_color": "#FEF9C3",
        "ch_outline": "#3F3F46",
        "jp_font": "M PLUS 1p",
        "jp_size": 35,
        "jp_color": "#FAFAFA",
        "jp_outline": "#3F3F46",
        "outline_size": 3,
        "shadow_size": 1,
        "description": "圆黑冰感",
    },
    "樋口爱专属 (落日绯红/低饱和)": {
        "ch_font": "LXGW WenKai",
        "ch_size": 55,
        "ch_color": "#FFF1F2",
        "ch_outline": "#3B050F",
        "jp_font": "Yu Mincho",
        "jp_size": 35,
        "jp_color": "#C02840",
        "jp_outline": "#3B050F",
        "outline_size": 3,
        "shadow_size": 2,
        "description": "抒情衬线",
    },
    "莫兰迪绿 (清新治愈风)": {
        "ch_font": "LXGW WenKai",
        "ch_size": 55,
        "ch_color": "#F0FDF4",
        "ch_outline": "#064E3B",
        "jp_font": "Zen Kaku Gothic New",
        "jp_size": 35,
        "jp_color": "#8FE3B0",
        "jp_outline": "#04382B",
        "outline_size": 3,
        "shadow_size": 1,
        "description": "清新治愈",
    },
    "紫罗兰光 (微醺梦幻风)": {
        "ch_font": "DengXian",
        "ch_size": 55,
        "ch_color": "#F5F3FF",
        "ch_outline": "#4C1D95",
        "jp_font": "M PLUS 1p",
        "jp_size": 35,
        "jp_color": "#C4A6FF",
        "jp_outline": "#4C1D95",
        "outline_size": 3,
        "shadow_size": 2,
        "description": "梦幻轻飘",
    },
    "黑金质感 (LiveHouse高级感)": {
        "ch_font": "SimHei",
        "ch_size": 55,
        "ch_color": "#FDE68A",
        "ch_outline": "#18181B",
        "jp_font": "Yu Gothic",
        "jp_size": 35,
        "jp_color": "#D4D4D8",
        "jp_outline": "#18181B",
        "outline_size": 3,
        "shadow_size": 2,
        "description": "现场硬朗",
    },
    "B站烤肉标配 (高亮高对比)": {
        "ch_font": "Microsoft YaHei",
        "ch_size": 60,
        "ch_color": "#FFFFFF",
        "ch_outline": "#000000",
        "jp_font": "Source Han Sans JP",
        "jp_size": 38,
        "jp_color": "#FFFFFF",
        "jp_outline": "#000000",
        "outline_size": 4,
        "shadow_size": 3,
        "description": "最高可读",
    },
    "极简电影 (无边细字)": {
        "ch_font": "SimSun",
        "ch_size": 48,
        "ch_color": "#EEEEEE",
        "ch_outline": "#000000",
        "jp_font": "Yu Mincho",
        "jp_size": 30,
        "jp_color": "#E8E8E8",
        "jp_outline": "#000000",
        "outline_size": 2,
        "shadow_size": 1,
        "description": "极简电影感",
    },
}


# Pristine copy of the built-ins.  User edits, renames and deletions are layered
# on top of this snapshot when the user preset file is loaded, so a deleted
# built-in preset cannot silently reappear on the next launch.
BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    name: dict(config) for name, config in ASS_PRESETS.items()
}


@dataclass(frozen=True)
class SubtitleBlock:
    start_ass: str
    end_ass: str
    start_ms: int
    end_ms: int
    lines: list[str]


class ASSMaker:
    """Generate styled ASS subtitles from SRT without relying on legacy modules."""

    def __init__(self, srt_path: str) -> None:
        self.srt_path = srt_path
        base_name = os.path.splitext(srt_path)[0]
        self.ass_path = f"{base_name}_顶级特效.ass"

    def generate_ass(self, style_config: str | dict[str, Any] = "默认双语 (纯白+浅灰)") -> str:
        _safe_log("\n[ASS 引擎] 正在构建特效字幕...")
        config = self._resolve_config(style_config)
        blocks = self._parse_srt(Path(self.srt_path))
        if not blocks:
            raise RuntimeError(f"SRT 中没有可用于生成 ASS 的字幕块: {self.srt_path}")
        blocks, adjusted_overlaps = _trim_micro_overlaps(blocks)
        if adjusted_overlaps:
            _safe_log(
                f"   -> 已消除 {adjusted_overlaps} 处毫秒级字幕重叠，避免播放器把字幕向上避让。"
            )

        events = [self._build_dialogue(block, blocks, index, config) for index, block in enumerate(blocks)]
        content = self._build_header(config) + "\n".join(events) + "\n"
        Path(self.ass_path).write_text(content, encoding="utf-8")
        return self.ass_path

    def _resolve_config(self, style_config: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(style_config, str) and style_config in ASS_PRESETS:
            _safe_log(f"   -> 已应用预设: {style_config}")
            return dict(ASS_PRESETS[style_config])
        if isinstance(style_config, dict):
            _safe_log("   -> 已应用自定义字幕样式")
            merged = dict(ASS_PRESETS["默认双语 (纯白+浅灰)"])
            merged.update(style_config)
            return merged
        _safe_log("   -> 未知预设，回退到默认双语样式")
        return dict(ASS_PRESETS["默认双语 (纯白+浅灰)"])

    def _parse_srt(self, srt_path: Path) -> list[SubtitleBlock]:
        content = srt_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not content:
            return []

        blocks: list[SubtitleBlock] = []
        for raw_block in re.split(r"\n\s*\n", content):
            lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
            if len(lines) < 3:
                continue
            time_line = next((line for line in lines if "-->" in line), "")
            if not time_line:
                continue
            try:
                start_raw, end_raw = [part.strip() for part in time_line.split("-->", 1)]
            except ValueError:
                continue
            text_start_index = lines.index(time_line) + 1
            text_lines = [_escape_ass_text(line) for line in lines[text_start_index:]]
            if not text_lines:
                continue
            blocks.append(
                SubtitleBlock(
                    start_ass=_srt_time_to_ass(start_raw),
                    end_ass=_srt_time_to_ass(end_raw),
                    start_ms=_srt_time_to_ms(start_raw),
                    end_ms=_srt_time_to_ms(end_raw),
                    lines=text_lines,
                )
            )
        return blocks

    def _build_dialogue(
        self,
        block: SubtitleBlock,
        blocks: list[SubtitleBlock],
        index: int,
        config: dict[str, Any],
    ) -> str:
        fade = self._fade_tag(block, blocks, index, str(config.get("fade_mode", "智能动态呼吸 (默认/推荐)")))
        text = self._style_text(block.lines, config, fade)
        return f"Dialogue: 0,{block.start_ass},{block.end_ass},Default,,0,0,0,,{text}"

    def _style_text(self, lines: list[str], config: dict[str, Any], fade: str) -> str:
        # Presets store the family the user picked; the ASS text must carry the
        # name the font matcher can actually find.
        ch_font = resolve_render_family(str(config.get("ch_font", "Microsoft YaHei")))
        jp_font = resolve_render_family(str(config.get("jp_font", "Meiryo")))
        ch_size = int(config.get("ch_size", 55))
        jp_size = int(config.get("jp_size", 35))
        outline_size = int(config.get("outline_size", 3))
        shadow_size = int(config.get("shadow_size", 1))
        ch_max_units = int(config.get("ch_max_units", 64))
        jp_max_units = int(config.get("jp_max_units", 84))

        ch_color = _hex_to_ass_color(str(config.get("ch_color", "#FFFFFF")))
        ch_outline = _hex_to_ass_color(str(config.get("ch_outline", config.get("outline_color", "#18181B"))))
        jp_color = _hex_to_ass_color(str(config.get("jp_color", "#CCCCCC")))
        jp_outline = _hex_to_ass_color(str(config.get("jp_outline", config.get("outline_color", "#18181B"))))

        if len(lines) >= 2:
            jp_text = wrap_ass_text(lines[0], jp_max_units)
            ch_text = "\\N".join(wrap_ass_text(line, ch_max_units) for line in lines[1:])
            styled_ch = (
                f"{fade}{{\\fn{ch_font}\\fs{ch_size}\\c{ch_color}\\3c{ch_outline}"
                f"\\bord{outline_size}\\shad{shadow_size}\\b1}}{ch_text}"
            )
            styled_jp = (
                f"{{\\fn{ch_font}\\fs10}}\\N"
                f"{{\\fn{jp_font}\\fs{jp_size}\\c{jp_color}\\3c{jp_outline}"
                f"\\bord{outline_size}\\shad{shadow_size}\\b0}}{jp_text}"
            )
            return styled_ch + styled_jp

        return (
            f"{fade}{{\\fn{ch_font}\\fs{ch_size}\\c{ch_color}\\3c{ch_outline}"
            f"\\bord{outline_size}\\shad{shadow_size}\\b1}}{wrap_ass_text(lines[0], ch_max_units)}"
        )

    def _fade_tag(self, block: SubtitleBlock, blocks: list[SubtitleBlock], index: int, fade_mode: str) -> str:
        if "强制全局淡入淡出" in fade_mode:
            return r"{\fad(200,200)}"
        if "硬切" in fade_mode:
            return ""

        duration = block.end_ms - block.start_ms
        if duration < 1200:
            return ""

        gap_prev = block.start_ms - blocks[index - 1].end_ms if index > 0 else 9999
        gap_next = blocks[index + 1].start_ms - block.end_ms if index < len(blocks) - 1 else 9999
        fade_in = 0 if gap_prev < 200 else 200
        fade_out = 0 if gap_next < 200 else 200
        return f"{{\\fad({fade_in},{fade_out})}}" if fade_in or fade_out else ""

    def _build_header(self, config: dict[str, Any]) -> str:
        resolution_x = int(config.get("resolution_x", 1920))
        resolution_y = int(config.get("resolution_y", 1080))
        margin_v = int(config.get("margin_v", 30))
        wrap_style = int(config.get("wrap_style", 0))
        return f"""[Script Info]
Title: UtaSyncNext Auto Generated ASS
ScriptType: v4.00+
WrapStyle: {wrap_style}
ScaledBorderAndShadow: yes
PlayResX: {resolution_x}
PlayResY: {resolution_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _srt_time_to_ms(value: str) -> int:
    hms, ms = value.split(",", 1)
    hours, minutes, seconds = [int(part) for part in hms.split(":")]
    return hours * 3_600_000 + minutes * 60_000 + seconds * 1000 + int(ms[:3])


def _srt_time_to_ass(value: str) -> str:
    return _milliseconds_to_ass(_srt_time_to_ms(value))


def _milliseconds_to_ass(total_ms: int) -> str:
    hours = total_ms // 3_600_000
    total_ms %= 3_600_000
    minutes = total_ms // 60_000
    total_ms %= 60_000
    seconds = total_ms // 1000
    centiseconds = (total_ms % 1000) // 10
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _trim_micro_overlaps(blocks: list[SubtitleBlock]) -> tuple[list[SubtitleBlock], int]:
    adjusted = list(blocks)
    adjustment_count = 0
    for index in range(len(adjusted) - 1):
        current = adjusted[index]
        following = adjusted[index + 1]
        overlap_ms = current.end_ms - following.start_ms
        if not (0 < overlap_ms <= MICRO_OVERLAP_LIMIT_MS):
            continue
        if following.start_ms <= current.start_ms:
            continue
        adjusted[index] = SubtitleBlock(
            start_ass=current.start_ass,
            end_ass=_milliseconds_to_ass(following.start_ms),
            start_ms=current.start_ms,
            end_ms=following.start_ms,
            lines=current.lines,
        )
        adjustment_count += 1
    return adjusted, adjustment_count


def _hex_to_ass_color(value: str) -> str:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return "&HFFFFFF&"
    red, green, blue = text[0:2], text[2:4], text[4:6]
    return f"&H{blue}{green}{red}&"


def _escape_ass_text(value: str) -> str:
    return value.replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _safe_log(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))
