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


def render_ass_preview_frame(config: dict[str, Any], japanese: str, chinese: str) -> Image.Image:
    """Use the same ASSMaker and libass path as export, on a neutral 16:9 frame.

    The temporary directory is scoped to the render, never the user's output.
    The sample is deliberately Japanese-first, matching the ASSMaker's SRT input.
    """
    ffmpeg = inspect_ffmpeg().get("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("未检测到 FFmpeg，无法显示真实 ASS 预览")

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
        # Match the compact workbench canvas so the ASS text stays legible when
        # the 16:9 frame is fitted into the preview card.
        preview_config["resolution_x"] = 640
        preview_config["resolution_y"] = 360
        ass_path = Path(ASSMaker(str(source)).generate_ass(preview_config))
        fonts_dir = app_root() / "fonts"
        fonts_filter = f":fontsdir={_escape_filter_path(fonts_dir)}" if fonts_dir.is_dir() else ""
        result = subprocess.run(
            [
                str(ffmpeg), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "color=c=0x050505:s=640x360:r=1:d=3",
                "-vf", f"subtitles={ass_path.name}{fonts_filter}",
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
