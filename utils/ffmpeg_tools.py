from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from threading import Event
from typing import Callable


CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def subprocess_kwargs(capture: bool = True, below_normal: bool = False) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "text": True,
        "encoding": "utf-8",
        "errors": "ignore",
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT
    else:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    if sys.platform == "win32":
        flags = CREATE_NO_WINDOW
        if below_normal:
            flags |= BELOW_NORMAL_PRIORITY_CLASS
        kwargs["creationflags"] = flags
    return kwargs


def safe_run(cmd: list[str], timeout: int | None = None, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, timeout=timeout, **subprocess_kwargs(capture=capture))


def safe_popen(cmd: list[str], capture: bool = True, below_normal: bool = True, **kwargs: object) -> subprocess.Popen[str]:
    popen_kwargs = subprocess_kwargs(capture=capture, below_normal=below_normal)
    popen_kwargs.update(kwargs)
    return subprocess.Popen(cmd, **popen_kwargs)


def ffmpeg_version_line() -> str:
    result = safe_run(["ffmpeg", "-version"], timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg -version failed with code {result.returncode}")
    return (result.stdout or "").splitlines()[0] if result.stdout else "ffmpeg responded without output"


def probe_media_duration(media_path: str) -> float:
    result = safe_run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(Path(media_path)),
        ],
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed with code {result.returncode}")
    output = (result.stdout or "").strip()
    return float(output) if output else 0.0


def describe_media(media_path: str) -> str:
    path = Path(media_path)
    size_mb = path.stat().st_size / (1024 * 1024)
    duration = probe_media_duration(str(path))
    minutes = int(duration // 60)
    seconds = int(duration % 60)
    return f"{path.name} | {size_mb:.2f} MB | {minutes:02d}:{seconds:02d}"


def ensure_path_exists(path: str) -> None:
    if not path or not os.path.exists(path):
        raise FileNotFoundError(path or "<empty path>")


class FFmpegAudioProcessor:
    """Audio extraction, chunking, and concatenation helpers for the Next worker."""

    @staticmethod
    def extract_audio(video_path: str, output_audio_path: str) -> str:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            output_audio_path,
        ]
        result = safe_run(cmd, capture=True)
        if result.returncode != 0 or not os.path.exists(output_audio_path):
            raise RuntimeError(f"FFmpeg audio extraction failed with code {result.returncode}: {(result.stdout or '')[-1200:]}")
        return output_audio_path

    @staticmethod
    def segment_audio(audio_path: str, chunks_dir: str, chunk_length_sec: int) -> list[str]:
        os.makedirs(chunks_dir, exist_ok=True)
        chunk_pattern = str(Path(chunks_dir) / "chunk_%03d.wav")
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            audio_path,
            "-f",
            "segment",
            "-segment_time",
            str(chunk_length_sec),
            "-c",
            "copy",
            chunk_pattern,
        ]
        result = safe_run(cmd, capture=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg audio segment failed with code {result.returncode}: {(result.stdout or '')[-1200:]}")
        chunks = [str(path) for path in Path(chunks_dir).glob("chunk_*.wav")]
        return sorted(chunks)

    @staticmethod
    def concat_audio(audio_files: list[str], output_path: str, temp_txt_dir: str) -> str:
        if not audio_files:
            raise ValueError("No audio files to concatenate.")

        concat_txt_path = Path(temp_txt_dir) / "concat_list.txt"
        with concat_txt_path.open("w", encoding="utf-8") as file:
            for audio_file in audio_files:
                safe_path = audio_file.replace("\\", "/").replace("'", "'\\''")
                file.write(f"file '{safe_path}'\n")

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_txt_path),
            "-c",
            "copy",
            output_path,
        ]
        result = safe_run(cmd, capture=True)
        if result.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(f"FFmpeg audio concat failed with code {result.returncode}: {(result.stdout or '')[-1200:]}")
        return output_path


class FFmpegSubtitleBurner:
    """Burn ASS subtitles into a video using a low-priority FFmpeg process."""

    ENCODERS = [
        ("NVIDIA NVENC", "h264_nvenc", ["-preset", "fast", "-pix_fmt", "yuv420p", "-cq", "22"]),
        ("CPU x264 fallback", "libx264", ["-preset", "superfast", "-crf", "22", "-threads", "4"]),
    ]

    @staticmethod
    def burn(
        media_path: str,
        ass_path: str,
        output_dir: str,
        hires_enabled: bool = False,
        cancel_event: Event | None = None,
        progress_cb: Callable[[float, str], None] | None = None,
        log_cb: Callable[[str], None] | None = None,
    ) -> str:
        media = Path(media_path)
        ass = Path(ass_path)
        target_dir = Path(output_dir)
        if not media.exists():
            raise FileNotFoundError(f"Media file not found: {media}")
        if not ass.exists():
            raise FileNotFoundError(f"ASS subtitle not found: {ass}")

        target_dir.mkdir(parents=True, exist_ok=True)
        base_name = media.stem
        output_ext = ".mkv" if hires_enabled else ".mp4"
        output_path = target_dir / f"{base_name}_硬字幕成品{output_ext}"
        temp_ass = target_dir / "_utasyncnext_burn.ass"
        shutil.copy2(ass, temp_ass)

        audio_args = ["-c:a", "flac", "-sample_fmt", "s32", "-ar", "48000"] if hires_enabled else ["-c:a", "aac", "-b:a", "320k"]
        last_error = ""
        start_time = time.monotonic()
        try:
            for encoder_name, codec, encoder_args in FFmpegSubtitleBurner.ENCODERS:
                if cancel_event and cancel_event.is_set():
                    raise RuntimeError("用户取消了视频压制。")

                if log_cb:
                    log_cb(f"🎬 [压制] 正在尝试 {encoder_name}...\n")

                cmd = [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-i",
                    str(media),
                    "-vf",
                    "scale=trunc(iw/2)*2:trunc(ih/2)*2,subtitles=_utasyncnext_burn.ass",
                    "-c:v",
                    codec,
                    *encoder_args,
                    *audio_args,
                    str(output_path),
                ]
                process = safe_popen(cmd, cwd=str(target_dir), capture=True, below_normal=True)
                total_seconds = 0.0
                last_percent = -1

                assert process.stdout is not None
                for line in process.stdout:
                    if cancel_event and cancel_event.is_set():
                        process.kill()
                        process.wait(timeout=5)
                        raise RuntimeError("用户取消了视频压制。")

                    if not total_seconds:
                        duration = _parse_ffmpeg_time(line, "Duration:")
                        if duration:
                            total_seconds = duration

                    current = _parse_ffmpeg_time(line, "time=")
                    if total_seconds and current:
                        ratio = max(0.0, min(current / total_seconds, 0.999))
                        percent = int(ratio * 100)
                        if percent != last_percent:
                            last_percent = percent
                            if progress_cb:
                                progress_cb(ratio, f"🎬 视频压制中... {percent}%")

                    if "Error" in line or "failed" in line.lower():
                        last_error = line.strip()

                return_code = process.wait()
                if return_code == 0 and output_path.exists() and output_path.stat().st_size > 0:
                    elapsed = int(time.monotonic() - start_time)
                    if progress_cb:
                        progress_cb(1.0, "🎬 视频压制完成")
                    if log_cb:
                        log_cb(f"✅ [压制完成] {encoder_name} 成功，用时 {elapsed}s：{output_path.name}\n")
                    return str(output_path.resolve())

                if log_cb:
                    log_cb(f"⚠️ [压制] {encoder_name} 不可用，准备回退。{last_error}\n")

            raise RuntimeError(f"所有 FFmpeg 编码器均失败。{last_error}")
        finally:
            try:
                temp_ass.unlink(missing_ok=True)
            except Exception:
                pass


def _parse_ffmpeg_time(line: str, label: str) -> float | None:
    pattern = rf"{re.escape(label)}\s*(\d{{2}}):(\d{{2}}):(\d{{2}}(?:\.\d+)?)"
    match = re.search(pattern, line)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
