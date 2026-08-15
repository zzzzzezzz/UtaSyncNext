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

from core.ass_config import build_ass_config
from core.subtitle_policy import apply_layout_policy, policy_log_line, resolve_subtitle_layout
from engine.cache_env import app_root, apply_next_cache_env
from engine.locator import find_engine_location
from engine.model_registry import find_complete_snapshot, has_cache_dir
from engine.protocol import done, error, log, progress, status


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

    audio_type, model_size, hardware_mode, skip_demucs, output_mode = _normalize_pipeline_settings(settings)
    api_key = str(settings.get("api_key") or "").strip()
    api_model = str(settings.get("api_model") or "").strip() or "deepseek-ai/DeepSeek-V3"
    ass_enabled = str(settings.get("ass_preset")) != "不生成 (仅SRT)"
    lyrics_text = "" if "留空" in raw_lyrics else raw_lyrics

    preview["will_call_api"] = bool(api_key)
    preview["will_run_demucs"] = not skip_demucs

    lines.append(f"🎬 媒体: {media.name}")
    lines.append(f"🧠 Whisper: {model_size}")
    lines.append("🎸 Demucs: 将跳过，优先快速出轴。" if skip_demucs else "🎸 Demucs: 将启用高质量人声分离，首次会很慢。")
    lines.append("🌍 翻译: 将调用 API。" if api_key else "🌍 翻译: 未配置 API Key，本次只生成原文字幕。")
    if api_key:
        lines.append(f"   -> 模型: {api_model}")
    lines.append("🎨 ASS: 将生成特效字幕。" if ass_enabled else "🎨 ASS: 仅生成 SRT。")

    try:
        model_ref = _resolve_whisper_model_arg(model_size)
        media_fingerprint = _media_fingerprint(media)
        layout_policy = resolve_subtitle_layout(media_path, settings)
        base_name = media.stem
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
        )
        asr_cache_hit = bool(_try_reuse_asr(expected_srt, asr_hash_path, asr_hash))
        preview["asr_cache_hit"] = asr_cache_hit
        lines.append("⚡ 打轴缓存: 命中，预计可直达翻译/ASS。" if asr_cache_hit else "⏳ 打轴缓存: 未命中，将重新听写。")

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
            translation_cache_hit = _read_text(trans_hash_path) == trans_hash
            preview["translation_cache_hit"] = translation_cache_hit
            lines.append("⚡ 翻译缓存: 命中，预计无需重新请求 API。" if translation_cache_hit else "⏳ 翻译缓存: 未命中，将请求 API。")
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

        audio_type, model_size, hardware_mode, skip_demucs, output_mode = _normalize_pipeline_settings(settings)
        lyrics_text = "" if "留空" in raw_lyrics else raw_lyrics
        api_key = str(settings.get("api_key") or "").strip()
        api_model = str(settings.get("api_model") or "deepseek-ai/DeepSeek-V3").strip()
        ass_enabled = str(settings.get("ass_preset")) != "不生成 (仅SRT)"
        layout_policy = resolve_subtitle_layout(media_path, settings)
        summary: dict[str, object] = {
            "media_name": Path(media_path).name,
            "asr_model": model_size,
            "audio_type": audio_type,
            "hardware_mode": hardware_mode,
            "demucs_skipped": skip_demucs,
            "output_mode": output_mode,
            "translation_requested": bool(api_key),
            "api_model": api_model if api_key else "",
            "ass_enabled": ass_enabled,
            "asr_cache_hit": False,
            "audio_cache_hit": False,
            "asr_backend": "",
            "asr_elapsed_seconds": 0,
            "translation_elapsed_seconds": 0,
            "translation_batches": 0,
            "translation_cache_hits": 0,
            "translation_api_calls": 0,
            "translation_failed_batches": 0,
            "translation_alignment_fallbacks": 0,
            "translation_speaker_label_warnings": 0,
            "translation_speaker_labels_removed": 0,
            "translation_lyric_alignment_warnings": 0,
            "translation_model": api_model if api_key else "",
            "translation_returned_models": [],
            "ass_generated": False,
            "cuda_fallback_used": False,
            "layout_profile": layout_policy.profile,
            "layout_note": layout_policy.note,
        }

        base_name = Path(media_path).stem
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
        )

        print("\n" + "=" * 50)
        print(f"🚀 [Next 任务总线启动] 目标文件: {Path(media_path).name}")
        print(_format_schedule_summary(audio_type, model_size, hardware_mode, skip_demucs, output_mode))
        if not skip_demucs:
            _ensure_demucs_weights_available()

        if cancel_event.is_set():
            msg_queue.put(done("任务已取消。"))
            return

        srt_path = _try_reuse_asr(expected_srt, asr_hash_path, asr_hash)
        summary["asr_cache_hit"] = bool(srt_path)
        if srt_path:
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
        _invalidate_translation_completion(trans_hash_path, trans_hash)

        final_srt = srt_path
        final_ass: str | None = None
        if api_key:
            from core.translator import SubtitleTranslator

            msg_queue.put(status("🌍 正在调用大模型翻译...", 0.60))
            temp_lyrics_path = output_dir / "temp_lyrics_next.txt"
            temp_lyrics_path.write_text(lyrics_text, encoding="utf-8")

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
                ch_max_units=layout_policy.ch_max_units,
                jp_max_units=layout_policy.jp_max_units,
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
                }
            )
            _safe_unlink(temp_lyrics_path)
            if not final_srt:
                raise RuntimeError("翻译阶段未返回有效字幕，请检查 API Key、Base URL、模型名或代理设置。")
            trans_hash_path.write_text(trans_hash, encoding="utf-8")
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
            print(policy_log_line(layout_policy))
            ass_config = build_ass_config(ASS_PRESETS, settings)
            ass_config = apply_layout_policy(ass_config, layout_policy)
            ass_path = ASSMaker(str(final_srt)).generate_ass(ass_config)
            final_ass = str(Path(ass_path).resolve())
            summary["ass_generated"] = True
            summary["layout_profile"] = layout_policy.profile
            summary["layout_note"] = layout_policy.note
            print(f"✅ 特效花字已存盘: {Path(ass_path).name}")

        artifacts = {
            "output_dir": str(subtitles_dir.resolve()),
            "srt": str(Path(final_srt).resolve()) if final_srt else "",
            "ass": final_ass or "",
            "media": str(Path(media_path).resolve()),
        }
        summary["total_elapsed_seconds"] = int(time.time() - task_started_at)
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
                os.add_dll_directory(str(candidate))
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


def _normalize_pipeline_settings(settings: dict[str, Any]) -> tuple[str, str, str, bool, str]:
    audio_raw = str(settings.get("audio_type", "Live 现场演唱 (常规/摇滚)"))
    audio_type = "speech" if "访谈" in audio_raw else ("live_soft" if "极柔气声" in audio_raw else "live")
    model_size = str(settings.get("model_size", "large-v2")).split(" ")[0]

    hardware_raw = str(settings.get("hardware_mode", "极限防爆"))
    hardware_mode = "medium" if "均衡" in hardware_raw else ("high" if "满血" in hardware_raw else "low")

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


def _format_schedule_summary(audio_type: str, model_size: str, hardware_mode: str, skip_demucs: bool, output_mode: str) -> str:
    audio_label = {
        "live": "Live 常规/摇滚",
        "live_soft": "Live 极柔气声/清唱",
        "speech": "访谈/播客",
    }.get(audio_type, audio_type)
    hardware_label = {
        "low": "极限防爆：60 秒切片 + Demucs segment=2",
        "medium": "均衡提速：180 秒切片",
        "high": "满血狂飙：1800 秒切片",
    }.get(hardware_mode, hardware_mode)
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


def _try_reuse_asr(expected_srt: Path, hash_path: Path, current_hash: str) -> str | None:
    if _is_valid_srt(expected_srt) and _read_text(hash_path) == current_hash:
        return str(expected_srt)
    return None


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
) -> str:
    payload: dict[str, Any] = {
        "version": 2,
        "media": media_fingerprint,
        "audio_type": audio_type,
        "model_size": model_size,
        "model_path": str(model_ref.path),
        "model_snapshot": model_ref.path.name,
        "skip_demucs": skip_demucs,
        "hardware_mode": hardware_mode,
    }
    profile = str(getattr(layout_policy, "profile", ""))
    if profile in {"square_safe", "portrait_safe"}:
        payload["version"] = 3
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
