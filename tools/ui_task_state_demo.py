"""Show the task-center lifecycle without loading a media file or model."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

from PIL import Image
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ui.main_window as main_window_module  # noqa: E402

# Do not load the user's private settings, lyrics, or previous output paths in
# a screen-recording demo. The UI still uses the real MainWindow implementation.
main_window_module.load_settings = lambda: {}
MainWindow = main_window_module.MainWindow


class _Rect(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]


def capture_window(hwnd: int, target: Path) -> None:
    """Capture the real Tk window with PrintWindow when desktop capture is unavailable."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    rect = _Rect()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetWindowRect failed")
    width = max(1, rect.right - rect.left)
    height = max(1, rect.bottom - rect.top)
    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, 2):
            raise OSError("PrintWindow failed")
        info = _BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        copied = gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
        if copied != height:
            raise OSError("GetDIBits failed")
        image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def main() -> None:
    app = MainWindow()
    # Keep this demo isolated from the user's private settings and files.
    app.save_current_settings = lambda settings=None: None
    app._set_media_path(r"C:\Demo\majiko_live.mp4")
    capture_root = Path(os.environ.get("UTASYNC_DEMO_CAPTURE_DIR", "")).resolve() if os.environ.get("UTASYNC_DEMO_CAPTURE_DIR") else None
    capture_index = 0

    def capture() -> None:
        nonlocal capture_index
        if capture_root is None:
            return
        capture_index += 1
        app.update_idletasks()
        capture_window(app.winfo_id(), capture_root / f"frame_{capture_index:02d}.png")

    app.after(800, capture)
    app.after(1500, lambda: (app.mark_task_preflight(), capture()))
    app.after(3200, lambda: (app.set_running_state(True), capture()))
    app.after(4600, lambda: (app.update_status(0.28, "正在识别音频"), capture()))
    app.after(6000, lambda: (app.update_status(0.62, "正在生成双语字幕"), capture()))
    app.after(7600, lambda: (app.update_status(0.91, "正在写入输出文件"), capture()))
    app.after(9200, lambda: (app.mark_task_finished("任务完成，字幕已生成。", True), capture()))
    app.after(11500, app.destroy)
    app.mainloop()


if __name__ == "__main__":
    main()
