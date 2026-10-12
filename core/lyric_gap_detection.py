from __future__ import annotations

import difflib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from core.lyrics_context import LyricsSection, parse_lyrics_sections


GAP_POLICY = "lyrics-opening-gap-review-v4-local-sequence"
MAX_OPENING_REFERENCE_INDEX = 7
_SRT_BLOCK_RE = re.compile(
    r"^\s*(\d+)\s*\r?\n"
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\r?\n"
    r"([\s\S]*?)\s*$"
)


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class OpeningLyricGapCandidate:
    section_title: str
    cue_index: int
    previous_cue_end: float
    detected_cue_start: float
    gap_start: float
    gap_end: float
    gap_seconds: float
    detected_reference_index: int
    missing_lines: tuple[str, ...]
    detected_text: str
    matched_reference_text: str
    detected_match_score: float
    supporting_matches: int
    suggested_window_start: float
    suggested_window_end: float
    review_only: bool = True


def detect_opening_lyric_gaps(
    srt_content: str,
    reference_lyrics: str,
    *,
    min_gap_seconds: float = 8.0,
) -> list[OpeningLyricGapCandidate]:
    """Find strong evidence that a song's opening lyrics have no source cues.

    The detector never creates or removes subtitles. It requires a large source
    timeline gap followed by multiple monotonic lyric matches, and ignores a
    section once any of its opening lines were already found earlier.
    """
    cues = parse_srt_cues(srt_content)
    if len(cues) < 1:
        return []

    candidates: list[OpeningLyricGapCandidate] = []
    for section in parse_lyrics_sections(reference_lyrics):
        candidate = _candidate_for_section(cues, section, min_gap_seconds=min_gap_seconds)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def write_opening_lyric_gap_report(
    srt_path: str | Path,
    reference_lyrics: str,
) -> dict[str, object]:
    source = Path(srt_path)
    candidates = detect_opening_lyric_gaps(
        source.read_text(encoding="utf-8", errors="replace"),
        reference_lyrics,
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "policy": GAP_POLICY,
        "source_srt": str(source.resolve()),
        "subtitle_text_changed": False,
        "candidate_count": len(candidates),
        "candidates": [asdict(candidate) for candidate in candidates],
    }
    output_path = opening_lyric_gap_report_path(srt_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(output_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return report


def opening_lyric_gap_report_path(srt_path: str | Path) -> Path:
    path = Path(srt_path)
    return path.with_name(f"{path.stem}_歌词缺轴候选.json")


def parse_srt_cues(content: str) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    for block in re.split(r"\r?\n\s*\r?\n", str(content or "").strip()):
        match = _SRT_BLOCK_RE.match(block)
        if not match:
            continue
        text = " ".join(line.strip() for line in match.group(4).splitlines() if line.strip())
        cues.append(
            SubtitleCue(
                index=int(match.group(1)),
                start=_parse_timestamp(match.group(2)),
                end=_parse_timestamp(match.group(3)),
                text=text,
            )
        )
    return cues


def _candidate_for_section(
    cues: list[SubtitleCue],
    section: LyricsSection,
    *,
    min_gap_seconds: float,
) -> OpeningLyricGapCandidate | None:
    lyric_lines = list(section.lyric_lines)
    if len(lyric_lines) < 4:
        return None

    matches = _best_matches(cues, lyric_lines)
    for position, reference_index, score in matches:
        if (
            reference_index < 2
            or reference_index >= MAX_OPENING_REFERENCE_INDEX
            or score < 0.66
        ):
            continue
        if any(
            earlier_position < position and earlier_reference < reference_index
            for earlier_position, earlier_reference, earlier_score in matches
            if earlier_score >= 0.66
        ):
            continue
        if _opening_prefix_already_present(cues, position, lyric_lines, reference_index):
            continue
        gap_bounds = _largest_recent_gap(cues, position, min_gap_seconds)
        if gap_bounds is None:
            continue
        gap_start, gap_end = gap_bounds
        gap = gap_end - gap_start
        supporting = _supporting_monotonic_matches(matches, position, reference_index)
        if supporting < 2:
            continue
        missing = tuple(lyric_lines[: min(reference_index, 8)])
        return OpeningLyricGapCandidate(
            section_title=section.title,
            cue_index=cues[position].index,
            previous_cue_end=round(gap_start, 3),
            detected_cue_start=round(cues[position].start, 3),
            gap_start=round(gap_start, 3),
            gap_end=round(gap_end, 3),
            gap_seconds=round(gap, 3),
            detected_reference_index=reference_index,
            missing_lines=missing,
            detected_text=cues[position].text,
            matched_reference_text=lyric_lines[reference_index],
            detected_match_score=round(score, 4),
            supporting_matches=supporting,
            suggested_window_start=round(max(0.0, round((gap_start - 8.0) / 5.0) * 5.0), 3),
            suggested_window_end=round(round((gap_end + 15.0) / 5.0) * 5.0, 3),
        )
    return None


def _largest_recent_gap(
    cues: list[SubtitleCue],
    position: int,
    min_gap_seconds: float,
) -> tuple[float, float] | None:
    largest: tuple[float, float] | None = None
    largest_duration = 0.0
    if position == 0:
        opening_gap = cues[0].start
        if opening_gap >= min_gap_seconds:
            return 0.0, cues[0].start
        return None
    if position <= 5 and cues[0].start >= min_gap_seconds:
        largest = (0.0, cues[0].start)
        largest_duration = cues[0].start
    for current in range(max(1, position - 5), position + 1):
        gap_start = cues[current - 1].end
        gap_end = cues[current].start
        duration = gap_end - gap_start
        if duration >= min_gap_seconds and duration > largest_duration:
            largest = (gap_start, gap_end)
            largest_duration = duration
    return largest


def _opening_prefix_already_present(
    cues: list[SubtitleCue],
    position: int,
    lyric_lines: list[str],
    reference_index: int,
) -> bool:
    if position <= 0 or reference_index < 2:
        return False
    observed = normalize_lyric_text("".join(cue.text for cue in cues[max(0, position - 6) : position]))
    expected = normalize_lyric_text("".join(lyric_lines[:reference_index]))
    if min(len(observed), len(expected)) < 12:
        return False
    match = difflib.SequenceMatcher(None, observed, expected, autojunk=False)
    return match.ratio() >= 0.68 and match.find_longest_match().size >= 12


def _best_matches(cues: list[SubtitleCue], lyric_lines: list[str]) -> list[tuple[int, int, float]]:
    """Find one coherent local lyric run instead of independent best matches.

    Independent matching prefers the first occurrence of a repeated line. A
    contiguous run lets an exact later refrain beat a near match in the song's
    opening, so a mid-song clip is not reported as a missing opening. Two
    consecutive lines remain enough for the existing strict repair-preview
    workflow; longer runs win whenever they are available.
    """
    best_run: list[tuple[int, int, float]] = []
    best_key: tuple[int, float, float, int] | None = None
    for start_position in range(len(cues)):
        for start_reference in range(len(lyric_lines)):
            run: list[tuple[int, int, float]] = []
            for offset in range(min(8, len(cues) - start_position, len(lyric_lines) - start_reference)):
                score = lyric_text_similarity(
                    cues[start_position + offset].text,
                    lyric_lines[start_reference + offset],
                )
                if score < 0.58:
                    break
                run.append((start_position + offset, start_reference + offset, score))
            if len(run) < 2:
                continue
            average = sum(item[2] for item in run) / len(run)
            minimum = min(item[2] for item in run)
            key = (len(run), average, minimum, -start_reference)
            if best_key is None or key > best_key:
                best_key = key
                best_run = run
    return best_run


def _supporting_monotonic_matches(
    matches: list[tuple[int, int, float]],
    cue_position: int,
    reference_index: int,
) -> int:
    support = 1
    last_reference = reference_index
    for position, candidate_reference, score in matches:
        if position <= cue_position or position > cue_position + 8 or score < 0.66:
            continue
        if candidate_reference <= last_reference or candidate_reference > last_reference + 5:
            continue
        support += 1
        last_reference = candidate_reference
        if support >= 3:
            break
    return support


def lyric_text_similarity(left: str, right: str) -> float:
    left = normalize_lyric_text(left)
    right = normalize_lyric_text(right)
    if min(len(left), len(right)) < 4:
        return 0.0
    if left in right or right in left:
        return min(len(left), len(right)) / max(len(left), len(right))
    match = difflib.SequenceMatcher(None, left, right, autojunk=False)
    longest = match.find_longest_match().size
    if longest < 4:
        return 0.0
    return match.ratio()


def normalize_lyric_text(value: str) -> str:
    return "".join(char.casefold() for char in str(value or "") if char.isalnum())


def _parse_timestamp(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, milliseconds = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000
