r"""Open the real engine manager from a packaged launcher and close it cleanly."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = NEXT_ROOT / "dist" / "UtaSyncNext" / "UtaSyncNext.exe"
MAIN_TITLE = "UtaSyncNext"
MANAGER_TITLE = "UtaSyncNext - 独立模型管理"
SMOKE_ENV = "UTASYNCNEXT_OPEN_MANAGER_SMOKE"

sys.path.insert(0, str(NEXT_ROOT))
from tools.probe_launcher_close import describe, find_app_window, user32, windows_for  # noqa: E402


def find_window(pid: int, title: str) -> int | None:
    for hwnd, visible, _klass, window_title in windows_for(pid):
        if visible and window_title.strip() == title:
            return hwnd
    return None


def wait_for_window(proc: subprocess.Popen, title: str, timeout: float) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and proc.poll() is None:
        hwnd = find_window(proc.pid, title)
        if hwnd:
            return hwnd
        time.sleep(0.25)
    return None


def close_window(hwnd: int) -> None:
    user32.PostMessageW(hwnd, 0x0010, 0, 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", default=str(DEFAULT_EXE))
    parser.add_argument("--startup-wait", type=float, default=20.0)
    parser.add_argument("--close-timeout", type=float, default=20.0)
    args = parser.parse_args(argv)
    exe = Path(args.exe).resolve()
    if not exe.is_file():
        print(f"FAIL missing launcher: {exe}")
        return 2

    child_env = os.environ.copy()
    child_env[SMOKE_ENV] = "1"
    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=child_env)
    try:
        main_window = wait_for_window(proc, MAIN_TITLE, args.startup_wait)
        if not main_window:
            print(f"FAIL no visible main window: {describe(proc.pid)}")
            return 1

        manager_window = wait_for_window(proc, MANAGER_TITLE, args.startup_wait)
        if not manager_window:
            error_log = exe.parent / "output" / "engine_manager_error.log"
            if error_log.is_file():
                print(error_log.read_text(encoding="utf-8", errors="replace").strip())
            print(f"FAIL no visible engine manager: {describe(proc.pid)}")
            return 1

        print(f"PASS packaged engine manager opened: {manager_window}")
        close_window(manager_window)
        time.sleep(0.4)
        close_window(main_window)

        deadline = time.monotonic() + args.close_timeout
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        if proc.poll() is None:
            print(f"FAIL packaged app did not close after manager check: {describe(proc.pid)}")
            return 1
        if proc.returncode != 0:
            print(f"FAIL packaged app exited with code {proc.returncode}")
            return 1
        print("PASS packaged app closed cleanly")
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
