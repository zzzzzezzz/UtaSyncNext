from __future__ import annotations

import time
from multiprocessing import Event, Queue

from engine.protocol import done, error, log, progress, status
from engine.status import inspect_engine_lightweight
from utils.ffmpeg_tools import describe_media, ensure_path_exists, ffmpeg_version_line


def run_worker_demo(msg_queue: Queue, cancel_event: Event, payload: dict[str, object] | None = None) -> None:
    """Worker demo entrypoint. Heavy AI imports are intentionally forbidden here."""
    payload = payload or {}
    media_path = str(payload.get("media_path") or "")
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}

    try:
        msg_queue.put(status("worker 已启动。", 0.05))

        engine_status = inspect_engine_lightweight()
        msg_queue.put(log(f"引擎状态：{engine_status.label}\n"))
        msg_queue.put(log(f"引擎路径：{engine_status.detail}\n"))
        if settings:
            msg_queue.put(log("当前设置：\n"))
            for key, value in settings.items():
                display_value = "***" if "key" in str(key).lower() and value else value
                msg_queue.put(log(f"  {key}: {display_value}\n"))

        if cancel_event.is_set():
            msg_queue.put(done("FFmpeg 检查前已取消任务。"))
            return

        msg_queue.put(status("正在检查 FFmpeg...", 0.15))
        msg_queue.put(log(ffmpeg_version_line() + "\n"))

        if media_path:
            if cancel_event.is_set():
                msg_queue.put(done("媒体探测前已取消任务。"))
                return
            msg_queue.put(status("正在探测媒体信息...", 0.25))
            ensure_path_exists(media_path)
            msg_queue.put(log("媒体信息：" + describe_media(media_path) + "\n"))
        else:
            msg_queue.put(log("未选择媒体，仅运行就绪检查。\n"))

        for step in range(1, 21):
            if cancel_event.is_set():
                msg_queue.put(done("worker 已安全取消。"))
                return
            pct = step / 20
            msg_queue.put(progress(0.30 + pct * 0.65, f"就绪检查进行中... {int(pct * 100)}%"))
            time.sleep(0.08)

        msg_queue.put(done("就绪检查完成，独立进程隔离工作正常。"))
    except Exception as exc:
        msg_queue.put(error(f"worker 错误：{exc}"))
