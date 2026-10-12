from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from tkinter import PhotoImage

from engine.cache_env import app_root


def apply_child_window_icon(window: object, owner: object | None = None) -> PhotoImage | None:
    """Apply the UtaSync icon after CustomTkinter's delayed default-icon pass."""
    root = app_root()
    frozen_root = getattr(sys, "_MEIPASS", None)
    roots = ([Path(str(frozen_root))] if frozen_root else []) + [root]
    ico_candidates = tuple(path / "assets" / "utasync.ico" for path in roots) + (root / "packaging" / "assets" / "utasync.ico",)
    png_candidates = tuple(path / "assets" / "utasync.png" for path in roots) + (root / "packaging" / "assets" / "utasync.png",)

    ico_path = next((path for path in ico_candidates if path.is_file()), None)

    photo = _owner_photo(owner)
    if photo is None:
        png_path = next((path for path in png_candidates if path.is_file()), None)
        if png_path is not None:
            try:
                photo = PhotoImage(file=str(png_path))
            except Exception:
                photo = None
    if photo is not None:
        setattr(window, "_utasync_icon_photo", photo)

    def apply_now() -> None:
        try:
            if not window.winfo_exists():
                return
        except Exception:
            return
        apply_dark_titlebar(window)
        if ico_path is not None:
            try:
                window.iconbitmap(str(ico_path))
                setattr(window, "_iconbitmap_method_called", True)
            except Exception:
                pass
        # A multi-resolution ICO is sharper in the 16px title-bar slot than
        # resizing the 256px PNG through Tk. Keep PNG only as a fallback for
        # source layouts that do not ship an ICO.
        if photo is not None and ico_path is None:
            try:
                window.iconphoto(False, photo)
            except Exception:
                pass

    apply_now()
    # CTkToplevel installs its blue default icon about 200 ms after creation.
    # Reapply beyond that point and once more after the window is mapped.
    for delay in (80, 280, 650):
        try:
            window.after(delay, apply_now)
        except Exception:
            break
    return photo


def apply_dark_titlebar(window: object) -> None:
    """Make the native Windows caption match the dark UtaSync workspace."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winfo_id())
        if not hwnd:
            return
        # Tk reports a child HWND for CTkToplevel. DWM caption attributes
        # must be applied to its real top-level parent window.
        parent_hwnd = int(ctypes.windll.user32.GetParent(hwnd))
        hwnd = parent_hwnd or hwnd
        dwmapi = ctypes.windll.dwmapi
        value = ctypes.c_int(1)
        # Windows 11 uses 20; Windows 10 builds use 19.
        for attribute in (20, 19):
            try:
                dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
            except Exception:
                pass
        # DWMWA_CAPTION_COLOR / DWMWA_TEXT_COLOR use COLORREF (BGR), not RGB.
        caption = ctypes.c_int(_colorref("#111722"))
        text = ctypes.c_int(_colorref("#E5E7EB"))
        for attribute, color in ((35, caption), (36, text)):
            try:
                dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(color), ctypes.sizeof(color))
            except Exception:
                pass
    except Exception:
        # Native caption styling is an enhancement; Tk remains fully usable if unavailable.
        return


def _colorref(value: str) -> int:
    raw = value.lstrip("#")
    red = int(raw[0:2], 16)
    green = int(raw[2:4], 16)
    blue = int(raw[4:6], 16)
    return red | (green << 8) | (blue << 16)


def _owner_photo(owner: object | None) -> PhotoImage | None:
    if owner is None:
        return None
    candidates = [owner]
    try:
        candidates.append(owner.winfo_toplevel())
    except Exception:
        pass
    for candidate in candidates:
        for name in ("_icon_photo", "_utasync_icon_photo"):
            photo = getattr(candidate, name, None)
            if photo is not None:
                return photo
    return None
