"""Render a single ASS subtitle frame for the lightweight style workbench."""

from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from core.ass_maker import ASSMaker
from engine.cache_env import app_root
from utils.ffmpeg_tools import _escape_filter_path, inspect_ffmpeg, subprocess_kwargs

# Size of the frame handed to the workbench preview card.
PREVIEW_WIDTH = 640
PREVIEW_HEIGHT = 360
# ASS size units are relative to PlayResY, so the style must be rendered against
# the export resolution and only then downscaled for display.
RENDER_WIDTH = 1920
RENDER_HEIGHT = 1080


def render_ass_preview_frame(
    config: dict[str, Any],
    japanese: str,
    chinese: str,
    *,
    output_size: tuple[int, int] | None = None,
) -> Image.Image:
    """Use the same ASSMaker and libass path as export, on a neutral frame.

    The temporary directory is scoped to the render, never the user's output.
    The sample is deliberately Japanese-first, matching the ASSMaker's SRT input.

    ``output_size`` only changes the pixel size of the returned frame. The ASS
    canvas always stays at the resolved export resolution because font sizes are
    relative to PlayResY; a preview window asks for the export size so the frame
    can be zoomed to 1:1, while the small workbench card keeps the cheap default.
    """
    ffmpeg = inspect_ffmpeg().get("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("未检测到 FFmpeg，无法显示真实 ASS 预览")
    frame_width, frame_height = output_size or (PREVIEW_WIDTH, PREVIEW_HEIGHT)

    with tempfile.TemporaryDirectory(prefix="utasync-ass-preview-") as tmp:
        temp_root = Path(tmp)
        source = temp_root / "sample.srt"
        source.write_text(
            "1\n00:00:00,000 --> 00:00:03,000\n"
            + japanese.replace("\n", " ") + "\n"
            + chinese.replace("\n", " ") + "\n",
            encoding="utf-8",
        )
        preview_config = dict(config)
        # A static style frame should not disappear inside the fade-in interval;
        # animation timing is checked separately during the video burn flow.
        preview_config["fade_mode"] = "硬切"
        # Keep the ASS PlayRes at the real export resolution. Font sizes in ASS
        # are relative to PlayResY, so shrinking the canvas inflates every font
        # and makes long lines look like they overflow while the exported video
        # is fine. The finished frame is downscaled for the preview card instead.
        preview_config["resolution_x"] = int(preview_config.get("resolution_x") or RENDER_WIDTH)
        preview_config["resolution_y"] = int(preview_config.get("resolution_y") or RENDER_HEIGHT)
        ass_path = Path(ASSMaker(str(source)).generate_ass(preview_config))
        fonts_dir = app_root() / "fonts"
        fonts_filter = f":fontsdir={_escape_filter_path(fonts_dir)}" if fonts_dir.is_dir() else ""
        result = subprocess.run(
            [
                str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i",
                f"color=c=0x050505:s={preview_config['resolution_x']}x{preview_config['resolution_y']}:r=1:d=3",
                "-vf",
                f"subtitles={ass_path.name}{fonts_filter}"
                f",scale={int(frame_width)}:{int(frame_height)}:flags=lanczos",
                "-ss", "1.0", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-",
            ],
            cwd=temp_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            creationflags=subprocess_kwargs(capture=False).get("creationflags", 0),
        )
        if result.returncode or not result.stdout:
            detail = result.stderr.decode("utf-8", errors="replace")[-400:]
            raise RuntimeError(f"ASS 渲染失败：{detail or result.returncode}")
        with Image.open(io.BytesIO(result.stdout)) as frame:
            return frame.convert("RGB")
