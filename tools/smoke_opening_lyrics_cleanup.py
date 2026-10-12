from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from core.asr_diagnostics import build_asr_diagnostics  # noqa: E402
from core.opening_lyrics_cleanup import apply_lyric_anchored_opening_cleanup  # noqa: E402
from ui.log_formatter import format_run_summary  # noqa: E402


def main() -> int:
    lyrics = """majiko - Sample Song
词：majiko
どこにでもある 偽りの色彩
愛しいモノほど 壊したくなる"""
    rows = [
        _row(1, 0, 4, "ご視聴ありがとうございました。", 0.72),
        _row(2, 60, 62, "大会の結果をお知らせします。", 0.71),
        _row(3, 62, 64, "私は、", 0.71),
        _row(4, 64, 66, "2013年、", 0.71),
        _row(5, 66, 68, "2014年、", 0.71),
        _row(6, 68, 70, "2015年、", 0.71),
        _row(7, 70, 85, "作詞・作曲・編曲・編曲 初音ミク", 0.56),
        _row(8, 85, 90, "イラスト 初音ミク", 0.10),
        _row(9, 90, 95, "映像 初音ミク", 0.10),
        _row(10, 96, 100, "本日は来てくれてありがとう", 0.75),
        _row(14, 100, 103, "作詞作曲した2曲目なんですが", 0.56),
        _row(11, 105, 110, "どこにでもある 偽りの色彩", 0.10),
        _row(12, 110, 114, "愛しいものほど 壊したくなる", 0.10),
        _row(13, 120, 124, "ご視聴ありがとうございました。", 0.91),
    ]
    with tempfile.TemporaryDirectory(prefix="utasyncnext-opening-cleanup-") as temp_dir:
        srt_path = Path(temp_dir) / "source.srt"
        _write_srt(srt_path, rows)
        diagnostic = build_asr_diagnostics(rows, source_srt=str(srt_path))
        result = apply_lyric_anchored_opening_cleanup(srt_path, lyrics, diagnostic)
        text = srt_path.read_text(encoding="utf-8")
        _require(result.changed, "expected cleanup to change this fixture")
        _require(result.removed_count == 10, "expected only proven edge hallucinations to be removed")
        _require("ご視聴" not in text and "2013年" not in text and "初音ミク" not in text, "hallucinations survived")
        _require("本日は来てくれてありがとう" in text, "ordinary opening MC must remain")
        _require("作詞作曲した2曲目なんですが" in text, "spoken songwriting MC must remain")
        _require("どこにでもある 偽りの色彩" in text, "first lyric anchor must remain")
        saved = json.loads((srt_path.with_name("source_识别诊断.json")).read_text(encoding="utf-8"))
        _require(saved["opening_cleanup"]["removed_count"] == 10, "audit metadata missing")
        _require(saved["summary"]["automatic_deletion_count"] == 10, "diagnostic deletion count missing")
        user_summary = format_run_summary(
            {
                "asr_backend": "cuda:float16",
                "asr_opening_cleanup_count": 10,
                "asr_opening_cleanup_anchor": result.anchor_text,
                "asr_review_candidates": 1,
                "asr_diagnostics_level": "full",
                "demucs_skipped": True,
                "output_mode": "bilingual",
            }
        )
        _require("普通口播和歌词不会仅凭文本相似自动删除" in user_summary, "user summary must explain MC safety")

        no_evidence = apply_lyric_anchored_opening_cleanup(
            srt_path,
            lyrics,
            {"evidence_source": "cached-srt-text-only", "segments": rows},
        )
        _require(not no_evidence.changed, "cached text-only diagnostics must not delete cues")

        template_path = Path(temp_dir) / "template-cluster.srt"
        template_rows = [
            _row(1, 31, 34, "この歌詞を歌わないでください。", 0.68),
            _row(2, 37, 40, "これは素晴らしい歌詞です!", 0.66),
            _row(3, 40, 43, "きっと、私たちの声は聴こえますか?", 0.64),
            _row(4, 43, 46, "私はほとんどの歌詞を歌わないでください。", 0.69),
            _row(5, 46, 49, "この歌詞は本当に素晴らしいです。", 0.65),
            _row(6, 49, 52, "私はこの歌詞が本当に大好きです。", 0.63),
            _row(7, 52, 55, "私は本当に大好きです。", 0.62),
            _row(8, 55, 58, "私は本当に大好きです。", 0.61),
            _row(9, 90, 94, "今日は来てくれてありがとう", 0.76),
            _row(10, 103, 108, "どこにでもある 偽りの色彩", 0.05),
        ]
        _write_srt(template_path, template_rows)
        template_result = apply_lyric_anchored_opening_cleanup(
            template_path,
            lyrics,
            build_asr_diagnostics(template_rows, source_srt=str(template_path)),
        )
        template_text = template_path.read_text(encoding="utf-8")
        _require(template_result.removed_count == 8, "expected the contiguous fake-lyric template cluster to be removed")
        _require("この歌詞" not in template_text and "私たちの声" not in template_text, "template cluster survived")
        _require("今日は来てくれてありがとう" in template_text, "isolated low-speech MC must remain")

        exact_path = Path(temp_dir) / "exact-opening-hallucination.srt"
        exact_rows = [
            _row(1, 0, 2, "音楽の歌詞です。", 0.82),
            _row(2, 5, 8, "今日は来てくれてありがとう", 0.76),
            _row(3, 20, 24, "どこにでもある 偽りの色彩", 0.05),
            _row(4, 24, 28, "愛しいものほど 壊したくなる", 0.05),
        ]
        _write_srt(exact_path, exact_rows)
        exact_result = apply_lyric_anchored_opening_cleanup(
            exact_path,
            lyrics,
            build_asr_diagnostics(exact_rows, source_srt=str(exact_path)),
        )
        exact_text = exact_path.read_text(encoding="utf-8")
        _require(exact_result.removed_count == 1, "confirmed exact opening hallucination was not removed")
        _require("音楽の歌詞です" not in exact_text, "confirmed exact opening hallucination survived")
        _require("今日は来てくれてありがとう" in exact_text, "ordinary opening MC next to exact hallucination was removed")

        low_confidence_path = Path(temp_dir) / "low-confidence-exact-phrase.srt"
        low_confidence_rows = [
            _row(1, 0, 2, "音楽の歌詞です。", 0.30),
            _row(2, 20, 24, "どこにでもある 偽りの色彩", 0.05),
            _row(3, 24, 28, "愛しいものほど 壊したくなる", 0.05),
        ]
        _write_srt(low_confidence_path, low_confidence_rows)
        low_confidence_result = apply_lyric_anchored_opening_cleanup(
            low_confidence_path,
            lyrics,
            build_asr_diagnostics(low_confidence_rows, source_srt=str(low_confidence_path)),
        )
        _require(not low_confidence_result.changed, "exact phrase without high no-speech evidence was removed")

    print("PASS: lyric-anchored opening cleanup removes only proven pre-song hallucinations")
    return 0


def _row(index: int, start: float, end: float, text: str, no_speech: float) -> dict[str, object]:
    return {
        "index": index,
        "start": start,
        "end": end,
        "text": text,
        "avg_logprob": -0.4,
        "no_speech_prob": no_speech,
        "compression_ratio": 1.0,
    }


def _write_srt(path: Path, rows: list[dict[str, object]]) -> None:
    lines: list[str] = []
    for row in rows:
        lines.extend([str(row["index"]), f"{_time(float(row['start']))} --> {_time(float(row['end']))}", str(row["text"]), ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _time(value: float) -> str:
    total = int(round(value * 1000))
    hours, total = divmod(total, 3_600_000)
    minutes, total = divmod(total, 60_000)
    seconds, milliseconds = divmod(total, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
