from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

from ui.advanced_settings_dialog import AdvancedSettingsDialog
from ui.basic_settings_panel import BasicSettingsPanel
from ui.theme import ACCENT_GREEN, BG_SIDEBAR, TEXT_MUTED, TEXT_TITLE


CallbackMap = dict[str, Callable[..., object]]


class ExperimentalSidebarPanel(ctk.CTkFrame):
    """The production Next workspace sidebar."""

    def __init__(
        self,
        master,
        settings: dict[str, Any],
        engine_ready: bool,
        callbacks: CallbackMap,
        engine_label: str = "",
        engine_detail: str = "",
        engine_accelerator: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            width=300,
            corner_radius=0,
            fg_color=BG_SIDEBAR,
            border_color="#1A2430",
            border_width=1,
            **kwargs,
        )
        self.settings = dict(settings)
        self.callbacks = callbacks
        self.engine_ready = engine_ready
        self.engine_detail = engine_detail
        self._advanced_settings = dict(settings)
        self._advanced_dialog: AdvancedSettingsDialog | None = None
        self.visual_settings_panel = None
        self.api_settings_panel = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        self._build_header(engine_label, engine_accelerator)
        self._build_drop_zone()
        self._build_core_settings()
        self._build_footer()
        self._init_dnd()

    def _build_header(self, engine_label: str, engine_accelerator: str) -> None:
        self.logo_label = ctk.CTkLabel(
            self,
            text="UtaSync",
            font=ctk.CTkFont(family="Segoe UI Black", size=32, weight="bold"),
            text_color=TEXT_TITLE,
        )
        self.logo_label.grid(row=0, column=0, padx=26, pady=(28, 0), sticky="w")
        self.subtitle_label = ctk.CTkLabel(
            self,
            text="NEXT STUDIO · UI LAB",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=ACCENT_GREEN,
        )
        self.subtitle_label.grid(row=1, column=0, padx=28, pady=(0, 11), sticky="w")

        status_text = engine_label or ("Next 独立引擎就绪" if self.engine_ready else "需要安装 Next 引擎")
        if engine_accelerator and engine_accelerator != "unknown":
            status_text = f"{status_text} · {engine_accelerator}"
        self.engine_status_label = ctk.CTkLabel(
            self,
            text=status_text,
            height=30,
            corner_radius=7,
            fg_color="#101820",
            text_color=ACCENT_GREEN if self.engine_ready else "#FACC15",
            font=ctk.CTkFont(family="Microsoft YaHei", size=10, weight="bold"),
        )
        self.engine_status_label.grid(row=2, column=0, padx=26, pady=(0, 9), sticky="ew")

        utility = ctk.CTkFrame(self, fg_color="transparent")
        utility.grid(row=3, column=0, padx=26, pady=(0, 13), sticky="ew")
        ctk.CTkButton(
            utility,
            text="ⓘ 参数说明",
            width=104,
            height=27,
            fg_color="#151D27",
            hover_color="#202D3B",
            text_color=TEXT_MUTED,
            command=self.callbacks.get("show_help"),
        ).pack(side="left")

    def _build_drop_zone(self) -> None:
        self.drop_zone = ctk.CTkFrame(
            self,
            height=54,
            fg_color="#0D131A",
            border_color="#263545",
            border_width=1,
            corner_radius=10,
            cursor="hand2",
        )
        self.drop_zone.grid(row=4, column=0, padx=22, pady=(0, 10), sticky="ew")
        self.drop_zone.grid_propagate(False)
        self.drop_zone.grid_columnconfigure(0, weight=1)

        def trigger_import(_event: object | None = None) -> None:
            callback = self.callbacks.get("import_video")
            if callback:
                callback()

        self.drop_icon = ctk.CTkLabel(self.drop_zone, text="＋", width=28, text_color="#7DD3FC", font=ctk.CTkFont(size=20))
        self.drop_icon.grid(row=0, column=0, padx=(12, 0), pady=7, sticky="w")
        self.file_label = ctk.CTkLabel(
            self.drop_zone,
            text="选择或拖入视音频 / 歌词",
            anchor="w",
            text_color="#D4DCE8",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
            wraplength=0,
        )
        self.file_label.grid(row=0, column=0, padx=(46, 12), pady=7, sticky="ew")
        for widget in (self.drop_zone, self.drop_icon, self.file_label):
            widget.bind("<Button-1>", trigger_import)

    def _build_core_settings(self) -> None:
        settings_host = ctk.CTkFrame(self, fg_color="transparent")
        settings_host.grid(row=5, column=0, padx=12, pady=(0, 8), sticky="nsew")
        settings_host.grid_columnconfigure(0, weight=1)
        settings_host.grid_rowconfigure(0, weight=1)
        self.basic_settings_panel = BasicSettingsPanel(settings_host, self.settings)
        self.basic_settings_panel.grid(row=0, column=0, sticky="nsew")

    def _build_footer(self) -> None:
        self.advanced_btn = ctk.CTkButton(
            self,
            text="高级设置  ·  字幕 / 视频 / API",
            height=38,
            fg_color="#162130",
            hover_color="#223247",
            border_color="#344A61",
            border_width=1,
            text_color="#D8E5F2",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
            command=self.open_advanced_settings,
        )
        self.advanced_btn.grid(row=6, column=0, padx=22, pady=(0, 8), sticky="ew")

        self.run_btn = ctk.CTkButton(
            self,
            text="⚡ 启 动 引 擎",
            height=50,
            corner_radius=10,
            text_color="#06130B",
            fg_color=ACCENT_GREEN,
            hover_color="#1ED760",
            font=ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold"),
            command=self.callbacks.get("toggle_process"),
        )
        self.run_btn.grid(row=7, column=0, padx=22, pady=(0, 5), sticky="ew")

        tools_row = ctk.CTkFrame(self, fg_color="transparent")
        tools_row.grid(row=8, column=0, padx=22, pady=(0, 8), sticky="ew")
        ctk.CTkButton(
            tools_row,
            text="引擎与模型管理",
            height=28,
            fg_color="transparent",
            hover_color="#151E29",
            text_color=TEXT_MUTED,
            command=self.callbacks.get("open_manager"),
        ).pack(fill="x")

        self.progressbar = ctk.CTkProgressBar(
            self,
            mode="determinate",
            height=4,
            fg_color="#1A2632",
            progress_color=ACCENT_GREEN,
            corner_radius=2,
        )
        self.progressbar.grid(row=9, column=0, padx=28, pady=(0, 18), sticky="ew")
        self.progressbar.set(0.0)

    def open_advanced_settings(self) -> None:
        if self._advanced_dialog is not None and self._advanced_dialog.winfo_exists():
            self._advanced_dialog.lift()
            self._advanced_dialog.focus_force()
            return
        self._advanced_dialog = AdvancedSettingsDialog(
            self.winfo_toplevel(),
            settings=self.get_current_settings(),
            callbacks=self.callbacks,
            on_save=self._save_advanced_settings,
            on_close=self._advanced_dialog_closed,
            get_settings_cb=self.get_current_settings,
            sync_presets_cb=lambda: self.winfo_toplevel().save_all_ui_states(),
        )
        self.visual_settings_panel = self._advanced_dialog.visual_settings_panel
        self.api_settings_panel = self._advanced_dialog.api_settings_panel

    def _save_advanced_settings(self, values: dict[str, Any]) -> None:
        self._advanced_settings.update(values)
        top = self.winfo_toplevel()
        if hasattr(top, "save_current_settings"):
            top.save_current_settings(self.get_current_settings())

    def _advanced_dialog_closed(self) -> None:
        self._advanced_dialog = None
        self.visual_settings_panel = None
        self.api_settings_panel = None
        top = self.winfo_toplevel()
        if hasattr(top, "workspace"):
            top.workspace.set_burn_mode(bool(self._advanced_settings.get("hires_export", False)))

    def get_current_settings(self) -> dict[str, Any]:
        values = dict(self._advanced_settings)
        values.update(self.basic_settings_panel.get_settings())
        if self._advanced_dialog is not None and self._advanced_dialog.winfo_exists():
            values.update(self._advanced_dialog.current_settings())
        return values

    def _init_dnd(self) -> None:
        try:
            from tkinterdnd2 import DND_FILES

            for target in (self, self.drop_zone, self.drop_icon, self.file_label):
                target.drop_target_register(DND_FILES)
                target.dnd_bind("<<Drop>>", self._on_file_drop)
                target.dnd_bind("<<DragEnter>>", self._on_drag_enter)
                target.dnd_bind("<<DragLeave>>", self._on_drag_leave)
        except Exception:
            pass

    def _on_drag_enter(self, _event: object) -> None:
        self.drop_zone.configure(border_color=ACCENT_GREEN, fg_color="#112016")
        self.file_label.configure(text_color=ACCENT_GREEN, text="松开鼠标立即导入")

    def _on_drag_leave(self, _event: object) -> None:
        self.drop_zone.configure(border_color="#263545", fg_color="#0D131A")
        self.file_label.configure(text_color="#D4DCE8", text="选择或拖入视音频 / 歌词")

    def _on_file_drop(self, event: object) -> None:
        self._on_drag_leave(event)
        file_path = str(getattr(event, "data", ""))
        if file_path.startswith("{") and file_path.endswith("}"):
            file_path = file_path[1:-1]
        callback = self.callbacks.get("handle_dropped_file")
        if callback:
            callback(file_path)
