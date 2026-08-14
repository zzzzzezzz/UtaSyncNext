from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

from ui.api_settings_panel import ApiSettingsPanel
from ui.basic_settings_panel import BasicSettingsPanel
from ui.theme import ACCENT_GREEN, BG_SIDEBAR, TEXT_MUTED, TEXT_TITLE
from ui.visual_settings_panel import VisualSettingsPanel


CallbackMap = dict[str, Callable[..., object]]


class SidebarPanel(ctk.CTkFrame):
    """Next-native sidebar with drag-drop, scrollable settings, and legacy-compatible setting keys."""

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
            width=380,
            corner_radius=0,
            fg_color=BG_SIDEBAR,
            border_color="#18181B",
            border_width=1,
            **kwargs,
        )
        self.settings = settings
        self.engine_ready = engine_ready
        self.engine_label = engine_label
        self.engine_detail = engine_detail
        self.engine_accelerator = engine_accelerator
        self.callbacks = callbacks

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        self._build_header()
        self._build_drop_zone()
        self._build_tabs()
        self._build_footer()
        self._init_dnd()

    def _build_header(self) -> None:
        self.logo_label = ctk.CTkLabel(
            self,
            text="UtaSync",
            font=ctk.CTkFont(family="Segoe UI Black", size=38, weight="bold"),
            text_color=TEXT_TITLE,
        )
        self.logo_label.grid(row=0, column=0, padx=30, pady=(40, 0), sticky="w")

        subtitle = "NEXT STUDIO · LIGHT ENGINE" if self.engine_ready else "NEXT STUDIO · SETUP REQUIRED"
        self.subtitle_label = ctk.CTkLabel(
            self,
            text=subtitle,
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=ACCENT_GREEN if self.engine_ready else TEXT_MUTED,
        )
        self.subtitle_label.grid(row=1, column=0, padx=32, pady=(0, 15), sticky="w")

        status_text = self.engine_label or ("Next 独立引擎就绪" if self.engine_ready else "需要安装 Next 引擎")
        if self.engine_accelerator and self.engine_accelerator != "unknown":
            status_text = f"{status_text} · {self.engine_accelerator}"
        self.engine_status_label = ctk.CTkLabel(
            self,
            text=status_text,
            font=ctk.CTkFont(family="Microsoft YaHei", size=11, weight="bold"),
            text_color=ACCENT_GREEN if self.engine_ready else "#FACC15",
            fg_color="#101418",
            corner_radius=8,
            height=32,
            wraplength=300,
        )
        self.engine_status_label.grid(row=2, column=0, padx=30, pady=(0, 8), sticky="ew")

        self.help_btn = ctk.CTkButton(
            self,
            text="参数指南与说明",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11, weight="bold"),
            width=120,
            height=28,
            fg_color="#121212",
            hover_color="#1E1E1E",
            text_color=TEXT_MUTED,
            border_color="#252525",
            border_width=1,
            corner_radius=6,
            command=self.callbacks.get("show_help"),
        )
        self.help_btn.grid(row=3, column=0, padx=30, pady=(0, 16), sticky="w")

    def _build_drop_zone(self) -> None:
        self.drop_zone = ctk.CTkFrame(
            self,
            width=330,
            height=100,
            fg_color="#0D0D0D",
            border_color="#222222",
            border_width=1,
            corner_radius=12,
            cursor="hand2",
        )
        self.drop_zone.grid(row=4, column=0, padx=25, pady=(0, 15), sticky="ew")
        self.drop_zone.grid_propagate(False)
        self.drop_zone.pack_propagate(False)

        def trigger_import(_event: object | None = None) -> None:
            callback = self.callbacks.get("import_video")
            if callback:
                callback()

        self.drop_zone.bind("<Button-1>", trigger_import)
        self.drop_icon = ctk.CTkLabel(self.drop_zone, text="📂", font=ctk.CTkFont(size=28))
        self.drop_icon.pack(pady=(18, 0))
        self.drop_icon.bind("<Button-1>", trigger_import)

        self.file_label = ctk.CTkLabel(
            self.drop_zone,
            text="点击或拖拽 视音频/歌词 至此",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
            text_color="#A1A1AA",
            wraplength=280,
        )
        self.file_label.pack(pady=(2, 0))
        self.file_label.bind("<Button-1>", trigger_import)

    def _build_tabs(self) -> None:
        self.tabview = ctk.CTkTabview(
            self,
            fg_color="transparent",
            bg_color="transparent",
            segmented_button_selected_color=ACCENT_GREEN,
            segmented_button_selected_hover_color="#1ED760",
            segmented_button_unselected_color="#121212",
            segmented_button_unselected_hover_color="#1A1A1A",
            text_color=TEXT_TITLE,
            corner_radius=10,
        )
        self.tabview.grid(row=5, column=0, padx=20, pady=(0, 15), sticky="nsew")

        self.tab_basic = self.tabview.add("基础控制")
        self.tab_visual = self.tabview.add("视觉特效")
        self.tab_api = self.tabview.add("大模型API")

        self._build_basic_tab()
        self._build_visual_tab()
        self._build_api_tab()

    def _build_footer(self) -> None:
        self.run_btn = ctk.CTkButton(
            self,
            text="⚡ 启 动 引 擎",
            font=ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold"),
            height=50,
            corner_radius=12,
            text_color="#000000",
            fg_color=ACCENT_GREEN,
            hover_color="#1ED760",
            command=self.callbacks.get("toggle_process"),
        )
        self.run_btn.grid(row=6, column=0, padx=25, pady=(5, 5), sticky="ew")

        self.mgr_btn = ctk.CTkButton(
            self,
            text="引擎与模型管理",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11, weight="bold"),
            height=30,
            fg_color="transparent",
            hover_color="#151515",
            text_color=TEXT_MUTED,
            command=self.callbacks.get("open_manager"),
        )
        self.mgr_btn.grid(row=7, column=0, pady=(0, 10))

        self.progressbar = ctk.CTkProgressBar(
            self,
            mode="determinate",
            height=4,
            fg_color="#1A1A1A",
            progress_color=ACCENT_GREEN,
            corner_radius=2,
        )
        self.progressbar.grid(row=8, column=0, padx=40, pady=(0, 25), sticky="ew")
        self.progressbar.set(0.0)

    def _build_basic_tab(self) -> None:
        self.basic_settings_panel = BasicSettingsPanel(self.tab_basic, self.settings)
        self.basic_settings_panel.pack(fill="both", expand=True)

    def _build_visual_tab(self) -> None:
        self.visual_settings_panel = VisualSettingsPanel(
            self.tab_visual,
            settings=self.settings,
            callbacks=self.callbacks,
            get_settings_cb=self.get_current_settings,
            sync_presets_cb=lambda: self.winfo_toplevel().save_all_ui_states(),
        )
        self.visual_settings_panel.pack(fill="both", expand=True)

    def _build_api_tab(self) -> None:
        self.api_settings_panel = ApiSettingsPanel(self.tab_api, self.settings)
        self.api_settings_panel.pack(fill="both", expand=True)

    def _init_dnd(self) -> None:
        try:
            from tkinterdnd2 import DND_FILES

            for target in [self, self.drop_zone, self.drop_icon, self.file_label]:
                target.drop_target_register(DND_FILES)
                target.dnd_bind("<<Drop>>", self._on_file_drop)
                target.dnd_bind("<<DragEnter>>", self._on_drag_enter)
                target.dnd_bind("<<DragLeave>>", self._on_drag_leave)
        except Exception:
            pass

    def _on_drag_enter(self, _event: object) -> None:
        self.drop_zone.configure(border_color=ACCENT_GREEN, fg_color="#112016")
        self.file_label.configure(text_color=ACCENT_GREEN, text="松开鼠标立即导入...")

    def _on_drag_leave(self, _event: object) -> None:
        self.drop_zone.configure(border_color="#222222", fg_color="#0D0D0D")
        self.file_label.configure(text_color="#A1A1AA", text="点击或拖拽 视音频/歌词 至此")

    def _on_file_drop(self, event: object) -> None:
        self._on_drag_leave(event)
        file_path = str(getattr(event, "data", ""))
        if file_path.startswith("{") and file_path.endswith("}"):
            file_path = file_path[1:-1]
        callback = self.callbacks.get("handle_dropped_file")
        if callback:
            callback(file_path)

    def get_current_settings(self) -> dict[str, Any]:
        basic_settings = self.basic_settings_panel.get_settings()
        visual_settings = self.visual_settings_panel.get_settings()
        api_settings = self.api_settings_panel.get_settings()

        return {
            **api_settings,
            **basic_settings,
            **visual_settings,
        }
