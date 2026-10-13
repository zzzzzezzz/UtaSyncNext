"""Check the style-workbench preview card really shows the subtitle band.

The card used to centre-crop the frame, which pushed the subtitle line out of
view so the card looked empty even though libass had rendered it. It also
ignored the 目标画幅比 setting, so 9:16 and 16:9 looked identical.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image

NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

WIDE = "16:9 (常规宽屏/默认)"
PORTRAIT = "9:16 (竖屏短视频)"
DEFAULT_PRESET = "默认双语 (纯白+浅灰)"


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _bottom_band_brightness(image: Image.Image) -> int:
    """Highest luminance in the lower part of the frame, where subtitles sit."""

    height = image.height
    band = image.convert("L").crop((0, int(height * 0.78), image.width, height))
    return max(band.getdata())


def _card_for_aspect(root: object, aspect: str, horizon: float = 45.0) -> dict[str, object]:
    from ui import gallery as gallery_module

    settings = {"aspect_ratio": aspect, "fade_mode": "硬切"}
    window = gallery_module.StyleGalleryWindow(
        master=root,  # type: ignore[arg-type]
        current_preset=DEFAULT_PRESET,
        apply_callback=lambda _name: None,
        get_settings_cb=lambda: dict(settings),
        sync_presets_cb=lambda: None,
    )
    card: dict[str, object] = {}
    original = gallery_module.StyleGalleryWindow._display_ass_preview

    def capture(self: object, generation: int, image: Image.Image) -> None:
        original(self, generation, image)  # type: ignore[arg-type]
        card["source"] = image
        card["card"] = self._preview_image.cget("light_image")  # type: ignore[attr-defined]

    gallery_module.StyleGalleryWindow._display_ass_preview = capture
    deadline = time.monotonic() + horizon
    try:
        while time.monotonic() < deadline and "card" not in card:
            root.update()  # type: ignore[attr-defined]
            time.sleep(0.05)
        _require("card" in card, f"{aspect}: 预览卡没有渲染出画面")
        window.update()
        card["stage"] = (window.preview_stage.winfo_width(), window.preview_stage.winfo_height())
    finally:
        gallery_module.StyleGalleryWindow._display_ass_preview = original
        window.destroy()
        root.update()  # type: ignore[attr-defined]
    return card


def _run() -> dict[str, object]:
    import customtkinter as ctk

    root = ctk.CTk()
    root.withdraw()
    try:
        results: dict[str, object] = {}
        shapes: list[float] = []
        for label, aspect in (("wide", WIDE), ("portrait", PORTRAIT)):
            card = _card_for_aspect(root, aspect)
            source = card["source"]
            frame = card["card"]
            assert isinstance(source, Image.Image) and isinstance(frame, Image.Image)
            shape = source.width / source.height
            shapes.append(shape)
            brightness = _bottom_band_brightness(frame)
            _require(
                brightness >= 120,
                f"{label}: 预览卡里看不到字幕（最亮 {brightness}），画面被裁掉了",
            )
            _require(
                abs(frame.width / frame.height - shape) < 0.05,
                f"{label}: 预览卡把画面压扁了：{frame.size} vs {source.size}",
            )
            stage_w, stage_h = card["stage"]
            _require(stage_h > 200, f"{label}: 预览卡高度没有撑开：{stage_w}x{stage_h}")
            results[label] = {
                "frame": f"{source.width}x{source.height}",
                "card": f"{frame.width}x{frame.height}",
                "stage": f"{stage_w}x{stage_h}",
                "subtitle_luma": brightness,
            }
        _require(
            abs(shapes[0] - shapes[1]) > 0.5,
            f"9:16 和 16:9 的画面比例仍然一样：{shapes}",
        )
        return {"status": "passed", "aspects": results}
    finally:
        root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path, default=NEXT_ROOT / "test_logs" / "style_workbench_preview.json"
    )
    args = parser.parse_args()

    report: dict[str, object] = {"status": "failed", "checks": []}
    try:
        checks = report["checks"]
        assert isinstance(checks, list)
        checks.append(_run())
        report["status"] = "passed"
    except Exception as exc:  # noqa: BLE001 - the report needs the reason
        report["error"] = f"{type(exc).__name__}: {exc}"

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["status"] == "passed":
        print("PASS: workbench preview follows the target aspect and keeps the subtitle visible")
        print(f"- report: {args.report}")
        return 0
    reason = report.get("error")
    print(f"FAIL: {reason}")
    print(f"- report: {args.report}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
