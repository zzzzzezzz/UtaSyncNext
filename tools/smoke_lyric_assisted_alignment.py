from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_lyric_assisted_alignment import _match_expected_lines, _write_full_preview  # noqa: E402


LINES = ["ゆらゆらり はらはらり", "色とりどり 乱れ咲き", "花の街思い出す この空から"]


def main() -> int:
    segments = [
        {
            "start": 20.0,
            "end": 26.0,
            "text": "ゆらゆらり はらはらり 色とりどり 乱れ咲き",
            "words": _words(
                (20.0, 23.0, "ゆらゆらりはらはらり", 0.90),
                (23.0, 26.0, "色とりどり乱れ咲き", 0.95),
            ),
        },
        {
            "start": 26.0,
            "end": 32.0,
            "text": "花の街思い出すこの空から",
            "words": _words((26.0, 32.0, "花の街思い出すこの空から", 0.88)),
        },
    ]
    matches = _match_expected_lines(segments, LINES)
    _require(len(matches) == 3 and all(item["accepted"] for item in matches), "word-level split did not recover all lines")
    _require([item["start"] for item in matches] == [20.0, 23.0, 26.0], "word-level starts changed")

    probe = {"matches": matches}
    source = (
        "1\n00:00:30,000 --> 00:00:32,000\n宇宙から\n\n"
        "2\n00:00:40,000 --> 00:00:42,000\nひょんな出来事から\n"
    )
    with tempfile.TemporaryDirectory(prefix="utasyncnext-lyric-assisted-") as temp_dir:
        root = Path(temp_dir)
        source_path = root / "source.srt"
        diagnostics_path = root / "diagnostics.json"
        preview_path = root / "preview.srt"
        source_path.write_text(source, encoding="utf-8")
        diagnostics_path.write_text(
            json.dumps(
                {"segments": [{"start": 30.0, "end": 32.0, "text": "宇宙から", "no_speech_prob": 0.90}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = _write_full_preview(preview_path, source_path, diagnostics_path, probe, LINES)
        preview = preview_path.read_text(encoding="utf-8")
        _require(result["written"], "strong evidence did not produce a preview")
        _require("宇宙から" not in preview, "high-no-speech mismatched fragment survived preview")
        _require(all(line in preview for line in LINES), "aligned lyrics are missing from preview")
        _require("ひょんな出来事から" in preview, "later source cue changed")
        _require(source_path.read_text(encoding="utf-8") == source, "production source was modified")

        weak_path = root / "weak.json"
        weak_path.write_text(
            json.dumps({"segments": [{"start": 30.0, "end": 32.0, "text": "宇宙から", "no_speech_prob": 0.50}]}),
            encoding="utf-8",
        )
        rejected_path = root / "rejected.srt"
        rejected = _write_full_preview(rejected_path, source_path, weak_path, probe, LINES)
        _require(not rejected["written"] and not rejected_path.exists(), "weak residual evidence produced a preview")

    print("PASS: lyric-assisted research preview requires exact timed lyrics and strong residual evidence")
    return 0


def _words(*groups: tuple[float, float, str, float]) -> list[dict[str, object]]:
    return [
        {"start": start, "end": end, "word": text, "probability": probability}
        for start, end, text, probability in groups
    ]


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
