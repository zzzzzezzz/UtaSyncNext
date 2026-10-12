from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PNG_PATH = ROOT / "packaging" / "assets" / "utasync.png"
ICO_PATH = ROOT / "packaging" / "assets" / "utasync.ico"
SPEC_PATH = ROOT / "packaging" / "launcher.spec"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    _require(PNG_PATH.is_file(), "PNG application icon is missing")
    _require(ICO_PATH.is_file(), "ICO application icon is missing")
    with Image.open(PNG_PATH) as image:
        _require(image.size == (256, 256), f"unexpected PNG icon size: {image.size}")
        _require(image.mode == "RGBA", f"PNG icon should preserve alpha, got {image.mode}")
    with Image.open(ICO_PATH) as image:
        _require(image.format == "ICO", "application icon is not a Windows ICO")
        _require(image.size[0] >= 256 and image.size[1] >= 256, "ICO lacks a high-resolution layer")
    spec = SPEC_PATH.read_text(encoding="utf-8")
    _require('icon=str(PROJECT_ROOT / "assets" / "utasync.ico")' in spec, "PyInstaller spec does not use the UtaSync icon")
    _require('(str(PROJECT_ROOT / "assets" / "utasync.png"), "assets")' in spec, "PNG icon is not bundled for Tk")
    print("PASS: UtaSync application icon assets and packaging references are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
