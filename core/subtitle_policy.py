from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from core.ass_config import FOLLOW_PRESET
from utils.ffmpeg_tools import safe_run


# A font size the operator picked by hand outranks the safe-area ceiling, but the
# per-line character budget is then recomputed so the bigger text still fits the
# frame instead of running past the canvas edge.
FRAME_FILL_RATIO = 0.9
MIN_LINE_UNITS = 8


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
class SubtitleTextScale:
    """Effective font sizes and per-line budgets for the current settings."""

    ch_size: int | None
    jp_size: int | None
    ch_max_units: int
    jp_max_units: int


@dataclass(frozen=True)
class TimedSubtitlePart:
    start: float
    end: float
    text: str


_SRT_BLOCK_RE = re.compile(
    r"^\s*(\d+)\s*\r?\n"
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\r?\n"
    r"([\s\S]*?)\s*$"
)


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


def explicit_size_override(settings: Mapping[str, Any], key: str) -> int | None:
    """Return the hand-picked font size for ``key``, or None to follow the preset."""

    raw = str(settings.get(key) or "").strip()
    if not raw or raw == FOLLOW_PRESET:
        return None
    try:
        value = int(float(raw))
    except ValueError:
        return None
    return value if value > 0 else None


def line_unit_budget(canvas_width: int, size: int | None, fallback: int) -> int:
    """Shrink the per-line budget when a larger font would overflow the canvas.

    ``display_units`` counts a full-width CJK glyph as two, so one unit is about
    ``size / 2`` pixels wide. The budget only ever tightens: a small font keeps the
    policy's original line length instead of suddenly packing in more text.
    """

    if size is None or canvas_width <= 0:
        return int(fallback)
    units = int(canvas_width * FRAME_FILL_RATIO * 2 / size)
    return max(MIN_LINE_UNITS, min(int(fallback), units))


def resolve_text_scale(
    policy: SubtitleLayoutPolicy,
    settings: Mapping[str, Any],
) -> SubtitleTextScale:
    ch_size = explicit_size_override(settings, "ch_size_override")
    jp_size = explicit_size_override(settings, "jp_size_override")
    return SubtitleTextScale(
        ch_size=ch_size,
        jp_size=jp_size,
        ch_max_units=line_unit_budget(policy.resolution_x, ch_size, policy.ch_max_units),
        jp_max_units=line_unit_budget(policy.resolution_x, jp_size, policy.jp_max_units),
    )


def apply_layout_policy(
    config: dict[str, Any],
    policy: SubtitleLayoutPolicy,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tuned = dict(config)
    scale = resolve_text_scale(policy, settings) if settings is not None else None

    if scale is not None and scale.ch_size is not None:
        tuned["ch_size"] = scale.ch_size
    else:
        tuned["ch_size"] = min(_as_int(tuned.get("ch_size"), 55), policy.ch_size_cap)
    if scale is not None and scale.jp_size is not None:
        tuned["jp_size"] = scale.jp_size
    else:
        tuned["jp_size"] = min(_as_int(tuned.get("jp_size"), 35), policy.jp_size_cap)

    tuned["layout_profile"] = policy.profile
    tuned["layout_note"] = policy.note
    tuned["source_width"] = policy.source_width
    tuned["source_height"] = policy.source_height
    tuned["resolution_x"] = policy.resolution_x
    tuned["resolution_y"] = policy.resolution_y
    tuned["margin_v"] = policy.margin_v
    tuned["ch_max_units"] = scale.ch_max_units if scale is not None else policy.ch_max_units
    tuned["jp_max_units"] = scale.jp_max_units if scale is not None else policy.jp_max_units
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
    max_duration_seconds: float | None = None,
) -> list[TimedSubtitlePart]:
    """Split dense cues while preserving every character and the full ASR time window."""
    clean_text = str(text or "").strip()
    start_value = max(0.0, float(start))
    end_value = max(start_value, float(end))
    original = [TimedSubtitlePart(start_value, end_value, clean_text)]
    if not clean_text or policy is None:
        return original

    is_narrow = policy.profile in {"square_safe", "portrait_safe"}
    requested_max_duration = max(0.0, float(max_duration_seconds or 0.0))
    if not is_narrow and (
        requested_max_duration <= 0.0 or end_value - start_value <= requested_max_duration
    ):
        return original

    metrics = measure_subtitle_density(clean_text, end_value - start_value, policy.jp_max_units)
    parts_for_lines = math.ceil(metrics.visual_lines / 2) if is_narrow else 1
    duration_limit = 5.0 if is_narrow else requested_max_duration
    duration_is_dense = (
        duration_limit > 0.0
        and metrics.duration_seconds > duration_limit
        and metrics.display_units >= max(24, int(policy.jp_max_units * 0.75))
    )
    parts_for_duration = math.ceil(metrics.duration_seconds / duration_limit) if duration_is_dense else 1
    requested_parts = max(1, parts_for_lines, parts_for_duration)

    # Avoid tiny flashes when a long Whisper segment contains very little text.
    minimum_part_units = 36 if not is_narrow and duration_limit <= 5.0 else max(
        20,
        min(28, policy.jp_max_units // 2),
    )
    possible_parts = max(1, metrics.display_units // minimum_part_units)
    part_count = min(requested_parts, possible_parts)
    if part_count <= 1:
        extreme_hold_threshold = max(18.0, duration_limit * 2.0)
        if duration_limit > 0.0 and metrics.duration_seconds > extreme_hold_threshold:
            return [
                TimedSubtitlePart(
                    start_value,
                    min(end_value, start_value + duration_limit),
                    clean_text,
                )
            ]
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


def cap_extreme_sparse_srt_cues(
    srt_path: str | Path,
    policy: SubtitleLayoutPolicy,
    *,
    max_duration_seconds: float,
) -> int:
    """Limit only extreme sparse holds, including cues loaded from ASR cache."""
    path = Path(srt_path)
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    if not content:
        return 0

    output_blocks: list[str] = []
    changed = 0
    for raw_block in re.split(r"\r?\n\s*\r?\n", content):
        match = _SRT_BLOCK_RE.match(raw_block)
        if not match:
            output_blocks.append(raw_block.strip())
            continue
        index, start_text, end_text, text = match.groups()
        start = _parse_srt_timestamp(start_text)
        end = _parse_srt_timestamp(end_text)
        parts = split_timed_subtitle_for_layout(
            " ".join(line.strip() for line in text.splitlines() if line.strip()),
            start,
            end,
            policy,
            max_duration_seconds=max_duration_seconds,
        )
        if len(parts) == 1 and parts[0].end < end - 0.001:
            end_text = _format_srt_timestamp(parts[0].end)
            changed += 1
        output_blocks.append(f"{index}\n{start_text} --> {end_text}\n{text.strip()}")

    if not changed:
        return 0
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.write_text("\n\n".join(output_blocks) + "\n", encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
    return changed


def _parse_srt_timestamp(value: str) -> float:
    hours, minutes, remainder = value.split(":")
    seconds, milliseconds = remainder.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000.0


def _format_srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def display_units(text: str) -> int:
    return sum(_char_units(char) for char in _strip_ass_tags(str(text or "")))


def policy_log_line(policy: SubtitleLayoutPolicy, config: Mapping[str, Any] | None = None) -> str:
    line = (
        f"字幕：{policy.note}，源视频 {policy.source_width}x{policy.source_height}，"
        f"ASS 画布 {policy.resolution_x}x{policy.resolution_y}。"
    )
    if config:
        line += (
            f" 字号: 中文 {_as_int(config.get('ch_size'), 0)} / 日文 {_as_int(config.get('jp_size'), 0)}"
            f"（安全区上限 中文 {policy.ch_size_cap} / 日文 {policy.jp_size_cap}）；"
            f"每行上限 中文 {_as_int(config.get('ch_max_units'), 0)} / 日文 {_as_int(config.get('jp_max_units'), 0)}。"
        )
    return line


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
