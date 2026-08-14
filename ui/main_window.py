from __future__ import annotations

import os
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from controllers.task_controller import TaskController
from engine.cache_env import app_root
from engine.status import inspect_engine_lightweight
from ui.ass_actions import AssActionRunner
from ui.dialogs import show_engine_manager, show_help_dialog
from ui.log_formatter import format_run_summary, format_status_text
from ui.output_actions import OutputActionRunner
from ui.sidebar import SidebarPanel
from ui.theme import ACCENT_GREEN, BG_WORKSPACE
from ui.workspace import WorkspacePanel
from utils.config import load_settings, save_settings
from utils.ffmpeg_tools import FFmpegSubtitleBurner

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


class MainWindow(DnDWindow):
    def __init__(self) -> None:
        super().__init__()
        self.title("UtaSyncNext - Next 独立轻量工作台")
        self.geometry("1300x940")
        self.minsize(1100, 800)
        self.configure(fg_color=BG_WORKSPACE)

        self.settings = load_settings()
        self.media_path = str(self.settings.get("media_path", ""))
        self.video_path = self.media_path
        self.last_outputs: dict[str, str] = {}
        self.burn_thread: threading.Thread | None = None
        self.burn_cancel_event: threading.Event | None = None
        self.controller = TaskController(self)
        self.ass_actions = AssActionRunner(self)
        self.output_actions = OutputActionRunner(self)
        self.engine_status = inspect_engine_lightweight()

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
            "handle_dropped_file": self.on_file_drop,
            "clear_cache": self.clear_cache_placeholder,
            "open_output_dir": self.open_output_dir,
            "copy_output_srt": lambda: self.copy_output_path("srt"),
            "copy_output_ass": lambda: self.copy_output_path("ass"),
            "copy_output_log": lambda: self.copy_output_path("log"),
        }

        self.sidebar = SidebarPanel(
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

        if self.media_path and hasattr(self.sidebar, "file_label"):
            self.sidebar.file_label.configure(text=os.path.basename(self.media_path), text_color=ACCENT_GREEN)
        if hasattr(self.sidebar, "subtitle_label"):
            self.sidebar.subtitle_label.configure(text="NEXT STUDIO · LIGHT ENGINE")

        self.update_status(0.0, "就绪")
        self._init_dnd()
        self._refresh_existing_outputs()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

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
        if hasattr(self.sidebar, "file_label"):
            self.sidebar.file_label.configure(text=os.path.basename(file_path), text_color=ACCENT_GREEN)
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

    def _refresh_existing_outputs(self) -> None:
        self.output_actions.refresh_existing_outputs()

    def update_status(self, progress_value: float, text: str) -> None:
        self.sidebar.progressbar.set(max(0.0, min(1.0, progress_value)))
        self.sidebar.run_btn.configure(text=format_status_text(text))

    def set_running_state(self, running: bool) -> None:
        if running:
            self.last_outputs = {}
        if hasattr(self, "workspace"):
            self.workspace.set_processing_state(running)
        if running:
            self.sidebar.run_btn.configure(text="⏹ 取 消 任 务", fg_color="#991B1B", hover_color="#7F1D1D", text_color="#FFFFFF")
        else:
            self.sidebar.run_btn.configure(text="⚡ 启 动 引 擎", fg_color=ACCENT_GREEN, hover_color="#1ED760", text_color="#000000")

    def mark_task_finished(self, text: str, success: bool) -> None:
        has_outputs = bool(self.last_outputs.get("srt") or self.last_outputs.get("ass") or self.last_outputs.get("log"))
        self.workspace.set_task_result(text=text, success=success, has_outputs=has_outputs)

    def preview_ass_style(self) -> None:
        self.ass_actions.preview_ass_style()

    def quick_redraw_ass(self) -> None:
        self.ass_actions.quick_redraw_ass()

    def quick_burn_video(self) -> None:
        if self.burn_thread and self.burn_thread.is_alive():
            if messagebox.askyesno("正在压制", "当前已有视频压制任务正在运行，是否请求取消？", parent=self):
                if self.burn_cancel_event:
                    self.burn_cancel_event.set()
                self.append_log("\n🛑 [压制] 已请求取消当前 FFmpeg 任务。\n")
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
        if not output_dir or not os.path.isdir(output_dir):
            output_dir = str(Path(ass_path).resolve().parent)

        settings = self.sidebar.get_current_settings()
        hires_enabled = bool(settings.get("hires_export"))
        target_type = "MKV + FLAC Hi-Res" if hires_enabled else "MP4 + AAC"
        if not messagebox.askyesno(
            "启动硬字幕压制",
            f"将使用 FFmpeg 把 ASS 特效字幕永久压入视频画面。\n\n输出模式：{target_type}\n字幕：{os.path.basename(ass_path)}\n\n压制会占用一定 CPU/GPU，但已使用低优先级子进程。\n是否开始？",
            parent=self,
        ):
            return

        self.burn_cancel_event = threading.Event()
        self.update_status(0.0, "🎬 准备压制...")
        self.append_log(
            f"\n🎬 [压制启动] 视频: {_display_path(self.media_path)}\n"
            f"🎬 [压制启动] 字幕: {_display_path(ass_path)}\n"
        )

        def ui_progress(value: float, text: str) -> None:
            self.after(0, lambda: self.update_status(value, text))

        def ui_log(text: str) -> None:
            self.after(0, lambda: self.append_log(text))

        def worker() -> None:
            try:
                output_video = FFmpegSubtitleBurner.burn(
                    media_path=self.media_path,
                    ass_path=ass_path,
                    output_dir=output_dir,
                    hires_enabled=hires_enabled,
                    cancel_event=self.burn_cancel_event,
                    progress_cb=ui_progress,
                    log_cb=ui_log,
                )
                def finish_success() -> None:
                    self.last_outputs["video"] = output_video
                    self.update_status(1.0, "🎬 压制完成")
                    self.append_log(f"✅ [硬字幕成品] {_display_path(output_video)}\n")
                    messagebox.showinfo(
                        "压制完成",
                        f"硬字幕视频已生成：\n\n{Path(output_video).name}\n\n位置：{_display_path(Path(output_video).parent)}",
                        parent=self,
                    )

                self.after(0, finish_success)
            except Exception as exc:
                error_text = str(exc)

                def finish_error() -> None:
                    self.update_status(0.0, "压制失败/已取消")
                    self.append_log(f"❌ [压制失败] {error_text}\n")
                    if "取消" not in error_text:
                        messagebox.showerror("压制失败", error_text, parent=self)

                self.after(0, finish_error)
            finally:
                self.burn_cancel_event = None

        self.burn_thread = threading.Thread(target=worker, daemon=True)
        self.burn_thread.start()

    def clear_cache_placeholder(self) -> None:
        self.output_actions.clear_cache()

    def save_current_settings(self, settings: dict[str, object] | None = None) -> None:
        current = settings or self.sidebar.get_current_settings()
        current["media_path"] = self.media_path
        current["cached_lyrics"] = self.workspace.get_lyrics()
        save_settings(current)

    def save_all_ui_states(self) -> None:
        self.save_current_settings()

    def _on_close(self) -> None:
        self.save_current_settings()
        if self.controller.is_running:
            self.controller.cancel_task()
            self.after(300, self.destroy)
            return
        self.destroy()
