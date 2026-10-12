from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.lyrics_context import select_lyrics_context  # noqa: E402


def main() -> int:
    book = """Artist - First Song
词：A
作曲：B
きみの青い空を見上げて
まだ歩いていく

Artist - Second Song
词：C
作曲：D
夜のハイウェイを飛ばして
朝まで走ろう"""
    second = select_lyrics_context(book, "1\n00:00:01,000 --> 00:00:03,000\n夜のハイウェイを飛ばして")
    _require(second.total_sections == 2, "lyrics book should split into two song sections")
    _require(second.selected_titles == ("Artist - Second Song",), "rough transcript should select matching song")
    _require("First Song" not in second.text and "Second Song" in second.text, "only matched section should be sent")

    unknown = select_lyrics_context(book, "1\n00:00:01,000 --> 00:00:03,000\nまったく違う会話です")
    _require(not unknown.matched, "unreliable match must not force unrelated lyrics")

    one_song = "End roll - yama\n词：yama\nエンドロールいらないよ\n眩しい軌跡をなぞるだけのお話"
    single = select_lyrics_context(one_song, "1\n00:00:01,000 --> 00:00:03,000\nエンドロールいらないよ")
    _require(single.text == one_song, "single-song reference behavior must remain unchanged")

    spaced_one_song = """# End roll - yama

词：yama
曲：yama/GeG

エンドロールいらないよ
眩しい軌跡をなぞるだけのお話"""
    spaced = select_lyrics_context(
        spaced_one_song,
        "1\n00:00:01,000 --> 00:00:03,000\nエンドロールいらないよ",
    )
    _require(spaced.total_sections == 1, "visual blank lines inside one song must not create fake songs")
    _require(spaced.text == spaced_one_song, "single-song spacing should preserve the user's original text")

    print("PASS: lyrics context selects only high-confidence song sections")
    return 0


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
