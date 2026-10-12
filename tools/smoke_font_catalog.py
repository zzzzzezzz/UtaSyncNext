"""Validate the user-font catalog without enumerating the system font registry."""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path

NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from ui.font_catalog import discover_user_fonts, import_font, import_fonts_from_directory


def main() -> int:
    candidates = [Path(r"C:\Windows\Fonts\arial.ttf"), Path(r"C:\Windows\Fonts\ARIAL.TTF")]
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        print("SKIP: Windows Arial fixture is unavailable.")
        return 0

    with tempfile.TemporaryDirectory(prefix="utasync-font-catalog-") as temp:
        root = Path(temp)
        imported = import_font(source, root)
        records = discover_user_fonts(root)
        if not imported.family or imported.family not in {record.family for record in records}:
            raise AssertionError("imported font family was not discoverable")
        if imported.path.parent != root:
            raise AssertionError("font was copied outside the selected catalog")
        batch_source = root / "batch-source"
        batch_source.mkdir()
        (batch_source / source.name).write_bytes(source.read_bytes())
        batch_records = import_fonts_from_directory(batch_source, root)
        if len(batch_records) != 1:
            raise AssertionError("folder import did not import one supported font")
        invalid = root / "not-a-font.txt"
        invalid.write_text("not a font", encoding="utf-8")
        try:
            import_font(invalid, root)
        except ValueError:
            pass
        else:
            raise AssertionError("unsupported font extension was accepted")
        print("PASS: user font import, family discovery, and extension guard are valid.")
        print(f"- family: {imported.family}")
        print(f"- files: {len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
