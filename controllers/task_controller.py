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
from engine.status import inspect_engine_lightweight
from engine.task_recovery import write_failure_reports
from utils.ffmpeg_tools import ffmpeg_status_text, inspect_ffmpeg
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
        self._last_status_text = ""

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
        self._last_status_text = "正在启动任务。"
        if hasattr(self.app, "mark_task_preflight"):
            self.app.mark_task_preflight()
        if not self._dependencies_ready():
            return
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

    def _dependencies_ready(self) -> bool:
        """Block a task before spawning a worker when required tools are absent."""
        engine_status = inspect_engine_lightweight()
        if not engine_status.ready:
            message = f"任务未启动：{engine_status.label}。{engine_status.detail}"
            self.app.append_log(f"\n❌ {message}\n")
            if hasattr(self.app, "mark_task_finished"):
                self.app.mark_task_finished(message, success=False)
            return False

        ffmpeg_paths = inspect_ffmpeg()
        if not all(ffmpeg_paths.get(name) for name in ("ffmpeg", "ffprobe")):
            message = f"任务未启动：{ffmpeg_status_text(ffmpeg_paths)}。"
            self.app.append_log(f"\n❌ {message}\n")
            if hasattr(self.app, "mark_task_finished"):
                self.app.mark_task_finished(message, success=False)
            return False
        return True

    def cancel_task(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
        if hasattr(self.app, "mark_task_cancelling"):
            self.app.mark_task_cancelling()
        self._cancel_started_at = time.monotonic()
        self.app.update_status(0.0, "正在取消任务...")
        self._record_log("已请求取消，正在等待 worker 安全退出...\n")
        self.app.append_log("已请求取消，正在等待 worker 安全退出...\n")

    def _ensure_polling(self) -> None:
        if not self._poll_started:
            self._poll_started = True
            self.app.after(80, self._poll_worker_messages)

    def _poll_worker_messages(self) -> None:
        msg_queue = self.msg_queue
        messages: list[dict[str, Any]] = []
        if msg_queue:
            for _ in range(200):
                try:
                    message = msg_queue.get_nowait()
                except queue.Empty:
                    break
                messages.append(message)
                if message.get("type") in {MSG_DONE, MSG_ERROR}:
                    break
            self._handle_message_batch(messages)

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
            self.app.after(16 if len(messages) >= 200 else 80, self._poll_worker_messages)
        else:
            self._poll_started = False

    def _handle_message_batch(self, messages: list[dict[str, Any]]) -> None:
        """Coalesce noisy worker output so Tk redraws once per poll, not once per line."""
        log_chunks: list[str] = []
        latest_status: tuple[float, str] | None = None

        def flush_ui() -> None:
            nonlocal latest_status
            if log_chunks:
                self.app.append_log("".join(log_chunks))
                log_chunks.clear()
            if latest_status is not None:
                self.app.update_status(*latest_status)
                latest_status = None

        for message in messages:
            msg_type = message.get("type")
            if msg_type == MSG_LOG:
                text = str(message.get("text", ""))
                self._record_log(text)
                if not text.lstrip().startswith("Traceback (most recent call last):"):
                    log_chunks.append(text)
                continue
            if msg_type in {MSG_PROGRESS, MSG_STATUS}:
                progress_value = float(message.get("progress", 0.0))
                text = str(message.get("text", ""))
                prefix = "progress" if msg_type == MSG_PROGRESS else "status"
                self._record_log(f"[{prefix} {progress_value:.2f}] {text}\n")
                self._last_status_text = text
                latest_status = (progress_value, text)
                continue

            flush_ui()
            self._handle_message(message)

        flush_ui()

    def _handle_message(self, message: dict[str, Any]) -> None:
        msg_type = message.get("type")
        if msg_type == MSG_LOG:
            text = str(message.get("text", ""))
            self._record_log(text)
            if text.lstrip().startswith("Traceback (most recent call last):"):
                return
            self.app.append_log(text)
        elif msg_type == MSG_PROGRESS:
            progress_value = float(message.get("progress", 0.0))
            text = str(message.get("text", ""))
            self._record_log(f"[progress {progress_value:.2f}] {text}\n")
            self._last_status_text = text
            self.app.update_status(progress_value, text)
        elif msg_type == MSG_STATUS:
            progress_value = float(message.get("progress", 0.0))
            text = str(message.get("text", ""))
            self._record_log(f"[status {progress_value:.2f}] {text}\n")
            self._last_status_text = text
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
        report = self._write_error_log(error_text)
        if report is None:
            return

        log_path, report_path, recovery = report
        self._last_error_log_path = log_path
        artifacts = dict(recovery.get("artifacts") or {})
        artifacts["log"] = str(log_path)
        artifacts["recovery_report"] = str(report_path)
        artifacts.setdefault("output_dir", str(log_path.parent))
        if hasattr(self.app, "set_last_outputs"):
            self.app.set_last_outputs(artifacts)
        reusable = "、".join(str(item) for item in recovery.get("reusable_parts") or []) or "无"
        self.app.append_log(f"\n错误日志已保存: {log_path}\n")
        self.app.append_log(f"恢复：可复用结果：{reusable}。{recovery.get('advice') or ''}\n")

    def _write_error_log(self, error_text: str) -> tuple[Path, Path, dict[str, object]] | None:
        try:
            payload = dict(self._current_payload)
            if not payload.get("media_path"):
                payload["media_path"] = str(getattr(self.app, "media_path", "") or "")
            return write_failure_reports(
                payload=payload,
                error_text=error_text,
                run_log=self._run_log,
                last_status=self._last_status_text,
            )
        except Exception as exc:
            self.app.append_log(f"\n错误日志保存失败: {exc}\n")
            return None

    def _is_cancel_text(self, text: str) -> bool:
        return "取消" in text or "已终止 worker 子进程树" in text
