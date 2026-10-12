from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from tools.smoke_translation_batch_resume_ui import (  # noqa: E402
    output_paths,
    parse_current_batch,
    payload_for,
    prepare_fixture,
    run_window,
)
from tools.ui_smoke_support import ui_call, wait_until_ui, widget_state, widget_text  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify user-facing logs for a permanent local API failure.")
    parser.add_argument("--media-source", required=True)
    parser.add_argument("--model", default="large-v2")
    parser.add_argument("--report", required=True)
    return parser.parse_args()


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FailureState:
    def __init__(self) -> None:
        self.batches: list[int] = []
        self.lock = threading.Lock()

    def add(self, batch: int) -> None:
        with self.lock:
            self.batches.append(batch)

    def snapshot(self) -> list[int]:
        with self.lock:
            return list(self.batches)


class FailureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: FailureState) -> None:
        self.state = state
        super().__init__(address, FailureHandler)


class FailureHandler(BaseHTTPRequestHandler):
    server: FailureServer

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        messages = payload.get("messages") or []
        prompt = str(messages[-1].get("content") or "") if messages else ""
        batch, _ = parse_current_batch(prompt)
        self.server.state.add(batch)
        body = json.dumps(
            {"error": {"message": "The requested local test model does not exist", "type": "invalid_request_error"}}
        ).encode("utf-8")
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


def main() -> int:
    args = parse_args()
    report_path = Path(args.report).resolve()
    run_root = report_path.parent
    media = run_root / "input" / "ui-api-failure-48.wav"
    settings_path = NEXT_ROOT / "user_settings.json"
    settings_backup = settings_path.read_bytes() if settings_path.exists() else None
    paths = prepare_fixture(Path(args.media_source).resolve(), media, args.model)
    state = FailureState()
    server = FailureServer(("127.0.0.1", 0), state)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1"
    app = MainWindow()
    app.withdraw()
    payload = payload_for(media, endpoint, args.model)
    report: dict[str, object] = {
        "status": "failed",
        "endpoint_scope": "127.0.0.1 only",
        "external_api_requests": 0,
        "checks": {},
    }
    checks = report["checks"]
    assert isinstance(checks, dict)

    try:
        def run_failure() -> None:
            started = time.monotonic()
            ui_call(app, lambda: app._set_media_path(str(media)))
            ui_call(app, lambda: setattr(app, "collect_task_payload", lambda: payload))
            ui_call(app, app.controller.start_pipeline_task)
            wait_until_ui(app, lambda: not app.controller.is_running, 30, "永久 API 错误处理完成")
            checks["elapsed_seconds"] = round(time.monotonic() - started, 2)
            checks["ui_state"] = ui_call(
                app,
                lambda: {
                    "system_status": widget_text(app.workspace.system_status_label),
                    "output_text": widget_text(app.workspace.output_label),
                    "copy_log_state": widget_state(app.workspace.copy_log_btn),
                    "clear_cache_state": widget_state(app.workspace.clear_cache_btn),
                    "console": str(app.workspace.console_textbox.get("1.0", "end")),
                    "outputs": dict(app.last_outputs),
                },
            )
            ui_call(app, app.quit)

        errors = run_window(app, run_failure)
        if errors:
            raise errors[0]
        batches = state.snapshot()
        require(1 <= len(batches) <= 2, f"永久配置错误发出了过多请求: {batches}")
        require(len(batches) == len(set(batches)), "同一批次在永久配置错误后被重复请求")
        ui_state = checks["ui_state"]
        require(isinstance(ui_state, dict), "无法读取失败后的界面状态")
        outputs = ui_state["outputs"]
        require(isinstance(outputs, dict), "失败后没有输出信息")
        log_path = Path(str(outputs.get("log") or ""))
        recovery_path = Path(str(outputs.get("recovery_report") or ""))
        require(log_path.is_file(), "永久 API 错误没有生成中文错误日志")
        require(recovery_path.is_file(), "永久 API 错误没有生成恢复报告")
        log_text = log_path.read_text(encoding="utf-8")
        recovery_text = recovery_path.read_text(encoding="utf-8")
        require("恢复建议" in log_text and "原文打轴" in log_text, "错误日志没有说明可复用结果")
        require("local-resume-test-only" not in log_text + recovery_text, "错误报告泄露测试 API Key")
        require(ui_state["copy_log_state"] == "normal", "失败后复制日志按钮不可用")
        require(ui_state["clear_cache_state"] == "normal", "失败后清理空间按钮没有恢复")
        require("日志:" in str(ui_state["output_text"]), "失败后输出栏没有显示错误日志")
        require("Traceback" not in str(ui_state["console"]), "主界面泄露了 Python 技术堆栈")
        require("Error code: 404" not in str(ui_state["console"]), "主界面泄露了 HTTP 错误术语")
        require("请查看错误日志和恢复建议" in str(ui_state["console"]), "主界面没有给出清晰的错误去向")
        require(paths["source_srt"].is_file(), "永久 API 错误后原文打轴丢失")
        require(not paths["translated_srt"].exists(), "永久 API 错误生成了假双语字幕")
        require(not paths["translation_hash"].exists(), "永久 API 错误写入了翻译完成指纹")
        require(float(checks["elapsed_seconds"]) < 15, "永久配置错误没有快速停止")
        api_trace = paths["api_trace"]
        require(api_trace.is_file() and "404" in api_trace.read_text(encoding="utf-8"), "API 调用记录没有保留技术原因")
        checks["requested_batches"] = batches
        checks["error_log"] = str(log_path.resolve())
        checks["recovery_report"] = str(recovery_path.resolve())
        checks["api_trace"] = str(api_trace.resolve())
        checks["source_srt_retained"] = True
        checks["no_false_translation_output"] = True
        report["status"] = "passed"
        print("PASS: permanent API failure produced clear logs and retained recovery artifacts.")
        print(f"- requests: {batches}")
        print(f"- error_log: {log_path}")
        return 0
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            app.destroy()
        except Exception:
            pass
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
        if settings_backup is None:
            settings_path.unlink(missing_ok=True)
        else:
            settings_path.write_bytes(settings_backup)
        report["settings_restored"] = True
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
