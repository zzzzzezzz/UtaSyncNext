from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from ui import main_window as main_window_module  # noqa: E402
from ui import output_actions as output_actions_module  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from utils.ffmpeg_tools import FFmpegSubtitleBurner  # noqa: E402
from tools.ui_smoke_support import ui_call, wait_until_ui, widget_state, widget_text  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exercise the real MainWindow subtitle burn flow.")
    parser.add_argument("--media", required=True)
    parser.add_argument("--ass", required=True)
    parser.add_argument("--source-srt")
    parser.add_argument("--report", required=True)
    return parser.parse_args()


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    args = parse_args()
    media = Path(args.media).resolve()
    ass = Path(args.ass).resolve()
    source_srt = Path(args.source_srt).resolve() if args.source_srt else None
    report_path = Path(args.report).resolve()
    require(media.is_file(), f"找不到测试视频：{media}")
    require(ass.is_file(), f"找不到测试 ASS：{ass}")
    if source_srt:
        require(source_srt.is_file(), f"找不到测试 SRT：{source_srt}")

    settings_path = NEXT_ROOT / "user_settings.json"
    settings_backup = settings_path.read_bytes() if settings_path.exists() else None
    stage_root = report_path.parent
    input_dir = stage_root / "input"
    safe_run_name = re.sub(r"[^A-Za-z0-9_-]+", "-", stage_root.name).strip("-") or "run"
    staged_media = input_dir / f"ui-radio-flow-{safe_run_name}.mp4"
    output_root = NEXT_ROOT / "output" / staged_media.stem
    subtitles_dir = output_root / "subtitles"
    stage_root.mkdir(parents=True, exist_ok=True)
    input_dir.mkdir(parents=True, exist_ok=True)
    if staged_media.exists():
        staged_media.unlink()
    try:
        os.link(media, staged_media)
    except OSError:
        shutil.copy2(media, staged_media)
    if output_root.exists():
        shutil.rmtree(output_root)
    subtitles_dir.mkdir(parents=True, exist_ok=True)
    staged_ass = subtitles_dir / f"{staged_media.stem}_双语字幕_顶级特效.ass"
    shutil.copy2(ass, staged_ass)
    staged_srt = None
    if source_srt:
        staged_srt = subtitles_dir / f"{staged_media.stem}_双语字幕.srt"
        shutil.copy2(source_srt, staged_srt)

    dialogs: list[dict[str, str]] = []
    opened_dirs: list[str] = []

    def record_dialog(kind: str, title: str, message: str = "", **_: object) -> bool:
        dialogs.append({"kind": kind, "title": str(title), "message": str(message)})
        return True

    original_dialogs = {
        "askyesno": main_window_module.messagebox.askyesno,
        "showinfo": main_window_module.messagebox.showinfo,
        "showwarning": main_window_module.messagebox.showwarning,
        "showerror": main_window_module.messagebox.showerror,
    }
    main_window_module.messagebox.askyesno = lambda title, message, **kwargs: record_dialog(
        "askyesno", title, message, **kwargs
    )
    main_window_module.messagebox.showinfo = lambda title, message, **kwargs: record_dialog(
        "showinfo", title, message, **kwargs
    )
    main_window_module.messagebox.showwarning = lambda title, message, **kwargs: record_dialog(
        "showwarning", title, message, **kwargs
    )
    main_window_module.messagebox.showerror = lambda title, message, **kwargs: record_dialog(
        "showerror", title, message, **kwargs
    )
    output_actions_module.messagebox.askyesno = main_window_module.messagebox.askyesno
    output_actions_module.messagebox.showinfo = main_window_module.messagebox.showinfo
    output_actions_module.messagebox.showwarning = main_window_module.messagebox.showwarning
    output_actions_module.messagebox.showerror = main_window_module.messagebox.showerror

    had_startfile = hasattr(output_actions_module.os, "startfile")
    original_startfile = getattr(output_actions_module.os, "startfile", None)
    output_actions_module.os.startfile = lambda path: opened_dirs.append(str(path))  # type: ignore[attr-defined]

    app: MainWindow | None = None
    original_burn = FFmpegSubtitleBurner.burn
    report: dict[str, object] = {
        "status": "failed",
        "media": str(media),
        "staged_media": str(staged_media),
        "ass": str(ass),
        "staged_ass": str(staged_ass),
        "checks": {},
    }
    checks = report["checks"]
    assert isinstance(checks, dict)
    orchestration_errors: list[BaseException] = []

    try:
        app = MainWindow()
        app.withdraw()

        def orchestrate() -> None:
            try:
                require(
                    "启动引擎" in str(ui_call(app, lambda: widget_text(app.sidebar.run_btn))).replace(" ", ""),
                    "空闲主按钮没有明确显示启动操作",
                )
                checks["idle_start_button_clear"] = True
                ui_call(app, lambda: app._set_media_path(str(staged_media)))
                # Visual settings live in the lazily-created advanced dialog;
                # open it before checking the optional Hi-Res switch.
                ui_call(app, app.sidebar.open_advanced_settings)
                wait_until_ui(
                    app,
                    lambda: getattr(app.sidebar, "visual_settings_panel", None) is not None,
                    10,
                    "高级设置面板打开",
                )
                ui_call(app, app.sidebar.visual_settings_panel.check_hires.deselect)

                require(ui_call(app, lambda: app.last_outputs.get("ass")) == str(staged_ass.resolve()), "界面没有发现已有 ASS")
                checks["existing_ass_discovered"] = True
                checks["existing_srt_discovered"] = bool(
                    staged_srt and ui_call(app, lambda: app.last_outputs.get("srt"))
                )

                ui_call(app, app.quick_burn_video)
                wait_until_ui(app, lambda: bool(app.burn_thread and app.burn_thread.is_alive()), 10, "压制线程启动")
                checks["running_state"] = ui_call(
                    app,
                    lambda: {
                        "system_status": widget_text(app.workspace.system_status_label),
                        "output_text": widget_text(app.workspace.output_label),
                        "engine_button_state": widget_state(app.sidebar.run_btn),
                        "burn_button_text": widget_text(app.workspace.burn_video_btn),
                    },
                )
                require(ui_call(app, lambda: widget_text(app.workspace.system_status_label)) == "视频压制中", "处理中状态没有同步到界面")
                require(ui_call(app, lambda: widget_state(app.sidebar.run_btn)) == "disabled", "压制时主引擎按钮没有禁用")
                require("取消" in str(ui_call(app, lambda: widget_text(app.workspace.burn_video_btn))), "压制按钮没有切换为取消")

                ui_call(app, app.quick_burn_video)
                wait_until_ui(app, lambda: bool(app.burn_thread and not app.burn_thread.is_alive()), 45, "取消完成")
                wait_until_ui(app, lambda: widget_text(app.workspace.system_status_label) == "空闲", 10, "取消状态回到空闲")
                require(not list(output_root.glob("*_处理中*")), "取消后残留处理中临时文件")
                require(not list(output_root.glob("*硬字幕成品*.mp4")), "取消后错误保留正式成品")
                checks["cancel_flow"] = ui_call(
                    app,
                    lambda: {
                        "system_status": widget_text(app.workspace.system_status_label),
                        "burn_button_text": widget_text(app.workspace.burn_video_btn),
                        "temporary_files_remaining": [],
                    },
                )

                ui_call(app, app.quick_burn_video)
                wait_until_ui(app, lambda: bool(app.burn_thread and app.burn_thread.is_alive()), 10, "重新压制线程启动")
                wait_until_ui(app, lambda: bool(app.burn_thread and not app.burn_thread.is_alive()), 180, "重新压制完成")
                wait_until_ui(app, lambda: bool(app.last_outputs.get("video")), 15, "成品显示到输出栏")
                output_video = Path(str(ui_call(app, lambda: app.last_outputs["video"])))
                require(output_video.is_file(), "界面完成后没有找到成品视频")
                success_state = ui_call(
                    app,
                    lambda: {
                        "video": str(output_video),
                        "system_status": widget_text(app.workspace.system_status_label),
                        "output_text": widget_text(app.workspace.output_label),
                        "open_output_state": widget_state(app.workspace.open_output_btn),
                        "copy_ass_state": widget_state(app.workspace.copy_ass_btn),
                    },
                )
                assert isinstance(success_state, dict)
                require("成品:" in str(success_state["output_text"]), "输出栏没有显示成品文件")
                require(success_state["open_output_state"] == "normal", "完成后打开目录按钮不可用")
                require(success_state["copy_ass_state"] == "normal", "完成后复制 ASS 按钮不可用")
                checks["success_flow"] = success_state

                ui_call(app, app.open_output_dir)
                require(opened_dirs and Path(opened_dirs[-1]).resolve() == output_root.resolve(), "打开目录没有指向任务输出目录")
                ui_call(app, lambda: app.copy_output_path("ass"))
                clipboard_ass = str(ui_call(app, app.clipboard_get))
                require(clipboard_ass == str(staged_ass.resolve()), "复制 ASS 路径内容不正确")
                checks["output_actions"] = {
                    "opened_directory": opened_dirs[-1],
                    "clipboard_ass": clipboard_ass,
                }

                def fail_burn(**_: object) -> str:
                    raise RuntimeError("界面回归模拟错误：无法写入视频")

                FFmpegSubtitleBurner.burn = staticmethod(fail_burn)
                ui_call(app, app.quick_burn_video)
                wait_until_ui(app, lambda: bool(app.burn_thread and not app.burn_thread.is_alive()), 15, "错误流程完成")
                wait_until_ui(app, lambda: bool(app.last_outputs.get("log")), 10, "错误日志显示到输出栏")
                error_log = Path(str(ui_call(app, lambda: app.last_outputs["log"])))
                require(error_log.is_file(), "错误日志没有保存")
                error_text = error_log.read_text(encoding="utf-8")
                require("界面回归模拟错误" in error_text and "源视频" in error_text and "ASS 字幕" in error_text, "错误日志信息不完整")
                require(ui_call(app, lambda: widget_state(app.workspace.copy_log_btn)) == "normal", "错误后复制日志按钮不可用")
                checks["error_flow"] = ui_call(
                    app,
                    lambda: {
                        "log": str(error_log),
                        "copy_log_state": widget_state(app.workspace.copy_log_btn),
                        "system_status": widget_text(app.workspace.system_status_label),
                    },
                )

                checks["dialogs"] = dialogs
                report["status"] = "passed"
                report["output_root"] = str(output_root.resolve())
                print("PASS: real MainWindow burn flow completed.")
                print(f"- output: {output_video}")
                print(f"- error log: {error_log}")
            except BaseException as exc:
                report["error"] = f"{type(exc).__name__}: {exc}"
                orchestration_errors.append(exc)
            finally:
                checks["dialogs"] = dialogs
                try:
                    ui_call(app, app.quit)
                except Exception:
                    pass

        orchestration_thread = threading.Thread(target=orchestrate, daemon=True)
        orchestration_thread.start()
        app.mainloop()
        orchestration_thread.join(timeout=10)
        require(not orchestration_thread.is_alive(), "界面流程调度线程没有退出")
        if orchestration_errors:
            raise orchestration_errors[0]
        return 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        FFmpegSubtitleBurner.burn = staticmethod(original_burn)
        if app is not None:
            try:
                app.destroy()
            except Exception:
                pass
        for name, callback in original_dialogs.items():
            setattr(main_window_module.messagebox, name, callback)
            setattr(output_actions_module.messagebox, name, callback)
        if had_startfile:
            output_actions_module.os.startfile = original_startfile  # type: ignore[attr-defined]
        else:
            delattr(output_actions_module.os, "startfile")
        if settings_backup is None:
            settings_path.unlink(missing_ok=True)
        else:
            settings_path.write_bytes(settings_backup)
        restored_settings = settings_path.read_bytes() if settings_path.exists() else None
        checks["settings_restored"] = restored_settings == settings_backup
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
