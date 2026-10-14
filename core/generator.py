from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Callable

from core.asr_diagnostics import (
    print_diagnostic_summary,
    segment_to_record,
    suppression_reason_for_segment,
    write_asr_diagnostics,
)
from core.lyric_assisted_alignment import (
    match_expected_lyric_lines,
    render_aligned_lyrics_srt,
    segment_records,
    strict_alignment_rejection_reason,
)
from core.subtitle_policy import SubtitleLayoutPolicy, split_timed_subtitle_for_layout
from engine.cache_env import task_base_name
from utils.ffmpeg_tools import FFmpegAudioProcessor


ProgressCallback = Callable[[float, str], None]


# Keep Windows DLL directory handles alive for the lifetime of the worker.
# Without this, cublas/cudnn can disappear from the loader search path before
# CTranslate2 creates its CUDA backend.
_DLL_DIRECTORY_HANDLES: list[object] = []

os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


class LiveSubtitleGenerator:
    """Next-native audio preparation and Faster-Whisper timing generator."""

    def __init__(self, video_path: str, output_dir: str = "output") -> None:
        self.video_path = os.path.abspath(video_path)
        self.base_name = task_base_name(video_path)
        self.output_dir = os.path.abspath(os.path.join(output_dir, self.base_name))
        self.audio_dir = os.path.join(self.output_dir, "audio")
        self.subs_dir = os.path.join(self.output_dir, "subtitles")
        os.makedirs(self.audio_dir, exist_ok=True)
        os.makedirs(self.subs_dir, exist_ok=True)

        self.safe_name = hashlib.md5(self.base_name.encode("utf-8")).hexdigest()[:8]
        self.audio_path = os.path.join(self.audio_dir, f"{self.safe_name}_源音频.wav")
        self.final_vocal_path = os.path.join(self.audio_dir, f"{self.base_name}_纯净人声.wav")
        self.strategy_flag_path = os.path.join(self.audio_dir, "separation_strategy.txt")
        self.cache_manifest_path = os.path.join(self.audio_dir, "audio_cache_manifest.json")
        self._cache_media_fingerprint: dict[str, object] | None = None
        self.vocal_path = self.audio_path
        self.cancel_event = None
        self.model = None
        self.model_backend = "unloaded"
        self.cuda_fallback_used = False
        self.last_run_summary: dict[str, object] = {}

    def run(
        self,
        model_size: str = "large-v2",
        audio_type: str = "live",
        skip_separation: bool = False,
        hardware_mode: str = "low",
        cancel_event: object | None = None,
        progress_cb: ProgressCallback | None = None,
        layout_policy: SubtitleLayoutPolicy | None = None,
    ) -> str | None:
        self.cancel_event = cancel_event
        started = time.time()
        self.cuda_fallback_used = False
        self.last_run_summary = {
            "audio_cache_hit": False,
            "asr_backend": "unloaded",
            "asr_elapsed_seconds": 0,
            "audio_extract_elapsed_seconds": 0.0,
            "demucs_elapsed_seconds": 0.0,
            "model_load_elapsed_seconds": 0.0,
            "transcription_elapsed_seconds": 0.0,
            "cuda_fallback_used": False,
            "demucs_skipped": skip_separation,
            "subtitle_density_splits": 0,
            "subtitle_density_extra_cues": 0,
            "asr_review_candidates": 0,
            "asr_diagnostics_path": "",
            "asr_diagnostics_level": "",
        }

        try:
            self._raise_if_cancelled("任务尚未开始即被取消")
            strategy = "skip" if skip_separation else "demucs"
            media_fingerprint = self._ensure_audio_cache_identity()
            audio_cache_hit = self._can_reuse_vocal_cache(strategy, media_fingerprint)
            self.last_run_summary["audio_cache_hit"] = audio_cache_hit

            if audio_cache_hit:
                print("   -> ⚡ 检测到有效音频缓存，跳过提取与分离，直达打轴！")
                self.vocal_path = self._cache_path_for_strategy(strategy)
                if progress_cb:
                    progress_cb(0.40, "⚡ 跳过分离 (使用缓存)")
            else:
                stage_started = time.perf_counter()
                self.extract_audio(progress_cb=progress_cb)
                self.last_run_summary["audio_extract_elapsed_seconds"] = round(
                    time.perf_counter() - stage_started, 3
                )
                # Record the source identity as soon as extraction succeeds so
                # an interrupted Demucs run can resume its valid chunks.
                self._write_audio_cache_manifest(media_fingerprint)
                stage_started = time.perf_counter()
                self.isolate_vocals(skip=skip_separation, hardware_mode=hardware_mode, progress_cb=progress_cb)
                self.last_run_summary["demucs_elapsed_seconds"] = round(
                    time.perf_counter() - stage_started, 3
                )
                self._write_audio_cache_manifest(media_fingerprint)
                Path(self.strategy_flag_path).write_text(strategy, encoding="utf-8")

            srt_path = self.transcribe_to_srt(
                model_size=model_size,
                audio_type=audio_type,
                skipped_demucs=skip_separation,
                layout_policy=layout_policy,
                progress_cb=progress_cb,
            )
            self.cleanup_temp_files()

            elapsed = int(time.time() - started)
            self.last_run_summary.update(
                {
                    "asr_backend": self.model_backend,
                    "asr_elapsed_seconds": elapsed,
                    "cuda_fallback_used": self.cuda_fallback_used,
                }
            )
            print(f"\n⏱️ [性能报告] 音频提取与打轴总耗时: {elapsed // 60} 分 {elapsed % 60} 秒")
            return srt_path
        except Exception as exc:
            if "用户中断" not in str(exc):
                print(f"\n❌ 程序运行出错: {exc}")
            return None

    def extract_audio(self, progress_cb: ProgressCallback | None = None) -> str:
        if progress_cb:
            progress_cb(0.02, "🎵 提取源音频...")
        print("🎤 [步骤 1/4] 正在提取/转换音频格式...")

        if self._is_valid_wav_cache(self.audio_path):
            return self.audio_path

        try:
            return FFmpegAudioProcessor.extract_audio(self.video_path, self.audio_path)
        except Exception as exc:
            raise RuntimeError(f"音频提取失败: {exc}") from exc

    def isolate_vocals(
        self,
        skip: bool = False,
        hardware_mode: str = "low",
        progress_cb: ProgressCallback | None = None,
    ) -> str:
        if skip:
            print("⏩ [步骤 2/4] 极速模式：已跳过 Demucs 人声分离，直接使用源音频！")
            self.vocal_path = self.audio_path
            if progress_cb:
                progress_cb(0.05, "⏩ 跳过人声分离")
            return self.vocal_path

        print("🎸 [步骤 2/4] 正在使用 Demucs 高精模型分离人声...")
        chunk_length, segment_args, mode_label = self._demucs_plan(hardware_mode)
        print(f"   -> {mode_label}")

        chunks_dir = os.path.join(self.audio_dir, "chunks")
        os.makedirs(chunks_dir, exist_ok=True)
        chunks = FFmpegAudioProcessor.segment_audio(self.audio_path, chunks_dir, chunk_length)
        if not chunks:
            raise RuntimeError("音频切片失败，未生成分段文件。")

        vocal_chunks: list[str] = []
        for index, chunk_file in enumerate(chunks, start=1):
            self._raise_if_cancelled("人声分离已停止")
            pct = int((index / len(chunks)) * 100)
            if progress_cb:
                progress_cb(0.05 + 0.35 * (index / len(chunks)), f"🎸 分离人声中... {pct}%")

            status_msg = f"正在提取分段 {index}/{len(chunks)}"
            if hardware_mode == "low":
                status_msg += " (极致锁死)"
            elif hardware_mode == "high":
                status_msg += " (火力全开)"
            print(f"      [分离进度] {status_msg} ➔ {pct}%")

            chunk_name = Path(chunk_file).stem
            chunk_vocal = os.path.join(chunks_dir, "htdemucs_ft", chunk_name, "vocals.wav")
            if self._is_valid_wav_cache(chunk_vocal):
                print(f"      -> ⏩ 分段 {index} 已完成，直接续跑下一段。")
                vocal_chunks.append(chunk_vocal)
                continue

            cmd = self._demucs_command(segment_args, chunks_dir, chunk_file)
            for attempt in range(1, 3):
                log_name = f"demucs_{chunk_name}_attempt_{attempt}.log"
                try:
                    self._run_cmd(
                        cmd,
                        task_name=f"人声分离 (第 {index} 分段)",
                        log_name=log_name,
                    )
                    if not self._is_valid_wav_cache(chunk_vocal):
                        raise RuntimeError(f"分段输出缺失或 WAV 文件不完整: {chunk_vocal}")
                    vocal_chunks.append(chunk_vocal)
                    break
                except Exception as exc:
                    shutil.rmtree(str(Path(chunk_vocal).parent), ignore_errors=True)
                    if attempt == 1:
                        print(f"      -> ⚠️ 分段 {index} 处理失败，将自动重试一次。")
                        time.sleep(2)
                        continue
                    raise RuntimeError(f"Demucs 在处理第 {index} 个切片时失败:\n{exc}") from exc

        if not vocal_chunks:
            raise RuntimeError("所有分段处理完毕，但未提取到任何人声文件。")

        print("   -> 🧩 所有分段处理完毕，正在无缝拼接最终人声...")
        FFmpegAudioProcessor.concat_audio(vocal_chunks, self.final_vocal_path, chunks_dir)
        self.vocal_path = self.final_vocal_path
        shutil.rmtree(chunks_dir, ignore_errors=True)
        print("   -> ✅ 拼接完成！音频分离调度机制运行成功。")
        return self.vocal_path

    def cleanup_temp_files(self) -> None:
        try:
            # Keep source and Demucs vocal caches side by side so switching
            # strategies does not force another extraction/separation pass.
            shutil.rmtree(os.path.join(self.output_dir, "htdemucs_ft"), ignore_errors=True)
        except Exception as exc:
            print(f"   -> ⚠️ 缓存清理时出现小问题 (可忽略): {exc}")

    def _run_cmd(
        self,
        cmd: list[str],
        task_name: str | None = None,
        log_name: str | None = None,
    ) -> int:
        log_path = os.path.join(self.audio_dir, log_name or "last_cmd.log")
        with open(log_path, "w", encoding="utf-8", errors="ignore") as out_file:
            out_file.write(f"[command] {subprocess.list2cmdline(cmd)}\n")
            out_file.flush()
            kwargs: dict[str, object] = {
                "stdout": out_file,
                "stderr": subprocess.STDOUT,
                "text": True,
                "encoding": "utf-8",
                "errors": "ignore",
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000 | 0x00004000

            try:
                process = subprocess.Popen(cmd, **kwargs)
            except FileNotFoundError as exc:
                raise RuntimeError(f"系统找不到核心工具 [{cmd[0]}]") from exc

            start_time = time.time()
            last_heartbeat = start_time
            while process.poll() is None:
                self._raise_if_cancelled("已安全切断外部进程", process=process)
                if task_name and time.time() - last_heartbeat >= 30:
                    elapsed = int(time.time() - start_time)
                    print(
                        f"      -> ⏳ [系统心跳] {task_name} 正在后台全速运行 "
                        f"(本段已耗时 {elapsed} 秒)..."
                    )
                    last_heartbeat = time.time()
                time.sleep(0.5)

        if process.returncode != 0:
            err_msg = "未能捕获到底层输出，请检查环境。"
            if os.path.exists(log_path):
                content = Path(log_path).read_text(encoding="utf-8", errors="ignore").strip()
                if content:
                    err_msg = content[-1500:]
            raise RuntimeError(
                f"进程异常退出 (退出码: {process.returncode})。\n"
                f"诊断日志: {log_path}\n"
                f"[底层真实报错日志]:\n{err_msg}"
            )
        return process.returncode

    @staticmethod
    def _is_valid_wav_cache(path: str | os.PathLike[str]) -> bool:
        try:
            if not os.path.exists(path) or os.path.getsize(path) <= 44:
                return False
            with wave.open(str(path), "rb") as handle:
                return (
                    handle.getnchannels() > 0
                    and handle.getframerate() > 0
                    and handle.getnframes() > 0
                )
        except (OSError, EOFError, wave.Error):
            return False

    def _can_reuse_vocal_cache(
        self,
        strategy: str,
        media_fingerprint: dict[str, object] | None = None,
    ) -> bool:
        cache_path = self._cache_path_for_strategy(strategy)
        current = media_fingerprint or self._cache_media_fingerprint or self._media_fingerprint()
        manifest = self._read_audio_cache_manifest()
        return bool(
            manifest
            and manifest.get("media") == current
            and self._is_valid_wav_cache(cache_path)
        )

    def _invalidate_vocal_cache_if_needed(self) -> None:
        self._invalidate_audio_cache()

    def _ensure_audio_cache_identity(self) -> dict[str, object]:
        current = self._media_fingerprint()
        stored = self._read_audio_cache_manifest()
        if not stored or stored.get("media") != current:
            if stored or any(
                os.path.exists(path)
                for path in (self.audio_path, self.final_vocal_path, self.strategy_flag_path)
            ):
                print("   -> 🔄 检测到媒体身份变化或旧版音频缓存，已清理旧缓存。")
            self._invalidate_audio_cache()
        self._cache_media_fingerprint = current
        return current

    def _invalidate_audio_cache(self) -> None:
        for path in (
            self.audio_path,
            self.final_vocal_path,
            self.strategy_flag_path,
            self.cache_manifest_path,
        ):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        shutil.rmtree(os.path.join(self.audio_dir, "chunks"), ignore_errors=True)

    def _media_fingerprint(self) -> dict[str, object]:
        path = Path(self.video_path).resolve()
        stat = path.stat()
        edge_hash = hashlib.md5()
        with path.open("rb") as file:
            edge_hash.update(file.read(1024 * 1024))
            if stat.st_size > 1024 * 1024:
                file.seek(max(0, stat.st_size - 1024 * 1024))
                edge_hash.update(file.read(1024 * 1024))
        return {
            "path": str(path),
            "name": path.name,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "edge_md5": edge_hash.hexdigest(),
        }

    def _read_audio_cache_manifest(self) -> dict[str, object]:
        try:
            payload = json.loads(Path(self.cache_manifest_path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_audio_cache_manifest(self, media_fingerprint: dict[str, object]) -> None:
        payload = {
            "version": 1,
            "media": media_fingerprint,
            "source_audio": Path(self.audio_path).name,
            "vocal_audio": Path(self.final_vocal_path).name,
        }
        _atomic_write_text(
            Path(self.cache_manifest_path),
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        )

    def _cache_path_for_strategy(self, strategy: str) -> str:
        return self.audio_path if strategy == "skip" else self.final_vocal_path

    def _demucs_plan(self, hardware_mode: str) -> tuple[int, list[str], str]:
        if hardware_mode == "high":
            return 1800, [], "🚀 启动【满血狂飙】引擎，解除显存封印 (每 1800 秒一段)..."
        if hardware_mode == "medium":
            return 180, [], "⚖️ 启动【均衡提速】引擎，兼顾速度与稳定 (每 180 秒一段)..."
        return 60, ["--segment", "2"], "🛡️ 启动【极限防爆】引擎，物理锁死显存峰值 (每 60 秒一段)..."

    def _demucs_command(self, segment_args: list[str], chunks_dir: str, chunk_file: str) -> list[str]:
        base_args = ["-n", "htdemucs_ft", "--two-stems=vocals", *segment_args, "-o", chunks_dir, chunk_file]
        runner = self._demucs_runner_path()
        engine_python = self._engine_python_path()
        if runner and engine_python:
            return [str(engine_python), str(runner), *base_args]
        return ["demucs", *base_args]

    def _demucs_runner_path(self) -> Path | None:
        override = os.environ.get("UTASYNCNEXT_DEMUCS_RUNNER", "").strip()
        candidates: list[Path] = []
        if override:
            candidates.append(Path(override))

        try:
            from engine.cache_env import app_root

            root = app_root()
            candidates.extend(
                [
                    root / "engine" / "demucs_runner.py",
                    root / "_internal" / "engine" / "demucs_runner.py",
                ]
            )
        except Exception:
            pass

        candidates.append(Path(__file__).resolve().parents[1] / "engine" / "demucs_runner.py")
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _engine_python_path(self) -> Path | None:
        engine_root = os.environ.get("UTASYNC_HOOKED_ENV", "").strip()
        candidates: list[Path] = []
        if engine_root:
            candidates.append(Path(engine_root) / "Scripts" / "python.exe")
        candidates.append(Path(sys.executable))

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _transcribe_options(self, audio_type: str, skipped_demucs: bool) -> tuple[str, float, bool, dict[str, float] | None]:
        if audio_type == "live":
            prompt = "これはライブ音楽の歌詞です。正確に書き起こしてください。"
            vad_threshold = 0.5
            silence_ms = 1000
            pad_ms = 200
            max_duration = 8.0
            use_vad = not skipped_demucs
        elif audio_type == "live_soft":
            prompt = "これはライブ音楽の歌詞です。正確に書き起こしてください。"
            vad_threshold = 0.3
            silence_ms = 1000
            pad_ms = 200
            max_duration = 8.0
            use_vad = not skipped_demucs
        else:
            prompt = "これは日本語のインタビューや日常会話です。正確に書き起こしてください。"
            vad_threshold = 0.5
            silence_ms = 500
            pad_ms = 0
            max_duration = 5.0
            use_vad = True

        vad_params = (
            {"threshold": vad_threshold, "min_silence_duration_ms": silence_ms, "speech_pad_ms": pad_ms}
            if use_vad
            else None
        )
        return prompt, max_duration, use_vad, vad_params

    def _should_drop_segment(self, text: str) -> bool:
        if not text:
            return True
        junk_phrases = ["書き起こしてください", "ライブ音楽"]
        return any(phrase in text for phrase in junk_phrases)

    def _raise_if_cancelled(self, message: str, process: subprocess.Popen[str] | None = None) -> None:
        if self.cancel_event and getattr(self.cancel_event, "is_set", lambda: False)():
            if process:
                process.kill()
                process.wait()
            raise RuntimeError(f"用户中断：{message}")

    def _format_timestamp(self, seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

    # CTranslate2 can fail either while loading the model or later while
    # iterating transcription segments, so the ASR path keeps a single
    # CUDA-to-CPU fallback implementation here.
    def transcribe_to_srt(
        self,
        model_size: str = "large-v2",
        audio_type: str = "live",
        skipped_demucs: bool = False,
        progress_cb: ProgressCallback | None = None,
        layout_policy: SubtitleLayoutPolicy | None = None,
    ) -> str:
        print(f"[ASR] Loading Faster-Whisper model: {model_size}")
        if progress_cb:
            progress_cb(0.40, "Loading ASR model...")
        self._raise_if_cancelled("ASR model loading cancelled")

        model_started = time.perf_counter()
        self.model = self._load_whisper_model(model_size)
        self.last_run_summary["model_load_elapsed_seconds"] = round(
            time.perf_counter() - model_started, 3
        )
        self._raise_if_cancelled("ASR model loaded but task was cancelled")

        srt_path = os.path.join(self.subs_dir, f"{self.base_name}_极致打轴.srt")
        print(f"[ASR] Start transcription mode={audio_type}, backend={self.model_backend}")
        transcription_started = time.perf_counter()
        try:
            self._write_transcription_srt(
                srt_path,
                audio_type,
                skipped_demucs,
                progress_cb=progress_cb,
                layout_policy=layout_policy,
            )
        except Exception as exc:
            if self.model_backend.startswith("cuda") and self._is_cuda_runtime_error(exc):
                print(f"[ASR] CUDA runtime failed during transcription: {exc}")
                print("[ASR] Falling back to CPU int8 and retrying once...")
                self.cuda_fallback_used = True
                self._release_model()
                self.model = self._load_cpu_whisper_model(model_size)
                self._write_transcription_srt(
                    srt_path,
                    audio_type,
                    skipped_demucs,
                    progress_cb=progress_cb,
                    layout_policy=layout_policy,
                )
            else:
                raise

        self.last_run_summary["transcription_elapsed_seconds"] = round(
            time.perf_counter() - transcription_started, 3
        )

        print(f"[ASR] SRT saved: {srt_path}")
        return srt_path

    def transcribe_window_to_srt(
        self,
        start_seconds: float,
        end_seconds: float,
        output_path: str | os.PathLike[str],
        *,
        audio_type: str,
        skipped_demucs: bool,
    ) -> str:
        """Rescan a short audio window with the already loaded ASR model."""
        if self.model is None:
            raise RuntimeError("Whisper model is not loaded for the local rescan.")
        if start_seconds < 0 or end_seconds <= start_seconds:
            raise ValueError("Invalid local rescan window.")
        if end_seconds - start_seconds > 120:
            raise ValueError("Local rescan window is too long.")

        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        clip_path = target.with_suffix(".wav")
        temp_path = target.with_suffix(target.suffix + ".tmp")
        prompt, _, use_vad, vad_params = self._transcribe_options(audio_type, skipped_demucs)
        try:
            self._raise_if_cancelled("歌词缺轴局部重扫已停止")
            window_source = self.video_path if skipped_demucs else self.vocal_path
            FFmpegAudioProcessor.extract_audio_window(
                window_source,
                str(clip_path),
                start_seconds,
                end_seconds,
            )
            segments, _ = self.model.transcribe(
                str(clip_path),
                language="ja",
                condition_on_previous_text=False,
                beam_size=5,
                initial_prompt=prompt,
                vad_filter=use_vad,
                vad_parameters=vad_params,
            )
            valid_index = 1
            with temp_path.open("w", encoding="utf-8") as handle:
                for segment in segments:
                    self._raise_if_cancelled("歌词缺轴局部重扫已停止")
                    text = str(getattr(segment, "text", "")).strip()
                    if self._should_drop_segment(text):
                        continue
                    start = start_seconds + float(getattr(segment, "start", 0.0))
                    end = start_seconds + float(getattr(segment, "end", 0.0))
                    if end <= start:
                        continue
                    handle.write(f"{valid_index}\n")
                    handle.write(f"{self._format_timestamp(start)} --> {self._format_timestamp(end)}\n")
                    handle.write(f"{text}\n\n")
                    valid_index += 1
            os.replace(temp_path, target)
            return str(target)
        finally:
            clip_path.unlink(missing_ok=True)
            temp_path.unlink(missing_ok=True)

    def transcribe_lyric_window_to_srt(
        self,
        start_seconds: float,
        end_seconds: float,
        output_path: str | os.PathLike[str],
        *,
        expected_lines: list[str],
        skipped_demucs: bool,
    ) -> dict[str, object]:
        """Build a strict word-timed lyric candidate without changing source subtitles."""
        if self.model is None:
            raise RuntimeError("Whisper model is not loaded for lyric-assisted alignment.")
        if start_seconds < 0 or end_seconds <= start_seconds:
            raise ValueError("Invalid lyric alignment window.")
        if end_seconds - start_seconds > 120:
            raise ValueError("Lyric alignment window is too long.")

        lyrics = [str(line).strip() for line in expected_lines if str(line).strip()]
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        clip_path = target.with_suffix(".wav")
        evidence_path = target.with_name(f"{target.stem}_词级对齐证据.json")
        prompt = "现场演唱可能包含以下歌词，只转写确实唱出的内容：\n" + "\n".join(lyrics)
        result: dict[str, object] = {
            "written": False,
            "output_path": "",
            "evidence_path": str(evidence_path.resolve()),
            "rejection_reason": "",
            "matched_line_count": 0,
        }
        try:
            self._raise_if_cancelled("歌词缺轴词级对齐已停止")
            window_source = self.video_path if skipped_demucs else self.vocal_path
            FFmpegAudioProcessor.extract_audio_window(
                window_source,
                str(clip_path),
                start_seconds,
                end_seconds,
            )
            segments_iter, _ = self.model.transcribe(
                str(clip_path),
                language="ja",
                condition_on_previous_text=False,
                beam_size=5,
                temperature=0.0,
                initial_prompt=prompt,
                vad_filter=False,
                word_timestamps=True,
            )
            records = segment_records(segments_iter, offset_seconds=start_seconds)
            matches = match_expected_lyric_lines(records, lyrics)
            rejection = strict_alignment_rejection_reason(matches, lyrics)
            matched_count = sum(bool(match.get("accepted")) for match in matches)
            if rejection:
                target.unlink(missing_ok=True)
            else:
                _atomic_write_text(target, render_aligned_lyrics_srt(matches))
                result["written"] = True
                result["output_path"] = str(target.resolve())
            result["rejection_reason"] = rejection
            result["matched_line_count"] = matched_count
            _atomic_write_text(
                evidence_path,
                json.dumps(
                    {
                        "schema_version": 1,
                        "policy": "lyrics-gap-word-alignment-v1",
                        "source_audio": str(Path(window_source).resolve()),
                        "window": {"start": start_seconds, "end": end_seconds},
                        "expected_lines": lyrics,
                        "matched_line_count": matched_count,
                        "written": not rejection,
                        "rejection_reason": rejection,
                        "matches": matches,
                        "segments": records,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            return result
        finally:
            clip_path.unlink(missing_ok=True)

    def _write_transcription_srt(
        self,
        srt_path: str,
        audio_type: str,
        skipped_demucs: bool,
        progress_cb: ProgressCallback | None = None,
        layout_policy: SubtitleLayoutPolicy | None = None,
    ) -> None:
        if self.model is None:
            raise RuntimeError("Whisper model is not loaded.")

        prompt, max_duration, use_vad, vad_params = self._transcribe_options(audio_type, skipped_demucs)
        segments, info = self.model.transcribe(
            self.vocal_path,
            language="ja",
            condition_on_previous_text=False,
            beam_size=5,
            initial_prompt=prompt,
            vad_filter=use_vad,
            vad_parameters=vad_params,
        )

        temp_srt_path = f"{srt_path}.tmp"
        # The output folder can be removed by cleanup/recovery while a worker
        # is preparing a task. Recreate it at the final write boundary so the
        # transcription result is always able to open its temporary SRT.
        Path(srt_path).parent.mkdir(parents=True, exist_ok=True)
        valid_index = 1
        last_logged_pct = -1
        split_source_segments = 0
        extra_cues = 0
        diagnostic_segments: list[dict[str, object]] = []
        suppressed_segments: list[dict[str, object]] = []
        pending_segment: object | None = None
        pending_record: dict[str, object] | None = None
        pending_position = 0
        candidate_position = 0

        def emit_segment(
            source_segment: object,
            record: dict[str, object],
            position: int,
            next_record: dict[str, object] | None,
        ) -> None:
            nonlocal valid_index, split_source_segments, extra_cues
            suppression_reason = suppression_reason_for_segment(
                record,
                position=position,
                next_record=next_record,
            )
            if suppression_reason:
                suppressed_segments.append({**record, "suppression_reason": suppression_reason})
                return

            diagnostic_segments.append(record)
            timed_parts = split_timed_subtitle_for_layout(
                str(record["text"]),
                float(getattr(source_segment, "start", 0.0)),
                float(getattr(source_segment, "end", 0.0)),
                layout_policy,
                max_duration_seconds=max_duration,
            )
            if len(timed_parts) > 1:
                split_source_segments += 1
                extra_cues += len(timed_parts) - 1

            for part in timed_parts:
                srt_file.write(f"{valid_index}\n")
                srt_file.write(f"{self._format_timestamp(part.start)} --> {self._format_timestamp(part.end)}\n")
                srt_file.write(f"{part.text}\n\n")
                valid_index += 1

        with open(temp_srt_path, "w", encoding="utf-8") as srt_file:
            for segment in segments:
                self._raise_if_cancelled("ASR transcription cancelled")
                if progress_cb and getattr(info, "duration", 0) > 0:
                    current_prog = segment.end / info.duration
                    pct = min(100, int(current_prog * 100))
                    progress_cb(0.40 + current_prog * 0.20, f"ASR transcribing... {pct}%")
                    if pct - last_logged_pct >= 5 or last_logged_pct == -1:
                        print(f"[ASR] progress {pct}%")
                        last_logged_pct = pct

                text = segment.text.strip()
                if self._should_drop_segment(text):
                    continue

                candidate_position += 1
                record = segment_to_record(segment, candidate_position)
                if pending_segment is not None and pending_record is not None:
                    emit_segment(pending_segment, pending_record, pending_position, record)
                pending_segment = segment
                pending_record = record
                pending_position = candidate_position

            if pending_segment is not None and pending_record is not None:
                emit_segment(pending_segment, pending_record, pending_position, None)

        os.replace(temp_srt_path, srt_path)
        self.last_run_summary["subtitle_density_splits"] = split_source_segments
        self.last_run_summary["subtitle_density_extra_cues"] = extra_cues
        self.last_run_summary["asr_auto_suppressed"] = len(suppressed_segments)
        try:
            diagnostic_report = write_asr_diagnostics(
                srt_path,
                diagnostic_segments,
                suppressed_segments=suppressed_segments,
            )
            diagnostic_summary = diagnostic_report.get("summary") or {}
            self.last_run_summary["asr_review_candidates"] = int(
                diagnostic_summary.get("review_candidate_count") or 0
            )
            self.last_run_summary["asr_diagnostics_path"] = str(
                Path(srt_path).with_name(f"{Path(srt_path).stem}_识别诊断.json").resolve()
            )
            self.last_run_summary["asr_diagnostics_level"] = "full"
            print_diagnostic_summary(diagnostic_report, srt_path)
        except Exception as exc:
            print(f"[打轴检查] 诊断文件保存失败，字幕结果不受影响: {exc}")
        if split_source_segments:
            print(f"字幕整理：已自动拆分 {split_source_segments} 条过长字幕，新增 {extra_cues} 个连续时间轴。")
        if suppressed_segments:
            print(f"字幕整理：已保守过滤 {len(suppressed_segments)} 条明显误识别，详情见诊断文件。")
        if valid_index == 1:
            print("[ASR] No valid subtitle segments were emitted.")

    def _load_whisper_model(self, model_size: str) -> object:
        from faster_whisper import WhisperModel

        if os.environ.get("UTASYNCNEXT_FORCE_CPU_ASR", "").strip() == "1":
            print("[ASR] CPU mode requested by UTASYNCNEXT_FORCE_CPU_ASR=1.")
            return self._load_cpu_whisper_model(model_size)

        if not self._cuda_runtime_available():
            print("[ASR] CUDA runtime DLLs are not complete; using CPU int8.")
            return self._load_cpu_whisper_model(model_size)

        for compute_type in ("float16", "int8_float16"):
            try:
                model = WhisperModel(model_size, device="cuda", compute_type=compute_type)
                self.model_backend = f"cuda:{compute_type}"
                print(f"[ASR] CUDA {compute_type} backend selected.")
                return model
            except Exception as gpu_exc:
                print(f"[ASR] CUDA {compute_type} model load failed: {gpu_exc}")

        print("[ASR] Falling back to CPU int8...")
        self.cuda_fallback_used = True
        return self._load_cpu_whisper_model(model_size)

    def _load_cpu_whisper_model(self, model_size: str) -> object:
        from faster_whisper import WhisperModel

        try:
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            self.model_backend = "cpu"
            print("[ASR] CPU int8 backend selected.")
            return model
        except Exception as cpu_exc:
            self.model_backend = "failed"
            raise RuntimeError(f"CPU ASR model load failed: {cpu_exc}") from cpu_exc

    def _cuda_runtime_available(self) -> bool:
        if sys.platform != "win32":
            return True

        self._add_cuda_dll_directories()

        try:
            import ctypes

            dll_name = "cublas" + "64_12.dll"
            getattr(ctypes, "WinDLL")(dll_name)
            return True
        except Exception:
            return False

    def _add_cuda_dll_directories(self) -> None:
        if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
            return

        candidates: list[Path] = []
        try:
            import torch

            candidates.append(Path(torch.__file__).resolve().parent / "lib")
        except Exception:
            pass
        try:
            import ctranslate2

            candidates.append(Path(ctranslate2.__file__).resolve().parent)
        except Exception:
            pass

        for directory in candidates:
            if not directory.exists():
                continue
            try:
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
            except Exception:
                pass

    def _release_model(self) -> None:
        self.model = None
        try:
            import gc

            gc.collect()
        except Exception:
            pass

    def _is_cuda_runtime_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        markers = (
            "cuda",
            "cublas",
            "cudnn",
            "cufft",
            "curand",
            "cusolver",
            "cusparse",
            "nvrtc",
            "dll is not found",
            "cannot be loaded",
        )
        return any(marker in text for marker in markers)
