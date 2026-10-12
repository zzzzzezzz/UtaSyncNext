from __future__ import annotations

import multiprocessing as mp
import os
import time
import traceback
from pathlib import Path

from engine.cache_env import app_root, apply_next_cache_env
from core.instance_lock import SingleInstanceLock


APP_LAUNCH_STARTED_AT = time.perf_counter()


def main() -> None:
    mp.freeze_support()
    apply_next_cache_env()
    os.chdir(app_root())

    instance_lock = SingleInstanceLock.acquire()
    if instance_lock is None:
        _show_already_running_message()
        return

    try:
        import customtkinter as ctk
        from ui.main_window import MainWindow

        ctk.set_appearance_mode("dark")
        app = MainWindow(launch_started_at=APP_LAUNCH_STARTED_AT)

        def _show_main_window() -> None:
            """Make the freshly launched app visible and ready for interaction."""
            app.deiconify()
            app.state("normal")
            app.lift()
            app.focus_force()
            # Give Windows one short activation pulse so the window is not
            # left behind the host terminal/Codex window on launch.
            app.attributes("-topmost", True)
            app.after(5000, lambda: app.attributes("-topmost", False))

        # A packaged/secondary launch can otherwise leave the Tk window behind
        # the terminal or the host app even though the process is healthy.
        app.after(180, _show_main_window)
        app.mainloop()
    finally:
        instance_lock.release()


def _show_already_running_message() -> None:
    try:
        from tkinter import Tk, messagebox

        root = Tk()
        root.withdraw()
        messagebox.showinfo(
            "UtaSyncNext 已在运行",
            "UtaSyncNext 已经打开。\n请使用当前窗口，避免重复占用模型和显存。",
            parent=root,
        )
        root.destroy()
    except Exception:
        print("UtaSyncNext 已经打开，请使用当前窗口。")


def _write_crash_log(exc: BaseException) -> None:
    try:
        root = app_root()
    except Exception:
        root = Path.cwd()
    log_path = root / "launcher_crash.log"
    log_path.write_text(
        "UtaSyncNext launcher crashed during startup.\n\n" + "".join(traceback.format_exception(exc)),
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        # mp.freeze_support() runs every spawned pipeline worker through this same
        # entry point and finishes with SystemExit(0). That is a normal worker
        # shutdown, so reporting it as a launcher crash produced a misleading
        # launcher_crash.log on every task run.
        if exc.code not in (0, None):
            _write_crash_log(exc)
        raise
    except BaseException as exc:
        _write_crash_log(exc)
        raise
