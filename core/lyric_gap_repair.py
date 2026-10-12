from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from core.lyric_gap_detection import SubtitleCue, lyric_text_similarity, parse_srt_cues


@dataclass(frozen=True)
class RepairInsertion:
    start: float
    end: float
    text: str
    detected_text: str
    matched_reference_text: str
    match_score: float


@dataclass(frozen=True)
class LyricGapRepairPreview:
    content: str
    insertions: tuple[RepairInsertion, ...]
    source_cue_count: int
    preview_cue_count: int
    source_text_changed: bool = False


@dataclass(frozen=True)
class AppliedLyricGapRepair:
    applied: bool
    needs_review: bool
    inserted_count: int
    missing_reference_count: int
    source_cue_count: int
    final_cue_count: int
    reason: str
    backup_path: str = ""
    evidence_path: str = ""


@dataclass(frozen=True)
class WrittenLyricGapRepairPreview:
    written: bool
    inserted_count: int
    missing_reference_count: int
    source_cue_count: int
    preview_cue_count: int
    reason: str
    preview_path: str = ""
    evidence_path: str = ""


def build_lyric_gap_repair_preview(
    source_srt: str,
    rescan_srt: str,
    candidate: dict[str, object],
    *,
    rescan_offset_seconds: float,
) -> LyricGapRepairPreview:
    """Insert only high-confidence rescan cues inside a proven empty gap.

    This produces a separate preview. Existing source cues are never replaced,
    shifted, shortened, or deleted.
    """
    source_cues = parse_srt_cues(source_srt)
    rescan_cues = parse_srt_cues(rescan_srt)
    gap_start = float(candidate.get("gap_start") or 0.0)
    gap_end = float(candidate.get("gap_end") or 0.0)
    missing_lines = [str(line).strip() for line in candidate.get("missing_lines") or [] if str(line).strip()]
    if not source_cues or not rescan_cues or gap_end <= gap_start or not missing_lines:
        return _unchanged_preview(source_srt, source_cues)

    shifted = [
        SubtitleCue(
            index=cue.index,
            start=cue.start + rescan_offset_seconds,
            end=cue.end + rescan_offset_seconds,
            text=cue.text,
        )
        for cue in rescan_cues
    ]
    usable = [
        cue
        for cue in shifted
        if cue.start >= gap_start - 0.05
        and cue.end <= gap_end + 0.05
        and not _overlaps_any(cue, source_cues)
    ]

    insertions: list[RepairInsertion] = []
    search_from = 0
    last_end = gap_start
    for reference_line in missing_lines:
        best_position = -1
        best_score = 0.0
        for position in range(search_from, len(usable)):
            cue = usable[position]
            if cue.start < last_end - 0.05:
                continue
            score = lyric_text_similarity(cue.text, reference_line)
            if score > best_score:
                best_position = position
                best_score = score
            if score >= 0.92:
                break
        if best_position < 0 or best_score < 0.62:
            continue
        cue = usable[best_position]
        insertions.append(
            RepairInsertion(
                start=round(cue.start, 3),
                end=round(cue.end, 3),
                text=reference_line,
                detected_text=cue.text,
                matched_reference_text=reference_line,
                match_score=round(best_score, 4),
            )
        )
        search_from = best_position + 1
        last_end = cue.end

    if len(insertions) < 2:
        return _unchanged_preview(source_srt, source_cues)

    inserted_cues = [
        SubtitleCue(index=0, start=item.start, end=item.end, text=item.text)
        for item in insertions
    ]
    merged = sorted([*source_cues, *inserted_cues], key=lambda cue: (cue.start, cue.end, cue.index))
    if _has_overlap(merged):
        return _unchanged_preview(source_srt, source_cues)
    content = _render_srt(merged)
    return LyricGapRepairPreview(
        content=content,
        insertions=tuple(insertions),
        source_cue_count=len(source_cues),
        preview_cue_count=len(merged),
    )


def write_lyric_gap_repair_preview(
    source_path: str | Path,
    rescan_path: str | Path,
    candidate_report_path: str | Path,
    output_path: str | Path,
    *,
    rescan_offset_seconds: float,
) -> tuple[Path, Path, LyricGapRepairPreview]:
    source = Path(source_path)
    rescan = Path(rescan_path)
    report = json.loads(Path(candidate_report_path).read_text(encoding="utf-8"))
    candidates = report.get("candidates") if isinstance(report, dict) else []
    candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
    preview = build_lyric_gap_repair_preview(
        source.read_text(encoding="utf-8-sig", errors="replace"),
        rescan.read_text(encoding="utf-8-sig", errors="replace"),
        candidate if isinstance(candidate, dict) else {},
        rescan_offset_seconds=rescan_offset_seconds,
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(preview.content, encoding="utf-8")
    evidence_path = output.with_name(f"{output.stem}_补轴预览证据.json")
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "policy": "lyrics-gap-repair-preview-v1",
                "source_srt": str(source.resolve()),
                "rescan_srt": str(rescan.resolve()),
                "rescan_offset_seconds": rescan_offset_seconds,
                "source_text_changed": preview.source_text_changed,
                "source_cue_count": preview.source_cue_count,
                "preview_cue_count": preview.preview_cue_count,
                "inserted_count": len(preview.insertions),
                "insertions": [asdict(item) for item in preview.insertions],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output, evidence_path, preview


def apply_high_confidence_lyric_gap_repair(
    source_path: str | Path,
    rescan_path: str | Path,
    candidate: dict[str, object],
    *,
    rescan_offset_seconds: float = 0.0,
) -> AppliedLyricGapRepair:
    """Apply a local rescan only when the evidence is strong and non-overlapping."""
    source = Path(source_path)
    rescan = Path(rescan_path)
    source_content = source.read_text(encoding="utf-8-sig", errors="replace")
    preview = build_lyric_gap_repair_preview(
        source_content,
        rescan.read_text(encoding="utf-8-sig", errors="replace"),
        candidate,
        rescan_offset_seconds=rescan_offset_seconds,
    )
    rejection = _automatic_repair_rejection_reason(preview, candidate)
    missing_lines = [str(line).strip() for line in candidate.get("missing_lines") or [] if str(line).strip()]
    if rejection:
        return AppliedLyricGapRepair(
            applied=False,
            needs_review=True,
            inserted_count=0,
            missing_reference_count=len(missing_lines),
            source_cue_count=preview.source_cue_count,
            final_cue_count=preview.source_cue_count,
            reason=rejection,
        )

    source_hash = hashlib.sha256(source_content.encode("utf-8")).hexdigest()
    needs_review = len(preview.insertions) < len(missing_lines)
    backup = source.with_name(f"{source.stem}_局部补轴前_{source_hash[:12]}.srt")
    evidence = source.with_name(f"{source.stem}_局部补轴记录.json")
    if not backup.exists():
        _atomic_write_text(backup, source_content)

    _atomic_write_text(source, preview.content)
    _atomic_write_text(
        evidence,
        json.dumps(
            {
                "schema_version": 1,
                "policy": "lyrics-gap-local-rescan-v1",
                "source_srt": str(source.resolve()),
                "rescan_srt": str(rescan.resolve()),
                "backup_srt": str(backup.resolve()),
                "source_sha256": source_hash,
                "existing_source_cues_changed": False,
                "source_cue_count": preview.source_cue_count,
                "final_cue_count": preview.preview_cue_count,
                "inserted_count": len(preview.insertions),
                "missing_reference_count": len(missing_lines),
                "needs_review": needs_review,
                "candidate": candidate,
                "insertions": [asdict(item) for item in preview.insertions],
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return AppliedLyricGapRepair(
        applied=True,
        needs_review=needs_review,
        inserted_count=len(preview.insertions),
        missing_reference_count=len(missing_lines),
        source_cue_count=preview.source_cue_count,
        final_cue_count=preview.preview_cue_count,
        reason="high-confidence local rescan matched consecutive reference lyrics",
        backup_path=str(backup.resolve()),
        evidence_path=str(evidence.resolve()),
    )


def write_high_confidence_lyric_gap_repair_preview(
    source_path: str | Path,
    rescan_path: str | Path,
    candidate: dict[str, object],
    output_path: str | Path,
    *,
    alignment_evidence_path: str | Path | None = None,
) -> WrittenLyricGapRepairPreview:
    """Write a strict candidate SRT while leaving the production source untouched."""
    source = Path(source_path)
    rescan = Path(rescan_path)
    output = Path(output_path)
    source_content = source.read_text(encoding="utf-8-sig", errors="replace")
    source_hash = hashlib.sha256(source_content.encode("utf-8")).hexdigest()
    preview = build_lyric_gap_repair_preview(
        source_content,
        rescan.read_text(encoding="utf-8-sig", errors="replace"),
        candidate,
        rescan_offset_seconds=0.0,
    )
    missing_lines = [str(line).strip() for line in candidate.get("missing_lines") or [] if str(line).strip()]
    rejection = _automatic_repair_rejection_reason(preview, candidate)
    if not rejection and len(preview.insertions) != len(missing_lines):
        rejection = "strict preview did not recover every expected lyric line"
    if rejection:
        output.unlink(missing_ok=True)
        return WrittenLyricGapRepairPreview(
            written=False,
            inserted_count=0,
            missing_reference_count=len(missing_lines),
            source_cue_count=preview.source_cue_count,
            preview_cue_count=preview.source_cue_count,
            reason=rejection,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = output.with_name(f"{output.stem}_证据.json")
    _atomic_write_text(output, preview.content)
    source_hash_after = hashlib.sha256(
        source.read_text(encoding="utf-8-sig", errors="replace").encode("utf-8")
    ).hexdigest()
    if source_hash_after != source_hash:
        output.unlink(missing_ok=True)
        raise RuntimeError("source subtitle changed while building a lyric repair preview")
    _atomic_write_text(
        evidence,
        json.dumps(
            {
                "schema_version": 1,
                "policy": "lyrics-gap-word-alignment-preview-v1",
                "source_srt": str(source.resolve()),
                "preview_srt": str(output.resolve()),
                "alignment_evidence": (
                    str(Path(alignment_evidence_path).resolve()) if alignment_evidence_path else ""
                ),
                "source_sha256_before": source_hash,
                "source_sha256_after": source_hash_after,
                "production_subtitle_changed": False,
                "source_cue_count": preview.source_cue_count,
                "preview_cue_count": preview.preview_cue_count,
                "inserted_count": len(preview.insertions),
                "missing_reference_count": len(missing_lines),
                "candidate": candidate,
                "insertions": [asdict(item) for item in preview.insertions],
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return WrittenLyricGapRepairPreview(
        written=True,
        inserted_count=len(preview.insertions),
        missing_reference_count=len(missing_lines),
        source_cue_count=preview.source_cue_count,
        preview_cue_count=preview.preview_cue_count,
        reason="strict word-timed lyric alignment preview generated",
        preview_path=str(output.resolve()),
        evidence_path=str(evidence.resolve()),
    )


def automatic_lyric_gap_rescan_rejection_reason(candidate: dict[str, object]) -> str:
    gap_seconds = float(candidate.get("gap_seconds") or 0.0)
    supporting_matches = int(candidate.get("supporting_matches") or 0)
    missing_lines = [str(line).strip() for line in candidate.get("missing_lines") or [] if str(line).strip()]
    if gap_seconds < 20.0 or gap_seconds > 120.0:
        return "gap duration is outside the conservative automatic range"
    if supporting_matches < 3:
        return "not enough later lyric anchors confirmed the gap"
    if len(missing_lines) < 3:
        return "not enough missing reference lines for a safe local rescan"
    return ""


def _unchanged_preview(source_srt: str, source_cues: list[SubtitleCue]) -> LyricGapRepairPreview:
    return LyricGapRepairPreview(
        content=source_srt,
        insertions=(),
        source_cue_count=len(source_cues),
        preview_cue_count=len(source_cues),
    )


def _automatic_repair_rejection_reason(
    preview: LyricGapRepairPreview,
    candidate: dict[str, object],
) -> str:
    candidate_rejection = automatic_lyric_gap_rescan_rejection_reason(candidate)
    if candidate_rejection:
        return candidate_rejection
    if len(preview.insertions) < 3:
        return "local rescan did not recover at least three lyric lines"

    scores = [item.match_score for item in preview.insertions]
    if min(scores) < 0.70 or sum(scores) / len(scores) < 0.82:
        return "local rescan lyric matches are not confident enough"

    missing_lines = [str(line) for line in candidate.get("missing_lines") or []]
    matched_positions: list[int] = []
    search_from = 0
    for insertion in preview.insertions:
        try:
            position = missing_lines.index(insertion.matched_reference_text, search_from)
        except ValueError:
            return "recovered lyrics are not in the expected reference order"
        matched_positions.append(position)
        search_from = position + 1
    if any(current != previous + 1 for previous, current in zip(matched_positions, matched_positions[1:])):
        return "recovered lyrics are not consecutive"
    return ""


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _overlaps_any(candidate: SubtitleCue, cues: list[SubtitleCue]) -> bool:
    return any(candidate.start < cue.end - 0.01 and candidate.end > cue.start + 0.01 for cue in cues)


def _has_overlap(cues: list[SubtitleCue]) -> bool:
    return any(cues[position].start < cues[position - 1].end - 0.01 for position in range(1, len(cues)))


def _render_srt(cues: list[SubtitleCue]) -> str:
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{cue.text}"
        )
    return "\n\n".join(blocks) + "\n"


def _timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"
