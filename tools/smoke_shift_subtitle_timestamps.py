from __future__ import annotations

import sys
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from tools.shift_subtitle_timestamps import shift_ass, shift_srt


def main() -> int:
    srt = "1\n00:00:01,250 --> 00:00:03,500\n字幕\n"
    shifted_srt = shift_srt(srt, 900_000)
    assert "00:15:01,250 --> 00:15:03,500" in shifted_srt

    ass = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.25,0:00:03.50,Default,,0,0,0,,字幕\n"
        "Comment: 1,0:00:04.00,0:00:05.00,Default,,0,0,0,,注释\n"
    )
    shifted_ass = shift_ass(ass, 900_000)
    assert "Dialogue: 0,0:15:01.25,0:15:03.50" in shifted_ass
    assert "Comment: 1,0:15:04.00,0:15:05.00" in shifted_ass
    assert "Format: Layer, Start, End" in shifted_ass

    print("PASS: SRT and ASS timestamps shift without changing subtitle payloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
