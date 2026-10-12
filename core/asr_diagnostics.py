from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
import difflib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DIAGNOSTIC_POLICY = "traceable-filter-v3-adjacent-duplicate-review"
_SRT_BLOCK_RE = re.compile(
    r"^\s*(\d+)\s*\r?\n"
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\r?\n"
    r"([\s\S]*?)\s*$"
)
_METADATA_RE = re.compile(
    r"^\s*(?:(?:作詞|作曲|編曲)(?:\s*[・/／]\s*(?:作詞|作曲|編曲)){1,2}\s+\S{1,30}|"
    r"(?:イラスト|動画|映像|字幕|制作)(?:\s+|[:：・]\s*)\S{1,30})\s*$"
)


def diagnostics_path_for_srt(srt_path: str | Path) -> Path:
    path = Path(srt_path)
    return path.with_name(f"{path.stem}_识别诊断.json")


def segment_to_record(segment: object, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "start": _safe_float(getattr(segment, "start", 0.0), 0.0),
        "end": _safe_float(getattr(segment, "end", 0.0), 0.0),
        "text": str(getattr(segment, "text", "") or "").strip(),
        "avg_logprob": _safe_float(getattr(segment, "avg_logprob", None)),
        "no_speech_prob": _safe_float(getattr(segment, "no_speech_prob", None)),
        "compression_ratio": _safe_float(getattr(segment, "compression_ratio", None)),
    }


def write_asr_diagnostics(
    srt_path: str | Path,
    segments: Iterable[dict[str, Any]],
    *,
    evidence_source: str = "faster-whisper",
    suppressed_segments: Iterable[dict[str, Any]] = (),
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = build_asr_diagnostics(
        segments,
        source_srt=str(Path(srt_path).resolve()),
        evidence_source=evidence_source,
        suppressed_segments=suppressed_segments,
    )
    report["source_srt_sha256"] = _file_sha256(srt_path)
    if extra_metadata:
        report.update(extra_metadata)
    output_path = diagnostics_path_for_srt(srt_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(
        f".{output_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temp_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        temp_path.replace(output_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return report


def ensure_srt_diagnostics(srt_path: str | Path) -> dict[str, Any]:
    output_path = diagnostics_path_for_srt(srt_path)
    source_hash = _file_sha256(srt_path)
    if output_path.exists():
        try:
            report = json.loads(output_path.read_text(encoding="utf-8"))
            if (
                report.get("source_srt_sha256") == source_hash
                and report.get("policy") == DIAGNOSTIC_POLICY
                and report.get("schema_version") == 1
            ):
                return report
        except (OSError, ValueError, TypeError):
            pass
    return write_asr_diagnostics(
        srt_path,
        read_srt_segments(srt_path),
        evidence_source="cached-srt-text-only",
    )


def read_srt_segments(srt_path: str | Path) -> list[dict[str, Any]]:
    content = Path(srt_path).read_text(encoding="utf-8", errors="replace").strip()
    records: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\s*\r?\n", content):
        match = _SRT_BLOCK_RE.match(block)
        if not match:
            continue
        text = " ".join(line.strip() for line in match.group(4).splitlines() if line.strip())
        records.append(
            {
                "index": int(match.group(1)),
                "start": _parse_srt_timestamp(match.group(2)),
                "end": _parse_srt_timestamp(match.group(3)),
                "text": text,
                "avg_logprob": None,
                "no_speech_prob": None,
                "compression_ratio": None,
            }
        )
    return records


def build_asr_diagnostics(
    segments: Iterable[dict[str, Any]],
    *,
    source_srt: str = "",
    evidence_source: str = "faster-whisper",
    suppressed_segments: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    rows = [_clean_record(record, position) for position, record in enumerate(segments, start=1)]
    suppressed_rows = [
        _clean_suppressed_record(record, position)
        for position, record in enumerate(suppressed_segments, start=1)
    ]
    reasons: list[set[str]] = [set() for _ in rows]

    for position, row in enumerate(rows):
        no_speech_prob = row.get("no_speech_prob")
        avg_logprob = row.get("avg_logprob")
        compression_ratio = row.get("compression_ratio")
        if no_speech_prob is not None and no_speech_prob >= 0.60:
            reasons[position].add("模型判断这一段可能没有清晰人声")
        if avg_logprob is not None and avg_logprob <= -1.0:
            reasons[position].add("模型对这段文字把握较低")
        if compression_ratio is not None and compression_ratio >= 2.4:
            reasons[position].add("识别文字的重复度异常")
        if _looks_like_credit_metadata(str(row.get("text") or "")):
            reasons[position].add("内容像制作名单，请确认现场是否真的说过")
        if _looks_like_isolated_single_character(row):
            reasons[position].add("孤立单字，可能是上下文被截断或误识别")
        duration = max(0.0, float(row.get("end") or 0.0) - float(row.get("start") or 0.0))
        if duration > 18.0:
            reasons[position].add(f"字幕持续 {duration:.1f} 秒，可能吞并了后续人声")

    _mark_adjacent_near_duplicates(rows, reasons)
    _mark_repeated_groups(rows, reasons)
    _mark_generic_phrase_groups(rows, reasons)

    candidates: list[dict[str, Any]] = []
    output_rows: list[dict[str, Any]] = []
    for row, row_reasons in zip(rows, reasons):
        output = dict(row)
        output["review_reasons"] = sorted(row_reasons)
        output["review_only"] = True
        output_rows.append(output)
        if row_reasons:
            candidates.append(output)

    return {
        "schema_version": 1,
        "policy": DIAGNOSTIC_POLICY,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_srt": source_srt,
        "evidence_source": evidence_source,
        "subtitle_text_changed": bool(suppressed_rows),
        "summary": {
            "segment_count": len(output_rows),
            "review_candidate_count": len(candidates),
            "automatic_deletion_count": len(suppressed_rows),
        },
        "candidates": candidates,
        "suppressed_segments": suppressed_rows,
        "segments": output_rows,
    }


def print_diagnostic_summary(report: dict[str, Any], srt_path: str | Path) -> None:
    summary = report.get("summary") if isinstance(report, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    candidates = report.get("candidates") if isinstance(report, dict) else []
    candidates = candidates if isinstance(candidates, list) else []
    count = int(summary.get("review_candidate_count") or 0)
    basic_only = report.get("evidence_source") != "faster-whisper"
    output_path = diagnostics_path_for_srt(srt_path)
    if count <= 0:
        if basic_only:
            print("[打轴检查] 基础检查未发现明显文本异常；缓存没有原始识别置信度，仍建议按需复听。")
        else:
            print("[打轴检查] 没有发现明显的连续重复或制作名单式字幕；系统没有自动删除任何内容。")
        return

    if basic_only:
        print(
            f"[打轴检查] 基础检查发现 {count} 条字幕建议人工复听；"
            "缓存没有原始识别置信度，系统没有自动删除任何内容。"
        )
    else:
        print(f"[打轴检查] 发现 {count} 条字幕建议人工复听；系统没有自动删除任何内容。")
    for candidate in candidates[:6]:
        start = _format_seconds(candidate.get("start"))
        text = str(candidate.get("text") or "").strip()
        reason_list = candidate.get("review_reasons") or []
        reason = "；".join(str(item) for item in reason_list)
        print(f"   -> {start}  {text}（{reason}）")
    if count > 6:
        print(f"   -> 其余 {count - 6} 条请查看诊断文件。")
    print(f"[打轴检查] 诊断文件: {output_path}")


def _clean_record(record: dict[str, Any], position: int) -> dict[str, Any]:
    return {
        "index": int(record.get("index") or position),
        "start": _safe_float(record.get("start"), 0.0),
        "end": _safe_float(record.get("end"), 0.0),
        "text": str(record.get("text") or "").strip(),
        "avg_logprob": _safe_float(record.get("avg_logprob")),
        "no_speech_prob": _safe_float(record.get("no_speech_prob")),
        "compression_ratio": _safe_float(record.get("compression_ratio")),
    }


def _clean_suppressed_record(record: dict[str, Any], position: int) -> dict[str, Any]:
    output = _clean_record(record, position)
    output["suppression_reason"] = str(record.get("suppression_reason") or "保守过滤")
    return output


def suppression_reason_for_segment(
    record: dict[str, Any],
    *,
    position: int,
    next_record: dict[str, Any] | None = None,
) -> str:
    """Return a reason only when a false ASR segment meets a strict pattern."""
    text = str(record.get("text") or "").strip()
    no_speech = _safe_float(record.get("no_speech_prob"))
    if text and no_speech is not None and no_speech >= 0.85 and _looks_like_credit_metadata(text):
        return "制作名单式文字且模型判断几乎无人声"

    if position != 1 or next_record is None:
        return ""
    next_text = str(next_record.get("text") or "").strip()
    next_no_speech = _safe_float(next_record.get("no_speech_prob"))
    duration = max(
        0.0,
        (_safe_float(record.get("end"), 0.0) or 0.0)
        - (_safe_float(record.get("start"), 0.0) or 0.0),
    )
    next_duration = max(
        0.0,
        (_safe_float(next_record.get("end"), 0.0) or 0.0)
        - (_safe_float(next_record.get("start"), 0.0) or 0.0),
    )
    gap = abs(
        (_safe_float(next_record.get("start"), 0.0) or 0.0)
        - (_safe_float(record.get("end"), 0.0) or 0.0)
    )
    normalized = _normalize_text(text)
    if (
        normalized
        and normalized == _normalize_text(next_text)
        and duration >= 8.0
        and next_duration <= 4.0
        and gap <= 0.05
        and no_speech is not None
        and next_no_speech is not None
        and no_speech >= 0.75
        and next_no_speech >= 0.75
    ):
        return "开场首条长低人声字幕与下一条完全重复"
    return ""


def _mark_repeated_groups(rows: list[dict[str, Any]], reasons: list[set[str]]) -> None:
    position = 0
    while position < len(rows):
        normalized = _normalize_text(str(rows[position].get("text") or ""))
        end = position + 1
        while end < len(rows):
            if _normalize_text(str(rows[end].get("text") or "")) != normalized:
                break
            if float(rows[end].get("start") or 0.0) - float(rows[end - 1].get("end") or 0.0) > 12.0:
                break
            end += 1
        if normalized and end - position >= 3:
            for index in range(position, end):
                reasons[index].add(f"同一句连续出现 {end - position} 次")
        position = end


def _mark_adjacent_near_duplicates(rows: list[dict[str, Any]], reasons: list[set[str]]) -> None:
    for position, (left, right) in enumerate(zip(rows, rows[1:])):
        left_text = _normalize_text(str(left.get("text") or ""))
        right_text = _normalize_text(str(right.get("text") or ""))
        if min(len(left_text), len(right_text)) < 6:
            continue
        gap = _safe_float(right.get("start"), 0.0) - _safe_float(left.get("end"), 0.0)
        if gap is None or gap > 0.25:
            continue
        similarity = difflib.SequenceMatcher(None, left_text, right_text, autojunk=False).ratio()
        if similarity < 0.88:
            continue
        reason = "与相邻字幕高度相似，可能是重复或幻觉，建议复听"
        reasons[position].add(reason)
        reasons[position + 1].add(reason)


def _mark_generic_phrase_groups(rows: list[dict[str, Any]], reasons: list[set[str]]) -> None:
    position = 0
    while position < len(rows):
        family = _generic_phrase_family(str(rows[position].get("text") or ""))
        if not family:
            position += 1
            continue
        end = position + 1
        while end < len(rows):
            if _generic_phrase_family(str(rows[end].get("text") or "")) != family:
                break
            if float(rows[end].get("start") or 0.0) - float(rows[end - 1].get("end") or 0.0) > 12.0:
                break
            end += 1
        if end - position >= 3:
            for index in range(position, end):
                reasons[index].add("短口头语连续出现，可能是器乐段误识别")
        position = end


def _generic_phrase_family(text: str) -> str:
    normalized = _normalize_text(text)
    if normalized in {"いいですね", "いいですよ", "いいね", "いいよ"}:
        return "いい"
    if normalized in {"そうですね", "そうですよ", "そうだね", "そうだよ"}:
        return "そう"
    return ""


def _looks_like_credit_metadata(text: str) -> bool:
    return bool(_METADATA_RE.fullmatch(text.strip()))


def _looks_like_isolated_single_character(row: dict[str, Any]) -> bool:
    text = _normalize_text(str(row.get("text") or ""))
    if len(text) != 1:
        return False
    char = text[0]
    if not (
        "\u3040" <= char <= "\u30ff"
        or "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
    ):
        return False
    duration = max(
        0.0,
        (_safe_float(row.get("end"), 0.0) or 0.0)
        - (_safe_float(row.get("start"), 0.0) or 0.0),
    )
    return duration <= 1.5


def _normalize_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).casefold()


def _parse_srt_timestamp(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, milliseconds = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000


def _format_seconds(value: object) -> str:
    seconds = max(0.0, _safe_float(value, 0.0) or 0.0)
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, sec = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"


def _safe_float(value: object, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
