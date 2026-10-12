from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.asr_diagnostics import write_asr_diagnostics


WATERMARK_HALLUCINATION_CLEANUP_POLICY = "watermark-cluster-cleanup-v1"
_SOUND_HODORI_RE = re.compile(r"sound\s*hodori", re.IGNORECASE)
_SOCIAL_RE = re.compile(r"instagram.*twitter|twitter.*instagram", re.IGNORECASE | re.DOTALL)
_SOCIAL_FOLLOW_RE = re.compile(r"(?:フォロワー|フォロー|follow|スター[トト]?ル)", re.IGNORECASE)
_BROKEN_URL_RE = re.compile(r"(?:https?://|www\.).*(?:instagram).*?(?:twitter)|(?:twitter).*?(?:instagram)", re.IGNORECASE)
_SRT_BLOCK_RE = re.compile(
    r"^\s*(\d+)\s*\r?\n"
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\r?\n"
    r"([\s\S]*?)\s*$"
)


@dataclass(frozen=True)
class WatermarkCleanupResult:
    changed: bool
    removed_count: int = 0
    reason: str = ""


def apply_watermark_hallucination_cleanup(
    srt_path: str | Path,
    diagnostic_report: dict[str, Any] | None = None,
) -> WatermarkCleanupResult:
    """Remove only a narrow family of known Whisper watermark hallucinations."""
    rows = _read_srt_blocks(srt_path)
    candidates = _select_candidates(rows)
    if not candidates:
        return WatermarkCleanupResult(False, reason="未命中已知水印式误识别")

    candidate_indexes = {int(row["index"]) for row in candidates}
    retained = [row for row in rows if int(row["index"]) not in candidate_indexes]
    _write_srt(Path(srt_path), retained)

    report = diagnostic_report or {}
    evidence_source = str(report.get("evidence_source") or "cached-srt-text-only")
    retained_evidence, suppressed_evidence = _retain_diagnostic_evidence(report, candidates, retained)
    previous_suppressed = report.get("suppressed_segments")
    previous_suppressed = previous_suppressed if isinstance(previous_suppressed, list) else []
    metadata: dict[str, Any] = {
        "watermark_cleanup": {
            "policy": WATERMARK_HALLUCINATION_CLEANUP_POLICY,
            "removed_count": len(candidates),
        }
    }
    if isinstance(report.get("opening_cleanup"), dict):
        metadata["opening_cleanup"] = report["opening_cleanup"]
    write_asr_diagnostics(
        srt_path,
        retained_evidence,
        evidence_source=evidence_source,
        suppressed_segments=[
            *previous_suppressed,
            *suppressed_evidence,
        ],
        extra_metadata=metadata,
    )
    return WatermarkCleanupResult(
        True,
        removed_count=len(candidates),
        reason="已移除已知 Sound Hodori 与异常社交账号水印式误识别",
    )


def _retain_diagnostic_evidence(
    report: dict[str, Any],
    candidates: list[dict[str, Any]],
    retained_srt_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence_rows = report.get("segments")
    if not isinstance(evidence_rows, list) or not evidence_rows:
        return retained_srt_rows, [
            {
                **row,
                "suppression_reason": str(row.get("watermark_cleanup_reason") or "已知水印式误识别"),
            }
            for row in candidates
        ]

    matched_evidence: set[int] = set()
    suppressed: list[dict[str, Any]] = []
    for candidate in candidates:
        match_index = _find_evidence_match(evidence_rows, candidate, matched_evidence)
        source = evidence_rows[match_index] if match_index is not None else candidate
        if match_index is not None:
            matched_evidence.add(match_index)
        suppressed.append(
            {
                **source,
                "suppression_reason": str(candidate.get("watermark_cleanup_reason") or "已知水印式误识别"),
            }
        )

    retained = [dict(row) for index, row in enumerate(evidence_rows) if index not in matched_evidence]
    return retained, suppressed


def _find_evidence_match(
    evidence_rows: list[dict[str, Any]],
    candidate: dict[str, Any],
    used_indexes: set[int],
) -> int | None:
    candidate_start = float(candidate.get("start") or 0.0)
    candidate_end = float(candidate.get("end") or 0.0)
    candidate_text = _normalize_text(str(candidate.get("text") or ""))
    best: tuple[float, int] | None = None
    for index, row in enumerate(evidence_rows):
        if index in used_indexes:
            continue
        start = float(row.get("start") or 0.0)
        end = float(row.get("end") or 0.0)
        if end < candidate_start - 0.05 or start > candidate_end + 0.05:
            continue
        text = _normalize_text(str(row.get("text") or ""))
        if candidate_text and text and candidate_text != text and candidate_text not in text and text not in candidate_text:
            continue
        distance = abs(start - candidate_start) + abs(end - candidate_end)
        if best is None or distance < best[0]:
            best = (distance, index)
    return best[1] if best is not None else None


def _normalize_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _select_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("text") or "")
        if _SOUND_HODORI_RE.search(text):
            selected.append({**row, "watermark_cleanup_reason": "已知 Sound Hodori 水印式误识别"})
            continue
        if _is_malformed_social_pair(text):
            selected.append({**row, "watermark_cleanup_reason": "异常 Instagram/Twitter 水印式误识别"})
    return selected


def _is_malformed_social_pair(text: str) -> bool:
    if not _SOCIAL_RE.search(text):
        return False
    return bool(_SOCIAL_FOLLOW_RE.search(text) or _BROKEN_URL_RE.search(text))


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


def _read_srt_blocks(path: str | Path) -> list[dict[str, Any]]:
    content = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    rows: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\s*\r?\n", content):
        match = _SRT_BLOCK_RE.match(block)
        if not match:
            continue
        rows.append(
            {
                "index": int(match.group(1)),
                "start": _parse_srt_time(match.group(2)),
                "end": _parse_srt_time(match.group(3)),
                # This cleaner can also be used on translated SRT. Keep the
                # language line break intact instead of collapsing it for diagnostics.
                "text": match.group(4).strip(),
            }
        )
    return rows


def _parse_srt_time(value: str) -> float:
    hours, minutes, seconds_and_ms = value.split(":")
    seconds, milliseconds = seconds_and_ms.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000


def _format_srt_time(value: float) -> str:
    milliseconds = int(round(value * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
