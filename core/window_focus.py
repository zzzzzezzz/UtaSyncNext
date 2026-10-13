"""Surface the window of an already-running UtaSyncNext instance.

The launcher refuses to start twice, so a user who clicks the shortcut again
while the app is alive has to land on the window that is already open. Windows
does not do that for us: the second process does not own that window, and
``SetForegroundWindow`` is restricted unless the caller owns the foreground
window. Attaching the calling thread to the foreground thread's input queue is
the documented way around that restriction, which is what
:func:`focus_window_by_title` does.

When activation is still refused - another desktop, a locked workstation, a
process in a different session - the window is pinned on top for a moment and
its taskbar button is flashed, so a launch never looks like it did nothing.
"""

from __future__ import annotations

import os


WINDOW_TITLE = "UtaSyncNext"

_SW_SHOW = 5
_SW_RESTORE = 9
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_SHOWWINDOW = 0x0040
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_FLASHW_ALL = 0x00000003
_FLASHW_TIMERNOFG = 0x0000000C


def focus_window_by_title(title: str = WINDOW_TITLE) -> bool:
    """Restore and foreground the first visible top-level window named *title*.

    Returns ``False`` on non-Windows hosts, when no matching window exists, and
    when the Win32 calls fail, so callers can fall back to a plain message.
    """
    if os.name != "nt":
        return False
    try:
        hwnd = _find_window(title)
        if hwnd is None:
            return False
        return _raise_window(hwnd)
    except Exception:
        # A missing window, a denied Win32 call, and a stripped-down host all
        # mean the same thing to the caller: nothing was brought forward.
        return False


def _find_window(title: str) -> int | None:
    import ctypes
    from ctypes import wintypes

    user32, _kernel32 = _win32()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    matches: list[int] = []

    def _visit(hwnd, _lparam) -> bool:
        if not matches and user32.IsWindowVisible(hwnd) and _window_title(user32, hwnd) == title:
            matches.append(int(hwnd))
        return True

    user32.EnumWindows(callback_type(_visit), 0)
    return matches[0] if matches else None


def _window_title(user32, hwnd) -> str:
    import ctypes

    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _raise_window(hwnd: int) -> bool:
    user32, kernel32 = _win32()
    user32.ShowWindow(hwnd, _SW_RESTORE if user32.IsIconic(hwnd) else _SW_SHOW)

    current_thread = kernel32.GetCurrentThreadId()
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = False
    if foreground_thread and foreground_thread != current_thread:
        attached = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
    try:
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        if attached:
            user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current_thread, foreground_thread, False)

    if user32.GetForegroundWindow() == hwnd:
        return True
    # Windows refused the activation. Show the window anyway and flash its
    # taskbar button rather than returning silently.
    user32.SetWindowPos(
        hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE | _SWP_SHOWWINDOW
    )
    user32.SetWindowPos(hwnd, _HWND_NOTOPMOST, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE)
    _flash_taskbar(user32, hwnd)
    return bool(user32.IsWindow(hwnd)) and bool(user32.IsWindowVisible(hwnd))


def _flash_taskbar(user32, hwnd: int) -> None:
    import ctypes
    from ctypes import wintypes

    class _FlashInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    info = _FlashInfo(ctypes.sizeof(_FlashInfo), hwnd, _FLASHW_ALL | _FLASHW_TIMERNOFG, 0, 0)
    user32.FlashWindowEx(ctypes.byref(info))


def _win32():
    """Return ``user32``/``kernel32`` with the argument types declared."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.EnumWindows.argtypes = [
        ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
        wintypes.LPARAM,
    ]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.FlashWindowEx.argtypes = [ctypes.c_void_p]
    user32.FlashWindowEx.restype = wintypes.BOOL
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.SetFocus.restype = wintypes.HWND
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    return user32, kernel32