from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.asr_diagnostics import read_srt_segments, write_asr_diagnostics


OPENING_LYRICS_CLEANUP_POLICY = "lyrics-anchored-edge-cleanup-v5"
_METADATA_RE = re.compile(
    r"^(?:词|詞|曲|作词|作詞|作曲|编曲|編曲|制作人|歌手|artist|lyrics|music|producer)\s*[:：]",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"\s[-–—]\s")
_CLOSING_RE = re.compile(r"(?:ご視聴ありがとうございました|チャンネル登録|大会の結果をお知らせ)", re.IGNORECASE)
_CREDIT_RE = re.compile(r"(?:作詞|作曲|編曲).{0,80}(?:作詞|作曲|編曲)|(?:作詞|作曲|編曲).{0,80}初音", re.I)
_CREDIT_GROUP_RE = re.compile(r"^(?:(?:作詞|作曲|編曲|イラスト|動画|映像|字幕|制作)\s*(?:[:：・/]\s*|\s+)).+", re.I)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}年?$")
_FAKE_LYRIC_TEMPLATE_RE = re.compile(
    r"(?:歌詞.{0,30}(?:歌わない|素晴らしい|大好き)|(?:歌わない|素晴らしい|大好き).{0,30}歌詞)",
    re.I,
)
_EXACT_OPENING_HALLUCINATION_RE = re.compile(r"^(?:音楽の)?歌詞です[。.!！]?$", re.I)


@dataclass(frozen=True)
class OpeningCleanupResult:
    changed: bool
    anchor_text: str = ""
    anchor_start: float = 0.0
    removed_count: int = 0
    reason: str = ""


def apply_lyric_anchored_opening_cleanup(
    srt_path: str | Path,
    reference_lyrics: str,
    diagnostic_report: dict[str, Any],
) -> OpeningCleanupResult:
    """Remove only proven pre-song hallucination patterns when lyrics provide an anchor.

    This deliberately leaves ordinary pre-song speech alone. It requires complete
    Faster-Whisper evidence, a strong lyric anchor, and a narrowly defined
    hallucination pattern before touching the SRT.
    """
    if not str(reference_lyrics or "").strip():
        return OpeningCleanupResult(False, reason="未提供歌词，不执行开场清理")
    if diagnostic_report.get("evidence_source") != "faster-whisper":
        return OpeningCleanupResult(False, reason="没有原始识别置信度，不执行开场清理")

    source_rows = [dict(row) for row in diagnostic_report.get("segments", []) if isinstance(row, dict)]
    if not source_rows:
        return OpeningCleanupResult(False, reason="没有可用于核对的识别记录")
    anchor = _find_first_lyric_anchor(source_rows, reference_lyrics)
    if anchor is None:
        return OpeningCleanupResult(False, reason="没有找到可信的首句歌词锚点")

    last_anchor = _find_last_lyric_anchor(source_rows, reference_lyrics) or anchor
    candidates = _select_hallucination_candidates(source_rows, float(anchor["start"]))
    candidates.extend(_select_closing_hallucination_candidates(source_rows, float(last_anchor["end"])))
    candidates = list({int(row["index"]): row for row in candidates}.values())
    candidates.sort(key=lambda row: int(row["index"]))
    if not candidates:
        return OpeningCleanupResult(
            False,
            anchor_text=str(anchor["text"]),
            anchor_start=float(anchor["start"]),
            reason="歌词首尾没有命中明确误识别模式",
        )

    output_rows = read_srt_segments(srt_path)
    candidate_ranges = [(float(row["start"]), float(row["end"])) for row in candidates]
    retained_output = [row for row in output_rows if not _inside_any_range(row, candidate_ranges)]
    if len(retained_output) == len(output_rows):
        return OpeningCleanupResult(
            False,
            anchor_text=str(anchor["text"]),
            anchor_start=float(anchor["start"]),
            reason="候选记录未对应到实际字幕，不修改输出",
        )

    _write_srt(Path(srt_path), retained_output)
    candidate_indexes = {int(row["index"]) for row in candidates}
    retained_source = [row for row in source_rows if int(row["index"]) not in candidate_indexes]
    suppressed = [
        {
            **row,
            "suppression_reason": str(row.get("opening_cleanup_reason") or "歌词锚点前的明确误识别"),
        }
        for row in candidates
    ]
    write_asr_diagnostics(
        srt_path,
        retained_source,
        evidence_source="faster-whisper",
        suppressed_segments=suppressed,
        extra_metadata={
            "opening_cleanup": {
                "policy": OPENING_LYRICS_CLEANUP_POLICY,
                "anchor_text": str(anchor["text"]),
                "anchor_start": float(anchor["start"]),
                "removed_count": len(candidates),
            }
        },
    )
    return OpeningCleanupResult(
        True,
        anchor_text=str(anchor["text"]),
        anchor_start=float(anchor["start"]),
        removed_count=len(candidates),
        reason="已按歌词锚点清理开唱前的明确误识别",
    )


def _find_first_lyric_anchor(rows: list[dict[str, Any]], reference_lyrics: str) -> dict[str, Any] | None:
    lyric_lines = _lyric_lines(reference_lyrics)
    for row in rows:
        cue = _normalize(str(row.get("text") or ""))
        if len(cue) < 8:
            continue
        for lyric in lyric_lines:
            if lyric in cue or cue in lyric:
                return row
            match = difflib.SequenceMatcher(None, cue, lyric, autojunk=False).find_longest_match()
            ratio = difflib.SequenceMatcher(None, cue, lyric, autojunk=False).ratio()
            if match.size >= 10 and ratio >= 0.90:
                return row
    return None


def _find_last_lyric_anchor(rows: list[dict[str, Any]], reference_lyrics: str) -> dict[str, Any] | None:
    lyric_lines = _lyric_lines(reference_lyrics)
    for row in reversed(rows):
        cue = _normalize(str(row.get("text") or ""))
        if len(cue) < 8:
            continue
        for lyric in reversed(lyric_lines):
            if lyric in cue or cue in lyric:
                return row
            match = difflib.SequenceMatcher(None, cue, lyric, autojunk=False).find_longest_match()
            ratio = difflib.SequenceMatcher(None, cue, lyric, autojunk=False).ratio()
            if match.size >= 10 and ratio >= 0.90:
                return row
    return None


def _lyric_lines(reference_lyrics: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(reference_lyrics).splitlines():
        line = raw_line.strip()
        if not line or _METADATA_RE.match(line) or _TITLE_RE.search(line):
            continue
        normalized = _normalize(line)
        if len(normalized) >= 8:
            lines.append(normalized)
    return lines


def _select_hallucination_candidates(rows: list[dict[str, Any]], anchor_start: float) -> list[dict[str, Any]]:
    before_anchor = [row for row in rows if float(row.get("start") or 0.0) < anchor_start]
    selected: dict[int, str] = {}
    for row in before_anchor:
        no_speech = _number(row.get("no_speech_prob"))
        text = str(row.get("text") or "").strip()
        if no_speech is not None and no_speech >= 0.70 and _EXACT_OPENING_HALLUCINATION_RE.fullmatch(text):
            selected[int(row["index"])] = "歌词锚点前已确认的高无声概率固定句幻觉"
        if no_speech is not None and no_speech >= 0.70 and _CLOSING_RE.search(text):
            selected[int(row["index"])] = "歌词锚点前的无声片尾/播报式幻觉"
        if no_speech is not None and no_speech >= 0.50 and _looks_like_credit_hallucination(text):
            selected[int(row["index"])] = "歌词锚点前的低人声制作名单式幻觉"

    # A proven false credit often arrives as a whole screen-credit sequence.
    # Expand only through adjacent credit-shaped cues after one member already
    # has low-speech evidence; ordinary speech never starts such a group.
    evidence_indexes = set(selected)
    for position, row in enumerate(before_anchor):
        if int(row["index"]) not in evidence_indexes:
            continue
        if not _CREDIT_GROUP_RE.match(str(row.get("text") or "").strip()):
            continue
        left = position
        right = position
        while left > 0 and _credit_group_neighbors(before_anchor[left - 1], before_anchor[left]):
            left -= 1
        while right + 1 < len(before_anchor) and _credit_group_neighbors(before_anchor[right], before_anchor[right + 1]):
            right += 1
        for neighbor in before_anchor[left : right + 1]:
            selected[int(neighbor["index"])] = "与低人声制作名单相邻的连续名单式幻觉"

    year_rows = [
        row
        for row in before_anchor
        if _number(row.get("no_speech_prob")) is not None
        and _number(row.get("no_speech_prob")) >= 0.70
        and _YEAR_RE.fullmatch(_normalize(str(row.get("text") or "")))
    ]
    if len(year_rows) >= 3:
        first_year = float(year_rows[0]["start"])
        last_year = float(year_rows[-1]["end"])
        for row in before_anchor:
            no_speech = _number(row.get("no_speech_prob"))
            start = float(row.get("start") or 0.0)
            end = float(row.get("end") or 0.0)
            if no_speech is not None and no_speech >= 0.70 and start >= first_year - 4.1 and end <= last_year + 0.1:
                selected[int(row["index"])] = "歌词锚点前的连续年份循环幻觉"

    for cluster in _low_speech_clusters(before_anchor):
        template_hits = sum(
            1 for row in cluster if _FAKE_LYRIC_TEMPLATE_RE.search(str(row.get("text") or ""))
        )
        cluster_end = float(cluster[-1].get("end") or 0.0)
        if len(cluster) < 3 or template_hits < 2 or anchor_start - cluster_end > 90.0:
            continue
        for row in cluster:
            selected[int(row["index"])] = "歌词锚点前连续低人声的伪歌词模板幻觉"

    result: list[dict[str, Any]] = []
    for row in before_anchor:
        reason = selected.get(int(row["index"]))
        if reason:
            result.append({**row, "opening_cleanup_reason": reason})
    return result


def _select_closing_hallucination_candidates(
    rows: list[dict[str, Any]],
    last_anchor_end: float,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if float(row.get("start") or 0.0) < last_anchor_end:
            continue
        no_speech = _number(row.get("no_speech_prob"))
        text = str(row.get("text") or "")
        if no_speech is None or no_speech < 0.70 or not _CLOSING_RE.search(text):
            continue
        result.append({**row, "opening_cleanup_reason": "末句歌词后的高无声概率片尾播报式幻觉"})
    return result


def _low_speech_clusters(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in rows:
        no_speech = _number(row.get("no_speech_prob"))
        if no_speech is None or no_speech < 0.60:
            if current:
                clusters.append(current)
                current = []
            continue
        if current and float(row.get("start") or 0.0) - float(current[-1].get("end") or 0.0) > 4.1:
            clusters.append(current)
            current = []
        current.append(row)
    if current:
        clusters.append(current)
    return clusters


def _credit_group_neighbors(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_text = str(left.get("text") or "").strip()
    right_text = str(right.get("text") or "").strip()
    if not _CREDIT_GROUP_RE.match(left_text) or not _CREDIT_GROUP_RE.match(right_text):
        return False
    return float(right.get("start") or 0.0) - float(left.get("end") or 0.0) <= 6.0


def _looks_like_credit_hallucination(text: str) -> bool:
    stripped = str(text or "").strip()
    if _CREDIT_GROUP_RE.match(stripped):
        return True
    return "初音" in stripped and bool(_CREDIT_RE.search(stripped))


def _inside_any_range(row: dict[str, Any], ranges: list[tuple[float, float]]) -> bool:
    start = float(row.get("start") or 0.0)
    end = float(row.get("end") or 0.0)
    return any(start >= lower - 0.001 and end <= upper + 0.001 for lower, upper in ranges)


def _write_srt(path: Path, rows: list[dict[str, Any]]) -> None:
    parts: list[str] = []
    for index, row in enumerate(rows, start=1):
        parts.extend(
            [
                str(index),
                f"{_format_srt_time(float(row['start']))} --> {_format_srt_time(float(row['end']))}",
                str(row["text"]),
                "",
            ]
        )
    path.write_text("\n".join(parts), encoding="utf-8")


def _format_srt_time(value: float) -> str:
    milliseconds = int(round(value * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if char.isalnum() or "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff")


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
