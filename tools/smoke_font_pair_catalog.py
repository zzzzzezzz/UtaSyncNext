"""Validate named Chinese/Japanese font pair persistence."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.font_catalog import load_user_font_pairs, save_user_font_pairs


def main() -> None:
    with TemporaryDirectory(prefix="utasync-font-pairs-") as temp_dir:
        path = Path(temp_dir) / "user_font_pairs.json"
        expected = {
            "majiko": ("M PLUS 1p", "Zen Kaku Gothic New"),
            "aimer": ("Source Han Sans CN", "Source Han Sans JP"),
        }
        save_user_font_pairs(expected, path)
        if load_user_font_pairs(path) != expected:
            raise AssertionError("named font pair round-trip failed")
        renamed = load_user_font_pairs(path)
        renamed["majiko 主视觉"] = renamed.pop("majiko")
        save_user_font_pairs(renamed, path)
        loaded = load_user_font_pairs(path)
        if "majiko 主视觉" not in loaded or "majiko" in loaded:
            raise AssertionError("named font pair rename did not persist")
        removed = load_user_font_pairs(path)
        removed.pop("aimer", None)
        save_user_font_pairs(removed, path)
        if "aimer" in load_user_font_pairs(path):
            raise AssertionError("named font pair delete did not persist")
        path.write_text('{"broken": "not a pair", "ok": {"ch_font": "A", "jp_font": "B"}}', encoding="utf-8")
        if load_user_font_pairs(path) != {"ok": ("A", "B")}:
            raise AssertionError("invalid named font pair was not ignored")
    print("PASS: named font pair persistence and validation")


if __name__ == "__main__":
    main()
