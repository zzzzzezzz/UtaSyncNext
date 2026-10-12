from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from core.asr_diagnostics import (  # noqa: E402
    build_asr_diagnostics,
    diagnostics_path_for_srt,
    ensure_srt_diagnostics,
    suppression_reason_for_segment,
)
from core.generator import LiveSubtitleGenerator  # noqa: E402
from ui.log_formatter import format_run_summary  # noqa: E402


def main() -> int:
    rows = [
        _row(1, 0, 4, "ライブを楽しんでください", avg=-0.2, no_speech=0.05, compression=1.1),
        _row(13, 5, 9, "数値異常は無視します", avg=float("nan")),
        _row(11, 10, 14, "今回は制作の裏側について話します"),
        _row(12, 20, 24, "作詞も作曲も僕らの物語の一部だ"),
        _row(2, 60, 65, "作詞・作曲・編曲 中島美穂"),
        _row(3, 65, 70, "イラスト 中島美穂"),
        _row(4, 100, 105, "dj"),
        _row(5, 105, 110, "dj"),
        _row(6, 110, 115, "dj"),
        _row(7, 130, 135, "いいですね"),
        _row(8, 135, 140, "いいですよ"),
        _row(9, 140, 145, "いいですね"),
        _row(10, 160, 165, "不確かな声", avg=-1.3, no_speech=0.72, compression=2.7),
        _row(14, 170, 171, "か", avg=-0.2, no_speech=0.49, compression=1.6),
        _row(15, 180, 221.11, "本当に?あの夢に", avg=-0.3, no_speech=0.30, compression=1.2),
    ]
    report = build_asr_diagnostics(rows, source_srt="fixture.srt")
    _require(report["subtitle_text_changed"] is False, "diagnostics must never alter subtitle text")
    _require(report["summary"]["automatic_deletion_count"] == 0, "diagnostics must not delete cues")
    _require(report["summary"]["review_candidate_count"] == 11, "unexpected review candidate count")
    _require([row["text"] for row in report["segments"]] == [row["text"] for row in rows], "text changed")

    reasons = {row["index"]: row["review_reasons"] for row in report["candidates"]}
    _require(11 not in reasons, "ordinary sentence containing 制作 must not look like a credit")
    _require(12 not in reasons, "lyric words 作詞/作曲 must not look like a credit without a signed name")
    _require(any("制作名单" in reason for reason in reasons[2]), "credit-like text was not marked")
    _require(any("连续出现 3 次" in reason for reason in reasons[4]), "repeated DJ was not marked")
    _require(any("短口头语" in reason for reason in reasons[7]), "generic repeated phrase was not marked")
    _require(any("没有清晰人声" in reason for reason in reasons[10]), "no-speech evidence was not retained")
    _require(any("孤立单字" in reason for reason in reasons[14]), "isolated single character was not marked")
    _require(any("持续 41.1 秒" in reason for reason in reasons[15]), "extreme held cue was not marked")
    _require(
        suppression_reason_for_segment(rows[-2], position=len(rows) - 1) == "",
        "isolated single character must remain review-only",
    )

    opening = _row(1, 1, 12, "エンドロール", no_speech=0.80)
    repeated_opening = _row(2, 12, 14, "エンドロール", no_speech=0.80)
    _require(
        "开场首条" in suppression_reason_for_segment(opening, position=1, next_record=repeated_opening),
        "opening duplicate was not selected for conservative suppression",
    )
    _require(
        not suppression_reason_for_segment(
            _row(1, 1, 6, "エンドロール", no_speech=0.80),
            position=1,
            next_record=repeated_opening,
        ),
        "ordinary opening lyric was incorrectly suppressed",
    )
    credit = _row(2, 60, 65, "作詞・作曲・編曲 中島美穂", no_speech=0.93)
    _require(
        "制作名单" in suppression_reason_for_segment(credit, position=2),
        "high-confidence credit hallucination was not selected",
    )
    spoken_credit = _row(2, 60, 65, "作詞・作曲・編曲 中島美穂", no_speech=0.20)
    _require(
        not suppression_reason_for_segment(spoken_credit, position=2),
        "possible spoken credit was incorrectly suppressed",
    )

    near_duplicate = build_asr_diagnostics(
        [
            _row(1, 10, 12, "からくりの中で廻る僕ら命だ", no_speech=0.10),
            _row(2, 12, 14, "からくりのなかで廻る僕ら命だ", no_speech=0.10),
        ],
        source_srt="near-duplicate.srt",
    )
    near_duplicate_reasons = {
        row["index"]: row["review_reasons"] for row in near_duplicate["candidates"]
    }
    _require(
        any("高度相似" in reason for reason in near_duplicate_reasons[1]),
        "adjacent near-duplicate cue was not marked for review",
    )
    _require(
        any("高度相似" in reason for reason in near_duplicate_reasons[2]),
        "second adjacent near-duplicate cue was not marked for review",
    )
    _require(
        near_duplicate["summary"]["automatic_deletion_count"] == 0,
        "near-duplicate diagnostics must remain review-only",
    )

    with tempfile.TemporaryDirectory(prefix="utasyncnext-asr-diagnostics-") as temp_dir:
        generator = LiveSubtitleGenerator("diagnostic_fixture.mp4", output_dir=temp_dir)
        generator.model = _FakeWhisperModel()
        srt_path = Path(temp_dir) / "diagnostic.srt"
        generator._write_transcription_srt(str(srt_path), "live", skipped_demucs=True)
        original_text = srt_path.read_text(encoding="utf-8")
        _require(original_text.count(" --> ") == 3, "generator removed or added subtitle cues")
        diagnostic_path = diagnostics_path_for_srt(srt_path)
        _require(diagnostic_path.exists(), "generator did not write diagnostics")
        saved = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        _require(saved["summary"]["review_candidate_count"] == 3, "generator diagnostics missed repetition")
        _require(generator.last_run_summary["asr_review_candidates"] == 3, "summary count missing")
        summary_text = format_run_summary(generator.last_run_summary)
        _require("系统没有自动删除任何字幕" in summary_text, "user summary must explain review-only behavior")

        srt_path.write_text(original_text + "\n", encoding="utf-8")
        refreshed = ensure_srt_diagnostics(srt_path)
        _require(refreshed["evidence_source"] == "cached-srt-text-only", "changed SRT kept stale diagnostics")
        _require(
            refreshed["source_srt_sha256"] != saved["source_srt_sha256"],
            "changed SRT did not refresh its diagnostic fingerprint",
        )
        srt_path.write_text(original_text, encoding="utf-8")
        diagnostic_path.unlink()
        cached = ensure_srt_diagnostics(srt_path)
        _require(cached["evidence_source"] == "cached-srt-text-only", "cached SRT fallback not used")
        _require(cached["summary"]["review_candidate_count"] == 3, "cached SRT diagnostics missed repetition")
        _require(srt_path.read_text(encoding="utf-8") == original_text, "cached diagnostics changed SRT")
        basic_summary = format_run_summary(
            {
                "asr_backend": "cache",
                "asr_cache_hit": True,
                "asr_review_candidates": 3,
                "asr_diagnostics_path": str(diagnostic_path),
                "asr_diagnostics_level": "basic",
                "demucs_skipped": True,
                "output_mode": "bilingual",
            }
        )
        _require("缓存没有保留原始识别置信度" in basic_summary, "basic diagnostics must explain their limit")

    with tempfile.TemporaryDirectory(prefix="utasyncnext-asr-filter-") as temp_dir:
        generator = LiveSubtitleGenerator("filter_fixture.mp4", output_dir=temp_dir)
        generator.model = _FakeSuppressionWhisperModel()
        srt_path = Path(temp_dir) / "filter.srt"
        generator._write_transcription_srt(str(srt_path), "live", skipped_demucs=True)
        filtered_text = srt_path.read_text(encoding="utf-8")
        _require(filtered_text.count(" --> ") == 2, "conservative ASR filter emitted an unexpected cue count")
        _require("作詞・作曲・編曲" not in filtered_text, "credit hallucination was not filtered")
        _require(filtered_text.count("エンドロール") == 1, "opening duplicate was not filtered")
        diagnostic_path = diagnostics_path_for_srt(srt_path)
        filtered_report = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        _require(filtered_report["summary"]["automatic_deletion_count"] == 2, "suppressed cues missing")
        _require(len(filtered_report["suppressed_segments"]) == 2, "suppressed cue evidence missing")
        _require(generator.last_run_summary["asr_auto_suppressed"] == 2, "summary suppression count missing")
        filtered_summary = format_run_summary(generator.last_run_summary)
        _require("已保守过滤 2 条明确误识别" in filtered_summary, "user summary hides suppression")

    print("PASS: ASR diagnostics are review-only")
    print("- Faster-Whisper evidence retained: yes")
    print("- repeated DJ and generic phrases marked: yes")
    print("- credit-like metadata marked: yes")
    print("- source SRT text and cue count preserved: yes")
    print("- cached SRT fallback supported: yes")
    return 0


def _row(
    index: int,
    start: float,
    end: float,
    text: str,
    *,
    avg: float | None = None,
    no_speech: float | None = None,
    compression: float | None = None,
) -> dict[str, object]:
    return {
        "index": index,
        "start": start,
        "end": end,
        "text": text,
        "avg_logprob": avg,
        "no_speech_prob": no_speech,
        "compression_ratio": compression,
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class _FakeWhisperModel:
    def transcribe(self, *_args, **_kwargs):
        segments = [
            SimpleNamespace(
                start=float(index * 5),
                end=float((index + 1) * 5),
                text="dj",
                avg_logprob=-0.4,
                no_speech_prob=0.2,
                compression_ratio=1.0,
            )
            for index in range(3)
        ]
        return segments, SimpleNamespace(duration=15.0)


class _FakeSuppressionWhisperModel:
    def transcribe(self, *_args, **_kwargs):
        segments = [
            SimpleNamespace(
                start=1.0,
                end=12.0,
                text="エンドロール",
                avg_logprob=-0.3,
                no_speech_prob=0.80,
                compression_ratio=1.0,
            ),
            SimpleNamespace(
                start=12.0,
                end=14.0,
                text="エンドロール",
                avg_logprob=-0.3,
                no_speech_prob=0.80,
                compression_ratio=1.0,
            ),
            SimpleNamespace(
                start=60.0,
                end=65.0,
                text="作詞・作曲・編曲 中島美穂",
                avg_logprob=-0.5,
                no_speech_prob=0.93,
                compression_ratio=1.0,
            ),
            SimpleNamespace(
                start=66.0,
                end=69.0,
                text="これは残す通常の歌詞です",
                avg_logprob=-0.2,
                no_speech_prob=0.10,
                compression_ratio=1.0,
            ),
        ]
        return segments, SimpleNamespace(duration=70.0)




if __name__ == "__main__":
    raise SystemExit(main())
