r"""Check that the packaged launcher starts, shows its window, and closes.

Run this after every launcher build:

    .\.venv\Scripts\python.exe tools\probe_launcher_close.py

The probe launches dist\UtaSyncNext\UtaSyncNext.exe, waits for the window titled
`UtaSyncNext` to appear, posts WM_CLOSE to that exact window, and then waits
for the process to exit on its own.

Sending the message to the right window is the whole point. A PowerShell driver
that reads `System.Diagnostics.Process.MainWindowHandle` looks equivalent but
is not: on Windows 11 the first visible top-level window of the process is often
`UAC_InputIndicatorOverlayWnd` (the UAC input indicator overlay), so
`CloseMainWindow()` lands there, the real window never sees the message, and
the script reports a hang the application does not have. Enumerating windows by
title avoids that trap.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = NEXT_ROOT / "dist" / "UtaSyncNext" / "UtaSyncNext.exe"
WINDOW_TITLE = "UtaSyncNext"
WM_CLOSE = 0x0010

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [EnumProc, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL


def windows_for(pid: int) -> list[tuple[int, bool, str, str]]:
    """Return (hwnd, visible, class name, title) for every top-level window."""
    found: list[tuple[int, bool, str, str]] = []

    def visit(hwnd, _lparam):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            length = user32.GetWindowTextLengthW(hwnd)
            text = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, text, length + 1)
            klass = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, klass, 256)
            found.append((int(hwnd), bool(user32.IsWindowVisible(hwnd)), klass.value, text.value))
        return True

    user32.EnumWindows(EnumProc(visit), 0)
    return found


def find_app_window(pid: int) -> int | None:
    for hwnd, visible, _klass, title in windows_for(pid):
        if visible and title.strip() == WINDOW_TITLE:
            return hwnd
    return None


def describe(pid: int) -> str:
    rows = windows_for(pid)
    listed = ", ".join(f"{klass or '?'}:{title!r}" for _hwnd, _v, klass, title in rows)
    return f"{len(rows)} top-level windows [{listed}]"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exe", default=str(DEFAULT_EXE), help="packaged launcher to probe")
    parser.add_argument("--startup-wait", type=float, default=15.0, help="seconds to wait for the window")
    parser.add_argument("--close-timeout", type=float, default=30.0, help="seconds to wait for the exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    exe = Path(args.exe)
    if not exe.exists():
        print(f"FAIL missing launcher: {exe}")
        return 2

    log = exe.parent / "launcher_startup.log"
    try:
        log.unlink()
    except FileNotFoundError:
        pass

    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    print(f"launched pid={proc.pid} exe={exe}")

    deadline = time.time() + args.startup_wait
    hwnd = None
    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"FAIL the launcher exited during startup with code {proc.returncode}")
            break
        hwnd = find_app_window(proc.pid)
        if hwnd:
            break
        time.sleep(0.5)

    if hwnd is None:
        if proc.poll() is None:
            print(f"FAIL no visible window titled {WINDOW_TITLE!r} after {args.startup_wait}s: {describe(proc.pid)}")
            proc.kill()
            proc.wait(timeout=10)
        if log.exists():
            print(log.read_text(encoding="utf-8", errors="replace").strip())
        return 1

    print(f"window found hwnd={hwnd}; posting WM_CLOSE")
    if not user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
        print("FAIL could not post WM_CLOSE")
        proc.kill()
        proc.wait(timeout=10)
        return 1

    started = time.monotonic()
    deadline = started + args.close_timeout
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.25)
    elapsed = time.monotonic() - started

    if proc.poll() is None:
        print(f"FAIL still running {elapsed:.1f}s after the close request; {describe(proc.pid)}")
        proc.kill()
        proc.wait(timeout=10)
        return 1

    print(f"PASS closed in {elapsed:.1f}s with exit code {proc.returncode}")
    if proc.returncode != 0:
        print(f"FAIL expected exit code 0, got {proc.returncode}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
