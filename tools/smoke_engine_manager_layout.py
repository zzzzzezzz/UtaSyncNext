"""Smoke-test the model manager footer grouping and engine 404 state."""

from __future__ import annotations

from pathlib import Path
import sys

import customtkinter as ctk

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.runtime_installer import EngineInstallError
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

    engine_manager.fetch_manifest = missing_manifest
    engine_manager.ctk.CTkToplevel = capture_toplevel
    root = ctk.CTk()
    root.withdraw()
    manager = None
    try:
        engine_manager.show_engine_manager(root, engine_ready=False)
        root.update_idletasks()
        manager = captured_manager["window"]
        if manager is None:
            raise AssertionError("模型管理窗口没有创建")
        manager.update_idletasks()

        common_title = _find(manager, "常用操作")
        check_title = _find(manager, "检查与安装")
        more_toggle = _find(manager, "更多工具  ▸")
        if not common_title or not check_title or not more_toggle:
            raise AssertionError("分区标题或收起按钮未创建")

        footer_children = list(common_title[0].master.winfo_children())
        common_row = footer_children[footer_children.index(common_title[0]) + 1]
        check_children = list(check_title[0].master.winfo_children())
        check_row = check_children[check_children.index(check_title[0]) + 1]
        # The manager intentionally keeps a 900px minimum width.  A withdrawn
        # root does not expose the mapped toplevel width reliably on Windows.
        window_width = max(manager.winfo_width(), 900)
        if common_row.winfo_reqwidth() > window_width or check_row.winfo_reqwidth() > window_width:
            raise AssertionError(
                f"操作行仍然超出窗口：common={common_row.winfo_reqwidth()}, "
                f"check={check_row.winfo_reqwidth()}, window={window_width}"
            )

        more_toggle[0].invoke()
        root.update_idletasks()
        if not _find(manager, "旧缓存位置") or not _find(manager, "GPU 升级指南"):
            raise AssertionError("更多工具展开后没有显示两个低频按钮")

        root.after(3000, root.quit)
        root.mainloop()
        if not _find(manager, "引擎资源未发布"):
            raise AssertionError("404 清单没有转换为‘引擎资源未发布’状态")
        if not _find(manager, "资源未发布"):
            raise AssertionError("引擎按钮没有进入禁用提示状态")

        print("PASS: model manager footer groups actions and handles a missing engine manifest")
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
