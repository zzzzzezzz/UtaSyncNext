from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from core.subtitle_policy import (  # noqa: E402
    SubtitleLayoutPolicy,
    cap_extreme_sparse_srt_cues,
    measure_subtitle_density,
    split_timed_subtitle_for_layout,
)
from core.ass_maker import ASSMaker  # noqa: E402
from core.generator import LiveSubtitleGenerator  # noqa: E402
from ui.log_formatter import format_run_summary  # noqa: E402


def main() -> int:
    square = _policy("square_safe", jp_max_units=54)
    wide = _policy("wide_16_9", jp_max_units=84)
    source = (
        "今日は山彦リフレインということで、配信限定のアフタートークをお届けします。"
        "今回は制作の裏側や最近の出来事について、ゆっくり振り返りながら話していきます。"
    )

    parts = split_timed_subtitle_for_layout(source, 7.520, 15.519, square)
    _require(len(parts) >= 2, "dense square cue should be split")
    _require("".join(part.text for part in parts) == source, "split must preserve every source character")
    _require(abs(parts[0].start - 7.520) < 0.001, "split must preserve original start")
    _require(abs(parts[-1].end - 15.519) < 0.001, "split must preserve original end")
    for left, right in zip(parts, parts[1:]):
        _require(abs(left.end - right.start) < 0.001, "split timelines must remain continuous")
    for part in parts:
        metrics = measure_subtitle_density(part.text, part.end - part.start, square.jp_max_units)
        _require(metrics.visual_lines <= 2, "each square cue should fit at most two Japanese visual lines")

    wide_parts = split_timed_subtitle_for_layout(source, 7.520, 15.519, wide)
    _require(len(wide_parts) == 1, "wide-screen cues must keep the established behavior")
    _require(wide_parts[0].text == source, "wide-screen text changed unexpectedly")

    wide_timed_parts = split_timed_subtitle_for_layout(
        source,
        7.520,
        45.999,
        wide,
        max_duration_seconds=5.0,
    )
    _require(len(wide_timed_parts) >= 2, "dense long speech must be split across its full ASR window")
    _require("".join(part.text for part in wide_timed_parts) == source, "wide split must preserve every character")
    _require(abs(wide_timed_parts[0].start - 7.520) < 0.001, "wide split must preserve original start")
    _require(abs(wide_timed_parts[-1].end - 45.999) < 0.001, "wide split must preserve original end")
    for left, right in zip(wide_timed_parts, wide_timed_parts[1:]):
        _require(abs(left.end - right.start) < 0.001, "wide split timelines must remain continuous")

    short = "短い字幕です。"
    short_parts = split_timed_subtitle_for_layout(short, 2.0, 10.0, square)
    _require(len(short_parts) == 1, "sparse text must not become tiny flashing cues")

    held_lyric_parts = split_timed_subtitle_for_layout(
        "遠くへ",
        2.0,
        18.0,
        wide,
        max_duration_seconds=8.0,
    )
    _require(len(held_lyric_parts) == 1, "sparse held lyrics must not be fragmented")
    _require(abs(held_lyric_parts[0].end - 18.0) < 0.001, "held lyrics must retain the full ASR window")

    swallowed_lyric_parts = split_timed_subtitle_for_layout(
        "本当に?あの夢に",
        26 * 60 + 23.250,
        27 * 60 + 4.360,
        wide,
        max_duration_seconds=8.0,
    )
    _require(len(swallowed_lyric_parts) == 1, "short extreme cue must remain one readable cue")
    _require(
        abs(swallowed_lyric_parts[0].end - (26 * 60 + 31.250)) < 0.001,
        "short extreme cue must not stay visible across a swallowed ASR window",
    )

    mixed = "yama さん talks about End roll, そして次の話題へ進みます。"
    mixed_parts = split_timed_subtitle_for_layout(mixed, 0.0, 8.0, square)
    _require("".join(part.text for part in mixed_parts) == mixed, "spaces and Latin text must be preserved")

    no_punctuation = "でその癖に気づくきっかけが電車に乗ってる時にちょっと面白いことがありました"
    no_punctuation_parts = split_timed_subtitle_for_layout(no_punctuation, 0.0, 8.0, square)
    boundaries = [part.text[-1:] + next_part.text[:1] for part, next_part in zip(no_punctuation_parts, no_punctuation_parts[1:])]
    _require("電車" not in boundaries, "Japanese fallback must not split the common compound 電車")

    with tempfile.TemporaryDirectory(prefix="utasyncnext-density-") as temp_dir:
        cached_sparse_srt = Path(temp_dir) / "cached_sparse.srt"
        cached_sparse_srt.write_text(
            "1\n00:26:23,250 --> 00:27:04,360\n本当に?あの夢に\n",
            encoding="utf-8",
        )
        _require(
            cap_extreme_sparse_srt_cues(
                cached_sparse_srt,
                wide,
                max_duration_seconds=8.0,
            )
            == 1,
            "cached sparse long cue was not repaired",
        )
        _require(
            "00:26:23,250 --> 00:26:31,250" in cached_sparse_srt.read_text(encoding="utf-8"),
            "cached sparse long cue received the wrong capped end time",
        )
        capped_summary = format_run_summary({"asr_extreme_duration_caps": 1})
        _require("已缩短 1 条超过 18 秒" in capped_summary, "long-cue summary is missing")

        generator = LiveSubtitleGenerator("density_fixture.mp4", output_dir=temp_dir)
        _require(
            generator._format_timestamp(55.399999999) == "00:00:55,400",
            "SRT timestamp formatting must round float noise to the nearest millisecond",
        )
        generator.model = _FakeWhisperModel(source)
        output_path = Path(temp_dir) / "density.srt"
        generator._write_transcription_srt(
            str(output_path),
            "live",
            skipped_demucs=True,
            layout_policy=square,
        )
        output_text = output_path.read_text(encoding="utf-8")
        _require(generator.last_run_summary.get("subtitle_density_splits") == 1, "generator must report split count")
        _require(output_text.count(" --> ") == len(parts), "generator must emit every split cue")
        summary_text = format_run_summary(generator.last_run_summary)
        _require("字幕长度：已自动整理 1 条过长字幕" in summary_text, "user summary must explain automatic splitting")

        speech_generator = LiveSubtitleGenerator("speech_density_fixture.mp4", output_dir=temp_dir)
        speech_generator.model = _FakeWhisperModel(source, start=7.520, end=45.999)
        speech_output_path = Path(temp_dir) / "speech_density.srt"
        speech_generator._write_transcription_srt(
            str(speech_output_path),
            "speech",
            skipped_demucs=True,
            layout_policy=wide,
        )
        speech_output = speech_output_path.read_text(encoding="utf-8")
        _require(
            speech_output.count(" --> ") == len(wide_timed_parts),
            "generator must emit every repaired long-speech cue",
        )
        _require(
            "00:00:45,999" in speech_output,
            "generator must not truncate a long ASR segment to five seconds",
        )

        overlap_srt = Path(temp_dir) / "micro_overlap.srt"
        overlap_text = """1
00:00:51,400 --> 00:00:55,400
第一句

2
00:00:55,399 --> 00:00:58,679
第二句

3
00:00:58,679 --> 00:01:03,100
第三句
"""
        overlap_srt.write_text(overlap_text, encoding="utf-8")
        ass_path = Path(ASSMaker(str(overlap_srt)).generate_ass())
        ass_dialogues = [
            line for line in ass_path.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue:")
        ]
        _require(len(ass_dialogues) == 3, "ASS conversion must preserve every cue")
        _require(
            ass_dialogues[0].startswith("Dialogue: 0,0:00:51.40,0:00:55.39,"),
            "ASS conversion must trim the previous cue for a millisecond overlap",
        )
        _require(
            ass_dialogues[1].startswith("Dialogue: 0,0:00:55.39,0:00:58.67,"),
            "ASS conversion must preserve the following cue start",
        )
        _require(
            overlap_srt.read_text(encoding="utf-8") == overlap_text,
            "ASS overlap protection must not modify the source SRT",
        )

    print("PASS: subtitle density policy")
    print(f"- dense square cue: 1 -> {len(parts)} cues")
    print("- text preserved: yes")
    print("- timeline preserved and continuous: yes")
    print("- wide-screen behavior unchanged: yes")
    print(f"- dense long speech: 1 -> {len(wide_timed_parts)} cues; full ASR window retained")
    print("- sparse long cue left intact: yes")
    print("- sparse extreme cue display duration capped: yes")
    print("- generator and user summary integration: yes")
    print("- millisecond ASS overlap protection: yes; source SRT unchanged")
    return 0


def _policy(profile: str, jp_max_units: int) -> SubtitleLayoutPolicy:
    return SubtitleLayoutPolicy(
        profile=profile,
        source_width=1080 if profile == "square_safe" else 1920,
        source_height=1080,
        resolution_x=1080 if profile == "square_safe" else 1920,
        resolution_y=1080,
        margin_v=96 if profile == "square_safe" else 60,
        ch_size_cap=44 if profile == "square_safe" else 60,
        jp_size_cap=28 if profile == "square_safe" else 38,
        ch_max_units=40 if profile == "square_safe" else 64,
        jp_max_units=jp_max_units,
        note="test",
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class _FakeWhisperModel:
    def __init__(self, text: str, *, start: float = 7.520, end: float = 15.519) -> None:
        self.text = text
        self.start = start
        self.end = end

    def transcribe(self, *_args, **_kwargs):
        segment = SimpleNamespace(start=self.start, end=self.end, text=self.text)
        return [segment], SimpleNamespace(duration=self.end)


if __name__ == "__main__":
    raise SystemExit(main())
