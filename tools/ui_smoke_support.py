from __future__ import annotations

import threading
import time
from typing import Callable


def ui_call(app: object, callback: Callable[[], object], timeout: float = 15) -> object:
    completed = threading.Event()
    result: dict[str, object] = {}

    def invoke() -> None:
        try:
            result["value"] = callback()
        except BaseException as exc:
            result["error"] = exc
        finally:
            completed.set()

    app.after(0, invoke)  # type: ignore[attr-defined]
    if not completed.wait(timeout):
        raise TimeoutError("界面主线程响应超时")
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    return result.get("value")


def wait_until_ui(app: object, predicate: Callable[[], bool], timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bool(ui_call(app, predicate)):
            return
        time.sleep(0.05)
    raise TimeoutError(f"等待超时：{label}")


def widget_text(widget: object) -> str:
    return str(widget.cget("text"))  # type: ignore[attr-defined]


def widget_state(widget: object) -> str:
    return str(widget.cget("state"))  # type: ignore[attr-defined]
