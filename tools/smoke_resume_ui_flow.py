from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import subprocess
import sys
import threading
import time
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from tools.ui_smoke_support import ui_call, wait_until_ui, widget_text  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Close and restart MainWindow around a completed ASR checkpoint.")
    parser.add_argument("--media", required=True)
    parser.add_argument("--model", default="large-v2")
    parser.add_argument("--report", required=True)
    parser.add_argument("--phase", choices=["parent", "interrupt", "resume"], default="parent")
    return parser.parse_args()


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pipeline_payload(media: Path, model: str, *, interrupt_at_translation: bool) -> dict[str, object]:
    api_settings: dict[str, str] = {"api_key": "", "api_url": "", "api_model": "", "proxy_url": ""}
    if interrupt_at_translation:
        api_settings = {
            "api_key": "fake-key",
            "api_url": "http://127.0.0.1:9/v1",
            "api_model": "resume-ui-test-model",
            "proxy_url": "",
        }
    return {
        "media_path": str(media),
        "lyrics": "",
        "settings": {
            "audio_type": "Live 现场演唱 (常规/摇滚)",
            "model_size": model,
            "hardware_mode": "极限防爆",
            "use_demucs": "先跳过 Demucs (极速/推荐先出轴)",
            "output_mode": "中日双语字幕",
            "ass_preset": "不生成 (仅SRT)",
            **api_settings,
        },
    }


def task_paths(media: Path) -> tuple[Path, Path, Path]:
    output_root = NEXT_ROOT / "output" / media.stem
    subtitles = output_root / "subtitles"
    return (
        output_root,
        subtitles / f"{media.stem}_极致打轴.srt",
        subtitles / f"{media.stem}_打轴指纹.hash",
    )


def console_text(app: MainWindow) -> str:
    return str(app.workspace.console_textbox.get("1.0", "end"))


def normalized_button_text(value: object) -> str:
    return "".join(str(value).split())


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
    require(not thread.is_alive(), "界面流程调度线程没有退出")
    return errors


def run_interrupt_phase(args: argparse.Namespace, media: Path, report_path: Path) -> int:
    output_root, source_srt, fingerprint = task_paths(media)
    require(source_srt.is_file() and fingerprint.is_file(), "缺少已完成的打轴断点")
    before_hash = file_hash(source_srt)
    before_mtime = source_srt.stat().st_mtime_ns
    baseline_logs = {path.resolve() for path in (output_root / "logs").glob("utasyncnext_error_*.txt")}
    app = MainWindow()
    app.withdraw()
    controlled_payload = pipeline_payload(media, args.model, interrupt_at_translation=True)
    report: dict[str, object] = {"status": "failed", "phase": "interrupt", "checks": {}}
    checks = report["checks"]
    assert isinstance(checks, dict)

    try:
        def close_at_translation() -> None:
            ui_call(app, lambda: app._set_media_path(str(media)))
            ui_call(app, lambda: setattr(app, "collect_task_payload", lambda: controlled_payload))
            ui_call(app, app.controller.start_pipeline_task)
            wait_until_ui(
                app,
                lambda: "调用大模型翻译" in app.controller._last_status_text,
                30,
                "窗口到达翻译入口",
            )
            checks["before_close"] = ui_call(
                app,
                lambda: {
                    "status": app.controller._last_status_text,
                    "run_button": widget_text(app.sidebar.run_btn),
                    "console_has_cache_hit": "已有可复用打轴结果" in console_text(app)
                    or "命中打轴缓存" in console_text(app),
                },
            )
            before_close = checks["before_close"]
            assert isinstance(before_close, dict)
            require(
                "取消任务" in normalized_button_text(before_close["run_button"]),
                "处理中按钮没有明确提供取消操作",
            )
            ui_call(app, app._on_close)

        errors = run_window(app, close_at_translation)
        if errors:
            raise errors[0]

        process = app.controller.worker_process
        if process is not None:
            deadline = time.monotonic() + 15
            while process.is_alive() and time.monotonic() < deadline:
                time.sleep(0.1)
            require(not process.is_alive(), "关闭窗口后 worker 没有退出")
            process.join(timeout=2)
        after_logs = {path.resolve() for path in (output_root / "logs").glob("utasyncnext_error_*.txt")}
        require(after_logs == baseline_logs, "用户关闭/取消任务被错误记录成故障日志")
        require(file_hash(source_srt) == before_hash, "关闭窗口后 SRT 内容发生变化")
        require(source_srt.stat().st_mtime_ns == before_mtime, "关闭窗口后 SRT 被重写")
        saved_settings = json.loads((NEXT_ROOT / "user_settings.json").read_text(encoding="utf-8"))
        checks.update(
            {
                "worker_stopped": True,
                "cancel_created_error_log": False,
                "source_srt_hash_unchanged": True,
                "source_srt_mtime_unchanged": True,
                "media_path_saved_for_restart": Path(str(saved_settings.get("media_path") or "")).resolve() == media,
            }
        )
        report["status"] = "passed"
        report["source_srt_sha256"] = before_hash
        print("PASS: interrupt window closed cleanly.")
        return 0
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            app.destroy()
        except Exception:
            pass
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_resume_phase(args: argparse.Namespace, media: Path, report_path: Path) -> int:
    _, source_srt, fingerprint = task_paths(media)
    require(source_srt.is_file() and fingerprint.is_file(), "缺少已完成的打轴断点")
    before_hash = file_hash(source_srt)
    before_mtime = source_srt.stat().st_mtime_ns
    app = MainWindow()
    app.withdraw()
    controlled_payload = pipeline_payload(media, args.model, interrupt_at_translation=False)
    report: dict[str, object] = {"status": "failed", "phase": "resume", "checks": {}}
    checks = report["checks"]
    assert isinstance(checks, dict)

    try:
        require(Path(app.media_path).resolve() == media, "重启窗口没有恢复上次媒体路径")

        def restart_and_resume() -> None:
            started = time.monotonic()
            ui_call(app, lambda: setattr(app, "collect_task_payload", lambda: controlled_payload))
            ui_call(app, app.controller.start_pipeline_task)
            wait_until_ui(
                app,
                lambda: "取消任务·" in normalized_button_text(widget_text(app.sidebar.run_btn)),
                15,
                "续跑按钮显示取消操作和当前阶段",
            )
            preview_text = str(ui_call(app, lambda: console_text(app)))
            checks["running_state"] = ui_call(
                app,
                lambda: {
                    "run_button": widget_text(app.sidebar.run_btn),
                    "preview_has_cache_hit": "已有可复用打轴结果" in preview_text,
                },
            )
            wait_until_ui(app, lambda: not app.controller.is_running, 60, "重启后续跑完成")
            elapsed = time.monotonic() - started
            final_console = str(ui_call(app, lambda: console_text(app)))
            relevant_lines = [
                line.strip()
                for line in final_console.splitlines()
                if any(keyword in line for keyword in ("缓存", "打轴", "复用", "跳过重新识别"))
            ]
            state = ui_call(
                app,
                lambda: {
                    "elapsed_seconds": round(elapsed, 2),
                    "run_button": widget_text(app.sidebar.run_btn),
                    "system_status": widget_text(app.workspace.system_status_label),
                    "output_text": widget_text(app.workspace.output_label),
                    "srt": app.last_outputs.get("srt", ""),
                },
            )
            assert isinstance(state, dict)
            running_state = checks["running_state"]
            assert isinstance(running_state, dict)
            require(running_state["preview_has_cache_hit"], "任务预检没有说明可复用打轴结果")
            require(
                "取消任务·" in normalized_button_text(running_state["run_button"]),
                "续跑期间按钮没有同时显示取消操作和当前阶段",
            )
            require(state["srt"] == str(source_srt.resolve()), "重启后界面返回了错误的 SRT")
            require("SRT:" in str(state["output_text"]), "续跑完成后输出栏没有显示 SRT")
            require(float(state["elapsed_seconds"]) < 60, "缓存续跑耗时异常")
            checks["completed_state"] = state
            checks["relevant_console_lines"] = relevant_lines
            ui_call(app, app.quit)

        errors = run_window(app, restart_and_resume)
        if errors:
            raise errors[0]
        require(file_hash(source_srt) == before_hash, "重启续跑后 SRT 内容被重写")
        require(source_srt.stat().st_mtime_ns == before_mtime, "重启续跑后 SRT 修改时间发生变化")
        checks["source_srt_hash_unchanged"] = True
        checks["source_srt_mtime_unchanged"] = True
        report["status"] = "passed"
        report["source_srt_sha256"] = before_hash
        print("PASS: restarted window reused completed ASR.")
        return 0
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            app.destroy()
        except Exception:
            pass
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_parent(args: argparse.Namespace, media: Path, report_path: Path) -> int:
    settings_path = NEXT_ROOT / "user_settings.json"
    settings_backup = settings_path.read_bytes() if settings_path.exists() else None
    phase_dir = report_path.parent / "ui-phases"
    phase_dir.mkdir(parents=True, exist_ok=True)
    phase_reports: dict[str, object] = {}
    report: dict[str, object] = {"status": "failed", "media": str(media), "phases": phase_reports}

    try:
        for phase in ("interrupt", "resume"):
            phase_report_path = phase_dir / f"{phase}.json"
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--media",
                str(media),
                "--model",
                args.model,
                "--report",
                str(phase_report_path),
                "--phase",
                phase,
            ]
            completed = subprocess.run(
                command,
                cwd=str(NEXT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            phase_data = json.loads(phase_report_path.read_text(encoding="utf-8")) if phase_report_path.exists() else {}
            phase_data["process_returncode"] = completed.returncode
            phase_data["stdout"] = completed.stdout.strip()
            phase_data["stderr"] = completed.stderr.strip()
            phase_reports[phase] = phase_data
            require(completed.returncode == 0 and phase_data.get("status") == "passed", f"{phase} 界面阶段失败")

        _, source_srt, _ = task_paths(media)
        interrupt_report = phase_reports["interrupt"]
        resume_report = phase_reports["resume"]
        assert isinstance(interrupt_report, dict) and isinstance(resume_report, dict)
        interrupt_hash = str(interrupt_report.get("source_srt_sha256") or "")
        resume_hash = str(resume_report.get("source_srt_sha256") or "")
        require(interrupt_hash and interrupt_hash == resume_hash == file_hash(source_srt), "两次进程间 SRT 哈希不一致")
        report["source_srt"] = str(source_srt.resolve())
        report["source_srt_sha256"] = interrupt_hash
        report["external_api_requests"] = 0
        report["status"] = "passed"
        print("PASS: separate-process MainWindow restart flow completed.")
        print(f"- source_srt: {source_srt}")
        print(f"- source_srt_sha256: {interrupt_hash}")
        return 0
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if settings_backup is None:
            settings_path.unlink(missing_ok=True)
        else:
            settings_path.write_bytes(settings_backup)
        restored = settings_path.read_bytes() if settings_path.exists() else None
        report["settings_restored"] = restored == settings_backup
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    media = Path(args.media).resolve()
    report_path = Path(args.report).resolve()
    require(media.is_file(), f"找不到测试媒体：{media}")
    if args.phase == "interrupt":
        return run_interrupt_phase(args, media, report_path)
    if args.phase == "resume":
        return run_resume_phase(args, media, report_path)
    return run_parent(args, media, report_path)


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
