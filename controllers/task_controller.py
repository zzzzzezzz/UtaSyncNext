from __future__ import annotations

import multiprocessing as mp
import queue
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from engine.cache_env import app_root
from engine.pipeline_worker import run_pipeline_worker
from engine.protocol import MSG_DONE, MSG_ERROR, MSG_LOG, MSG_PROGRESS, MSG_STATUS
from utils.process_tools import kill_process_tree


class TaskController:
    """Owns the GUI-to-worker lifecycle without importing heavy AI modules."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.is_running = False
        self.msg_queue: mp.Queue | None = None
        self.cancel_event: mp.Event | None = None
        self.worker_process: mp.Process | None = None
        self._poll_started = False
        self._cancel_started_at: float | None = None
        self._current_payload: dict[str, object] = {}
        self._run_log: list[str] = []
        self._last_error_log_path: Path | None = None

    def toggle_task(self) -> None:
        if self.is_running:
            self.cancel_task()
            return
        self.start_pipeline_task()

    def start_pipeline_task(self) -> None:
        payload = self.app.collect_task_payload()
        self._current_payload = payload
        self._run_log = []
        self._last_error_log_path = None
        if hasattr(self.app, "append_task_preview"):
            self.app.append_task_preview(payload)
        self.msg_queue = mp.Queue()
        self.cancel_event = mp.Event()
        self.worker_process = mp.Process(
            target=run_pipeline_worker,
            args=(self.msg_queue, self.cancel_event, payload),
            daemon=True,
        )
        self.worker_process.start()
        self.is_running = True
        self._cancel_started_at = None
        self.app.set_running_state(True)
        self._record_log("已启动 Next 独立管线 worker。\n")
        self.app.append_log("已启动 Next 独立管线 worker。\n")
        self._ensure_polling()

    def cancel_task(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
        self._cancel_started_at = time.monotonic()
        self.app.update_status(0.0, "正在取消任务...")
        self._record_log("已请求取消，正在等待 worker 安全退出...\n")
        self.app.append_log("已请求取消，正在等待 worker 安全退出...\n")

    def _ensure_polling(self) -> None:
        if not self._poll_started:
            self._poll_started = True
            self.app.after(50, self._poll_worker_messages)

    def _poll_worker_messages(self) -> None:
        msg_queue = self.msg_queue
        if msg_queue:
            while True:
                try:
                    message = msg_queue.get_nowait()
                except queue.Empty:
                    break
                self._handle_message(message)
                if self.msg_queue is None or self.msg_queue is not msg_queue:
                    break

        if self.worker_process and not self.worker_process.is_alive() and self.is_running:
            self.worker_process.join(timeout=0.1)
            if self.cancel_event and self.cancel_event.is_set():
                self._finish_task("任务已取消。", progress=0.0)
            else:
                self._finish_task("worker 已退出。", progress=0.0)

        if (
            self.worker_process
            and self.worker_process.is_alive()
            and self._cancel_started_at
            and time.monotonic() - self._cancel_started_at > 2.0
        ):
            self.app.append_log("worker 未及时退出，正在终止子进程树...\n")
            if self.worker_process.pid is not None:
                kill_process_tree(self.worker_process.pid)
            self._finish_task("已终止 worker 子进程树。", progress=0.0)

        if self.is_running or self.worker_process or self.msg_queue:
            self.app.after(50, self._poll_worker_messages)
        else:
            self._poll_started = False

    def _handle_message(self, message: dict[str, Any]) -> None:
        msg_type = message.get("type")
        if msg_type == MSG_LOG:
            text = str(message.get("text", ""))
            self._record_log(text)
            self.app.append_log(text)
        elif msg_type == MSG_PROGRESS:
            progress_value = float(message.get("progress", 0.0))
            text = str(message.get("text", ""))
            self._record_log(f"[progress {progress_value:.2f}] {text}\n")
            self.app.update_status(progress_value, text)
        elif msg_type == MSG_STATUS:
            progress_value = float(message.get("progress", 0.0))
            text = str(message.get("text", ""))
            self._record_log(f"[status {progress_value:.2f}] {text}\n")
            self.app.update_status(progress_value, text)
        elif msg_type == MSG_DONE:
            artifacts = message.get("artifacts")
            if isinstance(artifacts, dict) and hasattr(self.app, "set_last_outputs"):
                self.app.set_last_outputs(artifacts)
            summary = message.get("summary")
            if isinstance(summary, dict):
                self._record_log(f"[summary] {summary}\n")
                if hasattr(self.app, "append_run_summary"):
                    self.app.append_run_summary(summary)
            done_text = str(message.get("text", "worker 已完成。"))
            self._record_log(done_text + "\n")
            was_cancelled = bool(self.cancel_event and self.cancel_event.is_set()) or "取消" in done_text
            self._finish_task(done_text, progress=0.0 if was_cancelled else 1.0)
        elif msg_type == MSG_ERROR:
            text = str(message.get("text", "worker 执行失败。"))
            self._record_log(text + "\n")
            self._publish_error_log(text)
            self._finish_task(text, progress=0.0)

    def _finish_task(self, text: str, progress: float) -> None:
        if not self.is_running:
            return
        self.is_running = False
        if progress < 1.0 and not self._is_cancel_text(text):
            self._publish_error_log(text)
        self.app.update_status(progress, text)
        self.app.set_running_state(False)
        if hasattr(self.app, "mark_task_finished"):
            self.app.mark_task_finished(text, success=progress >= 1.0)
        self.app.append_log(text + "\n")
        self._cleanup_worker()

    def _cleanup_worker(self) -> None:
        if self.worker_process and self.worker_process.is_alive():
            time.sleep(0.2)
            self.worker_process.terminate()
        self.worker_process = None
        self.cancel_event = None
        self.msg_queue = None
        self._cancel_started_at = None

    def _record_log(self, text: str) -> None:
        if text:
            self._run_log.append(text)

    def _publish_error_log(self, error_text: str) -> None:
        if self._last_error_log_path is not None:
            return
        log_path = self._write_error_log(error_text)
        if log_path is None:
            return

        self._last_error_log_path = log_path
        artifacts = dict(getattr(self.app, "last_outputs", {}) or {})
        artifacts["log"] = str(log_path)
        artifacts.setdefault("output_dir", str(log_path.parent))
        if hasattr(self.app, "set_last_outputs"):
            self.app.set_last_outputs(artifacts)
        self.app.append_log(f"\n错误日志已保存: {log_path}\n")

    def _write_error_log(self, error_text: str) -> Path | None:
        try:
            media_path = str(self._current_payload.get("media_path") or getattr(self.app, "media_path", "") or "")
            media_name = Path(media_path).name if media_path else "未选择媒体"
            base_name = Path(media_path).stem if media_path else "general"
            log_dir = app_root() / "output" / base_name / "logs" if base_name else app_root() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = log_dir / f"utasyncnext_error_{timestamp}.txt"
            content = "\n".join(
                [
                    "UtaSyncNext 错误日志",
                    f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    f"媒体: {media_name}",
                    f"媒体路径: {media_path or '未选择'}",
                    f"错误: {error_text}",
                    "",
                    "---- 原始任务日志 ----",
                    "".join(self._run_log).strip(),
                    "",
                ]
            )
            log_path.write_text(content, encoding="utf-8")
            return log_path
        except Exception as exc:
            self.app.append_log(f"\n错误日志保存失败: {exc}\n")
            return None

    def _is_cancel_text(self, text: str) -> bool:
        return "取消" in text or "已终止 worker 子进程树" in text
