from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from utils.ffmpeg_tools import safe_run


@dataclass(frozen=True)
class SubtitleLayoutPolicy:
    profile: str
    source_width: int
    source_height: int
    resolution_x: int
    resolution_y: int
    margin_v: int
    ch_size_cap: int
    jp_size_cap: int
    ch_max_units: int
    jp_max_units: int
    note: str


@dataclass(frozen=True)
class TimedSubtitlePart:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class SubtitleDensityMetrics:
    display_units: int
    visual_lines: int
    duration_seconds: float


def resolve_subtitle_layout(media_path: str, settings: Mapping[str, Any]) -> SubtitleLayoutPolicy:
    width, height = _probe_video_size(media_path)
    aspect_setting = str(settings.get("aspect_ratio") or "")
    ratio = width / height if width and height else 16 / 9
    fast_caption = _is_fast_caption_mode(settings)

    if "9:16" in aspect_setting:
        return _with_fast_caption(_portrait_policy(width, height, "手动选择竖屏安全区"), fast_caption)
    if "1:1" in aspect_setting or "正方形" in aspect_setting:
        return _with_fast_caption(_square_policy(width, height, "手动选择正方形安全区"), fast_caption)
    if "4:3" in aspect_setting:
        return _with_fast_caption(
            SubtitleLayoutPolicy(
                profile="classic_4_3",
                source_width=width,
                source_height=height,
                resolution_x=1440,
                resolution_y=1080,
                margin_v=70,
                ch_size_cap=50,
                jp_size_cap=32,
                ch_max_units=52,
                jp_max_units=68,
                note="4:3 画幅安全区",
            ),
            fast_caption,
        )

    # Keep the user's old default usable, but automatically protect obvious
    # square/vertical sources where 16:9 subtitle coordinates will be clipped.
    if ratio <= 0.82:
        return _with_fast_caption(_portrait_policy(width, height, "检测到竖屏源，已自动启用竖屏安全区"), fast_caption)
    if ratio <= 1.20:
        return _with_fast_caption(_square_policy(width, height, "检测到正方形/窄画幅源，已自动启用正方形安全区"), fast_caption)
    if "自动侦测" in aspect_setting and ratio < 1.55:
        return _with_fast_caption(
            SubtitleLayoutPolicy(
                profile="classic_4_3",
                source_width=width,
                source_height=height,
                resolution_x=1440,
                resolution_y=1080,
                margin_v=70,
                ch_size_cap=50,
                jp_size_cap=32,
                ch_max_units=52,
                jp_max_units=68,
                note="自动侦测到窄屏源，启用 4:3 安全区",
            ),
            fast_caption,
        )

    return _with_fast_caption(
        SubtitleLayoutPolicy(
            profile="wide_16_9",
            source_width=width,
            source_height=height,
            resolution_x=1920,
            resolution_y=1080,
            margin_v=60,
            ch_size_cap=60,
            jp_size_cap=38,
            ch_max_units=64,
            jp_max_units=84,
            note="16:9 宽屏安全区",
        ),
        fast_caption,
    )


def apply_layout_policy(config: dict[str, Any], policy: SubtitleLayoutPolicy) -> dict[str, Any]:
    tuned = dict(config)
    tuned["layout_profile"] = policy.profile
    tuned["layout_note"] = policy.note
    tuned["source_width"] = policy.source_width
    tuned["source_height"] = policy.source_height
    tuned["resolution_x"] = policy.resolution_x
    tuned["resolution_y"] = policy.resolution_y
    tuned["margin_v"] = policy.margin_v
    tuned["ch_max_units"] = policy.ch_max_units
    tuned["jp_max_units"] = policy.jp_max_units
    tuned["ch_size"] = min(_as_int(tuned.get("ch_size"), 55), policy.ch_size_cap)
    tuned["jp_size"] = min(_as_int(tuned.get("jp_size"), 35), policy.jp_size_cap)
    return tuned


def wrap_ass_text(text: str, max_units: int) -> str:
    parts = str(text or "").split(r"\N")
    wrapped = [_wrap_plain_ass_line(part.strip(), max_units) for part in parts if part.strip()]
    return r"\N".join(wrapped) if wrapped else str(text or "")


def measure_subtitle_density(text: str, duration_seconds: float, max_units: int) -> SubtitleDensityMetrics:
    normalized = str(text or "").replace(r"\N", "\n")
    visual_lines = 0
    for line in normalized.splitlines() or [normalized]:
        units = display_units(line)
        visual_lines += max(1, math.ceil(units / max(1, max_units)))
    return SubtitleDensityMetrics(
        display_units=display_units(normalized),
        visual_lines=visual_lines,
        duration_seconds=max(0.0, float(duration_seconds)),
    )


def split_timed_subtitle_for_layout(
    text: str,
    start: float,
    end: float,
    policy: SubtitleLayoutPolicy | None,
) -> list[TimedSubtitlePart]:
    """Split only dense narrow-screen cues while preserving all text and time."""
    clean_text = str(text or "").strip()
    start_value = max(0.0, float(start))
    end_value = max(start_value, float(end))
    original = [TimedSubtitlePart(start_value, end_value, clean_text)]
    if not clean_text or policy is None or policy.profile not in {"square_safe", "portrait_safe"}:
        return original

    metrics = measure_subtitle_density(clean_text, end_value - start_value, policy.jp_max_units)
    parts_for_lines = math.ceil(metrics.visual_lines / 2)
    duration_is_dense = (
        metrics.duration_seconds > 5.0
        and metrics.display_units >= max(24, int(policy.jp_max_units * 0.75))
    )
    parts_for_duration = math.ceil(metrics.duration_seconds / 5.0) if duration_is_dense else 1
    requested_parts = max(1, parts_for_lines, parts_for_duration)

    # Avoid tiny flashes when a long Whisper segment contains very little text.
    minimum_part_units = max(20, min(28, policy.jp_max_units // 2))
    possible_parts = max(1, metrics.display_units // minimum_part_units)
    part_count = min(requested_parts, possible_parts)
    if part_count <= 1:
        return original

    text_parts = _split_text_evenly(clean_text, part_count, max_part_units=policy.jp_max_units * 2)
    if len(text_parts) <= 1:
        return original

    weights = [max(1, display_units(part)) for part in text_parts]
    total_weight = sum(weights)
    duration = end_value - start_value
    timed_parts: list[TimedSubtitlePart] = []
    elapsed_weight = 0
    for index, (part, weight) in enumerate(zip(text_parts, weights)):
        part_start = start_value + duration * elapsed_weight / total_weight
        elapsed_weight += weight
        part_end = end_value if index == len(text_parts) - 1 else start_value + duration * elapsed_weight / total_weight
        timed_parts.append(TimedSubtitlePart(part_start, part_end, part))
    return timed_parts


def display_units(text: str) -> int:
    return sum(_char_units(char) for char in _strip_ass_tags(str(text or "")))


def policy_log_line(policy: SubtitleLayoutPolicy) -> str:
    return (
        f"字幕：{policy.note}，源视频 {policy.source_width}x{policy.source_height}，"
        f"ASS 画布 {policy.resolution_x}x{policy.resolution_y}。"
    )


def _is_fast_caption_mode(settings: Mapping[str, Any]) -> bool:
    audio_type = str(settings.get("audio_type") or "")
    return "高语速" in audio_type or "短字幕" in audio_type


def _with_fast_caption(policy: SubtitleLayoutPolicy, enabled: bool) -> SubtitleLayoutPolicy:
    if not enabled:
        return policy

    if policy.profile == "wide_16_9":
        return replace(
            policy,
            ch_size_cap=min(policy.ch_size_cap, 52),
            jp_size_cap=min(policy.jp_size_cap, 32),
            ch_max_units=min(policy.ch_max_units, 52),
            jp_max_units=min(policy.jp_max_units, 68),
            note=f"{policy.note}，高语速短字幕",
        )
    if policy.profile == "classic_4_3":
        return replace(
            policy,
            ch_size_cap=min(policy.ch_size_cap, 46),
            jp_size_cap=min(policy.jp_size_cap, 30),
            ch_max_units=min(policy.ch_max_units, 44),
            jp_max_units=min(policy.jp_max_units, 60),
            note=f"{policy.note}，高语速短字幕",
        )
    return replace(
        policy,
        ch_size_cap=min(policy.ch_size_cap, 40),
        jp_size_cap=min(policy.jp_size_cap, 26),
        ch_max_units=min(policy.ch_max_units, 36),
        jp_max_units=min(policy.jp_max_units, 52),
        note=f"{policy.note}，高语速短字幕",
    )


def _square_policy(width: int, height: int, note: str) -> SubtitleLayoutPolicy:
    return SubtitleLayoutPolicy(
        profile="square_safe",
        source_width=width,
        source_height=height,
        resolution_x=1080,
        resolution_y=1080,
        margin_v=96,
        ch_size_cap=44,
        jp_size_cap=28,
        ch_max_units=40,
        jp_max_units=54,
        note=note,
    )


def _portrait_policy(width: int, height: int, note: str) -> SubtitleLayoutPolicy:
    return SubtitleLayoutPolicy(
        profile="portrait_safe",
        source_width=width,
        source_height=height,
        resolution_x=1080,
        resolution_y=1920,
        margin_v=150,
        ch_size_cap=42,
        jp_size_cap=26,
        ch_max_units=34,
        jp_max_units=46,
        note=note,
    )


def _probe_video_size(media_path: str) -> tuple[int, int]:
    path = Path(media_path)
    if not path.exists():
        return 0, 0
    try:
        result = safe_run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(path),
            ],
            timeout=20,
        )
        if result.returncode != 0:
            return 0, 0
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") if isinstance(data, dict) else None
        first = streams[0] if isinstance(streams, list) and streams else {}
        return int(first.get("width") or 0), int(first.get("height") or 0)
    except Exception:
        return 0, 0


def _wrap_plain_ass_line(text: str, max_units: int) -> str:
    if not text or display_units(text) <= max_units:
        return text

    lines: list[str] = []
    rest = text
    while rest and display_units(rest) > max_units:
        break_at = _find_break_index(rest, max_units)
        if break_at <= 0:
            break_at = _index_for_units(rest, max_units)
        head = rest[:break_at].strip()
        if head:
            lines.append(head)
        rest = rest[break_at:].strip()
    if rest:
        lines.append(rest)
    return r"\N".join(lines)


def _find_break_index(text: str, max_units: int) -> int:
    best = -1
    units = 0
    punctuation = "，。！？、,.!?;；:：）)]】」』"
    for index, char in enumerate(text):
        units += _char_units(char)
        if units > max_units:
            break
        if char in punctuation or char.isspace():
            best = index + 1
    if best > 0 and display_units(text[:best]) >= max_units * 0.45:
        return best
    return -1


def _index_for_units(text: str, max_units: int) -> int:
    units = 0
    for index, char in enumerate(text):
        units += _char_units(char)
        if units > max_units:
            return max(1, index)
    return len(text)


def _split_text_evenly(text: str, part_count: int, max_part_units: int | None = None) -> list[str]:
    parts: list[str] = []
    rest = text.strip()
    remaining_parts = part_count
    while rest and remaining_parts > 1:
        rest_units = display_units(rest)
        target_units = max(1, math.ceil(rest_units / remaining_parts))
        minimum_head_units = 1
        if max_part_units:
            minimum_head_units = max(1, rest_units - max_part_units * (remaining_parts - 1))
            target_units = min(max_part_units, max(target_units, minimum_head_units))
        break_at = _find_balanced_break_index(
            rest,
            target_units,
            minimum_units=minimum_head_units,
            maximum_units=max_part_units,
        )
        if break_at <= 0:
            break_at = _index_for_units(rest, target_units)
        head = rest[:break_at]
        if not head.strip():
            break
        parts.append(head)
        rest = rest[break_at:]
        remaining_parts -= 1
    if rest:
        parts.append(rest)
    return parts


def _find_balanced_break_index(
    text: str,
    target_units: int,
    *,
    minimum_units: int = 1,
    maximum_units: int | None = None,
) -> int:
    primary_candidates: list[tuple[int, int]] = []
    japanese_candidates: list[tuple[int, int]] = []
    units = 0
    punctuation = "、。！？!?，,；;：:)]）】」』"
    japanese_boundaries = ("から", "ので", "けど", "って", "たり", "して", "ても", "のに", "なら", "では", "には")
    japanese_particles = "はがをにでとへも"
    lower = max(minimum_units, int(target_units * 0.55))
    upper = max(lower, int(target_units * 1.45))
    if maximum_units:
        upper = min(upper, maximum_units)
    for index, char in enumerate(text):
        units += _char_units(char)
        if units > upper:
            break
        if units >= lower and (char in punctuation or char.isspace()):
            primary_candidates.append((abs(units - target_units), index + 1))
        prefix = text[: index + 1]
        if units >= lower and (
            char in japanese_particles or any(prefix.endswith(boundary) for boundary in japanese_boundaries)
        ):
            japanese_candidates.append((abs(units - target_units), index + 1))
    if primary_candidates:
        return min(primary_candidates)[1]
    if japanese_candidates:
        return min(japanese_candidates)[1]
    return -1


def _char_units(char: str) -> int:
    if unicodedata.east_asian_width(char) in {"F", "W", "A"}:
        return 2
    return 1


def _strip_ass_tags(text: str) -> str:
    return re.sub(r"\{[^}]*\}", "", text)


def _as_int(value: object, default: int) -> int:
    try:
        return int(str(value))
    except Exception:
        return default
