from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from ui.log_formatter import format_console_line
from ui.task_state import TaskPhase, TaskSnapshot
from ui.text_utils import compact_filename
from ui.theme import ACCENT_GREEN, ACCENT_WARNING, BG_CARD, BG_WORKSPACE, TEXT_MUTED, TEXT_TITLE


class WorkspacePanel(ctk.CTkFrame):
    """Right-side workspace: lyrics input plus a lightweight streaming console."""

    def __init__(self, master, cached_lyrics: str = "", callbacks: dict[str, object] | None = None, **kwargs) -> None:
        super().__init__(master, fg_color=BG_WORKSPACE, corner_radius=0, **kwargs)
        self.callbacks = callbacks or {}
        self.output_artifacts: dict[str, object] = {}
        self._burn_hires_enabled = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=3)
        self.grid_rowconfigure(3, weight=2)

        self.header_label = ctk.CTkLabel(
            self,
            text="工作区",
            font=ctk.CTkFont(family="Segoe UI Black", size=24, weight="bold"),
            text_color=TEXT_TITLE,
        )
        self.header_label.grid(row=0, column=0, padx=40, pady=(40, 20), sticky="w")

        self._build_task_panel()
        self._build_lyrics_panel(cached_lyrics)
        self._build_console_panel()

    def _build_task_panel(self) -> None:
        """Compact task center that stays stable while the rest of the UI moves."""
        self.task_panel = ctk.CTkFrame(
            self,
            height=128,
            fg_color="#111722",
            corner_radius=12,
            border_color="#27334A",
            border_width=1,
        )
        self.task_panel.grid(row=1, column=0, padx=40, pady=(0, 20), sticky="ew")
        self.task_panel.grid_propagate(False)
        self.task_panel.grid_columnconfigure(0, weight=1)
        self.task_panel.grid_columnconfigure(1, weight=0)

        heading = ctk.CTkLabel(
            self.task_panel,
            text="当前任务",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
            text_color=TEXT_TITLE,
        )
        heading.grid(row=0, column=0, padx=(18, 10), pady=(14, 2), sticky="w")

        self.task_phase_badge = ctk.CTkLabel(
            self.task_panel,
            text="未选择媒体",
            width=94,
            height=25,
            corner_radius=7,
            fg_color="#202733",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(family="Microsoft YaHei", size=11, weight="bold"),
        )
        self.task_phase_badge.grid(row=0, column=1, padx=(0, 18), pady=(12, 2), sticky="e")

        self.task_media_label = ctk.CTkLabel(
            self.task_panel,
            text="尚未导入视频或音频",
            anchor="w",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
            text_color="#D9E1F2",
        )
        self.task_media_label.grid(row=1, column=0, columnspan=2, padx=18, pady=(0, 0), sticky="ew")

        self.task_detail_label = ctk.CTkLabel(
            self.task_panel,
            text="请先导入视频或音频文件。",
            anchor="w",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            text_color=TEXT_MUTED,
        )
        self.task_detail_label.grid(row=2, column=0, columnspan=2, padx=18, pady=(0, 7), sticky="ew")

        self.task_progressbar = ctk.CTkProgressBar(
            self.task_panel,
            height=5,
            fg_color="#202733",
            progress_color=ACCENT_GREEN,
            corner_radius=3,
        )
        self.task_progressbar.grid(row=3, column=0, columnspan=2, padx=18, pady=(0, 14), sticky="ew")
        self.task_progressbar.set(0.0)

    def _build_lyrics_panel(self, cached_lyrics: str) -> None:
        self.lyrics_panel = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=12,
            border_color="#252525",
            border_width=1,
        )
        self.lyrics_panel.grid(row=2, column=0, padx=40, pady=(0, 20), sticky="nsew")
        self.lyrics_panel.grid_columnconfigure(0, weight=1)
        self.lyrics_panel.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.lyrics_panel, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(15, 5), sticky="ew")
        ctk.CTkLabel(
            header,
            text="参考歌词库",
            font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"),
            text_color=TEXT_TITLE,
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="可直接粘贴官方日文歌词；留空则盲翻",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            text_color=TEXT_MUTED,
        ).pack(side="right")

        self.lyrics_textbox = ctk.CTkTextbox(
            self.lyrics_panel,
            fg_color="#0F0F0F",
            text_color="#CCCCCC",
            border_color="#222222",
            border_width=1,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            corner_radius=8,
            spacing2=4,
        )
        self.lyrics_textbox.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")

        placeholder = "【留空即为盲翻】\n请将网易云/QQ音乐的日文歌词粘贴于此...\n\nねぇ、もしも全て投げ捨てられたら\n笑って生きることが楽になるの？"
        self.lyrics_textbox.insert("1.0", cached_lyrics if cached_lyrics else placeholder)

    def _build_console_panel(self) -> None:
        self.console_panel = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=12,
            border_color="#252525",
            border_width=1,
        )
        self.console_panel.grid(row=3, column=0, padx=40, pady=(0, 40), sticky="nsew")
        self.console_panel.grid_columnconfigure(0, weight=1)
        self.console_panel.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.console_panel, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(15, 5), sticky="ew")
        ctk.CTkLabel(
            header,
            text="任务日志",
            font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"),
            text_color=TEXT_TITLE,
        ).pack(side="left")

        status_frame = ctk.CTkFrame(header, fg_color="transparent")
        status_frame.pack(side="right")
        self.clear_cache_btn = None
        if "clear_cache" in self.callbacks:
            self.clear_cache_btn = ctk.CTkButton(
                status_frame,
                text="清理空间",
                width=76,
                height=24,
                font=ctk.CTkFont(family="Microsoft YaHei", size=11),
                fg_color="#374151",
                hover_color="#4B5563",
                command=self.callbacks["clear_cache"],
            )
            self.clear_cache_btn.pack(side="left", padx=(0, 15))

        self.system_dot_label = ctk.CTkLabel(
            status_frame,
            text="●",
            text_color=ACCENT_GREEN,
            font=ctk.CTkFont(size=12),
        )
        self.system_dot_label.pack(
            side="left", padx=(0, 4)
        )
        self.system_status_label = ctk.CTkLabel(
            status_frame,
            text="空闲",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=ACCENT_GREEN,
        )
        self.system_status_label.pack(side="left")

        self.console_textbox = ctk.CTkTextbox(
            self.console_panel,
            fg_color="#0A0A0A",
            border_color="#1E1E1E",
            border_width=1,
            text_color="#BBBBBB",
            font=ctk.CTkFont(family="Consolas", size=13),
            corner_radius=8,
            spacing1=4,
            spacing3=4,
            padx=18,
            pady=18,
        )
        self.console_textbox.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="nsew")
        self.console_textbox.tag_config("success", foreground=ACCENT_GREEN)
        self.console_textbox.tag_config("warning", foreground=ACCENT_WARNING)
        self.console_textbox.tag_config("error", foreground="#EF4444")
        self.console_textbox.tag_config("highlight", foreground="#38BDF8")
        self.console_textbox.tag_config("muted", foreground="#555555")
        self.console_textbox.configure(state="disabled")

        self.err_kws = ("错误", "失败", "异常", "不可用", "未完成", "Traceback")
        self.succ_kws = ("完成", "已保存", "已生成", "已更新", "已复制", "可复用")
        self.warn_kws = ("提示", "未检测", "跳过", "退回", "改用 CPU", "取消")
        self.hl_kws = ("任务：", "打轴：", "翻译：", "字幕：", "输出：", "性能：", "模型：", "网络：", "缓存：", "媒体：", "系统：", "日志：", "恢复：")
        self.muted_kws = ("进度", "开始处理", "正在", "本次")
        self._build_output_bar()

    def _build_output_bar(self) -> None:
        self.output_bar = ctk.CTkFrame(
            self.console_panel,
            fg_color="#10151E",
            corner_radius=10,
            border_color="#223044",
            border_width=1,
        )
        self.output_bar.grid(row=2, column=0, padx=20, pady=(0, 20), sticky="ew")
        self.output_bar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self.output_bar,
            text="输出结果",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
            text_color=TEXT_TITLE,
        ).grid(row=0, column=0, padx=(16, 10), pady=12, sticky="w")

        self.output_label = ctk.CTkLabel(
            self.output_bar,
            text="尚未生成字幕 · 完成打轴后按钮可用。",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.output_label.grid(row=0, column=1, padx=(0, 12), pady=12, sticky="ew")

        button_frame = ctk.CTkFrame(self.output_bar, fg_color="transparent")
        button_frame.grid(row=0, column=2, padx=(0, 12), pady=10, sticky="e")

        self.review_output_btn = self._output_button(button_frame, "听轴校对", "review_subtitles")
        self.open_output_btn = self._output_button(button_frame, "打开目录", "open_output_dir")
        self.copy_srt_btn = self._output_button(button_frame, "复制 SRT", "copy_output_srt")
        self.copy_ass_btn = self._output_button(button_frame, "复制 ASS", "copy_output_ass")
        self.copy_log_btn = self._output_button(button_frame, "复制日志", "copy_output_log")
        self.burn_video_btn = self._output_button(button_frame, self.burn_button_label(), "burn_video", accent=True)
        self._set_output_buttons(False, False, False, False)

    def burn_button_label(self) -> str:
        return "Hi-Res 压制" if self._burn_hires_enabled else "MP4 压制"

    def set_burn_mode(self, hires_enabled: bool) -> None:
        self._burn_hires_enabled = bool(hires_enabled)
        if hasattr(self, "burn_video_btn"):
            label = self.burn_button_label()
            self.burn_video_btn.configure(text=label, width=max(88, 28 + len(label) * 11))

    def _output_button(self, master, text: str, callback_name: str, accent: bool = False) -> ctk.CTkButton:
        width = max(72, 28 + len(text) * 11)
        button = ctk.CTkButton(
            master,
            text=text,
            width=width,
            height=28,
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            fg_color=ACCENT_GREEN if accent else "#1F2A3A",
            hover_color="#1ED760" if accent else "#2F3E55",
            text_color="#06130B" if accent else TEXT_TITLE,
            command=lambda: self._run_callback(callback_name),
        )
        button.pack(side="left", padx=(0, 6))
        return button

    def _run_callback(self, callback_name: str) -> None:
        callback = self.callbacks.get(callback_name)
        if callable(callback):
            callback()

    def _set_output_buttons(self, has_dir: bool, has_srt: bool, has_ass: bool, has_log: bool) -> None:
        self.review_output_btn.configure(state="normal" if has_srt else "disabled")
        self.open_output_btn.configure(state="normal" if has_dir else "disabled")
        self.copy_srt_btn.configure(state="normal" if has_srt else "disabled")
        self.copy_ass_btn.configure(state="normal" if has_ass else "disabled")
        self.copy_log_btn.configure(state="normal" if has_log else "disabled")
        self.burn_video_btn.configure(
            state="normal" if has_ass else "disabled",
            fg_color=ACCENT_GREEN if has_ass else "#1F2A3A",
            hover_color="#1ED760" if has_ass else "#1F2A3A",
            text_color="#06130B" if has_ass else TEXT_MUTED,
        )

    def set_burn_button_state(
        self,
        *,
        text: str,
        state: str,
        fg_color: str,
        hover_color: str,
        text_color: str,
    ) -> None:
        self.burn_video_btn.configure(
            text=text,
            state=state,
            fg_color=fg_color,
            hover_color=hover_color,
            text_color=text_color,
        )

    def apply_task_snapshot(self, snapshot: TaskSnapshot) -> None:
        """Render the task contract without making the panel own task logic."""
        previous = getattr(self, "_rendered_task_snapshot", None)
        phase_colors = {
            TaskPhase.EMPTY: (TEXT_MUTED, "#202733"),
            TaskPhase.READY: (ACCENT_GREEN, "#173326"),
            TaskPhase.PREFLIGHT: ("#38BDF8", "#123044"),
            TaskPhase.QUEUED: ("#A78BFA", "#2A2145"),
            TaskPhase.RUNNING: (ACCENT_WARNING, "#403314"),
            TaskPhase.CANCELLING: ("#FCA5A5", "#4A2020"),
            TaskPhase.SUCCEEDED: (ACCENT_GREEN, "#173326"),
            TaskPhase.FAILED: ("#F87171", "#431C25"),
            TaskPhase.CANCELLED: (TEXT_MUTED, "#202733"),
        }
        text_color, badge_color = phase_colors[snapshot.phase]
        if previous is None or previous.phase != snapshot.phase:
            self.task_phase_badge.configure(
                text=snapshot.phase_label,
                text_color=text_color,
                fg_color=badge_color,
            )
            self.task_progressbar.configure(progress_color=text_color if snapshot.is_active else ACCENT_GREEN)
        if previous is None or previous.media_name != snapshot.media_name:
            self.task_media_label.configure(
                text=compact_filename(snapshot.media_name, 72) if snapshot.media_name else "尚未导入视频或音频"
            )
        if previous is None or previous.detail != snapshot.detail:
            self.task_detail_label.configure(text=snapshot.detail)
        if previous is None or previous.progress != snapshot.progress:
            self.task_progressbar.set(snapshot.progress)
        self._rendered_task_snapshot = snapshot

    def set_processing_state(
        self,
        running: bool,
        status_text: str = "任务处理中",
        output_text: str = "任务执行中，完成后这里会显示 SRT / ASS 输出文件。",
    ) -> None:
        if running:
            self.system_dot_label.configure(text_color=ACCENT_WARNING)
            self.system_status_label.configure(text=status_text, text_color=ACCENT_WARNING)
            self.output_label.configure(
                text=output_text,
                text_color=TEXT_MUTED,
            )
            self._set_output_buttons(False, False, False, False)
            if self.clear_cache_btn is not None:
                self.clear_cache_btn.configure(state="disabled")
            return

        self.system_dot_label.configure(text_color=ACCENT_GREEN)
        self.system_status_label.configure(text="空闲", text_color=ACCENT_GREEN)
        if self.clear_cache_btn is not None:
            self.clear_cache_btn.configure(state="normal")

    def set_task_result(self, text: str, success: bool, has_outputs: bool) -> None:
        self.set_processing_state(False)
        if has_outputs:
            return
        if success:
            self.output_label.configure(
                text="任务结束，但没有收到 SRT / ASS 文件。请查看上方日志。",
                text_color=ACCENT_WARNING,
            )
        else:
            self.output_label.configure(text=f"任务未完成：{text}", text_color=ACCENT_WARNING)
        self._set_output_buttons(False, False, False, False)

    def update_outputs(self, artifacts: dict[str, object]) -> None:
        self.output_artifacts = artifacts
        output_dir = str(artifacts.get("output_dir") or "")
        srt_path = str(artifacts.get("srt") or "")
        ass_path = str(artifacts.get("ass") or "")
        log_path = str(artifacts.get("log") or "")
        video_path = str(artifacts.get("video") or "")
        recovery_report = str(artifacts.get("recovery_report") or "")

        outputs: list[tuple[str, str]] = []
        # A failed task may still have a valid source SRT.  In that state the
        # recovery/error log is the actionable output, so surface it first
        # instead of making the user infer the failure from a secondary SRT.
        if recovery_report and log_path:
            outputs.append(("日志", log_path))
        if video_path:
            outputs.append(("成品", video_path))
        if ass_path:
            outputs.append(("ASS", ass_path))
        if srt_path:
            outputs.append(("SRT", srt_path))
        if log_path and not recovery_report:
            outputs.append(("日志", log_path))
        output_text = "暂无可显示输出 · 完成打轴后按钮可用。"
        if outputs:
            label, path = outputs[0]
            output_text = f"{label}: {compact_filename(path, 38)}"
            if len(outputs) > 1:
                output_text += f"  ·  另有 {len(outputs) - 1} 项"
        self.output_label.configure(
            text=output_text,
            text_color=ACCENT_GREEN if outputs else TEXT_MUTED,
        )
        self._set_output_buttons(bool(output_dir), bool(srt_path), bool(ass_path), bool(log_path))

    def append_log(self, text: str) -> None:
        self.console_textbox.configure(state="normal")

        for line in text.splitlines(True):
            line = format_console_line(line)
            tag = None
            if any(keyword in line for keyword in self.err_kws):
                tag = "error"
            elif any(keyword in line for keyword in self.succ_kws):
                tag = "success"
            elif any(keyword in line for keyword in self.warn_kws):
                tag = "warning"
            elif any(keyword in line for keyword in self.hl_kws):
                tag = "highlight"
            elif any(keyword in line for keyword in self.muted_kws):
                tag = "muted"

            self.console_textbox.insert("end", line, tag) if tag else self.console_textbox.insert("end", line)

        try:
            current_lines = int(self.console_textbox.index("end-1c").split(".")[0])
            if current_lines > 1000:
                self.console_textbox.delete("1.0", "300.0")
        except Exception:
            pass

        self.console_textbox.see("end")
        self.console_textbox.configure(state="disabled")

    def get_lyrics(self) -> str:
        return self.lyrics_textbox.get("1.0", "end-1c")

    def update_lyrics(self, new_text: str) -> None:
        self.lyrics_textbox.delete("1.0", "end")
        self.lyrics_textbox.insert("1.0", new_text)
