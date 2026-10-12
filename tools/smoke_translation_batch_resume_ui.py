from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from core.subtitle_policy import resolve_subtitle_layout  # noqa: E402
from engine.pipeline_worker import (  # noqa: E402
    _build_asr_cache_hash,
    _media_fingerprint,
    _normalize_pipeline_settings,
    _resolve_whisper_model_arg,
)
from tools.ui_smoke_support import ui_call, wait_until_ui, widget_state, widget_text  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


FIXTURE_STEM = "ui-translation-resume-48"
LOCAL_MODEL = "local/translation-resume-test"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify partial translation cache across MainWindow restarts.")
    parser.add_argument("--media-source", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--model", default="large-v2")
    parser.add_argument("--phase", choices=["parent", "interrupt", "resume"], default="parent")
    parser.add_argument("--media")
    parser.add_argument("--endpoint")
    return parser.parse_args()


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def settings_for(endpoint: str, model: str) -> dict[str, object]:
    return {
        "audio_type": "Live 现场演唱 (常规/摇滚)",
        "model_size": model,
        "hardware_mode": "极限防爆",
        "use_demucs": "先跳过 Demucs (极速/推荐先出轴)",
        "output_mode": "中日双语字幕",
        "ass_preset": "不生成 (仅SRT)",
        "api_key": "fake-key",
        "api_url": endpoint,
        "api_model": LOCAL_MODEL,
        "proxy_url": "",
    }


def payload_for(media: Path, endpoint: str, model: str) -> dict[str, object]:
    return {"media_path": str(media), "lyrics": "", "settings": settings_for(endpoint, model)}


def output_paths(media: Path) -> dict[str, Path]:
    output_root = NEXT_ROOT / "output" / media.stem
    subtitles = output_root / "subtitles"
    source_base = subtitles / f"{media.stem}_极致打轴"
    return {
        "output_root": output_root,
        "source_srt": source_base.with_suffix(".srt"),
        "asr_hash": subtitles / f"{media.stem}_打轴指纹.hash",
        "batch_cache": source_base.with_name(f"{source_base.name}_翻译缓存.json"),
        "translated_srt": source_base.with_name(f"{source_base.name}_双语字幕.srt"),
        "translation_hash": subtitles / f"{media.stem}_翻译指纹.hash",
        "api_trace": source_base.with_name(f"{source_base.name}_api_trace.json"),
    }


def srt_time(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def fixture_srt() -> str:
    blocks: list[str] = []
    for index in range(1, 49):
        start = (index - 1) * 0.85
        end = start + 0.70
        blocks.append(
            f"{index}\n{srt_time(start)} --> {srt_time(end)}\n翻訳再開テスト原文{index}"
        )
    return "\n\n".join(blocks) + "\n"


def prepare_fixture(media_source: Path, media: Path, model: str) -> dict[str, Path]:
    require(media_source.is_file(), f"测试音频不存在: {media_source}")
    media.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(media_source, media)
    paths = output_paths(media)
    output_root = paths["output_root"].resolve()
    expected_parent = (NEXT_ROOT / "output").resolve()
    require(output_root.parent == expected_parent, "拒绝清理非测试输出目录")
    if output_root.exists():
        shutil.rmtree(output_root)
    paths["source_srt"].parent.mkdir(parents=True, exist_ok=True)
    paths["source_srt"].write_text(fixture_srt(), encoding="utf-8")

    settings = settings_for("http://127.0.0.1:1/v1", model)
    audio_type, model_size, hardware_mode, skip_demucs, _ = _normalize_pipeline_settings(settings)
    model_ref = _resolve_whisper_model_arg(model_size)
    layout_policy = resolve_subtitle_layout(str(media), settings)
    asr_hash = _build_asr_cache_hash(
        media_fingerprint=_media_fingerprint(media),
        audio_type=audio_type,
        model_size=model_size,
        model_ref=model_ref,
        skip_demucs=skip_demucs,
        hardware_mode=hardware_mode,
        layout_policy=layout_policy,
        reference_lyrics="",
    )
    paths["asr_hash"].write_text(asr_hash, encoding="utf-8")
    return paths


def cache_entries(path: Path) -> int:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return len(value) if isinstance(value, dict) else 0
    except Exception:
        return 0


def parse_current_batch(prompt: str) -> tuple[int, str]:
    current = str(prompt).split("【当前批次 SRT】", 1)[-1].strip()
    first_index = re.search(r"(?m)^\s*(\d+)\s*$", current)
    require(first_index is not None, "本地服务无法识别批次首条编号")
    batch = (int(first_index.group(1)) - 1) // 12 + 1
    return batch, current


def local_translation(srt_text: str) -> str:
    translated: list[str] = []
    for raw_block in re.split(r"\n\s*\n", srt_text.strip()):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        time_index = next((idx for idx, line in enumerate(lines) if "-->" in line), -1)
        if time_index < 0:
            continue
        index_text = lines[0] if time_index > 0 and lines[0].isdigit() else str(len(translated) + 1)
        source = lines[time_index + 1] if len(lines) > time_index + 1 else "翻訳再開テスト"
        translated.append(f"{index_text}\n{lines[time_index]}\n{source}\n本地续传测试译文{index_text}")
    return "\n\n".join(translated)


class LocalApiState:
    def __init__(self) -> None:
        self.mode = "interrupt"
        self.records: list[dict[str, object]] = []
        self.lock = threading.Lock()

    def record(self, batch: int) -> tuple[int, str]:
        with self.lock:
            request_id = len(self.records) + 1
            mode = self.mode
            self.records.append(
                {"request_id": request_id, "mode": mode, "batch": batch, "received": True, "responded": False}
            )
            return request_id, mode

    def mark_responded(self, request_id: int, delivered: bool) -> None:
        with self.lock:
            for record in self.records:
                if record["request_id"] == request_id:
                    record["responded"] = True
                    record["delivered"] = delivered
                    break

    def snapshot(self) -> list[dict[str, object]]:
        with self.lock:
            return [dict(record) for record in self.records]


class LocalTranslationServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: LocalApiState) -> None:
        self.state = state
        super().__init__(address, LocalTranslationHandler)


class LocalTranslationHandler(BaseHTTPRequestHandler):
    server: LocalTranslationServer

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            messages = body.get("messages") if isinstance(body, dict) else []
            user_prompt = str(messages[-1].get("content") or "") if messages else ""
            batch, current_srt = parse_current_batch(user_prompt)
            request_id, mode = self.server.state.record(batch)
            if mode == "interrupt" and batch != 1:
                time.sleep(20)
            else:
                time.sleep(0.15)
            content = local_translation(current_srt)
            response = {
                "id": f"local-resume-{request_id}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": LOCAL_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
            delivered = True
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                delivered = False
            self.server.state.mark_responded(request_id, delivered)
        except Exception as exc:
            encoded = json.dumps({"error": {"message": str(exc)}}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    def log_message(self, _format: str, *args: object) -> None:
        return


def console_text(app: MainWindow) -> str:
    return str(app.workspace.console_textbox.get("1.0", "end"))


def run_window(app: MainWindow, action: object) -> list[BaseException]:
    errors: list[BaseException] = []

    def orchestrate() -> None:
        try:
            action()  # type: ignore[operator]
        except BaseException as exc:
            errors.append(exc)
            try:
                ui_call(app, app.quit)
            except Exception:
                pass

    thread = threading.Thread(target=orchestrate, daemon=True)
    thread.start()
    app.mainloop()
    thread.join(timeout=10)
    require(not thread.is_alive(), "界面测试调度线程没有退出")
    return errors


def run_interrupt_phase(args: argparse.Namespace, media: Path, report_path: Path) -> int:
    require(args.endpoint, "interrupt 阶段缺少本地 API 地址")
    paths = output_paths(media)
    baseline_logs = {path.resolve() for path in (paths["output_root"] / "logs").glob("utasyncnext_error_*.txt")}
    app = MainWindow()
    app.withdraw()
    payload = payload_for(media, args.endpoint, args.model)
    report: dict[str, object] = {"status": "failed", "phase": "interrupt", "checks": {}}
    checks = report["checks"]
    assert isinstance(checks, dict)

    try:
        def interrupt_translation() -> None:
            ui_call(app, lambda: app._set_media_path(str(media)))
            ui_call(app, lambda: setattr(app, "collect_task_payload", lambda: payload))
            ui_call(app, app.controller.start_pipeline_task)
            wait_until_ui(app, lambda: cache_entries(paths["batch_cache"]) >= 1, 30, "首个翻译批次写入缓存")
            before_close = ui_call(
                app,
                lambda: {
                    "status": app.controller._last_status_text,
                    "run_button": widget_text(app.sidebar.run_btn),
                    "clear_cache_state": widget_state(app.workspace.clear_cache_btn),
                    "cache_entries": cache_entries(paths["batch_cache"]),
                    "console_mentions_batches": "翻译队列" in console_text(app),
                },
            )
            checks["before_close"] = before_close
            require(isinstance(before_close, dict), "无法读取关闭前界面状态")
            require(int(before_close["cache_entries"]) == 1, "中断前应只完成 1 个翻译批次")
            require("取消任务" in "".join(str(before_close["run_button"]).split()), "运行按钮没有取消操作")
            require(before_close["clear_cache_state"] == "disabled", "翻译运行时仍可清理中间缓存")
            close_started = time.monotonic()
            ui_call(app, app._on_close)
            checks["close_requested_at"] = close_started

        errors = run_window(app, interrupt_translation)
        if errors:
            raise errors[0]
        close_elapsed = round(time.monotonic() - float(checks["close_requested_at"]), 2)
        after_logs = {path.resolve() for path in (paths["output_root"] / "logs").glob("utasyncnext_error_*.txt")}
        require(not app.controller.is_running, "关闭窗口后翻译 worker 仍在运行")
        require(app.controller.worker_process is None, "关闭窗口后 worker 句柄未清理")
        require(after_logs == baseline_logs, "用户关闭被错误记录成故障")
        require(cache_entries(paths["batch_cache"]) == 1, "关闭后部分翻译缓存数量异常")
        require(not paths["translated_srt"].exists(), "中断任务不应生成完整双语 SRT")
        require(not paths["translation_hash"].exists(), "中断任务不应写入翻译完成指纹")
        require(close_elapsed < 6.0, f"关闭等待耗时异常: {close_elapsed}s")
        checks.update(
            {
                "worker_stopped": True,
                "cancel_created_error_log": False,
                "partial_cache_entries": 1,
                "no_false_completed_srt": True,
                "no_completion_fingerprint": True,
                "safe_close_elapsed_seconds": close_elapsed,
            }
        )
        report["status"] = "passed"
        print("PASS: translation interrupted after one cached batch.")
        return 0
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            app.destroy()
        except Exception:
            pass
        write_json(report_path, report)


def run_resume_phase(args: argparse.Namespace, media: Path, report_path: Path) -> int:
    require(args.endpoint, "resume 阶段缺少本地 API 地址")
    paths = output_paths(media)
    require(cache_entries(paths["batch_cache"]) == 1, "重启前应保留 1 个翻译批次")
    app = MainWindow()
    app.withdraw()
    payload = payload_for(media, args.endpoint, args.model)
    summaries: list[dict[str, object]] = []
    original_append_summary = app.append_run_summary
    app.append_run_summary = lambda summary: (summaries.append(dict(summary)), original_append_summary(summary))[1]
    report: dict[str, object] = {"status": "failed", "phase": "resume", "checks": {}}
    checks = report["checks"]
    assert isinstance(checks, dict)

    try:
        require(Path(app.media_path).resolve() == media.resolve(), "重启窗口没有恢复测试媒体")

        def resume_translation() -> None:
            started = time.monotonic()
            ui_call(app, lambda: setattr(app, "collect_task_payload", lambda: payload))
            ui_call(app, app.controller.start_pipeline_task)
            wait_until_ui(app, lambda: not app.controller.is_running, 60, "剩余翻译批次续跑完成")
            elapsed = round(time.monotonic() - started, 2)
            state = ui_call(
                app,
                lambda: {
                    "elapsed_seconds": elapsed,
                    "run_button": widget_text(app.sidebar.run_btn),
                    "system_status": widget_text(app.workspace.system_status_label),
                    "clear_cache_state": widget_state(app.workspace.clear_cache_btn),
                    "output_text": widget_text(app.workspace.output_label),
                    "srt": app.last_outputs.get("srt", ""),
                    "console": console_text(app),
                },
            )
            checks["completed_state"] = state
            ui_call(app, app.quit)

        errors = run_window(app, resume_translation)
        if errors:
            raise errors[0]
        require(summaries, "续跑完成后没有收到任务摘要")
        summary = summaries[-1]
        require(int(summary.get("translation_batches") or 0) == 4, "翻译批次数不是 4")
        require(int(summary.get("translation_cache_hits") or 0) == 1, "没有准确复用首个翻译批次")
        require(int(summary.get("translation_api_calls") or 0) == 3, "续跑没有只请求剩余 3 个批次")
        require(cache_entries(paths["batch_cache"]) == 4, "续跑后翻译缓存没有补齐 4 个批次")
        require(paths["translated_srt"].is_file(), "续跑没有生成双语 SRT")
        require(paths["translation_hash"].is_file(), "续跑没有写入翻译完成指纹")
        translated = paths["translated_srt"].read_text(encoding="utf-8")
        require(translated.count("-->") == 48, "双语 SRT 时间轴数量不是 48")
        require(translated.count("本地续传测试译文") == 48, "双语 SRT 没有完整保留 48 条译文")
        completed_state = checks["completed_state"]
        require(isinstance(completed_state, dict), "无法读取续跑完成状态")
        require(Path(str(completed_state["srt"])).resolve() == paths["translated_srt"].resolve(), "界面输出路径错误")
        require(completed_state["clear_cache_state"] == "normal", "任务完成后清理空间按钮没有恢复")
        require(
            "翻译断点: 检测到 1 个已保存批次" in str(completed_state["console"]),
            "开始前检查没有说明已保存的翻译批次",
        )
        checks["summary"] = summary
        checks["final_cache_entries"] = 4
        checks["bilingual_timeline_count"] = 48
        report["status"] = "passed"
        print("PASS: restart reused one batch and requested only the remaining three.")
        return 0
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            app.destroy()
        except Exception:
            pass
        write_json(report_path, report)


def run_phase_process(args: argparse.Namespace, phase: str, media: Path, endpoint: str, report: Path) -> dict[str, object]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--phase",
        phase,
        "--media-source",
        str(Path(args.media_source).resolve()),
        "--media",
        str(media.resolve()),
        "--endpoint",
        endpoint,
        "--model",
        args.model,
        "--report",
        str(report.resolve()),
    ]
    completed = subprocess.run(command, cwd=NEXT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    phase_report = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    phase_report["process_returncode"] = completed.returncode
    phase_report["stdout"] = completed.stdout.strip()
    phase_report["stderr"] = completed.stderr.strip()
    require(completed.returncode == 0, f"{phase} 界面阶段失败")
    require(phase_report.get("status") == "passed", f"{phase} 报告未通过")
    return phase_report


def run_parent(args: argparse.Namespace, report_path: Path) -> int:
    settings_path = NEXT_ROOT / "user_settings.json"
    settings_backup = settings_path.read_bytes() if settings_path.exists() else None
    run_root = report_path.parent
    media = run_root / "input" / f"{FIXTURE_STEM}.wav"
    paths = prepare_fixture(Path(args.media_source).resolve(), media, args.model)
    state = LocalApiState()
    server = LocalTranslationServer(("127.0.0.1", 0), state)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1"
    phase_dir = run_root / "ui-phases"
    report: dict[str, object] = {
        "status": "failed",
        "media": str(media.resolve()),
        "endpoint_scope": "127.0.0.1 only",
        "external_api_requests": 0,
        "phases": {},
    }

    try:
        interrupt_report = run_phase_process(args, "interrupt", media, endpoint, phase_dir / "interrupt.json")
        state.mode = "resume"
        resume_report = run_phase_process(args, "resume", media, endpoint, phase_dir / "resume.json")
        records = state.snapshot()
        resume_batches = [int(record["batch"]) for record in records if record["mode"] == "resume"]
        require(sorted(resume_batches) == [2, 3, 4], f"重启后本地请求批次异常: {resume_batches}")
        require(1 not in resume_batches, "重启后错误地重新请求了已缓存的第 1 批")
        report["phases"] = {"interrupt": interrupt_report, "resume": resume_report}
        report["local_api_requests"] = records
        report["resume_requested_batches"] = resume_batches
        report["final_cache_entries"] = cache_entries(paths["batch_cache"])
        report["translated_srt"] = str(paths["translated_srt"].resolve())
        report["status"] = "passed"
        print("PASS: separate-process partial translation resume flow completed.")
        print(f"- resumed_batches: {resume_batches}")
        print(f"- translated_srt: {paths['translated_srt'].resolve()}")
        return 0
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
        if settings_backup is None:
            settings_path.unlink(missing_ok=True)
        else:
            settings_path.write_bytes(settings_backup)
        report["settings_restored"] = True
        write_json(report_path, report)


def main() -> int:
    args = parse_args()
    report_path = Path(args.report).resolve()
    if args.phase == "parent":
        return run_parent(args, report_path)
    require(args.media, f"{args.phase} 阶段缺少 --media")
    media = Path(args.media).resolve()
    if args.phase == "interrupt":
        return run_interrupt_phase(args, media, report_path)
    return run_resume_phase(args, media, report_path)


if __name__ == "__main__":
    raise SystemExit(main())
