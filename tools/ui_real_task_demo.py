"""Record a real local UtaSyncNext worker run for UI review.

This is intentionally offline: it uses an existing project test clip, the
locally installed large-v2 model, skips Demucs, and leaves translation API
fields empty. The window and TaskController are the real application objects.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ui.main_window as main_window_module  # noqa: E402
from tools.ui_task_state_demo import capture_window  # noqa: E402


# Never load private user_settings.json while recording a review clip.
main_window_module.load_settings = lambda: {}
MainWindow = main_window_module.MainWindow

SOURCE_CLIP = ROOT / "test_workspace" / "lyric_alignment_matrix_v4" / "majiko-kokoronashi-preserve-negative" / "source_window.wav"


def main() -> None:
    capture_root = Path(os.environ.get("UTASYNC_REAL_DEMO_CAPTURE_DIR", "")).resolve()
    if not capture_root:
        raise SystemExit("UTASYNC_REAL_DEMO_CAPTURE_DIR is required")
    capture_root.mkdir(parents=True, exist_ok=True)
    for frame in capture_root.glob("frame_*.png"):
        frame.unlink(missing_ok=True)

    input_dir = capture_root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    media_path = input_dir / "majiko_real_task_demo_short_v2.wav"
    shutil.copyfile(SOURCE_CLIP, media_path)

    app = MainWindow()
    app.save_current_settings = lambda settings=None: None
    app._set_media_path(str(media_path))
    app.workspace.update_lyrics("")
    app.sidebar.basic_settings_panel.opt_model.set("small")

    payload = {
        "media_path": str(media_path),
        "lyrics": "",
        "settings": {
            "audio_type": "Live 现场演唱 (常规/摇滚)",
            "model_size": "small",
            "hardware_mode": "自动匹配显存 (推荐)",
            "use_demucs": "先跳过 Demucs (极速/推荐先出轴)",
            "output_mode": "纯中文字幕",
            "ass_preset": "不生成 (仅SRT)",
            "api_key": "",
            "api_url": "",
            "api_model": "",
            "proxy_url": "",
        },
    }
    app.collect_task_payload = lambda: payload

    frame_index = 0
    started = False
    finish_waits = 0

    def capture() -> None:
        nonlocal frame_index, finish_waits
        app.update_idletasks()
        frame_index += 1
        capture_window(app.winfo_id(), capture_root / f"frame_{frame_index:04d}.png")
        if app.controller.is_running:
            app.after(500, capture)
            return
        if not started:
            app.after(500, capture)
            return
        finish_waits += 1
        if finish_waits < 8:
            app.after(500, capture)
        else:
            app.after(800, app.destroy)

    def start_real_task() -> None:
        nonlocal started
        started = True
        app.controller.start_pipeline_task()

    app.after(300, capture)
    app.after(1200, start_real_task)
    app.mainloop()
    print(f"frames={frame_index}")
    print(f"media={media_path}")
    print(f"output_root={ROOT / 'output' / media_path.stem}")


if __name__ == "__main__":
    main()
