from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "packaging" / "assets"
PNG_PATH = ASSET_DIR / "utasync.png"
ICO_PATH = ASSET_DIR / "utasync.ico"
LEGACY_ICON = ROOT.parent / "icon.ico"
def main() -> None:
    if not LEGACY_ICON.exists():
        raise FileNotFoundError(f"legacy UtaSync icon not found: {LEGACY_ICON}")
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    # Keep every original ICO layer byte-for-byte. Small Windows taskbar icons
    # are especially sensitive to resampling and decorative overlays.
    shutil.copyfile(LEGACY_ICON, ICO_PATH)
    with Image.open(LEGACY_ICON) as source:
        try:
            preview = source.ico.getimage((256, 256)).convert("RGBA")
        except Exception:
            preview = source.convert("RGBA").resize((256, 256), Image.Resampling.LANCZOS)
    preview.save(PNG_PATH, format="PNG", optimize=True)
    print(f"restored exact V1 icon from {LEGACY_ICON}")
    print(f"created {PNG_PATH}")
    print(f"created {ICO_PATH}")


if __name__ == "__main__":
    main()
