from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from engine.cache_env import task_base_name


CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def _escape_filter_path(path: Path) -> str:
    """Escape a Windows path for FFmpeg's colon-separated filter options."""
    escaped = str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    return f"'{escaped}'"


def inspect_ffmpeg() -> dict[str, str | None]:
    """Return the external FFmpeg tools visible to the current process.

    This intentionally uses only ``PATH`` lookup.  It is safe to call from
    the lightweight UI preview because it does not start FFmpeg or import any
    engine package.
    """
    return {
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    }


def ffmpeg_status_text(paths: dict[str, str | None] | None = None) -> str:
    """Format a user-facing readiness message for FFmpeg and FFprobe."""
    paths = paths or inspect_ffmpeg()
    missing = [name for name in ("ffmpeg", "ffprobe") if not paths.get(name)]
    if missing:
        return f"缺少 {', '.join(missing)}（需要安装 FFmpeg 并加入 PATH）"
    return "ffmpeg 与 ffprobe 已就绪"


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
    def extract_audio_window(
        audio_path: str,
        output_audio_path: str,
        start_seconds: float,
        end_seconds: float,
    ) -> str:
        if start_seconds < 0 or end_seconds <= start_seconds:
            raise ValueError("Invalid audio window.")
        Path(output_audio_path).parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-to",
            f"{end_seconds:.3f}",
            "-i",
            audio_path,
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
            raise RuntimeError(
                "FFmpeg audio window extraction failed with code "
                f"{result.returncode}: {(result.stdout or '')[-1200:]}"
            )
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

    # 候选取自 V3 的做法，按顺序尝试，失败就换下一个；最后一项是 CPU 软压，几乎任何环境都可用。
    # VideoToolbox 是 macOS 专用，本应用只发布 Windows，因此不列入。
    # QSV 的编码器只接受 nv12/qsv 像素格式，必须显式给 -pix_fmt nv12。
    ENCODERS = [
        ("NVIDIA 显卡", "h264_nvenc", ["-preset", "fast", "-pix_fmt", "yuv420p", "-cq", "22"]),
        ("Intel 核显", "h264_qsv", ["-preset", "fast", "-global_quality", "22", "-pix_fmt", "nv12"]),
        ("AMD 显卡", "h264_amf", ["-quality", "speed", "-rc", "cqp", "-qp_i", "22", "-qp_p", "22", "-qp_b", "22", "-pix_fmt", "yuv420p"]),
        ("CPU 兼容模式", "libx264", ["-preset", "superfast", "-crf", "22", "-threads", "4"]),
    ]

    @staticmethod
    def burn(
        media_path: str,
        ass_path: str,
        output_dir: str,
        hires_enabled: bool = False,
        fonts_dir: str | None = None,
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
        base_name = task_base_name(media)
        output_ext = ".mkv" if hires_enabled else ".mp4"
        output_path = target_dir / f"{base_name}_硬字幕成品{output_ext}"
        work_output = target_dir / f"{base_name}_硬字幕成品_处理中{output_ext}"
        temp_ass = target_dir / "_utasyncnext_burn.ass"
        shutil.copy2(ass, temp_ass)
        fonts_root = Path(fonts_dir).resolve() if fonts_dir else None
        fonts_filter = ""
        if fonts_root and fonts_root.is_dir():
            fonts_filter = f":fontsdir={_escape_filter_path(fonts_root)}"

        audio_args = ["-c:a", "flac", "-sample_fmt", "s32", "-ar", "48000"] if hires_enabled else ["-c:a", "aac", "-b:a", "320k"]
        expected_duration = probe_media_duration(str(media))
        expect_audio = _media_has_stream(media, "audio")
        start_time = time.monotonic()
        try:
            for index, (encoder_name, codec, encoder_args) in enumerate(FFmpegSubtitleBurner.ENCODERS):
                if cancel_event and cancel_event.is_set():
                    raise RuntimeError("用户取消了视频压制。")

                work_output.unlink(missing_ok=True)
                if log_cb:
                    log_cb(f"[压制] 正在使用{encoder_name}处理视频。\n")

                cmd = [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-i",
                    str(media),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a?",
                    "-sn",
                    "-dn",
                    "-vf",
                    f"scale=trunc(iw/2)*2:trunc(ih/2)*2,subtitles=_utasyncnext_burn.ass{fonts_filter}",
                    "-c:v",
                    codec,
                    *encoder_args,
                    *audio_args,
                    *(["-movflags", "+faststart"] if not hires_enabled else []),
                    str(work_output),
                ]
                process = safe_popen(cmd, cwd=str(target_dir), capture=True, below_normal=True)
                total_seconds = 0.0
                last_percent = -1
                output_tail: list[str] = []

                assert process.stdout is not None
                line_queue: queue.Queue[str | None] = queue.Queue()

                def read_process_output() -> None:
                    try:
                        for output_line in process.stdout:
                            line_queue.put(output_line)
                    finally:
                        line_queue.put(None)

                reader = Thread(target=read_process_output, daemon=True)
                reader.start()
                reader_finished = False
                while process.poll() is None or not reader_finished or not line_queue.empty():
                    if cancel_event and cancel_event.is_set() and process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                        reader.join(timeout=1)
                        work_output.unlink(missing_ok=True)
                        raise RuntimeError("用户取消了视频压制。")
                    try:
                        line = line_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if line is None:
                        reader_finished = True
                        continue
                    clean_line = line.strip()
                    if clean_line:
                        output_tail.append(clean_line)
                        if len(output_tail) > 40:
                            output_tail.pop(0)

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
                                progress_cb(ratio, f"视频压制中... {percent}%")

                reader.join(timeout=1)
                return_code = process.wait()
                last_error = ""
                if return_code == 0:
                    try:
                        _validate_burn_output(work_output, expected_duration, expect_audio)
                    except Exception as exc:
                        last_error = str(exc)
                    else:
                        os.replace(work_output, output_path)
                        elapsed = int(time.monotonic() - start_time)
                        if progress_cb:
                            progress_cb(1.0, "视频压制完成")
                        if log_cb:
                            log_cb(f"[压制完成] 已使用{encoder_name}生成成品，用时 {elapsed} 秒：{output_path.name}\n")
                        return str(output_path.resolve())

                if not last_error:
                    last_error = _summarize_ffmpeg_failure(output_tail, return_code)
                work_output.unlink(missing_ok=True)
                if log_cb:
                    if index == len(FFmpegSubtitleBurner.ENCODERS) - 1:
                        log_cb(f"[压制失败] {encoder_name}也未成功。原因：{last_error}\n")
                    else:
                        log_cb(f"[压制回退] {encoder_name}未成功，正在尝试下一个编码器。原因：{last_error}\n")

            raise RuntimeError("视频压制失败，所有候选编码器都未成功。请导出错误日志后检查 FFmpeg 环境。")
        finally:
            try:
                work_output.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                temp_ass.unlink(missing_ok=True)
            except Exception:
                pass


def _media_has_stream(media_path: Path, stream_type: str) -> bool:
    result = safe_run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            f"{stream_type[0]}:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(media_path),
        ],
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError("读取源视频轨道失败，无法开始压制。")
    try:
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except json.JSONDecodeError as exc:
        raise RuntimeError("读取源视频轨道失败，FFprobe 返回了无效结果。") from exc
    return any(str(stream.get("codec_type") or "") == stream_type for stream in streams)


def _validate_burn_output(output_path: Path, expected_duration: float, expect_audio: bool) -> None:
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("FFmpeg 没有生成可用的视频文件。")
    result = safe_run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(output_path),
        ],
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("成品文件无法读取，可能是不完整的半成品。")
    try:
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams", [])
        actual_duration = float(payload.get("format", {}).get("duration") or 0.0)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError("成品文件信息不完整，无法确认压制成功。") from exc
    stream_types = {str(stream.get("codec_type") or "") for stream in streams}
    if "video" not in stream_types:
        raise RuntimeError("成品缺少视频画面。")
    if expect_audio and "audio" not in stream_types:
        raise RuntimeError("成品缺少原视频声音。")
    duration_tolerance = max(2.0, expected_duration * 0.02)
    if expected_duration > 0 and abs(actual_duration - expected_duration) > duration_tolerance:
        raise RuntimeError(
            f"成品时长异常：源视频 {expected_duration:.2f} 秒，成品 {actual_duration:.2f} 秒。"
        )


def _summarize_ffmpeg_failure(lines: list[str], return_code: int) -> str:
    for line in reversed(lines):
        lowered = line.lower()
        if "error" in lowered or "failed" in lowered or "cannot" in lowered:
            return line[-500:]
    return f"FFmpeg 返回码 {return_code}。"


def _parse_ffmpeg_time(line: str, label: str) -> float | None:
    pattern = rf"{re.escape(label)}\s*(\d{{2}}):(\d{{2}}):(\d{{2}}(?:\.\d+)?)"
    match = re.search(pattern, line)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
