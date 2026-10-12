from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

from ui.api_settings_panel import ApiSettingsPanel
from ui.theme import ACCENT_GREEN, BG_WORKSPACE, TEXT_MUTED, TEXT_TITLE
from ui.visual_settings_panel import VisualSettingsPanel
from ui.window_icon import apply_child_window_icon


class AdvancedSettingsDialog(ctk.CTkToplevel):
    """Real, reversible Next UI experiment for non-critical task settings."""

    def __init__(
        self,
        master,
        settings: dict[str, Any],
        callbacks: dict[str, Callable[..., object]],
        on_save: Callable[[dict[str, Any]], object],
        on_close: Callable[[], object],
        get_settings_cb: Callable[[], dict[str, Any]],
        sync_presets_cb: Callable[[], object],
    ) -> None:
        super().__init__(master)
        self._on_save = on_save
        self._on_close = on_close
        self._closed = False

        self.title("UtaSyncNext · 高级设置")
        apply_child_window_icon(self, master)
        self.geometry("860x760")
        self.minsize(760, 660)
        self.configure(fg_color=BG_WORKSPACE)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        shell = ctk.CTkFrame(self, fg_color=BG_WORKSPACE, corner_radius=0)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(shell, fg_color="#111A27", corner_radius=0, height=72)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="高级设置",
            font=ctk.CTkFont(family="Microsoft YaHei", size=20, weight="bold"),
            text_color=TEXT_TITLE,
        ).grid(row=0, column=0, padx=28, pady=(15, 0), sticky="w")
        ctk.CTkLabel(
            header,
            text="配置字幕样式、视频压制和翻译接口；关闭弹窗不会影响当前媒体与日志。",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            text_color=TEXT_MUTED,
        ).grid(row=1, column=0, padx=28, pady=(0, 13), sticky="w")

        self.tabs = ctk.CTkTabview(
            shell,
            fg_color="#0E141D",
            segmented_button_selected_color="#166534",
            segmented_button_selected_hover_color="#15803D",
            segmented_button_unselected_color="#182231",
            segmented_button_unselected_hover_color="#24344A",
            text_color=TEXT_TITLE,
            corner_radius=10,
        )
        self.tabs.grid(row=1, column=0, padx=24, pady=(12, 12), sticky="nsew")
        visual_tab = self.tabs.add("字幕与视频")
        api_tab = self.tabs.add("翻译与网络")

        self.visual_settings_panel = VisualSettingsPanel(
            visual_tab,
            settings=settings,
            callbacks=callbacks,
            get_settings_cb=get_settings_cb,
            sync_presets_cb=sync_presets_cb,
        )
        self.visual_settings_panel.pack(fill="both", expand=True)

        self.api_settings_panel = ApiSettingsPanel(api_tab, settings)
        self.api_settings_panel.pack(fill="both", expand=True)

        footer = ctk.CTkFrame(shell, fg_color=BG_WORKSPACE)
        footer.grid(row=2, column=0, padx=24, pady=(0, 16), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer,
            text="取消",
            width=96,
            height=38,
            fg_color="#182231",
            hover_color="#24344A",
            border_color="#3A4C61",
            border_width=1,
            command=self.close,
        ).grid(row=0, column=1, padx=(0, 10))
        ctk.CTkButton(
            footer,
            text="保存设置",
            width=132,
            height=38,
            fg_color=ACCENT_GREEN,
            hover_color="#1ED760",
            text_color="#06130B",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
            command=self.save_and_close,
        ).grid(row=0, column=2)

        self.after(60, self._activate_modal)

    def current_settings(self) -> dict[str, Any]:
        return {
            **self.visual_settings_panel.get_settings(),
            **self.api_settings_panel.get_settings(),
        }

    def save_and_close(self) -> None:
        self._on_save(self.current_settings())
        self.close()

    def _activate_modal(self) -> None:
        if not self.winfo_exists():
            return
        self.grab_set()
        self.focus_force()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.grab_release()
        except Exception:
            pass
        self._on_close()
        self.destroy()
