from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from ui.log_formatter import format_console_line
from ui.theme import ACCENT_GREEN, ACCENT_WARNING, BG_CARD, BG_WORKSPACE, TEXT_MUTED, TEXT_TITLE


class WorkspacePanel(ctk.CTkFrame):
    """Right-side workspace: lyrics input plus a lightweight streaming console."""

    def __init__(self, master, cached_lyrics: str = "", callbacks: dict[str, object] | None = None, **kwargs) -> None:
        super().__init__(master, fg_color=BG_WORKSPACE, corner_radius=0, **kwargs)
        self.callbacks = callbacks or {}
        self.output_artifacts: dict[str, object] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=3)
        self.grid_rowconfigure(2, weight=2)

        self.header_label = ctk.CTkLabel(
            self,
            text="工作区",
            font=ctk.CTkFont(family="Segoe UI Black", size=24, weight="bold"),
            text_color=TEXT_TITLE,
        )
        self.header_label.grid(row=0, column=0, padx=40, pady=(40, 20), sticky="w")

        self._build_lyrics_panel(cached_lyrics)
        self._build_console_panel()

    def _build_lyrics_panel(self, cached_lyrics: str) -> None:
        self.lyrics_panel = ctk.CTkFrame(
            self,
            fg_color=BG_CARD,
            corner_radius=12,
            border_color="#252525",
            border_width=1,
        )
        self.lyrics_panel.grid(row=1, column=0, padx=40, pady=(0, 20), sticky="nsew")
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
        self.console_panel.grid(row=2, column=0, padx=40, pady=(0, 40), sticky="nsew")
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
        if "clear_cache" in self.callbacks:
            ctk.CTkButton(
                status_frame,
                text="清理空间",
                width=76,
                height=24,
                font=ctk.CTkFont(family="Microsoft YaHei", size=11),
                fg_color="#374151",
                hover_color="#4B5563",
                command=self.callbacks["clear_cache"],
            ).pack(side="left", padx=(0, 15))

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
        self.hl_kws = ("任务：", "打轴：", "翻译：", "字幕：", "输出：", "性能：", "模型：", "网络：", "缓存：", "媒体：", "系统：", "日志：")
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
            text="等待任务完成后显示最新 SRT / ASS 文件。",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.output_label.grid(row=0, column=1, padx=(0, 12), pady=12, sticky="ew")

        button_frame = ctk.CTkFrame(self.output_bar, fg_color="transparent")
        button_frame.grid(row=0, column=2, padx=(0, 12), pady=10, sticky="e")

        self.open_output_btn = self._output_button(button_frame, "打开目录", "open_output_dir")
        self.copy_srt_btn = self._output_button(button_frame, "复制 SRT", "copy_output_srt")
        self.copy_ass_btn = self._output_button(button_frame, "复制 ASS", "copy_output_ass")
        self.copy_log_btn = self._output_button(button_frame, "复制日志", "copy_output_log")
        self._set_output_buttons(False, False, False, False)

    def _output_button(self, master, text: str, callback_name: str) -> ctk.CTkButton:
        button = ctk.CTkButton(
            master,
            text=text,
            width=82,
            height=28,
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            fg_color="#1F2A3A",
            hover_color="#2F3E55",
            command=lambda: self._run_callback(callback_name),
        )
        button.pack(side="left", padx=(0, 8))
        return button

    def _run_callback(self, callback_name: str) -> None:
        callback = self.callbacks.get(callback_name)
        if callable(callback):
            callback()

    def _set_output_buttons(self, has_dir: bool, has_srt: bool, has_ass: bool, has_log: bool) -> None:
        self.open_output_btn.configure(state="normal" if has_dir else "disabled")
        self.copy_srt_btn.configure(state="normal" if has_srt else "disabled")
        self.copy_ass_btn.configure(state="normal" if has_ass else "disabled")
        self.copy_log_btn.configure(state="normal" if has_log else "disabled")

    def set_processing_state(self, running: bool) -> None:
        if running:
            self.system_dot_label.configure(text_color=ACCENT_WARNING)
            self.system_status_label.configure(text="任务处理中", text_color=ACCENT_WARNING)
            self.output_label.configure(
                text="任务执行中，完成后这里会显示 SRT / ASS 输出文件。",
                text_color=TEXT_MUTED,
            )
            self._set_output_buttons(False, False, False, False)
            return

        self.system_dot_label.configure(text_color=ACCENT_GREEN)
        self.system_status_label.configure(text="空闲", text_color=ACCENT_GREEN)

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

        parts: list[str] = []
        if srt_path:
            parts.append(f"SRT: {Path(srt_path).name}")
        if ass_path:
            parts.append(f"ASS: {Path(ass_path).name}")
        if log_path:
            parts.append(f"日志: {Path(log_path).name}")
        self.output_label.configure(
            text="  |  ".join(parts) if parts else "暂无可显示输出。完成任务后这里会显示 SRT / ASS 文件。",
            text_color=ACCENT_GREEN if parts else TEXT_MUTED,
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
