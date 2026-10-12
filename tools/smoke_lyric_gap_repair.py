from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from core.lyric_gap_detection import detect_opening_lyric_gaps, parse_srt_cues  # noqa: E402
from core.lyric_gap_repair import (  # noqa: E402
    apply_high_confidence_lyric_gap_repair,
    build_lyric_gap_repair_preview,
    write_high_confidence_lyric_gap_repair_preview,
)


def main() -> int:
    pipeline_source = (NEXT_ROOT / "engine" / "pipeline_worker.py").read_text(encoding="utf-8")
    _require(
        "write_high_confidence_lyric_gap_repair_preview(" in pipeline_source,
        "production pipeline does not create strict lyric previews",
    )
    _require(
        "apply_high_confidence_lyric_gap_repair(" not in pipeline_source,
        "production pipeline still mutates source subtitles during lyric repair",
    )
    lyrics = """Test Song - Artist
词：A
最初の歌詞です
二番目の歌詞です
三番目の歌詞です
四番目の歌詞です
"""
    source = _srt(
        (1, 0.0, 4.0, "それでは歌います"),
        (2, 4.0, 7.0, "準備してください"),
        (3, 26.0, 29.0, "三番目の歌詞です"),
        (4, 29.0, 32.0, "四番目の歌詞です"),
    )
    candidate = detect_opening_lyric_gaps(source, lyrics)[0]
    rescan = _srt(
        (1, 0.0, 3.0, "最初の歌詞です"),
        (2, 3.0, 6.0, "二番目の歌詞です"),
        (3, 18.0, 21.0, "三番目の歌詞です"),
    )
    preview = build_lyric_gap_repair_preview(
        source,
        rescan,
        asdict_candidate(candidate),
        rescan_offset_seconds=8.0,
    )
    _require(len(preview.insertions) == 2, "missing lyric cues were not inserted")
    _require(preview.source_text_changed is False, "preview claimed to mutate source")
    _require(preview.source_cue_count == 4 and preview.preview_cue_count == 6, "cue counts are wrong")
    cues = parse_srt_cues(preview.content)
    _require([cue.text for cue in cues[2:4]] == ["最初の歌詞です", "二番目の歌詞です"], "inserted order changed")
    _require(all(cues[index].start >= cues[index - 1].end for index in range(1, len(cues))), "preview overlaps")
    _require(source == _srt(
        (1, 0.0, 4.0, "それでは歌います"),
        (2, 4.0, 7.0, "準備してください"),
        (3, 26.0, 29.0, "三番目の歌詞です"),
        (4, 29.0, 32.0, "四番目の歌詞です"),
    ), "source fixture changed")

    overlapping = _srt((1, 16.0, 20.0, "最初の歌詞です"), (2, 20.0, 27.0, "二番目の歌詞です"))
    rejected = build_lyric_gap_repair_preview(
        source,
        overlapping,
        asdict_candidate(candidate),
        rescan_offset_seconds=0.0,
    )
    _require(not rejected.insertions, "overlapping rescan cues were inserted")
    _require(rejected.content == source, "rejected preview changed source")

    strict_source = _srt(
        (1, 0.0, 4.0, "それでは歌います"),
        (2, 30.0, 33.0, "四番目の歌詞です"),
        (3, 33.0, 36.0, "五番目の歌詞です"),
    )
    strict_rescan = _srt(
        (1, 10.0, 12.0, "最初の歌詞です"),
        (2, 12.0, 14.0, "二番目の歌詞です"),
        (3, 14.0, 16.0, "三番目の歌詞です"),
    )
    strict_candidate = {
        "gap_start": 4.0,
        "gap_end": 30.0,
        "gap_seconds": 26.0,
        "supporting_matches": 3,
        "missing_lines": ["最初の歌詞です", "二番目の歌詞です", "三番目の歌詞です"],
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        source_path = temp_root / "source.srt"
        rescan_path = temp_root / "rescan.srt"
        source_path.write_text(strict_source, encoding="utf-8")
        rescan_path.write_text(strict_rescan, encoding="utf-8")
        applied = apply_high_confidence_lyric_gap_repair(
            source_path,
            rescan_path,
            strict_candidate,
        )
        _require(applied.applied and applied.inserted_count == 3, "strong repair was not applied")
        _require(not applied.needs_review, "complete repair was marked for review")
        _require(Path(applied.backup_path).read_text(encoding="utf-8") == strict_source, "backup changed")
        repaired_cues = parse_srt_cues(source_path.read_text(encoding="utf-8"))
        _require(len(repaired_cues) == 6, "repaired source cue count is wrong")
        _require(repaired_cues[0].text == "それでは歌います", "existing source cue changed")
        _require(repaired_cues[-2].text == "四番目の歌詞です", "later source cue moved or changed")
        evidence = json.loads(Path(applied.evidence_path).read_text(encoding="utf-8"))
        _require(evidence["existing_source_cues_changed"] is False, "evidence claims source mutation")
        _require(evidence["needs_review"] is False, "complete evidence was marked for review")

        preview_source_path = temp_root / "preview-source.srt"
        preview_output_path = temp_root / "strict-preview.srt"
        preview_source_path.write_text(strict_source, encoding="utf-8")
        strict_preview = write_high_confidence_lyric_gap_repair_preview(
            preview_source_path,
            rescan_path,
            strict_candidate,
            preview_output_path,
        )
        _require(strict_preview.written and strict_preview.inserted_count == 3, "strict preview was not written")
        _require(preview_source_path.read_text(encoding="utf-8") == strict_source, "preview changed source SRT")
        preview_evidence = json.loads(Path(strict_preview.evidence_path).read_text(encoding="utf-8"))
        _require(preview_evidence["production_subtitle_changed"] is False, "preview evidence claims mutation")
        _require(
            preview_evidence["source_sha256_before"] == preview_evidence["source_sha256_after"],
            "preview source hash changed",
        )

        partial_path = temp_root / "partial.srt"
        partial_path.write_text(strict_source, encoding="utf-8")
        partial_candidate = {
            **strict_candidate,
            "missing_lines": [*strict_candidate["missing_lines"], "まだ見つからない歌詞です"],
        }
        partial = apply_high_confidence_lyric_gap_repair(partial_path, rescan_path, partial_candidate)
        _require(partial.applied and partial.inserted_count == 3, "strong partial repair was not applied")
        _require(partial.needs_review, "partial repair was reported as complete")
        partial_evidence = json.loads(Path(partial.evidence_path).read_text(encoding="utf-8"))
        _require(partial_evidence["needs_review"] is True, "partial evidence was reported as complete")

        rejected_path = temp_root / "rejected.srt"
        rejected_path.write_text(strict_source, encoding="utf-8")
        weak_candidate = {**strict_candidate, "supporting_matches": 2}
        weak = apply_high_confidence_lyric_gap_repair(rejected_path, rescan_path, weak_candidate)
        _require(not weak.applied, "weak repair was applied")
        _require(rejected_path.read_text(encoding="utf-8") == strict_source, "weak repair changed source")

    print("PASS: lyric gap repair only applies strong consecutive non-overlapping matches")
    return 0


def asdict_candidate(candidate) -> dict[str, object]:
    return {
        "gap_start": candidate.gap_start,
        "gap_end": candidate.gap_end,
        "missing_lines": list(candidate.missing_lines),
    }


def _srt(*rows: tuple[int, float, float, str]) -> str:
    return "\n\n".join(
        f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}"
        for index, start, end, text in rows
    ) + "\n"


def _timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
