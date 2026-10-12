from __future__ import annotations

import sys
import tempfile
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from core.lyric_gap_detection import (  # noqa: E402
    detect_opening_lyric_gaps,
    opening_lyric_gap_report_path,
    write_opening_lyric_gap_report,
)
from ui.log_formatter import format_run_summary  # noqa: E402


LYRICS = """色彩 - yama
词：くじら
曲：くじら
じっと機微を見逃さないで
きっと大人はそれらしくするので
かかりきりになるんだ
関心はまだ僕ら
真珠の欠片だって無価値
そこに物語がなければ
からくりのなかで廻る僕ら命だ

愛を解く - yama
词：yama
曲：yama
手放したっていいさ
傷を付け合う運命
ぞんざいな日々は
手離したっていいさ
軽薄な言葉も紛れもなく愛だろ
"""

REPEATED_LYRICS = """色彩 - yama
词：くじら
曲：くじら
じっと機微を見逃さないで
きっと大人はそれらしくするので
かかりきりになるんだ
関心はまだ僕ら
真珠の欠片だって無価値
そこに物語がなければ
からくりのなかで廻る僕ら命だ
じっと日々を見逃さないで
きっと大人はそれらしくするので
かかりきりになるんだ
関心はまだ僕ら
真珠のかけらだって無価値
そこに物語がなければ
からくりの中で廻る僕ら命だ
"""


def main() -> int:
    missing_opening = _srt(
        (1, 0.0, 4.0, "1曲目やりますか"),
        (2, 4.0, 7.0, "これ置いてください"),
        (3, 26.0, 29.0, "かかりきりになるんだ"),
        (4, 29.0, 32.0, "関心はまだ僕ら"),
        (5, 32.0, 35.0, "真珠のかけらだって無価値"),
    )
    candidates = detect_opening_lyric_gaps(missing_opening, LYRICS)
    _require(len(candidates) == 1, "strong opening gap was not detected")
    _require(candidates[0].section_title.startswith("色彩"), "wrong lyrics section selected")
    _require(candidates[0].missing_lines[:2] == ("じっと機微を見逃さないで", "きっと大人はそれらしくするので"), "missing lines changed")
    _require(candidates[0].suggested_window_start == 0.0, "repair window did not include enough opening context")

    first_cue_after_missing_opening = _srt(
        (1, 30.0, 33.0, "かかりきりになるんだ"),
        (2, 33.0, 36.0, "関心はまだ僕ら"),
        (3, 36.0, 39.0, "真珠のかけらだって無価値"),
    )
    first_cue_candidates = detect_opening_lyric_gaps(first_cue_after_missing_opening, LYRICS)
    _require(len(first_cue_candidates) == 1, "opening gap before the first retained cue was not detected")
    _require(first_cue_candidates[0].gap_start == 0.0, "first-cue opening gap did not start at the media origin")
    _require(first_cue_candidates[0].previous_cue_end == 0.0, "first-cue candidate has an invalid previous cue end")

    opening_fragment = _srt(
        (1, 30.0, 32.0, "宇宙から"),
        (2, 40.0, 42.0, "かかりきりになるんだ"),
        (3, 42.0, 45.0, "関心はまだ僕ら"),
        (4, 45.0, 48.0, "真珠のかけらだって無価値"),
    )
    fragment_candidates = detect_opening_lyric_gaps(opening_fragment, LYRICS)
    _require(len(fragment_candidates) == 1, "a bad first fragment hid the true opening gap")
    _require(fragment_candidates[0].gap_start == 0.0 and fragment_candidates[0].gap_end == 30.0, "media-origin gap was not preferred")

    combined_existing_opening = _srt(
        (1, 9.0, 16.0, "手放したっていいさ 傷を付け合う運命"),
        (2, 16.0, 22.0, "ぞんざいな日々は 手離したっていいさ"),
        (3, 22.0, 28.0, "軽薄な言葉も紛れもなく愛だろ"),
    )
    _require(not detect_opening_lyric_gaps(combined_existing_opening, LYRICS), "combined existing opening lyrics were flagged as missing")

    short_intro = _srt(
        (1, 6.0, 9.0, "かかりきりになるんだ"),
        (2, 9.0, 12.0, "関心はまだ僕ら"),
        (3, 12.0, 15.0, "真珠のかけらだって無価値"),
    )
    _require(not detect_opening_lyric_gaps(short_intro, LYRICS), "short intro was treated as a missing opening")

    complete_opening = _srt(
        (1, 10.0, 13.0, "じっと機微を見逃さないで"),
        (2, 13.0, 16.0, "きっと大人はそれらしくするので"),
        (3, 16.0, 19.0, "かかりきりになるんだ"),
    )
    _require(not detect_opening_lyric_gaps(complete_opening, LYRICS), "complete opening was flagged")

    interlude_resume = _srt(
        (1, 1.0, 4.0, "手放したっていいさ"),
        (2, 4.0, 7.0, "傷を付け合う運命"),
        (3, 30.0, 33.0, "手離したっていいさ"),
        (4, 33.0, 36.0, "I don't know"),
    )
    _require(not detect_opening_lyric_gaps(interlude_resume, LYRICS), "mid-song interlude was treated as an opening gap")

    repeated_resume = _srt(
        (1, 26.0, 28.0, "じっと日々を見逃さないで"),
        (2, 28.0, 31.0, "きっと大人はそれらしくするので"),
        (3, 31.0, 33.0, "かかりきりになるんだ"),
        (4, 33.0, 35.0, "関心はまだ僕ら"),
        (5, 35.0, 37.0, "真珠のかけらだって無価値"),
        (6, 37.0, 39.0, "そこに物語がなければ"),
        (7, 39.0, 41.0, "からくりの中で廻る僕ら命だ"),
    )
    _require(
        not detect_opening_lyric_gaps(repeated_resume, REPEATED_LYRICS),
        "a later repeated refrain was treated as a missing opening",
    )

    with tempfile.TemporaryDirectory(prefix="utasyncnext-lyric-gap-") as temp_dir:
        srt_path = Path(temp_dir) / "fixture.srt"
        srt_path.write_text(missing_opening, encoding="utf-8")
        report = write_opening_lyric_gap_report(srt_path, LYRICS)
        _require(report["subtitle_text_changed"] is False, "report changed subtitle text")
        _require(report["candidate_count"] == 1, "report candidate count differs")
        _require(opening_lyric_gap_report_path(srt_path).exists(), "report file was not written")
        summary = format_run_summary(
            {
                "asr_backend": "cuda:float16",
                "asr_lyric_gap_candidates": 1,
                "asr_lyric_gap_report_path": str(opening_lyric_gap_report_path(srt_path)),
                "demucs_skipped": True,
            }
        )
        _require("证据不足，需要人工复听" in summary, "user summary hides conservative fallback")
        _require("系统没有强行补写" in summary, "user summary hides non-destructive behavior")
        partial_summary = format_run_summary(
            {
                "asr_backend": "cuda:float16",
                "asr_lyric_gap_candidates": 1,
                "asr_lyric_gap_partially_repaired_candidates": 1,
                "asr_lyric_gap_inserted_cues": 5,
                "asr_lyric_gap_partial_inserted_cues": 5,
                "asr_lyric_gap_report_path": str(opening_lyric_gap_report_path(srt_path)),
            }
        )
        _require("仅补回了证据可靠的部分" in partial_summary, "partial repair was reported as complete")
        _require("没有完全确认" in partial_summary, "partial repair review warning is missing")
        preview_summary = format_run_summary(
            {
                "asr_backend": "cuda:float16",
                "asr_lyric_gap_candidates": 1,
                "asr_lyric_gap_preview_candidates": 1,
                "asr_lyric_gap_preview_inserted_cues": 4,
                "asr_lyric_gap_preview_path": str(Path(temp_dir) / "preview.srt"),
                "asr_lyric_gap_preview_evidence_path": str(Path(temp_dir) / "preview-evidence.json"),
            }
        )
        _require("严格修复预览" in preview_summary, "strict preview is missing from user summary")
        _require("正式字幕未修改" in preview_summary, "preview summary hides source preservation")
        _require("确认后再采用" in preview_summary, "preview summary sounds automatically applied")

    print("PASS: lyric opening gap detection reports unresolved candidates conservatively")
    print("- strong missing opening detected: yes")
    print("- complete opening ignored: yes")
    print("- missing lyrics before first retained cue detected: yes")
    print("- bad first fragment cannot hide media-origin gap: yes")
    print("- combined existing opening lyrics ignored: yes")
    print("- short intro ignored: yes")
    print("- mid-song live variation ignored: yes")
    print("- later repeated refrain does not create a false opening gap: yes")
    return 0


def _srt(*rows: tuple[int, float, float, str]) -> str:
    blocks = []
    for index, start, end, text in rows:
        blocks.append(f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}")
    return "\n\n".join(blocks) + "\n"


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
