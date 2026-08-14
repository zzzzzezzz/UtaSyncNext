from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from core.subtitle_policy import SubtitleLayoutPolicy, split_timed_subtitle_for_layout
from utils.ffmpeg_tools import FFmpegAudioProcessor


ProgressCallback = Callable[[float, str], None]

os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


class LiveSubtitleGenerator:
    """Next-native audio preparation and Faster-Whisper timing generator."""

    def __init__(self, video_path: str, output_dir: str = "output") -> None:
        self.video_path = os.path.abspath(video_path)
        self.base_name = Path(video_path).stem
        self.output_dir = os.path.abspath(os.path.join(output_dir, self.base_name))
        self.audio_dir = os.path.join(self.output_dir, "audio")
        self.subs_dir = os.path.join(self.output_dir, "subtitles")
        os.makedirs(self.audio_dir, exist_ok=True)
        os.makedirs(self.subs_dir, exist_ok=True)

        self.safe_name = hashlib.md5(self.base_name.encode("utf-8")).hexdigest()[:8]
        self.audio_path = os.path.join(self.audio_dir, f"{self.safe_name}_源音频.wav")
        self.final_vocal_path = os.path.join(self.audio_dir, f"{self.base_name}_纯净人声.wav")
        self.strategy_flag_path = os.path.join(self.audio_dir, "separation_strategy.txt")
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
            "cuda_fallback_used": False,
            "demucs_skipped": skip_separation,
            "subtitle_density_splits": 0,
            "subtitle_density_extra_cues": 0,
        }

        try:
            self._raise_if_cancelled("任务尚未开始即被取消")
            strategy = "skip" if skip_separation else "demucs"
            audio_cache_hit = self._can_reuse_vocal_cache(strategy)
            self.last_run_summary["audio_cache_hit"] = audio_cache_hit

            if audio_cache_hit:
                print("   -> ⚡ 检测到有效音频缓存，跳过提取与分离，直达打轴！")
                self.vocal_path = self._cache_path_for_strategy(strategy)
                if progress_cb:
                    progress_cb(0.40, "⚡ 跳过分离 (使用缓存)")
            else:
                self.extract_audio(progress_cb=progress_cb)
                self.isolate_vocals(skip=skip_separation, hardware_mode=hardware_mode, progress_cb=progress_cb)
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

        if os.path.exists(self.audio_path) and os.path.getsize(self.audio_path) > 0:
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

            cmd = self._demucs_command(segment_args, chunks_dir, chunk_file)
            try:
                self._run_cmd(cmd, task_name=f"人声分离 (第 {index} 分段)")
            except Exception as exc:
                raise RuntimeError(f"Demucs 在处理第 {index} 个切片时失败:\n{exc}") from exc

            chunk_name = Path(chunk_file).stem
            chunk_vocal = os.path.join(chunks_dir, "htdemucs_ft", chunk_name, "vocals.wav")
            if os.path.exists(chunk_vocal):
                vocal_chunks.append(chunk_vocal)
            else:
                print(f"      -> ⚠️ 找不到分段 {index} 的人声文件，可能该片段过短或 Demucs 输出异常。")

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

    def _run_cmd(self, cmd: list[str], task_name: str | None = None) -> int:
        log_path = os.path.join(self.audio_dir, "last_cmd.log")
        with open(log_path, "w", encoding="utf-8", errors="ignore") as out_file:
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
                    print(f"      -> ⏳ [系统心跳] {task_name} 正在后台全速运行 (已耗时 {elapsed} 秒)...")
                    last_heartbeat = time.time()
                time.sleep(0.5)

        if process.returncode != 0:
            err_msg = "未能捕获到底层输出，请检查环境。"
            if os.path.exists(log_path):
                content = Path(log_path).read_text(encoding="utf-8", errors="ignore").strip()
                if content:
                    err_msg = content[-1500:]
            raise RuntimeError(f"进程异常退出 (退出码: {process.returncode})。\n[底层真实报错日志]:\n{err_msg}")
        return process.returncode

    def _can_reuse_vocal_cache(self, strategy: str) -> bool:
        cache_path = self._cache_path_for_strategy(strategy)
        return os.path.exists(cache_path) and os.path.getsize(cache_path) > 0

    def _invalidate_vocal_cache_if_needed(self) -> None:
        if os.path.exists(self.final_vocal_path):
            print("   -> 🔄 检测到分离策略变更，已作废旧缓存，重新提取音频...")
            try:
                os.remove(self.final_vocal_path)
            except OSError:
                pass

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
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        milliseconds = int((seconds - int(seconds)) * 1000)
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

        self.model = self._load_whisper_model(model_size)
        self._raise_if_cancelled("ASR model loaded but task was cancelled")

        srt_path = os.path.join(self.subs_dir, f"{self.base_name}_极致打轴.srt")
        print(f"[ASR] Start transcription mode={audio_type}, backend={self.model_backend}")
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

        print(f"[ASR] SRT saved: {srt_path}")
        return srt_path

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
        valid_index = 1
        last_logged_pct = -1
        split_source_segments = 0
        extra_cues = 0
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

                timed_parts = split_timed_subtitle_for_layout(
                    text,
                    segment.start,
                    segment.end,
                    layout_policy,
                )
                if len(timed_parts) > 1:
                    split_source_segments += 1
                    extra_cues += len(timed_parts) - 1

                for part in timed_parts:
                    start_val = part.start
                    end_val = part.end
                    if layout_policy is None or layout_policy.profile not in {"square_safe", "portrait_safe"}:
                        end_val = min(end_val, start_val + max_duration)
                    srt_file.write(f"{valid_index}\n")
                    srt_file.write(f"{self._format_timestamp(start_val)} --> {self._format_timestamp(end_val)}\n")
                    srt_file.write(f"{part.text}\n\n")
                    valid_index += 1

        os.replace(temp_srt_path, srt_path)
        self.last_run_summary["subtitle_density_splits"] = split_source_segments
        self.last_run_summary["subtitle_density_extra_cues"] = extra_cues
        if split_source_segments:
            print(f"字幕整理：已自动拆分 {split_source_segments} 条过长字幕，新增 {extra_cues} 个连续时间轴。")
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
                os.add_dll_directory(str(directory))
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
