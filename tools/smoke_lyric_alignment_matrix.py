from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_lyric_alignment_matrix import evaluate_expectations  # noqa: E402


def main() -> int:
    positive = evaluate_expectations(
        {
            "lyrics_matches": 4,
            "preview_written": True,
            "full_preview_written": True,
            "source_unchanged": True,
        },
        {
            "minimum_lyrics_matches": 4,
            "expect_preview_written": True,
            "expect_full_preview_written": True,
            "expect_source_unchanged": True,
        },
    )
    _require(positive["passed"], "valid positive case failed")

    negative = evaluate_expectations(
        {
            "lyrics_matches": 1,
            "preview_written": False,
            "full_preview_written": False,
            "source_unchanged": True,
        },
        {
            "maximum_lyrics_matches": 2,
            "expect_preview_written": False,
            "expect_full_preview_written": False,
            "expect_source_unchanged": True,
        },
    )
    _require(negative["passed"], "valid negative case failed")

    unsafe = evaluate_expectations(
        {
            "lyrics_matches": 5,
            "preview_written": True,
            "full_preview_written": True,
            "source_unchanged": False,
        },
        {
            "maximum_lyrics_matches": 2,
            "expect_preview_written": False,
            "expect_full_preview_written": False,
            "expect_source_unchanged": True,
        },
    )
    _require(not unsafe["passed"] and len(unsafe["failures"]) == 4, "unsafe result was not fully rejected")
    print("PASS: lyric alignment matrix evaluates positive, negative, and source-preservation gates")
    return 0


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
