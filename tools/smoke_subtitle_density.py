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
    measure_subtitle_density,
    split_timed_subtitle_for_layout,
)
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

    short = "短い字幕です。"
    short_parts = split_timed_subtitle_for_layout(short, 2.0, 10.0, square)
    _require(len(short_parts) == 1, "sparse text must not become tiny flashing cues")

    mixed = "yama さん talks about End roll, そして次の話題へ進みます。"
    mixed_parts = split_timed_subtitle_for_layout(mixed, 0.0, 8.0, square)
    _require("".join(part.text for part in mixed_parts) == mixed, "spaces and Latin text must be preserved")

    no_punctuation = "でその癖に気づくきっかけが電車に乗ってる時にちょっと面白いことがありました"
    no_punctuation_parts = split_timed_subtitle_for_layout(no_punctuation, 0.0, 8.0, square)
    boundaries = [part.text[-1:] + next_part.text[:1] for part, next_part in zip(no_punctuation_parts, no_punctuation_parts[1:])]
    _require("電車" not in boundaries, "Japanese fallback must not split the common compound 電車")

    with tempfile.TemporaryDirectory(prefix="utasyncnext-density-") as temp_dir:
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

    print("PASS: subtitle density policy")
    print(f"- dense square cue: 1 -> {len(parts)} cues")
    print("- text preserved: yes")
    print("- timeline preserved and continuous: yes")
    print("- wide-screen behavior unchanged: yes")
    print("- sparse long cue left intact: yes")
    print("- generator and user summary integration: yes")
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
    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, *_args, **_kwargs):
        segment = SimpleNamespace(start=7.520, end=15.519, text=self.text)
        return [segment], SimpleNamespace(duration=15.519)


if __name__ == "__main__":
    raise SystemExit(main())
