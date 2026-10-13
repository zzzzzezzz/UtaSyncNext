"""Drive the in-app real ASS preview window and check what it displays.

The window replaced a browser/CSS approximation, so this smoke proves the frame
really comes from libass, that the canvas follows the resolved layout, and that
zoom and 1:1 keep working without re-rendering.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

LANDSCAPE = {"resolution_x": 1920, "resolution_y": 1080}
PORTRAIT = {"resolution_x": 1080, "resolution_y": 1920}
BASE_STYLE = {
    "ch_font": "Microsoft YaHei",
    "ch_size": 55,
    "ch_color": "#FFFFFF",
    "ch_outline": "#18181B",
    "jp_font": "Meiryo",
    "jp_size": 35,
    "jp_color": "#CCCCCC",
    "jp_outline": "#18181B",
    "outline_size": 3,
    "shadow_size": 1,
}
SAMPLE_ZH = "在这光芒万丈的舞台上"
SAMPLE_JP = "この光り輝くステージの上で"


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _wait_for_render(root: object, window: object, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()  # type: ignore[attr-defined]
        if getattr(window, "_source", None) is not None:
            return
        time.sleep(0.05)
    raise TimeoutError("预览渲染超时")


class _FakeSidebar:
    """Stands in for the settings sidebar so the real action runner can run."""

    def __init__(self, settings: dict[str, object], dialog: object) -> None:
        self._settings = settings
        self._advanced_dialog = dialog

    def get_current_settings(self) -> dict[str, object]:
        return dict(self._settings)


class _FakeHost:
    def __init__(self, settings: dict[str, object], dialog: object) -> None:
        self.sidebar = _FakeSidebar(settings, dialog)
        self.video_path = ""
        self.media_path = ""


def _run_entry_point(root: object) -> dict[str, object]:
    """The 目标画幅比 dropdown must reach the window even without a media file.

    Regression: the runner only applied the safe-area policy when a video was
    already imported, so 16:9 and 9:16 produced the same 1920x1080 preview.
    """

    import customtkinter as ctk

    from ui.ass_actions import AssActionRunner
    from ui.ass_preview_window import AssPreviewWindow

    dialog = ctk.CTkToplevel(root)  # type: ignore[arg-type]
    dialog.withdraw()
    try:
        results: dict[str, object] = {}
        for label, aspect, expected in (
            ("wide", "16:9 (常规宽屏/默认)", (1920, 1080)),
            ("portrait", "9:16 (竖屏短视频)", (1080, 1920)),
        ):
            AssPreviewWindow._instance = None
            settings = {
                "ass_preset": "默认双语 (纯白+浅灰)",
                "aspect_ratio": aspect,
                "fade_mode": "硬切",
            }
            runner = AssActionRunner(_FakeHost(settings, dialog))
            runner.preview_ass_style()
            window = AssPreviewWindow._instance
            _require(window is not None, f"{label}: 预览窗口没有打开")
            _wait_for_render(root, window)
            size = window._source.size
            _require(size == expected, f"{label}: 画幅比没有生效，得到 {size}")
            geometry = str(window.geometry())
            width, height = (int(part) for part in geometry.split("+")[0].split("x"))
            _require(
                (height > width) == (expected[1] > expected[0]),
                f"{label}: 窗口形状和画幅不一致：{geometry}",
            )
            results[label] = f"{size[0]}x{size[1]}"
            window.close()
            root.update()  # type: ignore[attr-defined]
        return results
    finally:
        dialog.destroy()


def _run() -> dict[str, object]:
    import customtkinter as ctk

    from ui.ass_preview_window import AssPreviewWindow

    AssPreviewWindow._instance = None
    root = ctk.CTk()
    root.withdraw()
    window = None
    try:
        window = AssPreviewWindow.show(
            root,
            config={**BASE_STYLE, **LANDSCAPE},
            preset_name="smoke-landscape",
            chinese=SAMPLE_ZH,
            japanese=SAMPLE_JP,
            source_note="16:9 宽屏安全区",
        )
        _wait_for_render(root, window)
        _require(window._source.size == (1920, 1080), f"预览分辨率不对：{window._source.size}")
        meta = str(window._meta.cget("text"))
        _require("1920x1080" in meta and "smoke-landscape" in meta, f"说明行不正确：{meta}")
        _require("16:9" in meta, "说明行没有带上画幅来源")
        _require(0 < window._fit_scale <= 1, f"适配比例异常：{window._fit_scale}")
        _require("真实 ASS 预览" in str(window._status.cget("text")), "状态没有标记真实 ASS 预览")

        window._on_double_click(None)
        root.update()
        _require(window._zoom > 1.0, "双击没有切到 1:1 细节")
        window._on_double_click(None)
        root.update()
        _require(abs(window._zoom - 1.0) < 1e-6, "再次双击没有回到适应窗口")
        window._on_wheel(type("WheelEvent", (), {"delta": 120})())
        root.update()
        _require(window._zoom > 1.0, "滚轮没有放大")
        _require(window._frame_label.cget("image") is not None, "画面上没有图片")

        # A portrait layout must not be squeezed into a wide canvas.
        window._render(
            {**BASE_STYLE, **PORTRAIT},
            preset_name="smoke-portrait",
            chinese=SAMPLE_ZH,
            japanese=SAMPLE_JP,
            source_note="手动选择竖屏安全区",
        )
        _wait_for_render(root, window)
        _require(window._source.size == (1080, 1920), f"竖屏预览分辨率不对：{window._source.size}")
        geometry = str(window.geometry())
        width, height = (int(part) for part in geometry.split("+")[0].split("x"))
        _require(height > width, f"竖屏预览窗口没有跟着变窄：{geometry}")

        window.close()
        root.update()
        _require(AssPreviewWindow._instance is None, "关闭后实例没有释放")
        entry_points = _run_entry_point(root)
        return {
            "status": "passed",
            "landscape": "1920x1080",
            "portrait": "1080x1920",
            "zoom_modes": ["fit", "1:1", "wheel"],
            "aspect_from_settings": entry_points,
        }
    finally:
        if window is not None and window.winfo_exists():
            window.close()
        root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path, default=NEXT_ROOT / "test_logs" / "ass_preview_window.json"
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

    report_path = args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["status"] == "passed":
        print("PASS: in-app ASS preview renders the export frame and follows the layout aspect")
        print(f"- report: {report_path}")
        return 0
    print(f"FAIL: {report.get('error')}")
    print(f"- report: {report_path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
