from __future__ import annotations

import multiprocessing as mp
import os
import time
import traceback
from pathlib import Path

from engine.cache_env import app_root, apply_next_cache_env
from core.instance_lock import SingleInstanceLock
from core.window_focus import focus_window_by_title


APP_LAUNCH_STARTED_AT = time.perf_counter()


def main() -> None:
    mp.freeze_support()
    apply_next_cache_env()
    os.chdir(app_root())

    instance_lock = SingleInstanceLock.acquire()
    if instance_lock is None:
        raised = focus_window_by_title()
        _startup_log(f"second launch raised={raised}")
        if not raised:
            _show_already_running_message()
        return
    _startup_log("started")

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


def _startup_log(message: str) -> None:
    """Append one line about this launch so a silent failure leaves evidence.

    "Double-clicking does nothing" cannot be diagnosed from the outside: a
    launcher that exited on the instance lock looks exactly like one that never
    started. The file is append-only, tiny, and sits next to the crash log.
    """
    try:
        path = app_root() / "launcher_startup.log"
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} pid={os.getpid()} {message}\n")
    except Exception:
        # Diagnostics must never be the reason a launch fails.
        pass


def _show_already_running_message() -> None:
    """Explain a launch that could not reach any window.

    The caller already tried to wake a running window. Reaching this means the
    lock belongs to a process without a window (a killed GUI thread, a crashed
    mainloop), so the message names the recovery instead of leaving the user
    with an app that will not open.
    """
    text = (
        "UtaSyncNext 已经在运行，但没有找到它的窗口。\n\n"
        "请在任务管理器里结束 UtaSyncNext.exe 后重新启动。"
        "刚关掉界面时进程还会占用单实例锁，等几秒再试一次即可。"
    )
    try:
        from tkinter import Tk, messagebox

        root = Tk()
        root.withdraw()
        messagebox.showinfo("UtaSyncNext 已在运行", text, parent=root)
        root.destroy()
    except Exception:
        print(text)


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
