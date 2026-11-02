"""Exercise the Demucs manager state and background download without network I/O."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import customtkinter as ctk

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.demucs_weights import DemucsWeightsStatus
from ui import engine_manager


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _find(widget, text: str):
    for item in _walk(widget):
        try:
            if str(item.cget("text")) == text:
                return item
        except Exception:
            pass
    return None


def main() -> int:
    original_inspect = engine_manager.inspect_demucs_weights
    original_download = engine_manager.download_demucs_weights
    original_fetch = engine_manager.fetch_manifest
    original_toplevel = engine_manager.ctk.CTkToplevel
    original_ask = engine_manager.messagebox.askyesno
    original_info = engine_manager.messagebox.showinfo
    original_error = engine_manager.messagebox.showerror

    cache = ROOT / "build" / "smoke-demucs-cache"
    missing = DemucsWeightsStatus("htdemucs_ft", cache, ("a.th", "b.th"), ("a.th", "b.th"))
    ready = DemucsWeightsStatus("htdemucs_ft", cache, ("a.th", "b.th"), ())
    state = {"value": missing}
    download_started = threading.Event()
    release_download = threading.Event()
    captured = {"manager": None}
    calls = {"proxy": None}

    def fake_inspect():
        return state["value"]

    def fake_download(*, proxy_url: str, progress_cb):
        calls["proxy"] = proxy_url
        download_started.set()
        release_download.wait(5)
        progress_cb("fake Demucs download", 0.5)
        state["value"] = ready
        return ready

    def fake_toplevel(*args, **kwargs):
        window = original_toplevel(*args, **kwargs)
        captured["manager"] = window
        return window

    engine_manager.inspect_demucs_weights = fake_inspect
    engine_manager.download_demucs_weights = fake_download
    engine_manager.fetch_manifest = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("offline smoke"))
    engine_manager.ctk.CTkToplevel = fake_toplevel
    engine_manager.messagebox.askyesno = lambda *_args, **_kwargs: True
    engine_manager.messagebox.showinfo = lambda *_args, **_kwargs: None
    engine_manager.messagebox.showerror = lambda *_args, **_kwargs: None

    root = ctk.CTk()
    root.withdraw()
    manager = None
    try:
        engine_manager.show_engine_manager(root, engine_ready=True)
        manager = captured["manager"]
        if manager is None:
            raise AssertionError("manager did not open")

        def pump_until(predicate, label: str, timeout_ms: int = 5000):
            result = {"ok": False}

            def poll():
                if predicate():
                    result["ok"] = True
                    root.quit()
                else:
                    root.after(20, poll)

            root.after(0, poll)
            root.after(timeout_ms, root.quit)
            root.mainloop()
            if not result["ok"]:
                raise AssertionError(f"timed out: {label}")

        pump_until(lambda: bool(_find(manager, "人声分离权重：未准备")), "missing state")
        button = _find(manager, "下载/准备权重")
        if button is None or str(button.cget("state")) != "normal":
            raise AssertionError("missing weights did not enable download")

        button.invoke()
        pump_until(download_started.is_set, "download worker started")
        timer_fired = {"value": False}
        root.after(50, lambda: timer_fired.__setitem__("value", True))
        pump_until(lambda: timer_fired["value"], "UI remains responsive during download")
        release_download.set()
        pump_until(lambda: bool(_find(manager, "人声分离权重：已就绪")), "ready state")
        if calls["proxy"] is None:
            raise AssertionError("current proxy was not forwarded")
        ready_button = _find(manager, "已就绪")
        if ready_button is None or str(ready_button.cget("state")) != "disabled":
            raise AssertionError("ready weights did not disable download")

        print("PASS: missing and ready Demucs states; background download leaves UI responsive")
        return 0
    finally:
        release_download.set()
        if manager is not None:
            manager.destroy()
        root.destroy()
        engine_manager.inspect_demucs_weights = original_inspect
        engine_manager.download_demucs_weights = original_download
        engine_manager.fetch_manifest = original_fetch
        engine_manager.ctk.CTkToplevel = original_toplevel
        engine_manager.messagebox.askyesno = original_ask
        engine_manager.messagebox.showinfo = original_info
        engine_manager.messagebox.showerror = original_error


if __name__ == "__main__":
    raise SystemExit(main())
