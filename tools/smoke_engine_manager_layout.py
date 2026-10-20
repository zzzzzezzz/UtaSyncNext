"""Smoke-test the model manager footer grouping and engine 404 state."""

from __future__ import annotations

from pathlib import Path
import sys

import customtkinter as ctk

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.runtime_installer import EngineAsset, EngineInstallError, EngineManifest
from ui import engine_manager


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _find(widget, text: str):
    matches = []
    for item in _walk(widget):
        try:
            if str(item.cget("text")) == text:
                matches.append(item)
        except Exception:
            continue
    return matches


def main() -> int:
    original_fetch = engine_manager.fetch_manifest
    original_toplevel = engine_manager.ctk.CTkToplevel
    captured_manager = {"window": None}

    def missing_manifest(*_args, **_kwargs):
        raise EngineInstallError("无法读取引擎下载清单：HTTP 404 Not Found", status_code=404)

    def capture_toplevel(*args, **kwargs):
        window = original_toplevel(*args, **kwargs)
        captured_manager["window"] = window
        return window

    engine_manager.ctk.CTkToplevel = capture_toplevel
    root = ctk.CTk()
    root.withdraw()
    manager = None
    try:
        engine_manager.fetch_manifest = missing_manifest
        engine_manager.show_engine_manager(root, engine_ready=False)
        manager = captured_manager["window"]
        if manager is None:
            raise AssertionError("模型管理窗口没有创建")
        manager.geometry("976x700")

        def pump_until(predicate, label: str, timeout: float = 5.0) -> None:
            timed_out = {"value": False}

            def poll() -> None:
                if predicate():
                    root.quit()
                    return
                root.after(20, poll)

            def expire() -> None:
                timed_out["value"] = not predicate()
                root.quit()

            root.after(0, poll)
            root.after(max(1, int(timeout * 1000)), expire)
            root.mainloop()
            if not timed_out["value"] and predicate():
                return
            visible = []
            for item in _walk(manager):
                try:
                    text = str(item.cget("text"))
                except Exception:
                    continue
                if text:
                    visible.append(text)
            raise AssertionError(f"等待超时：{label}；当前文本：{visible}")

        pump_until(lambda: bool(_find(manager, "在线安装资源：未发布")), "404 资源状态")

        common_title = _find(manager, "常用操作")
        check_title = _find(manager, "检查与安装")
        more_toggle = _find(manager, "更多工具  ▸")
        if not common_title or not check_title or not more_toggle:
            raise AssertionError("分区标题或收起按钮未创建")
        if not _find(manager, "本地引擎：未安装，需要先下载并安装引擎"):
            raise AssertionError("本地引擎状态没有和在线资源状态分开显示")

        footer_children = list(common_title[0].master.winfo_children())
        common_row = footer_children[footer_children.index(common_title[0]) + 1]
        check_children = list(check_title[0].master.winfo_children())
        check_row = check_children[check_children.index(check_title[0]) + 1]
        window_width = max(manager.winfo_width(), 960)
        if common_row.winfo_reqwidth() > window_width or check_row.winfo_reqwidth() > window_width:
            raise AssertionError(
                f"操作行仍然超出窗口：common={common_row.winfo_reqwidth()}, "
                f"check={check_row.winfo_reqwidth()}, window={window_width}"
            )

        content = common_title[0].master
        canvas = content._parent_canvas
        root.update()
        scroll_bbox = canvas.bbox("all")
        if not scroll_bbox or scroll_bbox[3] <= canvas.winfo_height():
            raise AssertionError("短窗口下操作区没有形成可滚动内容")
        canvas.yview_moveto(1.0)
        root.update()
        viewport_top = canvas.winfo_rooty()
        viewport_bottom = viewport_top + canvas.winfo_height()
        for text in ("导入本地模型", "刷新", "打开模型目录", "模型源巡检", "本机体检"):
            widget = _find(manager, text)
            if not widget:
                raise AssertionError(f"缺少操作按钮：{text}")
            widget_top = widget[0].winfo_rooty()
            widget_bottom = widget_top + widget[0].winfo_height()
            if widget_top < viewport_top or widget_bottom > viewport_bottom + 2:
                raise AssertionError(f"短窗口滚到底后仍无法访问：{text}")

        more_toggle[0].invoke()
        root.update()
        if not _find(manager, "旧缓存位置") or not _find(manager, "GPU 升级指南"):
            raise AssertionError("更多工具展开后没有显示两个低频按钮")

        if not _find(manager, "资源未发布"):
            raise AssertionError("引擎按钮没有进入禁用提示状态")

        manager.destroy()
        manager = None

        fake_manifest = EngineManifest(
            version="smoke-0.1.3",
            assets=(EngineAsset("engine.zip", "https://example.invalid/engine.zip", "0" * 64, 1024),),
            total_size=1024,
            source_url="https://example.invalid/manifest.json",
        )
        engine_manager.fetch_manifest = lambda *_args, **_kwargs: fake_manifest
        engine_manager.show_engine_manager(root, engine_ready=False)
        manager = captured_manager["window"]
        if manager is None:
            raise AssertionError("第二次模型管理窗口没有创建")
        manager.geometry("976x700")
        pump_until(lambda: bool(_find(manager, "在线安装资源：可用")), "在线资源可用状态")
        if not _find(manager, "本地引擎：未安装，需要先下载并安装引擎"):
            raise AssertionError("在线资源可用时丢失了本地引擎未安装状态")
        install_button = _find(manager, "安装 Next 引擎")
        if not install_button or str(install_button[0].cget("state")) != "normal":
            raise AssertionError("在线资源可用时没有显示可操作的引擎安装按钮")

        print("PASS: model manager separates local engine and online resource state; short-window actions are reachable")
        return 0
    finally:
        if manager is not None:
            try:
                manager.destroy()
            except Exception:
                pass
        root.destroy()
        engine_manager.fetch_manifest = original_fetch
        engine_manager.ctk.CTkToplevel = original_toplevel


if __name__ == "__main__":
    raise SystemExit(main())
