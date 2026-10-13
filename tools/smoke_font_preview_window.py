"""Prove the shared font preview window serves both real entry points.

The style workbench and the advanced settings panel each owned their own font
preview (or none at all), so one entry point could show a font the other never
rendered. This smoke drives the shared window directly, then proves both entry
points hand it the fonts the user actually selected.
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

DEFAULT_PRESET = "默认双语 (纯白+浅灰)"
TONE_PRESET = "yama专属 (克莱因蓝/低饱和)"
MANUAL_CH = "M PLUS 1p"
MANUAL_JP = "Zen Kaku Gothic New"
SAMPLE_ZH = "在这光芒万丈的舞台上"
SAMPLE_JP = "この光り輝くステージの上で"
LANDSCAPE = {"resolution_x": 1920, "resolution_y": 1080}


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _wait_for_render(root: object, window: object, generation: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        status = str(window._status.cget("text"))
        if status == "预览失败":
            raise AssertionError(f"字体预览渲染失败：{window._frame_label.cget('text')}")
        if window._generation == generation and status == "真实 ASS 预览":
            return
        time.sleep(0.05)
    raise TimeoutError("字体预览渲染超时")


class _Recorder:
    """Capture the arguments the real entry points hand to the shared window."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, *args: object, **kwargs: object) -> None:
        call = dict(kwargs)
        call["master"] = args[0] if args else None
        self.calls.append(call)


def _run_window(root: object) -> dict[str, object]:
    from ui.font_preview_window import FontPreviewWindow

    FontPreviewWindow._instance = None
    config = {"fade_mode": "硬切", **LANDSCAPE}
    window = FontPreviewWindow.show(
        root,
        config=config,
        ch_font="Source Han Sans CN",
        jp_font="Zen Kaku Gothic New",
        preset_name=TONE_PRESET,
        chinese=SAMPLE_ZH,
        japanese=SAMPLE_JP,
        sample_label="样例 00:12.4",
        source_note="跟随预设字体",
    )
    _wait_for_render(root, window, window._generation)
    _require(window._source is not None, "窗口没有渲染出画面")
    ratio = window._source.width / window._source.height
    _require(abs(ratio - 16 / 9) < 0.01, f"预览分辨率不像 16:9：{window._source.size}")
    meta = str(window._meta.cget("text"))
    for expected in (
        "中文：Source Han Sans CN",
        "日文：Zen Kaku Gothic New",
        f"预设：{TONE_PRESET}",
        "跟随预设字体",
    ):
        _require(expected in meta, f"说明行缺少 {expected}：{meta}")
    sample = str(window._sample.cget("text"))
    _require(
        "样例 00:12.4" in sample and SAMPLE_ZH in sample and SAMPLE_JP in sample,
        f"样例行不正确：{sample}",
    )
    _require(str(window.title()) == "UtaSyncNext · 字体预览", f"窗口标题不正确：{window.title()}")
    _require(window._frame_label.cget("image") is not None, "画面上没有图片")
    histogram = window._source.convert("L").histogram()
    brightness = max(level for level, count in enumerate(histogram) if count)
    _require(brightness >= 120, f"画面里看不到字幕（最亮 {brightness}）")

    # The second entry point must reuse the same window instead of stacking a copy.
    reused = FontPreviewWindow.show(
        root,
        config=config,
        ch_font="Microsoft YaHei",
        jp_font="Meiryo",
        preset_name=DEFAULT_PRESET,
        chinese=SAMPLE_ZH,
        japanese=SAMPLE_JP,
    )
    _require(reused is window, "字体预览不是单例窗口")
    _wait_for_render(root, reused, reused._generation)
    meta = str(reused._meta.cget("text"))
    _require("中文：Microsoft YaHei" in meta and "日文：Meiryo" in meta, f"二次渲染没有更新说明行：{meta}")
    opened = [child for child in root.winfo_children() if isinstance(child, FontPreviewWindow)]
    _require(len(opened) == 1, f"打开的字体验证窗口不止一个：{len(opened)}")

    FontPreviewWindow.close_for(root)
    root.update()
    _require(FontPreviewWindow._instance is None, "关闭后实例没有释放")
    _require(not [child for child in root.winfo_children() if isinstance(child, FontPreviewWindow)], "窗口没有销毁")
    return {"window": "ok", "singleton": True, "sample_line": sample}


def _run_workbench_entry(root: object) -> dict[str, object]:
    from ui import gallery as gallery_module
    from ui.font_preview_window import FontPreviewWindow

    gallery_module.StyleGalleryWindow._request_ass_preview = lambda self, config: None
    window = gallery_module.StyleGalleryWindow(
        master=root,
        current_preset=DEFAULT_PRESET,
        apply_callback=lambda _name: None,
        get_settings_cb=lambda: {"aspect_ratio": "16:9 (常规宽屏/默认)", "fade_mode": "硬切"},
        sync_presets_cb=lambda: None,
    )
    recorder = _Recorder()
    original = FontPreviewWindow.show
    FontPreviewWindow.show = recorder
    try:
        expected_pair = (window.ch_font_option.get().strip(), window.jp_font_option.get().strip())
        window._open_font_preview()
    finally:
        FontPreviewWindow.show = original
        window.destroy()
        root.update()
    _require(len(recorder.calls) == 1, f"工作台没有走共享预览窗口：{len(recorder.calls)} 次调用")
    call = recorder.calls[0]
    pair = (call.get("ch_font"), call.get("jp_font"))
    _require(pair == expected_pair, "工作台字体和控件不一致：" + repr(pair) + " vs " + repr(expected_pair))
    _require(call.get("preset_name") == DEFAULT_PRESET, f"工作台没有传当前预设：{call.get('preset_name')}")
    _require(call.get("chinese") and call.get("japanese"), "工作台没有传样例字幕")
    _require(isinstance(call.get("config"), dict) and call["config"], "工作台没有传预设配置")
    return {"workbench_pair": list(pair)}


def _run_settings_entry(root: object) -> dict[str, object]:
    import customtkinter as ctk

    from core.ass_maker import ASS_PRESETS
    from ui.font_preview_window import FontPreviewWindow
    from ui.visual_settings_panel import VisualSettingsPanel

    holder = ctk.CTkFrame(root)
    panel = VisualSettingsPanel(
        holder,
        settings={},
        callbacks={},
        get_settings_cb=lambda: {},
        sync_presets_cb=lambda: None,
    )
    recorder = _Recorder()
    original = FontPreviewWindow.show
    FontPreviewWindow.show = recorder
    try:
        panel.opt_ass_preset.set(TONE_PRESET)
        panel.opt_ch_font.set(panel._format_font_display("跟随预设"))
        panel.opt_jp_font.set(panel._format_font_display("跟随预设"))
        panel._preview_fonts()
        preset_fonts = (ASS_PRESETS[TONE_PRESET]["ch_font"], ASS_PRESETS[TONE_PRESET]["jp_font"])
        _require(len(recorder.calls) == 1, f"高级设置没有走共享预览窗口：{len(recorder.calls)} 次调用")
        call = recorder.calls[0]
        _require(
            (call.get("ch_font"), call.get("jp_font")) == preset_fonts,
            f"跟随预设时没有取预设字体：{call.get('ch_font')} / {call.get('jp_font')}",
        )
        _require(call.get("source_note") == "跟随预设字体", f"说明行不正确：{call.get('source_note')}")
        _require(call.get("preset_name") == TONE_PRESET, f"没有传预设名：{call.get('preset_name')}")

        recorder.calls.clear()
        panel.opt_ch_font.set(panel._format_font_display(MANUAL_CH))
        panel.opt_jp_font.set(panel._format_font_display(MANUAL_JP))
        panel._preview_fonts()
        _require(len(recorder.calls) == 1, "手动指定字体没有触发预览")
        call = recorder.calls[0]
        _require(
            (call.get("ch_font"), call.get("jp_font")) == (MANUAL_CH, MANUAL_JP),
            f"手动指定字体没有生效：{call.get('ch_font')} / {call.get('jp_font')}",
        )
        _require(call.get("source_note") == "手动指定字体", f"说明行不正确：{call.get('source_note')}")
    finally:
        FontPreviewWindow.show = original
        holder.destroy()
        root.update()
    return {"preset_fonts": list(preset_fonts), "manual_fonts": [MANUAL_CH, MANUAL_JP]}


def _run() -> dict[str, object]:
    import customtkinter as ctk

    from ui import ass_actions
    from ui import font_preview_window as preview_module

    # Both entry points must agree on the sample text, or the same pair previews
    # differently depending on where the user opened it.
    _require(ass_actions.SAMPLE_CHINESE == preview_module.SAMPLE_CHINESE, "中文样例文案不一致")
    _require(ass_actions.SAMPLE_JAPANESE == preview_module.SAMPLE_JAPANESE, "日文样例文案不一致")

    root = ctk.CTk()
    root.withdraw()
    try:
        results: dict[str, object] = {"shared_sample": True}
        results["window"] = _run_window(root)
        results["workbench_entry"] = _run_workbench_entry(root)
        results["settings_entry"] = _run_settings_entry(root)
        return {"status": "passed", **results}
    finally:
        root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", type=Path, default=NEXT_ROOT / "test_logs" / "font_preview_window.json"
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
        print("PASS: the shared font preview window renders the pair and both entry points use it")
        print(f"- report: {args.report}")
        return 0
    print(f"FAIL: {report.get('error')}")
    print(f"- report: {args.report}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
