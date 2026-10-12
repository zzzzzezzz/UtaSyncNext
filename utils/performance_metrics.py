from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any

from engine.cache_env import app_root


def collect_process_memory() -> dict[str, float]:
    if os.name != "nt":
        return {}

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        process = kernel32.GetCurrentProcess()

        get_memory_info = getattr(kernel32, "K32GetProcessMemoryInfo", None)
        if get_memory_info is None:
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            get_memory_info = psapi.GetProcessMemoryInfo
        get_memory_info.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        get_memory_info.restype = wintypes.BOOL
        ok = get_memory_info(process, ctypes.byref(counters), counters.cb)
        if not ok:
            return {}
    except Exception:
        return {}

    unit = 1024 * 1024
    return {
        "process_memory_mb": round(counters.WorkingSetSize / unit, 1),
        "process_peak_memory_mb": round(counters.PeakWorkingSetSize / unit, 1),
    }


def collect_gpu_info() -> dict[str, object]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"gpu_detected": False}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            creationflags=creationflags,
        )
        first_line = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
        parts = [part.strip() for part in first_line.split(",")]
        if result.returncode != 0 or len(parts) < 3:
            return {"gpu_detected": False}
        return {
            "gpu_detected": True,
            "gpu_name": parts[0],
            "gpu_memory_total_mb": int(float(parts[1])),
            "gpu_driver": parts[2],
        }
    except Exception:
        return {"gpu_detected": False}


def write_task_performance_report(output_dir: str | Path, summary: dict[str, object]) -> tuple[Path, Path]:
    report_dir = Path(output_dir) / "logs"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"utasyncnext_performance_{stamp}.json"
    text_path = report_dir / f"utasyncnext_performance_{stamp}.txt"
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        **summary,
        **collect_process_memory(),
        **collect_gpu_info(),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    text_path.write_text(_performance_text(payload), encoding="utf-8")
    return text_path.resolve(), json_path.resolve()


def record_startup_metric(seconds: float, engine_label: str, accelerator: str) -> Path:
    report_dir = app_root() / "performance"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "startup_history.jsonl"
    payload: dict[str, Any] = {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "ui_ready_seconds": round(max(0.0, seconds), 3),
        "engine_label": engine_label,
        "accelerator": accelerator,
        **collect_process_memory(),
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def _performance_text(payload: dict[str, object]) -> str:
    process_memory = _format_memory(payload.get("process_memory_mb"))
    process_peak_memory = _format_memory(payload.get("process_peak_memory_mb"))
    cache_reason = str(payload.get("asr_cache_reason") or "").strip()
    if payload.get("asr_cache_hit"):
        audio_cache_status = "未使用（已直接复用打轴结果）"
    else:
        audio_cache_status = "命中" if payload.get("audio_cache_hit") else "未命中"
    lines = [
        "UtaSyncNext 本次任务性能报告",
        f"生成时间：{payload.get('generated_at', '')}",
        f"媒体：{payload.get('media_name', '')}",
        f"打轴模型：{payload.get('asr_model', '')}",
        f"实际后端：{payload.get('asr_backend', '') or '未重新打轴'}",
        f"显卡：{payload.get('gpu_name', '') or '未检测到 NVIDIA 显卡'}",
        f"显存总量：{payload.get('gpu_memory_total_mb', 0)} MB",
        f"处理进程当前内存：{process_memory}",
        f"处理进程峰值内存：{process_peak_memory}",
        "",
        "阶段耗时：",
        f"- 音频提取：{payload.get('audio_extract_elapsed_seconds', 0)} 秒",
        f"- 人声分离：{payload.get('demucs_elapsed_seconds', 0)} 秒",
        f"- 模型加载：{payload.get('model_load_elapsed_seconds', 0)} 秒",
        f"- 语音识别：{payload.get('transcription_elapsed_seconds', 0)} 秒",
        f"- 翻译：{payload.get('translation_elapsed_seconds', 0)} 秒",
        f"- ASS 生成：{payload.get('ass_elapsed_seconds', 0)} 秒",
        f"- 总耗时：{payload.get('total_elapsed_seconds', 0)} 秒",
        "",
        f"网络：{payload.get('network_mode', '直连或系统代理')}",
        f"缓存：打轴={'命中' if payload.get('asr_cache_hit') else '未命中'}，"
        f"音频={audio_cache_status}",
    ]
    if cache_reason:
        lines.append(f"打轴缓存说明：{cache_reason}")
    return "\n".join(lines) + "\n"


def _format_memory(value: object) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "未采集到"
    if amount <= 0:
        return "未采集到"
    return f"{amount:g} MB"
