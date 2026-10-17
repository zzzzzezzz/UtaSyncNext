from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import PhotoImage, filedialog, messagebox

import customtkinter as ctk

from controllers.task_controller import TaskController
from engine.cache_env import app_root
from engine.status import inspect_engine_lightweight
from ui.ass_actions import AssActionRunner
from ui.dialogs import show_engine_manager, show_help_dialog
from ui.experimental_sidebar import ExperimentalSidebarPanel
from ui.log_formatter import format_run_summary, format_status_text
from ui.output_actions import OutputActionRunner
from ui.sidebar import SidebarPanel
from ui.task_state import TaskPhase, TaskSnapshot
from ui.text_utils import compact_filename
from ui.theme import ACCENT_GREEN, BG_WORKSPACE
from ui.workspace import WorkspacePanel
from ui.window_icon import apply_child_window_icon, apply_dark_titlebar
from utils.config import load_settings, save_settings
from utils.ffmpeg_tools import FFmpegSubtitleBurner
from utils.performance_metrics import record_startup_metric

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    class DnDWindow(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)

    HAS_DND = True
except ImportError:

    class DnDWindow(ctk.CTk):
        pass

    HAS_DND = False


def _display_path(path: str | Path) -> str:
    raw = str(path)
    try:
        resolved = Path(path).resolve()
        root = app_root().resolve()
        try:
            return "<Next>\\" + str(resolved.relative_to(root))
        except ValueError:
            return resolved.name or raw
    except Exception:
        return Path(raw).name or raw


def _find_ui_asset(name: str) -> Path | None:
    """Locate a bundled UI asset in source and PyInstaller layouts."""
    candidates: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(str(frozen_root)) / "assets" / name)
    root = app_root()
    candidates.extend((root / "assets" / name, root / "packaging" / "assets" / name))
    return next((path for path in candidates if path.is_file()), None)


def _resolve_ui_variant(settings: dict[str, object]) -> str:
    override = os.environ.get("UTASYNC_UI_VARIANT", "").strip().lower()
    # The new workspace is the product layout. Keep the classic panel only as
    # an environment-gated emergency fallback for old installations/tests.
    if override == "classic":
        return "classic"
    if override == "next":
        return override
    return "next"


class MainWindow(DnDWindow):
    def __init__(self, launch_started_at: float | None = None) -> None:
        super().__init__()
        self._icon_photo: PhotoImage | None = None
        self._apply_app_icon()
        self.geometry("1300x840")
        self.minsize(1100, 720)
        self.configure(fg_color=BG_WORKSPACE)

        self.settings = load_settings()
        self.ui_variant = _resolve_ui_variant(self.settings)
        self.title("UtaSyncNext")
        self.media_path = str(self.settings.get("media_path", ""))
        self.video_path = self.media_path
        self.task_snapshot = TaskSnapshot.initial(self.media_path)
        self.last_outputs: dict[str, str] = {}
        self.burn_thread: threading.Thread | None = None
        self.burn_cancel_event: threading.Event | None = None
        self._close_requested = False
        self._close_deadline = 0.0
        self.controller = TaskController(self)
        self.ass_actions = AssActionRunner(self)
        self.output_actions = OutputActionRunner(self)
        self.engine_status = inspect_engine_lightweight()

        self.grid_columnconfigure(0, weight=0, minsize=300 if self.ui_variant == "next" else 256)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.callbacks = {
            "show_help": lambda: show_help_dialog(self, getattr(self, "_icon_photo", None)),
            "import_video": self.import_media,
            "toggle_process": self.controller.toggle_task,
            "open_manager": lambda: show_engine_manager(self, getattr(self, "_icon_photo", None), self.engine_status.ready),
            "preview_ass": self.preview_ass_style,
            "redraw_ass": self.quick_redraw_ass,
            "burn_video": self.quick_burn_video,
            "burn_mode_changed": self._on_burn_mode_changed,
            "handle_dropped_file": self.on_file_drop,
            "clear_cache": self.clear_cache_placeholder,
            "open_output_dir": self.open_output_dir,
            "review_subtitles": self.open_subtitle_reviewer,
            "copy_output_srt": lambda: self.copy_output_path("srt"),
            "copy_output_ass": lambda: self.copy_output_path("ass"),
            "copy_output_log": lambda: self.copy_output_path("log"),
            "toggle_ui_variant": self.toggle_ui_variant,
        }

        sidebar_class = ExperimentalSidebarPanel if self.ui_variant == "next" else SidebarPanel
        self.sidebar = sidebar_class(
            self,
            settings=self.settings,
            engine_ready=self.engine_status.ready,
            callbacks=self.callbacks,
            engine_label=self.engine_status.label,
            engine_detail=self.engine_status.detail,
            engine_accelerator=self.engine_status.accelerator,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        self.workspace = WorkspacePanel(
            self,
            cached_lyrics=str(self.settings.get("cached_lyrics", "")),
            callbacks=self.callbacks,
        )
        self.workspace.grid(row=0, column=1, sticky="nsew")
        self.workspace.set_burn_mode(bool(self.settings.get("hires_export", False)))
        self.workspace.apply_task_snapshot(self.task_snapshot)

        if self.media_path and hasattr(self.sidebar, "file_label"):
            self.sidebar.file_label.configure(text=compact_filename(self.media_path, 21), text_color=ACCENT_GREEN)
        if hasattr(self.sidebar, "subtitle_label"):
            subtitle = "NEXT STUDIO · WORKSPACE" if self.ui_variant == "next" else "NEXT STUDIO · LIGHT ENGINE"
            self.sidebar.subtitle_label.configure(text=subtitle)

        self.update_status(0.0, "就绪")
        self._init_dnd()
        self._refresh_existing_outputs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if launch_started_at is not None:
            self.after_idle(lambda: self._record_ui_ready(launch_started_at))

    def _apply_app_icon(self) -> None:
        """Apply the same UtaSync mark to the window and child dialogs."""
        self._icon_photo = apply_child_window_icon(self, self)
        apply_dark_titlebar(self)
        ico_path = _find_ui_asset("utasync.ico")
        if ico_path:
            try:
                self.iconbitmap(str(ico_path))
            except Exception:
                pass
        png_path = _find_ui_asset("utasync.png")
        if png_path:
            try:
                self._icon_photo = PhotoImage(file=str(png_path))
                self.iconphoto(True, self._icon_photo)
            except Exception:
                self._icon_photo = None
        for delay in (80, 280):
            self.after(delay, lambda: apply_dark_titlebar(self))

    def _record_ui_ready(self, launch_started_at: float) -> None:
        elapsed = max(0.0, time.perf_counter() - launch_started_at)
        try:
            report_path = record_startup_metric(
                elapsed,
                self.engine_status.label,
                self.engine_status.accelerator,
            )
            self.append_log(f"\n性能：界面可操作用时 {elapsed:.2f} 秒，记录已保存到 {report_path.name}。\n")
        except Exception:
            pass

    def _init_dnd(self) -> None:
        if not HAS_DND:
            self.after(300, lambda: self.append_log("⚠️ 未检测到 tkinterdnd2，拖拽功能暂不可用。\n"))
            return

        try:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self.on_file_drop)
            self.after(300, lambda: self.append_log("💡 拖拽引擎已激活，可直接拖入视频、音频或歌词文件。\n"))
        except Exception as exc:
            self.after(300, lambda: self.append_log(f"⚠️ 拖拽注册失败: {exc}\n"))

    def import_media(self) -> None:
        file_path = filedialog.askopenfilename(
            title="选择视频 / 音频文件",
            filetypes=[
                ("媒体文件", "*.mp4 *.mkv *.avi *.ts *.webm *.mov *.mp3 *.wav *.flac *.m4a"),
                ("所有文件", "*.*"),
            ],
        )
        if file_path:
            self._set_media_path(file_path)

    def on_file_drop(self, event: object) -> None:
        file_path = str(event.data if hasattr(event, "data") else event)
        if file_path.startswith("{") and file_path.endswith("}"):
            file_path = file_path[1:-1]

        ext = os.path.splitext(file_path)[1].lower()
        if ext in {".txt", ".lrc", ".srt"}:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
                    self.workspace.update_lyrics(file.read().strip())
                self.append_log(f"\n📑 [拖拽加载成功] 参考歌词: {os.path.basename(file_path)}\n")
            except Exception as exc:
                self.append_log(f"\n❌ 读取文本失败: {exc}\n")
            return

        self._set_media_path(file_path)

    def _set_media_path(self, file_path: str) -> None:
        self.media_path = file_path
        self.video_path = file_path
        self._set_task_snapshot(TaskSnapshot.initial(file_path))
        if hasattr(self.sidebar, "file_label"):
            self.sidebar.file_label.configure(text=compact_filename(file_path, 21), text_color=ACCENT_GREEN)
        self.append_log(f"\n🎯 [媒体挂载成功] 输入源: {file_path}\n")
        self.save_current_settings()
        self._refresh_existing_outputs()

    def collect_task_payload(self) -> dict[str, object]:
        settings = self.sidebar.get_current_settings()
        self.save_current_settings(settings=settings)
        return {
            "media_path": self.media_path,
            "lyrics": self.workspace.get_lyrics(),
            "settings": settings,
        }

    def append_task_preview(self, payload: dict[str, object]) -> None:
        try:
            from engine.pipeline_worker import build_pipeline_preview

            preview = build_pipeline_preview(payload)
            lines = [str(line) for line in preview.get("lines", [])]
            if not lines:
                return
            self.append_log("\n🧭 [任务预检]\n")
            for line in lines:
                self.append_log(f"   {line}\n")
            self.append_log("\n")
        except Exception as exc:
            self.append_log(f"\n⚠️ [任务预检] 无法生成预检摘要: {exc}\n")

    def append_log(self, text: str) -> None:
        self.workspace.append_log(text)

    def append_run_summary(self, summary: dict[str, object]) -> None:
        self.append_log(format_run_summary(summary))

    def set_last_outputs(self, artifacts: dict[str, object]) -> None:
        self.output_actions.set_last_outputs(artifacts)

    def open_output_dir(self) -> None:
        self.output_actions.open_output_dir()

    def copy_output_path(self, artifact_key: str) -> None:
        self.output_actions.copy_output_path(artifact_key)

    def open_subtitle_reviewer(self) -> None:
        self.output_actions.open_subtitle_reviewer()

    def _refresh_existing_outputs(self) -> None:
        self.output_actions.refresh_existing_outputs()

    def update_status(self, progress_value: float, text: str) -> None:
        if self.task_snapshot.is_active or self.controller.is_running or (self.burn_thread and self.burn_thread.is_alive()):
            self._set_task_snapshot(
                self.task_snapshot.evolve(
                    progress=progress_value,
                    detail=text,
                )
            )
        self.sidebar.progressbar.set(max(0.0, min(1.0, progress_value)))
        status_text = format_status_text(text)
        if self.controller.is_running:
            status_text = "⏹ 取消任务" if self.ui_variant == "next" else f"⏹ 取消任务 · {status_text}"
        elif self.burn_thread and self.burn_thread.is_alive():
            self.sidebar.run_btn.configure(text=status_text)
            return
        else:
            status_text = "⚡ 启 动 引 擎"
        self.sidebar.run_btn.configure(text=status_text)

    def set_running_state(self, running: bool) -> None:
        if running:
            self.last_outputs = {}
            self._set_task_snapshot(
                self.task_snapshot.evolve(
                    phase=TaskPhase.RUNNING,
                    progress=0.0,
                    detail="任务已启动，正在等待处理阶段反馈。",
                )
            )
        if hasattr(self, "workspace"):
            self.workspace.set_processing_state(running)
        if running:
            self.sidebar.run_btn.configure(text="⏹ 取 消 任 务", fg_color="#991B1B", hover_color="#7F1D1D", text_color="#FFFFFF")
        else:
            if self.task_snapshot.is_active:
                self._set_task_snapshot(
                    self.task_snapshot.evolve(
                        phase=TaskPhase.READY if self.media_path else TaskPhase.EMPTY,
                        progress=0.0,
                        detail="任务已停止，可以重新开始。" if self.media_path else "请先导入视频或音频文件。",
                    )
                )
            self.sidebar.run_btn.configure(text="⚡ 启 动 引 擎", fg_color=ACCENT_GREEN, hover_color="#1ED760", text_color="#000000")

    def mark_task_finished(self, text: str, success: bool) -> None:
        has_outputs = bool(self.last_outputs.get("srt") or self.last_outputs.get("ass") or self.last_outputs.get("log"))
        lowered = text.lower()
        cancelled = "取消" in text or "终止" in text or "cancel" in lowered
        phase = TaskPhase.CANCELLED if cancelled else (TaskPhase.SUCCEEDED if success else TaskPhase.FAILED)
        self._set_task_snapshot(
            self.task_snapshot.evolve(
                phase=phase,
                progress=1.0 if success and not cancelled else 0.0,
                detail=text,
            )
        )
        self.workspace.set_task_result(text=text, success=success, has_outputs=has_outputs)

    def mark_task_preflight(self) -> None:
        self._set_task_snapshot(
            self.task_snapshot.evolve(
                phase=TaskPhase.PREFLIGHT,
                progress=0.0,
                detail="正在检查引擎、FFmpeg 和任务输入。",
            )
        )

    def mark_task_cancelling(self) -> None:
        self._set_task_snapshot(
            self.task_snapshot.evolve(
                phase=TaskPhase.CANCELLING,
                detail="正在等待 worker 安全退出。",
            )
        )

    def _set_task_snapshot(self, snapshot: TaskSnapshot) -> None:
        self.task_snapshot = snapshot
        if hasattr(self, "workspace"):
            self.workspace.apply_task_snapshot(snapshot)

    def preview_ass_style(self) -> None:
        self.ass_actions.preview_ass_style()

    def quick_redraw_ass(self) -> None:
        self.ass_actions.quick_redraw_ass()

    def quick_burn_video(self) -> None:
        if self.burn_thread and self.burn_thread.is_alive():
            if messagebox.askyesno("正在压制", "当前已有视频压制任务正在运行，是否请求取消？", parent=self):
                if self.burn_cancel_event:
                    self.burn_cancel_event.set()
                self.update_status(0.0, "正在取消视频压制...")
                self.workspace.set_burn_button_state(
                    text="正在取消...",
                    state="disabled",
                    fg_color="#991B1B",
                    hover_color="#7F1D1D",
                    text_color="#FFFFFF",
                )
                self.append_log("\n[压制] 已请求取消当前视频压制任务。\n")
            return

        if not self.media_path or not os.path.exists(self.media_path):
            messagebox.showwarning("提示", "请先导入需要压制的视频文件。", parent=self)
            return

        self._refresh_existing_outputs()
        ass_path = self.last_outputs.get("ass", "")
        output_dir = self.last_outputs.get("output_dir", "")
        if not ass_path or not os.path.exists(ass_path):
            messagebox.showwarning("提示", "未找到可用于压制的 ASS 特效字幕，请先完成处理或点击“极速重绘”。", parent=self)
            return
        ass_parent = Path(ass_path).resolve().parent
        if ass_parent.name.lower() == "subtitles":
            output_dir = str(ass_parent.parent)
        elif not output_dir or not os.path.isdir(output_dir):
            output_dir = str(ass_parent)
        elif Path(output_dir).name.lower() == "subtitles":
            output_dir = str(Path(output_dir).resolve().parent)

        settings = self.sidebar.get_current_settings()
        hires_enabled = bool(settings.get("hires_export"))
        self.workspace.set_burn_mode(hires_enabled)
        target_type = (
            "MKV + FLAC（避免再次有损压缩，不会提升原音频质量）"
            if hires_enabled
            else "MP4 + AAC"
        )
        if not messagebox.askyesno(
            "启动硬字幕压制",
            f"将使用 FFmpeg 把 ASS 特效字幕永久压入视频画面。\n\n输出模式：{target_type}\n字幕：{os.path.basename(ass_path)}\n\n压制会占用一定 CPU/GPU，但已使用低优先级子进程。\n是否开始？",
            parent=self,
        ):
            return

        self.burn_cancel_event = threading.Event()
        burn_log: list[str] = []
        self._set_burn_running_state(True)
        self.update_status(0.0, "🎬 准备压制...")
        self.append_log(
            f"\n🎬 [压制启动] 视频: {_display_path(self.media_path)}\n"
            f"🎬 [压制启动] 字幕: {_display_path(ass_path)}\n"
        )

        def ui_progress(value: float, text: str) -> None:
            self.after(0, lambda: self.update_status(value, text))

        def ui_log(text: str) -> None:
            clean = text.strip()
            if clean:
                burn_log.append(clean)
            self.after(0, lambda: self.append_log(text))

        def worker() -> None:
            try:
                output_video = FFmpegSubtitleBurner.burn(
                    media_path=self.media_path,
                    ass_path=ass_path,
                    output_dir=output_dir,
                    hires_enabled=hires_enabled,
                    fonts_dir=str(app_root() / "fonts"),
                    cancel_event=self.burn_cancel_event,
                    progress_cb=ui_progress,
                    log_cb=ui_log,
                )
                def finish_success() -> None:
                    self.last_outputs["video"] = output_video
                    self.last_outputs["output_dir"] = str(Path(output_video).parent)
                    self.update_status(1.0, "🎬 压制完成")
                    self._set_burn_running_state(False)
                    self._set_task_snapshot(
                        self.task_snapshot.evolve(
                            phase=TaskPhase.SUCCEEDED,
                            progress=1.0,
                            detail="视频压制完成。",
                        )
                    )
                    self.workspace.update_outputs(self.last_outputs)
                    self.append_log(f"✅ [硬字幕成品] {Path(output_video).resolve()}\n")
                    messagebox.showinfo(
                        "压制完成",
                        f"硬字幕视频已生成：\n\n{Path(output_video).name}\n\n"
                        f"完整路径：{Path(output_video).resolve()}",
                        parent=self,
                    )

                self.after(0, finish_success)
            except Exception as exc:
                error_text = str(exc)
                error_log = ""
                if "取消" not in error_text:
                    error_log = self._write_burn_error_log(
                        output_dir=output_dir,
                        media_path=self.media_path,
                        ass_path=ass_path,
                        messages=burn_log,
                        error_text=error_text,
                    )

                def finish_error() -> None:
                    self.update_status(0.0, "压制失败/已取消")
                    self._set_burn_running_state(False)
                    self._set_task_snapshot(
                        self.task_snapshot.evolve(
                            phase=TaskPhase.CANCELLED if "取消" in error_text else TaskPhase.FAILED,
                            progress=0.0,
                            detail=error_text,
                        )
                    )
                    if error_log:
                        self.last_outputs["log"] = error_log
                        self.last_outputs["output_dir"] = str(Path(error_log).parent.parent)
                    self.workspace.update_outputs(self.last_outputs)
                    self.append_log(f"❌ [压制失败] {error_text}\n")
                    if error_log:
                        self.append_log(f"[压制日志] 已保存：{_display_path(error_log)}\n")
                    if "取消" not in error_text:
                        messagebox.showerror("压制失败", error_text, parent=self)

                self.after(0, finish_error)
            finally:
                self.burn_cancel_event = None

        self.burn_thread = threading.Thread(target=worker, daemon=True)
        self.burn_thread.start()

    def _set_burn_running_state(self, running: bool) -> None:
        if running:
            self._set_task_snapshot(
                self.task_snapshot.evolve(
                    phase=TaskPhase.RUNNING,
                    progress=0.0,
                    detail="视频压制中，正在生成硬字幕成品。",
                )
            )
            self.workspace.set_processing_state(
                True,
                status_text="视频压制中",
                output_text="正在生成硬字幕视频，校验完成后才会显示正式成品。",
            )
            self.sidebar.run_btn.configure(state="disabled")
            self.workspace.set_burn_button_state(
                text="取消压制",
                state="normal",
                fg_color="#991B1B",
                hover_color="#7F1D1D",
                text_color="#FFFFFF",
            )
            return

        self.workspace.set_processing_state(False)
        self.sidebar.run_btn.configure(
            text="⚡ 启 动 引 擎",
            state="normal",
            fg_color=ACCENT_GREEN,
            hover_color="#1ED760",
            text_color="#000000",
        )
        self.workspace.set_burn_button_state(
            text=self.workspace.burn_button_label(),
            state="normal" if self.last_outputs.get("ass") else "disabled",
            fg_color=ACCENT_GREEN,
            hover_color="#1ED760",
            text_color="#06130B",
        )

    def _on_burn_mode_changed(self, hires_enabled: bool) -> None:
        if hasattr(self, "workspace"):
            self.workspace.set_burn_mode(hires_enabled)

    def _write_burn_error_log(
        self,
        *,
        output_dir: str,
        media_path: str,
        ass_path: str,
        messages: list[str],
        error_text: str,
    ) -> str:
        try:
            log_dir = Path(output_dir) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = log_dir / f"utasyncnext_burn_error_{stamp}.txt"
            lines = [
                "UtaSyncNext 视频压制错误日志",
                f"时间：{datetime.now().isoformat(timespec='seconds')}",
                f"源视频：{media_path}",
                f"ASS 字幕：{ass_path}",
                f"错误：{error_text}",
                "",
                "压制过程：",
                *(messages or ["没有收到 FFmpeg 过程日志。"]),
                "",
            ]
            log_path.write_text("\n".join(lines), encoding="utf-8")
            return str(log_path.resolve())
        except Exception:
            return ""

    def clear_cache_placeholder(self) -> None:
        self.output_actions.clear_cache()

    def toggle_ui_variant(self) -> None:
        target = "classic" if self.ui_variant == "next" else "next"
        current = dict(self.settings)
        current.update(self.sidebar.get_current_settings())
        current["ui_variant"] = target
        current["media_path"] = self.media_path
        current["cached_lyrics"] = self.workspace.get_lyrics()
        self.settings = current
        save_settings(self.settings)
        target_label = "经典布局" if target == "classic" else "实验布局"
        messagebox.showinfo(
            "界面布局已切换",
            f"已选择{target_label}。\n\n关闭并重新打开 UtaSyncNext 后生效；任务参数和输出不会被删除。",
            parent=self,
        )

    def save_current_settings(self, settings: dict[str, object] | None = None) -> None:
        current = dict(self.settings)
        current.update(settings or self.sidebar.get_current_settings())
        current["media_path"] = self.media_path
        current["cached_lyrics"] = self.workspace.get_lyrics()
        proxy_url = str(current.get("proxy_url") or current.get("model_download_proxy") or "").strip()
        current["proxy_url"] = proxy_url
        current["model_download_proxy"] = proxy_url
        self.settings = current
        save_settings(self.settings)

    def save_all_ui_states(self) -> None:
        self.save_current_settings()

    def _on_close(self) -> None:
        if self._close_requested:
            return
        self.save_current_settings()
        closing_task = False
        if self.burn_thread and self.burn_thread.is_alive():
            if self.burn_cancel_event:
                self.burn_cancel_event.set()
            closing_task = True
        if self.controller.is_running:
            self.controller.cancel_task()
            closing_task = True
        if closing_task:
            self._close_requested = True
            self._close_deadline = time.monotonic() + 6.0
            self.update_status(0.0, "正在安全关闭，请稍候...")
            self.after(100, self._finish_close_when_idle)
            return
        self.output_actions.close_reviewers()
        self.destroy()

    def _finish_close_when_idle(self) -> None:
        worker_running = self.controller.is_running
        burn_running = bool(self.burn_thread and self.burn_thread.is_alive())
        if not worker_running and not burn_running:
            self.output_actions.close_reviewers()
            self.destroy()
            return
        if time.monotonic() >= self._close_deadline:
            self.output_actions.close_reviewers()
            self.destroy()
            return
        self.after(100, self._finish_close_when_idle)
