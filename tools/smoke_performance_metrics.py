from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from utils import performance_metrics  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="utasync-performance-") as temp_dir:
        root = Path(temp_dir)
        summary = {
            "media_name": "sample.mp4",
            "asr_model": "large-v2",
            "asr_backend": "cuda-float16",
            "audio_extract_elapsed_seconds": 1.2,
            "demucs_elapsed_seconds": 0.0,
            "model_load_elapsed_seconds": 2.3,
            "transcription_elapsed_seconds": 12.4,
            "translation_elapsed_seconds": 3.5,
            "ass_elapsed_seconds": 0.4,
            "total_elapsed_seconds": 19.8,
            "network_mode": "代理 http://127.0.0.1:10808",
            "asr_cache_hit": False,
            "audio_cache_hit": True,
        }
        text_path, json_path = performance_metrics.write_task_performance_report(root, summary)
        _require(text_path.exists() and json_path.exists(), "任务性能报告没有写入")
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        report_text = text_path.read_text(encoding="utf-8")
        _require(payload["transcription_elapsed_seconds"] == 12.4, "打轴耗时没有进入性能报告")
        _require("代理 http://127.0.0.1:10808" in report_text, "性能报告缺少网络方式")
        _require("语音识别：12.4 秒" in report_text, "人类可读报告缺少阶段耗时")
        if os.name == "nt":
            _require(float(payload.get("process_memory_mb") or 0) > 0, "Windows 当前内存没有采集到")
            _require(float(payload.get("process_peak_memory_mb") or 0) > 0, "Windows 峰值内存没有采集到")

        unavailable_text = performance_metrics._performance_text({})
        _require("处理进程当前内存：未采集到" in unavailable_text, "内存缺失时仍显示为 0 MB")
        cached_text = performance_metrics._performance_text({"asr_cache_hit": True})
        _require("音频=未使用（已直接复用打轴结果）" in cached_text, "打轴命中时误报音频缓存未命中")

        original_app_root = performance_metrics.app_root
        performance_metrics.app_root = lambda: root
        try:
            startup_path = performance_metrics.record_startup_metric(0.876, "Next engine", "CUDA")
        finally:
            performance_metrics.app_root = original_app_root
        startup = json.loads(startup_path.read_text(encoding="utf-8").splitlines()[-1])
        _require(startup["ui_ready_seconds"] == 0.876, "启动耗时记录不准确")
        _require(startup["accelerator"] == "CUDA", "启动记录缺少加速器信息")

    print("PASS: startup and task performance reports are lightweight and readable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
