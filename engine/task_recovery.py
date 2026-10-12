from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from engine.cache_env import app_root, task_base_name


_SENSITIVE_KEY_RE = re.compile(r"(api[_\s-]?key\s*[:=]\s*)(\S+)", re.IGNORECASE)


def inspect_recovery_state(media_path: str, output_root: Path | None = None) -> dict[str, object]:
    """Describe durable task artifacts without assuming they are safe to reuse."""
    base_name = task_base_name(media_path)
    root = output_root or (app_root() / "output")
    task_dir = root / base_name
    subtitles_dir = task_dir / "subtitles"
    logs_dir = task_dir / "logs"
    source_srt = subtitles_dir / f"{base_name}_极致打轴.srt"
    diagnostics = subtitles_dir / f"{base_name}_极致打轴_识别诊断.json"
    translation_cache = subtitles_dir / f"{base_name}_极致打轴_翻译缓存.json"
    audio_dir = task_dir / "audio"

    has_source_srt = _is_valid_srt(source_srt)
    has_audio_cache = bool(audio_dir.exists() and any(path.is_file() for path in audio_dir.rglob("*")))
    has_translation_cache = _is_non_empty_file(translation_cache)
    has_asr_fingerprint = _is_non_empty_file(subtitles_dir / f"{base_name}_打轴指纹.hash")

    reusable_parts: list[str] = []
    if has_source_srt:
        reusable_parts.append("原文打轴")
    elif has_audio_cache:
        reusable_parts.append("音频缓存")
    if has_translation_cache:
        reusable_parts.append("已完成的翻译批次")

    can_skip_asr = bool(has_source_srt and has_asr_fingerprint)
    can_resume_translation = bool(can_skip_asr and has_translation_cache)

    if can_skip_asr:
        advice = "原文打轴和校验信息已保留。使用相同媒体与设置再次启动，会跳过重新听写。"
    elif has_source_srt:
        advice = "原文打轴已保留，但缺少校验信息，系统不会直接复用；再次启动时仍会重新听写。"
    elif has_audio_cache:
        advice = "音频缓存已保留，再次启动可跳过提取或分离；本次未完成的打轴进度不能续接，仍会从头听写。"
    else:
        advice = "没有发现可复用的中间结果。修复问题后可直接重新启动任务。"
    if can_resume_translation:
        advice += " 已完成的翻译批次会保留，翻译失败后无需从头请求。"
    elif has_translation_cache:
        advice += " 检测到翻译批次缓存，但需先重新生成并校验原文打轴，不能保证直接续接。"

    artifacts: dict[str, str] = {}
    if subtitles_dir.exists():
        artifacts["output_dir"] = str(subtitles_dir.resolve())
    elif logs_dir.exists():
        artifacts["output_dir"] = str(logs_dir.resolve())
    if has_source_srt:
        artifacts["srt"] = str(source_srt.resolve())
    if diagnostics.exists():
        artifacts["asr_diagnostics"] = str(diagnostics.resolve())

    return {
        "base_name": base_name,
        "task_dir": str(task_dir.resolve()),
        "has_source_srt": has_source_srt,
        "has_audio_cache": has_audio_cache,
        "has_translation_cache": has_translation_cache,
        "has_asr_fingerprint": has_asr_fingerprint,
        "resume_available": bool(reusable_parts),
        "can_reuse_audio": has_audio_cache,
        "can_skip_asr": can_skip_asr,
        "can_resume_translation": can_resume_translation,
        "resume_scope": (
            "translation"
            if can_resume_translation
            else "post_asr"
            if can_skip_asr
            else "audio_only"
            if has_audio_cache
            else "none"
        ),
        "reusable_parts": reusable_parts,
        "advice": advice,
        "artifacts": artifacts,
    }


def write_failure_reports(
    *,
    payload: dict[str, object],
    error_text: str,
    run_log: list[str],
    last_status: str,
    output_root: Path | None = None,
) -> tuple[Path, Path, dict[str, object]]:
    """Save an ordinary-language error log plus a machine-readable recovery record."""
    media_path = str(payload.get("media_path") or "")
    root = output_root or (app_root() / "output")
    recovery = inspect_recovery_state(media_path, root)
    logs_dir = Path(str(recovery["task_dir"])) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"utasyncnext_error_{stamp}.txt"
    report_path = logs_dir / f"utasyncnext_recovery_{stamp}.json"

    safe_error = _redact_text(error_text, payload)
    safe_status = _redact_text(last_status, payload)
    safe_log = _redact_text("".join(run_log).strip(), payload)
    reusable = "、".join(str(item) for item in recovery["reusable_parts"]) or "无"
    content = "\n".join(
        [
            "UtaSyncNext 任务错误日志",
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"媒体: {Path(media_path).name if media_path else '未选择'}",
            f"最后状态: {safe_status or '未收到阶段状态'}",
            f"错误: {safe_error}",
            "",
            "恢复建议",
            f"可复用结果: {reusable}",
            f"下一步: {recovery['advice']}",
            f"任务目录: {recovery['task_dir']}",
            "",
            "---- 原始任务日志 ----",
            safe_log,
            "",
        ]
    )
    log_path.write_text(content, encoding="utf-8")
    record = {
        "schema": "utasyncnext-recovery-v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "media_path": media_path,
        "error": safe_error,
        "last_status": safe_status,
        "recovery": recovery,
        "settings": _safe_settings(payload.get("settings")),
    }
    report_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return log_path, report_path, recovery


def _safe_settings(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if not any(token in str(key).lower() for token in ("key", "token", "secret", "password"))
    }


def _redact_text(text: str, payload: dict[str, object]) -> str:
    safe = str(text or "")
    settings = payload.get("settings")
    if isinstance(settings, dict):
        for key, value in settings.items():
            if any(token in str(key).lower() for token in ("key", "token", "secret", "password")) and value:
                safe = safe.replace(str(value), "[已隐藏]")
    return _SENSITIVE_KEY_RE.sub(r"\1[已隐藏]", safe)


def _is_non_empty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _is_valid_srt(path: Path) -> bool:
    if not _is_non_empty_file(path):
        return False
    try:
        return " --> " in path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return False
