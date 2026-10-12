from __future__ import annotations

import hashlib
import json
import os
import re
import site
import sys
import time
import traceback
from multiprocessing import Event, Queue
from pathlib import Path
from typing import Any

from core.asr_diagnostics import diagnostics_path_for_srt, ensure_srt_diagnostics, print_diagnostic_summary
from core.lyric_gap_detection import opening_lyric_gap_report_path, write_opening_lyric_gap_report
from core.lyric_gap_repair import (
    automatic_lyric_gap_rescan_rejection_reason,
    write_high_confidence_lyric_gap_repair_preview,
)
from core.opening_lyrics_cleanup import apply_lyric_anchored_opening_cleanup
from core.watermark_hallucination_cleanup import apply_watermark_hallucination_cleanup
from core.ass_config import build_ass_config
from core.subtitle_policy import (
    apply_layout_policy,
    cap_extreme_sparse_srt_cues,
    policy_log_line,
    resolve_subtitle_layout,
    resolve_text_scale,
)
from engine.cache_env import app_root, apply_next_cache_env, task_base_name
from engine.hardware_profile import (
    HardwareProfile,
    inspect_hardware_profile,
    resolve_demucs_hardware_mode,
    whisper_hardware_note,
)
from engine.locator import find_engine_location
from engine.model_registry import find_complete_snapshot, has_cache_dir
from engine.protocol import done, error, log, progress, status
from engine.status import inspect_engine_lightweight
from utils.network_proxy import ProxyConfigError, proxy_status_text
from utils.ffmpeg_tools import ffmpeg_status_text, inspect_ffmpeg
from utils.performance_metrics import write_task_performance_report


# Windows keeps DLL search directories alive only while the handle returned by
# os.add_dll_directory() is alive.  The worker is long-lived, so retain these
# handles at module scope instead of letting them be garbage-collected.
_DLL_DIRECTORY_HANDLES: list[object] = []


class PipelineUserError(RuntimeError):
    """Expected user-facing pipeline problem that should not include a traceback."""


class WhisperModelRef:
    def __init__(self, model_name: str, path: Path, source_label: str) -> None:
        self.model_name = model_name
        self.path = path
        self.source_label = source_label

    @property
    def display_name(self) -> str:
        return f"{self.model_name} ({self.path.name})"


class WorkerStdoutRedirector:
    """Queue-backed stdout facade that still looks like a real stream to native libs."""

    def __init__(self, msg_queue: Queue) -> None:
        self.msg_queue = msg_queue
        self.encoding = "utf-8"

    def write(self, text: str) -> None:
        if text:
            self.msg_queue.put(log(text))

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        try:
            return sys.__stdout__.fileno()
        except Exception:
            return 1

    @property
    def closed(self) -> bool:
        return False


def build_pipeline_preview(payload: dict[str, object] | None = None) -> dict[str, object]:
    payload = payload or {}
    media_path = str(payload.get("media_path") or "")
    raw_lyrics = str(payload.get("lyrics") or "")
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}

    preview: dict[str, object] = {
        "ok": False,
        "lines": [],
        "asr_cache_hit": False,
        "translation_cache_hit": False,
        "translation_partial_cache_batches": 0,
        "will_call_api": False,
        "will_run_demucs": False,
    }
    lines: list[str] = []
    preview["lines"] = lines

    if not media_path:
        lines.append("❌ 未选择媒体文件。")
        return preview

    media = Path(media_path)
    if not media.exists():
        lines.append(f"❌ 媒体不存在: {_display_path(media_path)}")
        return preview

    hardware_profile = inspect_hardware_profile()
    audio_type, model_size, hardware_mode, skip_demucs, output_mode = _normalize_pipeline_settings(
        settings, hardware_profile
    )
    _, hardware_reason = resolve_demucs_hardware_mode(settings.get("hardware_mode"), hardware_profile)
    api_key = str(settings.get("api_key") or "").strip()
    api_model = str(settings.get("api_model") or "").strip() or "deepseek-ai/DeepSeek-V3"
    ass_enabled = str(settings.get("ass_preset")) != "不生成 (仅SRT)"
    lyrics_text = "" if "留空" in raw_lyrics else raw_lyrics

    preview["will_call_api"] = bool(api_key)
    preview["will_run_demucs"] = not skip_demucs

    lines.append(f"🎬 媒体: {media.name}")
    lines.append(f"🧠 Whisper: {model_size}")
    engine_status = inspect_engine_lightweight()
    if not engine_status.ready:
        lines.append(f"❌ AI 引擎未就绪: {engine_status.label}。{engine_status.detail}")
        return preview
    ffmpeg_paths = inspect_ffmpeg()
    if not all(ffmpeg_paths.get(name) for name in ("ffmpeg", "ffprobe")):
        lines.append(f"❌ 外部依赖未就绪: {ffmpeg_status_text(ffmpeg_paths)}")
        return preview
    lines.append(f"🧰 外部依赖: {ffmpeg_status_text(ffmpeg_paths)}")
    lines.append("🎸 Demucs: 将跳过，优先快速出轴。" if skip_demucs else "🎸 Demucs: 将启用高质量人声分离，首次会很慢。")
    lines.append(f"⚡ 硬件: {whisper_hardware_note(hardware_profile)}")
    if not skip_demucs:
        mode_label = {"low": "保守分离", "medium": "均衡分离", "high": "快速分离"}[hardware_mode]
        lines.append(f"🎛 人声分离算力: {mode_label}（{hardware_reason}）。")
    translation_line_index = len(lines)
    lines.append("🌍 翻译: 已配置接口，正在检查可复用结果。" if api_key else "🌍 翻译: 未配置 API Key，本次只生成原文字幕。")
    if api_key:
        lines.append(f"   -> 模型: {api_model}")
        try:
            lines.append(f"🌐 网络: {proxy_status_text(settings.get('proxy_url'))}。")
        except ProxyConfigError as exc:
            lines.append(f"❌ 网络代理格式有误: {exc}")
            return preview
    lines.append("🎨 ASS: 将生成特效字幕。" if ass_enabled else "🎨 ASS: 仅生成 SRT。")

    try:
        model_ref = _resolve_whisper_model_arg(model_size)
        media_fingerprint = _media_fingerprint(media)
        layout_policy = resolve_subtitle_layout(media_path, settings)
        base_name = task_base_name(media_path)
        subtitles_dir = app_root() / "output" / base_name / "subtitles"
        expected_srt = subtitles_dir / f"{base_name}_极致打轴.srt"
        asr_hash_path = subtitles_dir / f"{base_name}_打轴指纹.hash"
        asr_hash = _build_asr_cache_hash(
            media_fingerprint=media_fingerprint,
            audio_type=audio_type,
            model_size=model_size,
            model_ref=model_ref,
            skip_demucs=skip_demucs,
            hardware_mode=hardware_mode,
            layout_policy=layout_policy,
            reference_lyrics=lyrics_text,
        )
        legacy_asr_hash = _build_legacy_asr_cache_hash(
            media_fingerprint=media_fingerprint,
            audio_type=audio_type,
            model_size=model_size,
            model_ref=model_ref,
            skip_demucs=skip_demucs,
            hardware_mode=hardware_mode,
            layout_policy=layout_policy,
            reference_lyrics=lyrics_text,
        )
        asr_cache_reason = _asr_cache_reason(
            expected_srt,
            asr_hash_path,
            asr_hash,
            legacy_hashes=(legacy_asr_hash,),
        )
        asr_cache_hit = not asr_cache_reason
        preview["asr_cache_hit"] = asr_cache_hit
        lines.append(
            "⚡ 打轴缓存: 命中，预计可直达翻译/ASS。"
            if asr_cache_hit
            else f"⏳ 打轴缓存: 未命中（{asr_cache_reason}），将重新听写。"
        )

        if api_key and asr_cache_hit:
            trans_hash_path = subtitles_dir / f"{base_name}_翻译指纹.hash"
            trans_hash = _build_translation_cache_hash(
                media_fingerprint=media_fingerprint,
                source_srt=expected_srt,
                lyrics_text=lyrics_text,
                output_mode=output_mode,
                settings=settings,
                layout_policy=layout_policy,
            )
            translated_srt = _translation_output_path(expected_srt, output_mode)
            translation_cache_hit = _translation_completion_cache_hit(
                trans_hash_path,
                trans_hash,
                translated_srt,
            )
            preview["translation_cache_hit"] = translation_cache_hit
            if translation_cache_hit:
                preview["will_call_api"] = False
                lines[translation_line_index] = "🌍 翻译: 将复用完整缓存，不会调用接口。"
                lines.append("⚡ 翻译缓存: 命中，预计无需重新请求 API。")
            else:
                partial_batches = _translation_batch_cache_count(expected_srt)
                preview["translation_partial_cache_batches"] = partial_batches
                if partial_batches:
                    lines[translation_line_index] = "🌍 翻译: 将校验已保存断点，只请求未命中的批次。"
                    lines.append(
                        f"⏩ 翻译断点: 检测到 {partial_batches} 个已保存批次；"
                        "启动后会校验，匹配的批次直接复用。"
                    )
                else:
                    lines[translation_line_index] = "🌍 翻译: 没有可复用结果，本次会调用接口。"
                    lines.append("⏳ 翻译缓存: 未命中，将请求 API。")
    except Exception as exc:
        lines.append(f"⚠️ 预检提示: {exc}")
        return preview

    preview["ok"] = True
    return preview


def run_pipeline_worker(msg_queue: Queue, cancel_event: Event, payload: dict[str, object] | None = None) -> None:
    """Run the Next pipeline inside a disposable child process."""
    payload = payload or {}
    media_path = str(payload.get("media_path") or "")
    raw_lyrics = str(payload.get("lyrics") or "")
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}

    sys.stdout = WorkerStdoutRedirector(msg_queue)
    sys.stderr = WorkerStdoutRedirector(msg_queue)
    task_started_at = time.time()

    try:
        if not media_path:
            msg_queue.put(error("请先导入媒体文件。"))
            return
        if not os.path.exists(media_path):
            msg_queue.put(error(f"找不到媒体文件: {_display_path(media_path)}"))
            return

        msg_queue.put(status("正在准备 Next 独立引擎进程...", 0.01))
        _prepare_engine_runtime()

        hardware_profile = inspect_hardware_profile()
        audio_type, model_size, hardware_mode, skip_demucs, output_mode = _normalize_pipeline_settings(
            settings, hardware_profile
        )
        _, hardware_reason = resolve_demucs_hardware_mode(settings.get("hardware_mode"), hardware_profile)
        lyrics_text = "" if "留空" in raw_lyrics else raw_lyrics
        api_key = str(settings.get("api_key") or "").strip()
        api_model = str(settings.get("api_model") or "deepseek-ai/DeepSeek-V3").strip()
        ass_enabled = str(settings.get("ass_preset")) != "不生成 (仅SRT)"
        layout_policy = resolve_subtitle_layout(media_path, settings)
        text_scale = resolve_text_scale(layout_policy, settings)
        summary: dict[str, object] = {
            "media_name": Path(media_path).name,
            "asr_model": model_size,
            "audio_type": audio_type,
            "hardware_mode": hardware_mode,
            "hardware_mode_source": hardware_reason,
            "hardware_gpu_name": hardware_profile.gpu_name,
            "hardware_gpu_memory_mb": hardware_profile.gpu_memory_total_mb,
            "demucs_skipped": skip_demucs,
            "output_mode": output_mode,
            "translation_requested": bool(api_key),
            "api_model": api_model if api_key else "",
            "ass_enabled": ass_enabled,
            "asr_cache_hit": False,
            "asr_cache_reason": "",
            "audio_cache_hit": False,
            "asr_backend": "",
            "asr_elapsed_seconds": 0,
            "translation_elapsed_seconds": 0,
            "audio_extract_elapsed_seconds": 0.0,
            "demucs_elapsed_seconds": 0.0,
            "model_load_elapsed_seconds": 0.0,
            "transcription_elapsed_seconds": 0.0,
            "ass_elapsed_seconds": 0.0,
            "translation_batches": 0,
            "translation_cache_hits": 0,
            "translation_api_calls": 0,
            "translation_failed_batches": 0,
            "translation_alignment_fallbacks": 0,
            "translation_bilingual_coverage_retries": 0,
            "translation_speaker_label_warnings": 0,
            "translation_speaker_labels_removed": 0,
            "translation_lyric_alignment_warnings": 0,
            "translation_model": api_model if api_key else "",
            "translation_returned_models": [],
            "ass_generated": False,
            "cuda_fallback_used": False,
            "asr_diagnostics_level": "",
            "asr_opening_cleanup_count": 0,
            "asr_opening_cleanup_anchor": "",
            "asr_lyric_gap_candidates": 0,
            "asr_lyric_gap_report_path": "",
            "asr_lyric_gap_repaired_candidates": 0,
            "asr_lyric_gap_partially_repaired_candidates": 0,
            "asr_lyric_gap_inserted_cues": 0,
            "asr_lyric_gap_partial_inserted_cues": 0,
            "asr_lyric_gap_repair_evidence_path": "",
            "asr_lyric_gap_preview_candidates": 0,
            "asr_lyric_gap_preview_inserted_cues": 0,
            "asr_lyric_gap_preview_path": "",
            "asr_lyric_gap_preview_evidence_path": "",
            "asr_watermark_cleanup_count": 0,
            "asr_extreme_duration_caps": 0,
            "layout_profile": layout_policy.profile,
            "layout_note": layout_policy.note,
            "network_mode": proxy_status_text(settings.get("proxy_url")),
        }

        base_name = task_base_name(media_path)
        output_dir = app_root() / "output" / base_name
        subtitles_dir = output_dir / "subtitles"
        subtitles_dir.mkdir(parents=True, exist_ok=True)

        expected_srt = subtitles_dir / f"{base_name}_极致打轴.srt"
        asr_hash_path = subtitles_dir / f"{base_name}_打轴指纹.hash"
        trans_hash_path = subtitles_dir / f"{base_name}_翻译指纹.hash"

        model_ref = _resolve_whisper_model_arg(model_size)
        media_fingerprint = _media_fingerprint(media_path)
        asr_hash = _build_asr_cache_hash(
            media_fingerprint=media_fingerprint,
            audio_type=audio_type,
            model_size=model_size,
            model_ref=model_ref,
            skip_demucs=skip_demucs,
            hardware_mode=hardware_mode,
            layout_policy=layout_policy,
            reference_lyrics=lyrics_text,
        )

        print("\n" + "=" * 50)
        print(f"🚀 [Next 任务总线启动] 目标文件: {Path(media_path).name}")
        print(
            _format_schedule_summary(
                audio_type,
                model_size,
                hardware_mode,
                skip_demucs,
                output_mode,
                hardware_reason=hardware_reason,
            )
        )
        print(f"🖥 [硬件预检] {whisper_hardware_note(hardware_profile)}")
        if not skip_demucs:
            _ensure_demucs_weights_available()

        if cancel_event.is_set():
            msg_queue.put(done("任务已取消。"))
            return

        generator = None
        legacy_asr_hash = _build_legacy_asr_cache_hash(
            media_fingerprint=media_fingerprint,
            audio_type=audio_type,
            model_size=model_size,
            model_ref=model_ref,
            skip_demucs=skip_demucs,
            hardware_mode=hardware_mode,
            layout_policy=layout_policy,
            reference_lyrics=lyrics_text,
        )
        summary["asr_cache_reason"] = _asr_cache_reason(
            expected_srt,
            asr_hash_path,
            asr_hash,
            legacy_hashes=(legacy_asr_hash,),
        )
        srt_path = _try_reuse_asr(
            expected_srt,
            asr_hash_path,
            asr_hash,
            legacy_hashes=(legacy_asr_hash,),
        )
        summary["asr_cache_hit"] = bool(srt_path)
        if srt_path:
            summary["asr_cache_reason"] = "已命中，直接复用完整打轴结果"
            summary["asr_backend"] = "cache"
            msg_queue.put(status("⏩ 命中打轴缓存，直达翻译阶段...", 0.50))
            print(f"⏩ [断点续传] 打轴指纹匹配，直接读取: {_display_path(srt_path)}")
        else:
            print(f"🧠 [模型预检] 使用 {model_ref.source_label} Faster-Whisper 模型: {model_ref.display_name}")
            # Heavy ASR import intentionally happens only after local model preflight.
            from core.generator import LiveSubtitleGenerator

            msg_queue.put(status("🎧 正在执行声学处理与打轴...", 0.05))
            generator = LiveSubtitleGenerator(video_path=media_path)
            srt_path = generator.run(
                model_size=str(model_ref.path),
                audio_type=audio_type,
                skip_separation=skip_demucs,
                hardware_mode=hardware_mode,
                layout_policy=layout_policy,
                cancel_event=cancel_event,
                progress_cb=lambda value, text: msg_queue.put(progress(value, text)),
            )
            summary.update(getattr(generator, "last_run_summary", {}) or {})
            if not _is_valid_srt(srt_path):
                raise RuntimeError("打轴阶段未返回有效 SRT。")
            asr_hash_path.write_text(asr_hash, encoding="utf-8")

        capped_cues = cap_extreme_sparse_srt_cues(
            srt_path,
            layout_policy,
            max_duration_seconds=8.0 if audio_type in {"live", "live_soft"} else 5.0,
        )
        if capped_cues:
            summary["asr_extreme_duration_caps"] = capped_cues
            print(
                f"字幕整理：已缩短 {capped_cues} 条超过 18 秒的异常短文本长轴；"
                "起点和字幕文字均未修改。"
            )

        asr_diagnostics_path = ""
        try:
            diagnostic_report = ensure_srt_diagnostics(srt_path)
            cleanup_result = apply_lyric_anchored_opening_cleanup(srt_path, lyrics_text, diagnostic_report)
            if cleanup_result.changed:
                print(
                    "[歌词边界检查] 已按歌词与识别置信度清理 "
                    f"{cleanup_result.removed_count} 条明确误识别；"
                    f"参考锚点: {cleanup_result.anchor_text}"
                )
                diagnostic_report = ensure_srt_diagnostics(srt_path)
                summary["asr_opening_cleanup_count"] = cleanup_result.removed_count
                summary["asr_opening_cleanup_anchor"] = cleanup_result.anchor_text
            watermark_cleanup = apply_watermark_hallucination_cleanup(srt_path, diagnostic_report)
            if watermark_cleanup.changed:
                print(f"[识别清理] 已移除 {watermark_cleanup.removed_count} 条已知水印式误识别。")
                diagnostic_report = ensure_srt_diagnostics(srt_path)
                summary["asr_watermark_cleanup_count"] = watermark_cleanup.removed_count
            diagnostic_summary = diagnostic_report.get("summary") or {}
            asr_diagnostics_path = str(diagnostics_path_for_srt(srt_path).resolve())
            summary["asr_review_candidates"] = int(diagnostic_summary.get("review_candidate_count") or 0)
            summary["asr_diagnostics_path"] = asr_diagnostics_path
            summary["asr_diagnostics_level"] = (
                "full" if diagnostic_report.get("evidence_source") == "faster-whisper" else "basic"
            )
            if summary["asr_cache_hit"]:
                print_diagnostic_summary(diagnostic_report, srt_path)
        except Exception as exc:
            print(f"[打轴检查] 诊断文件保存失败，字幕结果不受影响: {exc}")

        if lyrics_text:
            try:
                gap_report = write_opening_lyric_gap_report(srt_path, lyrics_text)
                gap_count = int(gap_report.get("candidate_count") or 0)
                summary["asr_lyric_gap_candidates"] = gap_count
                summary["asr_lyric_gap_report_path"] = str(
                    opening_lyric_gap_report_path(srt_path).resolve()
                )
                if gap_count:
                    preview_candidates = 0
                    preview_inserted_cues = 0
                    preview_path = ""
                    preview_evidence_path = ""
                    candidates = gap_report.get("candidates")
                    if generator is not None and audio_type in {"live", "live_soft"} and isinstance(candidates, list):
                        for candidate_index, candidate in enumerate(candidates[:3], start=1):
                            if not isinstance(candidate, dict):
                                continue
                            if automatic_lyric_gap_rescan_rejection_reason(candidate):
                                continue
                            window_start = float(candidate.get("suggested_window_start") or 0.0)
                            window_end = float(candidate.get("suggested_window_end") or 0.0)
                            if window_end <= window_start or window_end - window_start > 120.0:
                                continue
                            print(
                                f"[歌词缺轴复查] 正在复听第 {candidate_index} 处明显空档 "
                                f"({window_start:.1f}–{window_end:.1f} 秒)。"
                            )
                            msg_queue.put(progress(0.58, "正在复听可能漏掉的歌词..."))
                            rescan_path = subtitles_dir / f"{base_name}_歌词词级对齐_{candidate_index:02d}.srt"
                            candidate_preview_path = (
                                subtitles_dir / f"{base_name}_歌词缺轴修复预览_{candidate_index:02d}.srt"
                            )
                            try:
                                alignment = generator.transcribe_lyric_window_to_srt(
                                    window_start,
                                    window_end,
                                    rescan_path,
                                    expected_lines=[
                                        str(line).strip()
                                        for line in candidate.get("missing_lines") or []
                                        if str(line).strip()
                                    ],
                                    skipped_demucs=skip_demucs,
                                )
                                if not alignment.get("written"):
                                    print(
                                        "[歌词缺轴复查] 词级证据不足，未生成候选字幕。原因: "
                                        f"{alignment.get('rejection_reason') or 'unknown'}"
                                    )
                                    continue
                                repair = write_high_confidence_lyric_gap_repair_preview(
                                    srt_path,
                                    rescan_path,
                                    candidate,
                                    candidate_preview_path,
                                    alignment_evidence_path=str(alignment.get("evidence_path") or ""),
                                )
                            except Exception as exc:
                                if cancel_event.is_set():
                                    raise
                                print(
                                    f"[歌词缺轴复查] 局部复听未完成，已保留原字幕。原因: {exc}"
                                )
                                continue
                            if repair.written:
                                preview_candidates += 1
                                preview_inserted_cues += repair.inserted_count
                                preview_path = repair.preview_path
                                preview_evidence_path = repair.evidence_path
                                print(
                                    f"[歌词缺轴复查] 已生成 {repair.inserted_count} 条歌词轴的独立修复预览；"
                                    "正式字幕未修改，请人工确认后再采用。"
                                )
                            else:
                                print(
                                    "[歌词缺轴复查] 合并预览证据不足，已保留正式字幕。原因: "
                                    f"{repair.reason}"
                                )

                    summary["asr_lyric_gap_preview_candidates"] = preview_candidates
                    summary["asr_lyric_gap_preview_inserted_cues"] = preview_inserted_cues
                    summary["asr_lyric_gap_preview_path"] = preview_path
                    summary["asr_lyric_gap_preview_evidence_path"] = preview_evidence_path
                    print(
                        f"[歌词缺轴检查] 发现 {gap_count} 处候选，生成 {preview_candidates} 份严格修复预览；"
                        "正式字幕没有自动写入任何歌词。"
                    )
            except Exception as exc:
                if cancel_event.is_set():
                    raise
                print(f"[歌词缺轴检查] 检查未完成，字幕结果不受影响: {exc}")

        if cancel_event.is_set():
            msg_queue.put(done("任务已取消。"))
            return

        trans_hash = _build_translation_cache_hash(
            media_fingerprint=media_fingerprint,
            source_srt=srt_path,
            lyrics_text=lyrics_text,
            output_mode=output_mode,
            settings=settings,
            layout_policy=layout_policy,
        )
        translated_cache_path = _translation_output_path(srt_path, output_mode)
        translation_completion_hit = _translation_completion_cache_hit(
            trans_hash_path,
            trans_hash,
            translated_cache_path,
        )
        if not translation_completion_hit:
            _invalidate_translation_completion(trans_hash_path, trans_hash)

        final_srt = srt_path
        final_ass: str | None = None
        if api_key and translation_completion_hit:
            final_srt = str(translated_cache_path.resolve())
            cached_batches = _srt_batch_count(srt_path)
            summary.update(
                {
                    "translation_elapsed_seconds": 0,
                    "translation_batches": cached_batches,
                    "translation_cache_hits": cached_batches,
                    "translation_api_calls": 0,
                    "translation_failed_batches": 0,
                    "translation_model": api_model,
                }
            )
            msg_queue.put(status("⚡ 命中完整翻译缓存，直接生成字幕...", 0.90))
            print(f"[翻译缓存] 完整结果命中，直接复用: {_display_path(translated_cache_path)}")
        elif api_key:
            from core.translator import SubtitleTranslator

            msg_queue.put(status("🌍 正在调用大模型翻译...", 0.60))
            temp_lyrics_path = output_dir / "temp_lyrics_next.txt"
            temp_lyrics_path.write_text(lyrics_text, encoding="utf-8")
            try:
                primary_model, reasoning_model = _translation_models_for(api_model)
                translation_started_at = time.time()
                translator = SubtitleTranslator(
                    api_key=api_key,
                    base_url=str(settings.get("api_url") or "https://api.siliconflow.cn/v1").strip(),
                    primary_model=primary_model,
                    reasoning_model=reasoning_model,
                    srt_path=str(srt_path),
                    reference_txt_path=str(temp_lyrics_path),
                    output_mode=output_mode,
                    proxy_url=str(settings.get("proxy_url") or "").strip(),
                    layout_profile=layout_policy.profile,
                    ch_max_units=text_scale.ch_max_units,
                    jp_max_units=text_scale.jp_max_units,
                    context_title=base_name,
                )
                final_srt = translator.run(
                    cancel_event=cancel_event,
                    progress_cb=lambda value, text: msg_queue.put(progress(value, text)),
                )
                summary.update(
                    {
                        "translation_elapsed_seconds": int(time.time() - translation_started_at),
                        "translation_batches": int(getattr(translator, "total_batches", 0) or 0),
                        "translation_cache_hits": int(getattr(translator, "cache_hits", 0) or 0),
                        "translation_api_calls": int(getattr(translator, "api_calls", 0) or 0),
                        "translation_failed_batches": int(getattr(translator, "failed_batches", 0) or 0),
                        "translation_alignment_fallbacks": int(getattr(translator, "alignment_fallbacks", 0) or 0),
                        "translation_bilingual_coverage_retries": int(
                            getattr(translator, "bilingual_coverage_retries", 0) or 0
                        ),
                        "translation_model": str(getattr(translator, "active_model", primary_model) or primary_model),
                        "translation_returned_models": sorted(
                            str(model) for model in (getattr(translator, "returned_models", set()) or set()) if model
                        ),
                        "translation_speaker_labels_removed": int(
                            getattr(translator, "speaker_labels_removed", 0) or 0
                        ),
                        "translation_speaker_label_warnings": int(
                            getattr(translator, "speaker_label_warnings", 0) or 0
                        ),
                        "translation_lyric_alignment_warnings": int(
                            getattr(translator, "lyric_alignment_warnings", 0) or 0
                        ),
                        "translation_lyric_gap_warnings": int(
                            getattr(translator, "lyric_gap_warnings", 0) or 0
                        ),
                    }
                )
            finally:
                _safe_unlink(temp_lyrics_path)
            if not final_srt:
                raise RuntimeError("翻译阶段未返回有效字幕，请检查 API Key、Base URL、模型名或代理设置。")
            if not _update_translation_completion_marker(
                trans_hash_path,
                trans_hash,
                int(getattr(translator, "failed_batches", 0) or 0),
            ):
                print("[翻译缓存] 部分批次未完成；已保留成功批次，重新启动只补失败部分。")
        else:
            msg_queue.put(status("⚠️ 未配置 API Key，跳过翻译。", 0.60))
            print("⚠️ 未检测到 API Key，跳过翻译流程。")

        if cancel_event.is_set():
            msg_queue.put(done("任务已取消。"))
            return

        if final_srt and ass_enabled:
            from core.ass_maker import ASSMaker, ASS_PRESETS

            _load_user_presets(ASS_PRESETS)
            msg_queue.put(status("🎨 正在生成 ASS 特效字幕...", 0.95))
            ass_config = build_ass_config(ASS_PRESETS, settings)
            ass_config = apply_layout_policy(ass_config, layout_policy, settings)
            print(policy_log_line(layout_policy, ass_config))
            ass_started_at = time.perf_counter()
            ass_path = ASSMaker(str(final_srt)).generate_ass(ass_config)
            summary["ass_elapsed_seconds"] = round(time.perf_counter() - ass_started_at, 3)
            final_ass = str(Path(ass_path).resolve())
            summary["ass_generated"] = True
            summary["layout_profile"] = layout_policy.profile
            summary["layout_note"] = layout_policy.note
            summary["subtitle_ch_size"] = int(ass_config.get("ch_size", 0) or 0)
            summary["subtitle_jp_size"] = int(ass_config.get("jp_size", 0) or 0)
            summary["subtitle_ch_max_units"] = int(ass_config.get("ch_max_units", 0) or 0)
            summary["subtitle_jp_max_units"] = int(ass_config.get("jp_max_units", 0) or 0)
            print(f"✅ 特效花字已存盘: {Path(ass_path).name}")

        artifacts = {
            "output_dir": str(subtitles_dir.resolve()),
            "srt": str(Path(final_srt).resolve()) if final_srt else "",
            "source_srt": str(Path(srt_path).resolve()) if srt_path else "",
            "translated_srt": (
                str(Path(final_srt).resolve())
                if final_srt and Path(final_srt).resolve() != Path(srt_path).resolve()
                else ""
            ),
            "ass": final_ass or "",
            "media": str(Path(media_path).resolve()),
            "asr_diagnostics": asr_diagnostics_path,
        }
        summary["total_elapsed_seconds"] = round(time.time() - task_started_at, 3)
        try:
            performance_text, performance_json = write_task_performance_report(output_dir, summary)
            artifacts["log"] = str(performance_text)
            artifacts["performance_report"] = str(performance_text)
            artifacts["performance_json"] = str(performance_json)
            print(f"[性能报告] 已保存: {_display_path(performance_text)}")
        except Exception as exc:
            print(f"[性能报告] 保存失败，字幕结果不受影响: {exc}")
        msg_queue.put(done("🎉 Next 管线处理完成！", artifacts=artifacts, summary=summary))
    except PipelineUserError as exc:
        msg_queue.put(error(f"❌ {exc}"))
    except Exception as exc:
        if cancel_event.is_set():
            msg_queue.put(error("🛑 任务已由用户取消。"))
        else:
            msg_queue.put(log(traceback.format_exc()))
            msg_queue.put(error(f"❌ Next 管线异常: {exc}"))


def _prepare_engine_runtime() -> None:
    apply_next_cache_env()
    location = find_engine_location()
    if not location.root or not location.site_packages:
        raise PipelineUserError(
            "没有找到 Next 独立引擎环境。\n"
            "请运行：powershell -ExecutionPolicy Bypass -File UtaSyncNext\\tools\\bootstrap_venv.ps1 -Engine"
        )
    if location.missing_modules:
        missing = "、".join(location.missing_modules)
        raise PipelineUserError(
            f"Next 独立引擎环境依赖未完整，缺少：{missing}\n"
            "请运行：powershell -ExecutionPolicy Bypass -File UtaSyncNext\\tools\\bootstrap_venv.ps1 -Engine"
        )
    if location.site_packages:
        site.addsitedir(str(location.site_packages))
        os.environ["UTASYNC_SITE_PACKAGES"] = str(location.site_packages)
    if location.scripts:
        os.environ["PATH"] = str(location.scripts) + os.pathsep + os.environ.get("PATH", "")
    if location.root:
        os.environ["UTASYNC_HOOKED_ENV"] = str(location.root)

    _register_dll_dirs(location.site_packages)


def _register_dll_dirs(site_packages: Path | None) -> None:
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return

    candidates: list[Path] = []
    if site_packages:
        candidates.extend(
            [
                site_packages,
                site_packages / "torch" / "lib",
                site_packages / "nvidia" / "cublas" / "bin",
                site_packages / "nvidia" / "cudnn" / "bin",
                site_packages / "nvidia" / "cufft" / "bin",
                site_packages / "nvidia" / "curand" / "bin",
                site_packages / "nvidia" / "cusolver" / "bin",
                site_packages / "nvidia" / "cusparse" / "bin",
                site_packages / "nvidia" / "nvtx" / "bin",
            ]
        )

    for candidate in candidates:
        if candidate.exists():
            os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
            try:
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(candidate)))
            except Exception:
                pass


def _ensure_demucs_weights_available() -> None:
    site_packages_text = os.environ.get("UTASYNC_SITE_PACKAGES", "").strip()
    if not site_packages_text:
        return

    remote_dir = Path(site_packages_text) / "demucs" / "remote"
    bag_path = remote_dir / "htdemucs_ft.yaml"
    files_path = remote_dir / "files.txt"
    if not bag_path.exists() or not files_path.exists():
        return

    bag_text = bag_path.read_text(encoding="utf-8", errors="ignore")
    signatures = re.findall(r"['\"]([0-9a-f]{8})['\"]", bag_text)
    if not signatures:
        return

    available_names: dict[str, str] = {}
    for raw_line in files_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.endswith(".th") or "-" not in line:
            continue
        signature = line.split("-", 1)[0]
        available_names[signature] = line

    required = [available_names.get(signature, f"{signature}.th") for signature in signatures]
    checkpoints_dir = _torch_checkpoints_dir()
    missing = [name for name in required if not _is_non_empty_file(checkpoints_dir / name)]
    if not missing:
        return

    missing_text = "\n".join(f"- {name}" for name in missing)
    raise PipelineUserError(
        "Demucs htdemucs_ft 权重尚未准备好，已在分离前停止。\n"
        f"缺少文件:\n{missing_text}\n"
        f"请先下载/导入到:\n{checkpoints_dir}\n"
        "临时想继续打轴，可以在左侧选择「强制跳过」Demucs。"
    )


def _torch_checkpoints_dir() -> Path:
    torch_home = os.environ.get("TORCH_HOME", "").strip()
    if torch_home:
        return Path(torch_home) / "hub" / "checkpoints"

    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg_cache:
        return Path(xdg_cache) / "torch" / "hub" / "checkpoints"

    return app_root() / ".cache" / "torch" / "hub" / "checkpoints"


def _is_non_empty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _normalize_pipeline_settings(
    settings: dict[str, Any],
    hardware_profile: HardwareProfile | None = None,
) -> tuple[str, str, str, bool, str]:
    audio_raw = str(settings.get("audio_type", "Live 现场演唱 (常规/摇滚)"))
    audio_type = "speech" if "访谈" in audio_raw else ("live_soft" if "极柔气声" in audio_raw else "live")
    model_size = str(settings.get("model_size", "large-v2")).split(" ")[0]

    hardware_mode, _ = resolve_demucs_hardware_mode(settings.get("hardware_mode"), hardware_profile)

    demucs_raw = str(settings.get("use_demucs", "先跳过 Demucs"))
    skip_demucs = "跳过" in demucs_raw or "Skip" in demucs_raw or audio_type == "speech"

    output_raw = str(settings.get("output_mode", "中日双语字幕"))
    output_mode = "chinese_only" if "纯中文" in output_raw else "bilingual"
    return audio_type, model_size, hardware_mode, skip_demucs, output_mode


def _resolve_whisper_model_arg(model_size: str) -> WhisperModelRef:
    for models_root, source_label in _model_roots():
        snapshot_path = _find_whisper_snapshot(model_size, models_root)
        if snapshot_path:
            return WhisperModelRef(model_size, snapshot_path, source_label)

    available_parts: list[str] = []
    incomplete_parts: list[str] = []
    for models_root, source_label in _model_roots():
        available = _available_whisper_models(models_root)
        incomplete = _incomplete_whisper_models(models_root)
        available_parts.append(f"{source_label}: {('、'.join(available) if available else '无完整模型')}")
        if incomplete:
            incomplete_parts.append(f"{source_label}: {'、'.join(incomplete)}")

    incomplete_text = ""
    if incomplete_parts:
        incomplete_text = "\n检测到未完整下载模型：" + "；".join(incomplete_parts) + "。"

    raise PipelineUserError(
        f"本地未找到 Faster-Whisper 模型 [{model_size}]，已阻止自动联网下载。\n"
        f"当前完整可用模型：{'；'.join(available_parts)}。{incomplete_text}\n"
        "请在左侧选择已完整下载模型，或通过「引擎与模型管理」下载对应模型后再运行。"
    )


def _display_path(path: str | Path) -> str:
    """Return a short user-facing path while keeping internal artifacts absolute."""
    raw = str(path)
    try:
        resolved = Path(path).resolve()
        root = app_root().resolve()
        try:
            return "<Next>\\" + str(resolved.relative_to(root))
        except ValueError:
            return resolved.name or raw
    except Exception:
        return Path(raw).name or raw


def _model_roots() -> list[tuple[Path, str]]:
    next_root = app_root() / "models_faster"
    return [(next_root, "Next 本地")]


def _is_whisper_model_available(model_size: str, models_root: Path) -> bool:
    return _find_whisper_snapshot(model_size, models_root) is not None


def _find_whisper_snapshot(model_size: str, models_root: Path) -> Path | None:
    return find_complete_snapshot(model_size, models_root)


def _available_whisper_models(models_root: Path) -> list[str]:
    if not models_root.exists():
        return []

    found: list[str] = []
    for model in ["large-v2", "large-v3", "large-v3-turbo", "medium", "small", "base"]:
        if _is_whisper_model_available(model, models_root):
            found.append(model)
    return found


def _incomplete_whisper_models(models_root: Path) -> list[str]:
    if not models_root.exists():
        return []

    incomplete: list[str] = []
    for model in ["large-v2", "large-v3", "large-v3-turbo", "medium", "small", "base"]:
        if _is_whisper_model_available(model, models_root):
            continue
        if _has_whisper_cache_dir(model, models_root):
            incomplete.append(model)
    return incomplete


def _has_whisper_cache_dir(model_size: str, models_root: Path) -> bool:
    return has_cache_dir(model_size, models_root)


def _format_schedule_summary(
    audio_type: str,
    model_size: str,
    hardware_mode: str,
    skip_demucs: bool,
    output_mode: str,
    *,
    hardware_reason: str = "",
) -> str:
    audio_label = {
        "live": "Live 常规/摇滚",
        "live_soft": "Live 极柔气声/清唱",
        "speech": "访谈/播客",
    }.get(audio_type, audio_type)
    if skip_demucs:
        hardware_label = "本次跳过人声分离，不使用 Demucs 显存档位"
    else:
        hardware_label = {
            "low": "保守分离：60 秒切片 + Demucs segment=2",
            "medium": "均衡分离：180 秒切片",
            "high": "快速分离：1800 秒切片",
        }.get(hardware_mode, hardware_mode)
        if hardware_reason:
            hardware_label += f"（{hardware_reason}）"
    demucs_label = "跳过 Demucs，直接听写源音频" if skip_demucs else "启用 Demucs 人声分离"
    output_label = "纯中文字幕" if output_mode == "chinese_only" else "中日双语字幕"
    return (
        "🧭 [Next 调度]\n"
        f"   -> 声学模型: {audio_label}\n"
        f"   -> Whisper 模型: {model_size}\n"
        f"   -> 算力策略: {hardware_label}\n"
        f"   -> 分离策略: {demucs_label}\n"
        f"   -> 输出模式: {output_label}"
    )


def _load_user_presets(ass_presets: dict[str, dict[str, Any]]) -> None:
    preset_path = Path("user_presets.json")
    if not preset_path.exists():
        return
    try:
        data = json.loads(preset_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            ass_presets.update(data)
    except Exception:
        pass


def _try_reuse_asr(
    expected_srt: Path,
    hash_path: Path,
    current_hash: str,
    *,
    legacy_hashes: tuple[str, ...] = (),
) -> str | None:
    if not _is_valid_srt(expected_srt):
        return None
    stored_hash = _read_text(hash_path)
    if stored_hash == current_hash:
        return str(expected_srt)
    if stored_hash in legacy_hashes:
        # One-time migration from the pre-v7 marker.  The SRT itself remains
        # untouched; only the marker is upgraded after a validated reuse.
        hash_path.write_text(current_hash, encoding="utf-8")
        return str(expected_srt)
    return None


def _asr_cache_reason(
    expected_srt: Path,
    hash_path: Path,
    current_hash: str,
    *,
    legacy_hashes: tuple[str, ...] = (),
) -> str:
    if not expected_srt.exists():
        return "没有历史打轴字幕"
    if not _is_valid_srt(expected_srt):
        return "历史打轴字幕不完整"
    stored_hash = _read_text(hash_path)
    if not stored_hash:
        return "缺少完整打轴标记"
    if stored_hash == current_hash or stored_hash in legacy_hashes:
        return ""
    if stored_hash != current_hash:
        return "媒体、模型、模式或打轴规则已变化"
    return ""


def _translation_batch_cache_count(source_srt: str | Path) -> int:
    source_path = Path(source_srt)
    source_base = source_path.with_suffix("")
    cache_path = source_base.with_name(f"{source_base.name}_翻译缓存.json")
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return len(data) if isinstance(data, dict) else 0
    except (OSError, ValueError, TypeError):
        return 0


def _translation_output_path(source_srt: str | Path, output_mode: str) -> Path:
    source_path = Path(source_srt)
    base_path = source_path.with_suffix("")
    suffix = "纯中文字幕" if output_mode == "chinese_only" else "双语字幕"
    return base_path.with_name(f"{base_path.name}_{suffix}.srt")


def _translation_completion_cache_hit(
    hash_path: str | Path,
    expected_hash: str,
    translated_srt: str | Path,
) -> bool:
    return _read_text(Path(hash_path)) == expected_hash and _is_valid_srt(translated_srt)


def _srt_batch_count(source_srt: str | Path, batch_size: int = 12) -> int:
    try:
        cue_count = Path(source_srt).read_text(encoding="utf-8-sig", errors="ignore").count("-->")
    except OSError:
        return 0
    return (cue_count + batch_size - 1) // batch_size if cue_count else 0


def _is_valid_srt(path: str | Path | None) -> bool:
    if not path:
        return False
    srt_path = Path(path)
    try:
        if not srt_path.exists() or srt_path.stat().st_size <= 0:
            return False
        return "-->" in srt_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


def _translation_models_for(model: str) -> tuple[str, str]:
    selected = str(model or "").strip() or "deepseek-ai/DeepSeek-V3"
    return selected, selected


def _hash_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _build_asr_cache_hash(
    *,
    media_fingerprint: dict[str, Any],
    audio_type: str,
    model_size: str,
    model_ref: WhisperModelRef,
    skip_demucs: bool,
    hardware_mode: str,
    layout_policy: Any | None = None,
    reference_lyrics: str = "",
) -> str:
    # Reference lyrics are a post-processing/translation input, not an ASR
    # input.  Keeping them out of this marker lets users correct or replace
    # lyrics without forcing an expensive Demucs/Whisper rerun.  The argument
    # remains for call-site compatibility and documents that lyric-aware
    # cleanup happens after ASR cache reuse.
    payload: dict[str, Any] = {
        "version": 7,
        "media": media_fingerprint,
        "audio_type": audio_type,
        "model_size": model_size,
        "model_path": str(model_ref.path),
        "model_snapshot": model_ref.path.name,
        "skip_demucs": skip_demucs,
        "hardware_mode": hardware_mode if not skip_demucs else "low",
        "subtitle_timing_policy": "full_window_reflow_and_conservative_filter_v2",
        "opening_cleanup_policy": "lyrics-anchored-opening-cleanup-v2",
        "watermark_cleanup_policy": "watermark-cluster-cleanup-v1",
    }
    profile = str(getattr(layout_policy, "profile", ""))
    if profile in {"square_safe", "portrait_safe"}:
        payload["subtitle_density"] = {
            "profile": profile,
            "jp_max_units": int(getattr(layout_policy, "jp_max_units", 0)),
            "max_visual_lines": 2,
            "target_duration_seconds": 5,
        }
    return _hash_payload(payload)


def _build_legacy_asr_cache_hash(
    *,
    media_fingerprint: dict[str, Any],
    audio_type: str,
    model_size: str,
    model_ref: WhisperModelRef,
    skip_demucs: bool,
    hardware_mode: str,
    layout_policy: Any | None = None,
    reference_lyrics: str = "",
) -> str:
    """Build the pre-v7 marker so existing caches can be upgraded safely."""
    payload: dict[str, Any] = {
        "version": 6,
        "media": media_fingerprint,
        "audio_type": audio_type,
        "model_size": model_size,
        "model_path": str(model_ref.path),
        "model_snapshot": model_ref.path.name,
        "skip_demucs": skip_demucs,
        "hardware_mode": hardware_mode if not skip_demucs else "low",
        "subtitle_timing_policy": "full_window_reflow_and_conservative_filter_v2",
        "opening_cleanup_policy": "lyrics-anchored-opening-cleanup-v2",
        "opening_cleanup_lyrics": reference_lyrics,
        "watermark_cleanup_policy": "watermark-cluster-cleanup-v1",
    }
    if reference_lyrics:
        payload["lyric_gap_rescan_policy"] = "lyrics-gap-word-alignment-preview-v1"
    profile = str(getattr(layout_policy, "profile", ""))
    if profile in {"square_safe", "portrait_safe"}:
        payload["subtitle_density"] = {
            "profile": profile,
            "jp_max_units": int(getattr(layout_policy, "jp_max_units", 0)),
            "max_visual_lines": 2,
            "target_duration_seconds": 5,
        }
    return _hash_payload(payload)


def _build_translation_cache_hash(
    *,
    media_fingerprint: dict[str, Any],
    source_srt: str | Path | None,
    lyrics_text: str,
    output_mode: str,
    settings: dict[str, Any],
    layout_policy: Any | None = None,
) -> str:
    layout_payload = {}
    if layout_policy is not None:
        layout_payload = {
            "profile": getattr(layout_policy, "profile", ""),
            "ch_max_units": getattr(layout_policy, "ch_max_units", 0),
            "jp_max_units": getattr(layout_policy, "jp_max_units", 0),
        }
    return _hash_payload(
        {
            "version": 8,
            "media": media_fingerprint,
            "source_srt": _file_content_hash(source_srt),
            "lyrics": lyrics_text,
            "output_mode": output_mode,
            "api_url": settings.get("api_url"),
            "api_model": settings.get("api_model"),
            "model_policy": "user_selected_v1",
            "layout": layout_payload,
        }
    )


def _invalidate_translation_completion(hash_path: str | Path, expected_hash: str) -> bool:
    marker_path = Path(hash_path)
    if _read_text(marker_path) == expected_hash:
        return False
    # Batch cache keys already include source text, model, prompt policy, lyrics,
    # layout and title. Keep valid completed batches so failed jobs can resume.
    _safe_unlink(marker_path)
    return True


def _update_translation_completion_marker(hash_path: str | Path, expected_hash: str, failed_batches: int) -> bool:
    marker_path = Path(hash_path)
    if failed_batches > 0:
        _safe_unlink(marker_path)
        return False
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(expected_hash, encoding="utf-8")
    return True


def _hash_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _media_fingerprint(media_path: str | Path) -> dict[str, Any]:
    path = Path(media_path).resolve()
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


def _file_content_hash(path: str | Path | None) -> str:
    if not path:
        return ""
    target = Path(path)
    digest = hashlib.md5()
    with target.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
