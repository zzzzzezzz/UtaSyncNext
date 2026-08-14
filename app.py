from __future__ import annotations

import multiprocessing as mp
import os
import traceback
from pathlib import Path

from engine.cache_env import app_root, apply_next_cache_env


def main() -> None:
    mp.freeze_support()
    apply_next_cache_env()
    os.chdir(app_root())

    import customtkinter as ctk
    from ui.main_window import MainWindow

    ctk.set_appearance_mode("dark")
    app = MainWindow()
    app.mainloop()


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
    except BaseException as exc:
        _write_crash_log(exc)
        raise
