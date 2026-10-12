"""Capture the real experimental sidebar, settings, and gallery windows."""

from __future__ import annotations

import os
from pathlib import Path

from ui_task_state_demo import MainWindow, capture_window


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    os.environ["UTASYNC_UI_VARIANT"] = "next"
    output_dir = Path(
        os.environ.get(
            "UTASYNC_DEMO_CAPTURE_DIR",
            str(ROOT / "test_logs" / "ui_experimental_preview"),
        )
    ).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app = MainWindow()
    app.save_current_settings = lambda settings=None: None
    app._set_media_path(r"C:\Demo\majiko_live.mp4")

    def capture_main() -> None:
        app.update_idletasks()
        capture_window(app.winfo_id(), output_dir / "main.png")

    def open_advanced() -> None:
        app.sidebar.open_advanced_settings()

    def capture_advanced() -> None:
        dialog = app.sidebar._advanced_dialog
        if dialog is None or not dialog.winfo_exists():
            raise RuntimeError("advanced settings dialog did not open")
        dialog.update_idletasks()
        dialog.update()
        capture_window(dialog.winfo_id(), output_dir / "advanced.png")

    def open_gallery() -> None:
        dialog = app.sidebar._advanced_dialog
        if dialog is None or not dialog.winfo_exists():
            raise RuntimeError("advanced settings dialog did not open")
        dialog.visual_settings_panel.open_style_gallery()

    def capture_gallery() -> None:
        from ui.gallery import StyleGalleryWindow

        gallery = StyleGalleryWindow._instance
        if gallery is None or not gallery.winfo_exists():
            raise RuntimeError("style gallery did not open")
        gallery.update_idletasks()
        capture_window(gallery.winfo_id(), output_dir / "gallery.png")

    def expand_gallery_editor() -> None:
        from ui.gallery import StyleGalleryWindow

        gallery = StyleGalleryWindow._instance
        if gallery is not None and gallery.winfo_exists():
            gallery._toggle_editor()

    def capture_gallery_editor() -> None:
        from ui.gallery import StyleGalleryWindow

        gallery = StyleGalleryWindow._instance
        if gallery is None or not gallery.winfo_exists():
            raise RuntimeError("style gallery did not open")
        gallery.update_idletasks()
        capture_window(gallery.winfo_id(), output_dir / "gallery_editor.png")

    def finish() -> None:
        from ui.gallery import StyleGalleryWindow

        gallery = StyleGalleryWindow._instance
        if gallery is not None and gallery.winfo_exists():
            gallery.destroy()
        dialog = app.sidebar._advanced_dialog
        if dialog is not None and dialog.winfo_exists():
            dialog.close()
        app.destroy()

    app.after(700, capture_main)
    app.after(1100, open_advanced)
    app.after(2200, capture_advanced)
    app.after(2400, open_gallery)
    # ASS/libass preview is rendered through the same FFmpeg path as export;
    # leave enough time for the frame to arrive before capturing it.
    app.after(6500, capture_gallery)
    app.after(7600, expand_gallery_editor)
    app.after(8500, capture_gallery_editor)
    app.after(10500, finish)
    app.mainloop()


if __name__ == "__main__":
    main()
