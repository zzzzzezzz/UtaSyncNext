from __future__ import annotations

import sys
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from core.generator import LiveSubtitleGenerator  # noqa: E402
from core.lyric_gap_detection import parse_srt_cues  # noqa: E402


class FakeWhisperModel:
    def __init__(self, word_probability: float = 0.9) -> None:
        self.word_probability = word_probability

    def transcribe(self, audio_path: str, **kwargs: object):
        if not Path(audio_path).exists():
            raise AssertionError("local rescan audio was not created")
        if kwargs.get("word_timestamps"):
            words = [
                SimpleNamespace(start=2.0, end=4.0, word="最初の歌詞です", probability=self.word_probability),
                SimpleNamespace(start=4.1, end=6.5, word="二番目の歌詞です", probability=self.word_probability),
                SimpleNamespace(start=6.7, end=9.0, word="三番目の歌詞です", probability=self.word_probability),
            ]
            return (
                [
                    SimpleNamespace(
                        start=2.0,
                        end=9.0,
                        text="最初の歌詞です 二番目の歌詞です 三番目の歌詞です",
                        words=words,
                        avg_logprob=-0.1,
                        no_speech_prob=0.02,
                    )
                ],
                SimpleNamespace(duration=10.0),
            )
        return (
            [
                SimpleNamespace(start=2.0, end=4.0, text=" 最初の歌詞です "),
                SimpleNamespace(start=4.0, end=6.5, text="二番目の歌詞です"),
            ],
            SimpleNamespace(duration=10.0),
        )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="utasyncnext-gap-window-") as temp_dir:
        root = Path(temp_dir)
        generator = LiveSubtitleGenerator("fixture.mp4", output_dir=str(root / "output"))
        generator.model = FakeWhisperModel()
        generator.vocal_path = str(root / "full.wav")
        Path(generator.vocal_path).write_bytes(b"fixture")
        target = root / "rescan.srt"

        def fake_extract(_source: str, output: str, start: float, end: float) -> str:
            _require(start == 100.0 and end == 120.0, "wrong local rescan bounds")
            Path(output).write_bytes(b"clip")
            return output

        with patch("core.generator.FFmpegAudioProcessor.extract_audio_window", side_effect=fake_extract):
            generator.transcribe_window_to_srt(
                100.0,
                120.0,
                target,
                audio_type="live",
                skipped_demucs=True,
            )

            expected = ["最初の歌詞です", "二番目の歌詞です", "三番目の歌詞です"]
            aligned_path = root / "aligned.srt"
            aligned = generator.transcribe_lyric_window_to_srt(
                100.0,
                120.0,
                aligned_path,
                expected_lines=expected,
                skipped_demucs=True,
            )
            _require(bool(aligned["written"]), "strong word-level alignment was rejected")
            aligned_cues = parse_srt_cues(aligned_path.read_text(encoding="utf-8"))
            _require([cue.text for cue in aligned_cues] == expected, "merged lyric segment was not split")
            _require(aligned_cues[0].start == 102.0 and aligned_cues[-1].end == 109.0, "word times changed")
            evidence = json.loads(Path(str(aligned["evidence_path"])).read_text(encoding="utf-8"))
            _require(evidence["policy"] == "lyrics-gap-word-alignment-v1", "wrong alignment policy")

            generator.model = FakeWhisperModel(word_probability=0.6)
            weak_path = root / "weak.srt"
            weak = generator.transcribe_lyric_window_to_srt(
                100.0,
                120.0,
                weak_path,
                expected_lines=expected,
                skipped_demucs=True,
            )
            _require(not weak["written"] and not weak_path.exists(), "weak word evidence produced subtitles")
            _require("word probability" in str(weak["rejection_reason"]), "weak rejection reason was lost")

        cues = parse_srt_cues(target.read_text(encoding="utf-8"))
        _require(len(cues) == 2, "local rescan cue count is wrong")
        _require(cues[0].start == 102.0 and cues[1].end == 106.5, "timestamps were not restored")
        _require(not target.with_suffix(".wav").exists(), "temporary rescan audio was not removed")

        try:
            generator.transcribe_window_to_srt(
                0.0,
                121.0,
                target,
                audio_type="live",
                skipped_demucs=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("oversized local rescan window was accepted")

    print("PASS: local lyric-gap rescan restores original timestamps and cleans temporary audio")
    return 0


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
