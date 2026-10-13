"""Prove a second launch lands on the window of the running instance.

The launcher refuses to start twice, so the only way a user sees the app again
is by bringing the already-open window forward. This smoke checks the helper
that does that: it must not report a window that does not exist, and it must
find and surface a real visible window with the expected title.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from core.window_focus import focus_window_by_title  # noqa: E402


def _require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    _require(
        not focus_window_by_title("UtaSyncNext · 不存在的窗口"),
        "窗口激活报告了一个并不存在的窗口",
    )
    if os.name != "nt":
        print("PASS: 非 Windows 平台上窗口激活按空操作处理")
        return 0

    import tkinter

    title = "UtaSyncNext · smoke-activation"
    root = tkinter.Tk()
    try:
        root.title(title)
        root.update()
        _require(focus_window_by_title(title), "没有找到已经打开的可见窗口")
    finally:
        root.destroy()
    print("PASS: 第二次启动会把已运行的窗口带到前台")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
