from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk

from core.ass_maker import ASS_PRESETS
from ui.constants import FONT_ALIAS_MAP, FONT_WHITELIST, GARBAGE_FONT_KEYWORDS
from ui.sidebar_options import (
    ASPECT_RATIO_OPTIONS,
    ASS_DISABLED_OPTION,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_ASS_PRESET,
    DEFAULT_FADE_MODE,
    FADE_MODE_OPTIONS,
    FONT_SIZE_OPTIONS,
)
from ui.theme import ACCENT_GREEN, TEXT_TITLE


CallbackMap = dict[str, Callable[..., object]]


class VisualSettingsPanel(ctk.CTkScrollableFrame):
    """ASS export, font overrides, layout, and render action controls."""

    def __init__(
        self,
        master,
        settings: dict[str, Any],
        callbacks: CallbackMap,
        get_settings_cb: Callable[[], dict[str, Any]],
        sync_presets_cb: Callable[[], object],
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.settings = settings
        self.callbacks = callbacks
        self.get_settings_cb = get_settings_cb
        self.sync_presets_cb = sync_presets_cb
        self.all_system_fonts: list[str] = []

        self.grid_columnconfigure(0, weight=1)
        self._load_user_presets()
        self._build()

    def _build(self) -> None:
        row = 0

        row = self._add_group("特效字幕预设 (ASS Export)", row)
        ass_options = [ASS_DISABLED_OPTION] + list(ASS_PRESETS.keys())
        self.opt_ass_preset = self._option(
            row,
            ass_options,
            self.settings.get("ass_preset", DEFAULT_ASS_PRESET),
            fg_color="#18181B",
            button_color="#27272A",
        )
        row += 1

        self.font_search_entry = ctk.CTkEntry(
            self,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            placeholder_text="输入中文/英文/拼音过滤找字...",
            height=30,
            fg_color="#121212",
            border_color="#2A2A2A",
        )
        self.font_search_entry.grid(row=row, column=0, padx=20, pady=(10, 5), sticky="ew")
        self.font_search_entry.bind("<KeyRelease>", self._on_font_search)
        row += 1

        font_options = ["跟随预设"] + [self._format_font_display(name) for name in FONT_WHITELIST]
        self.all_system_fonts = font_options

        row = self._add_group("主字体 (中文) / 副字体 (日文)", row)
        font_frame = ctk.CTkFrame(self, fg_color="transparent")
        font_frame.grid(row=row, column=0, padx=20, pady=(0, 5), sticky="ew")
        font_frame.grid_columnconfigure((0, 1), weight=1)
        self.opt_ch_font = ctk.CTkComboBox(
            font_frame,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            values=font_options,
            fg_color="#18181B",
            button_color="#27272A",
        )
        self.opt_ch_font.set(self._format_font_display(self.settings.get("ch_font_override", "跟随预设")))
        self.opt_ch_font.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        self.opt_jp_font = ctk.CTkComboBox(
            font_frame,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            values=font_options,
            fg_color="#18181B",
            button_color="#27272A",
        )
        self.opt_jp_font.set(self._format_font_display(self.settings.get("jp_font_override", "跟随预设")))
        self.opt_jp_font.grid(row=0, column=1, padx=(5, 0), sticky="ew")
        row += 1
        self._lazy_load_system_fonts()

        row = self._add_group("主字号 (中文) / 副字号 (日文)", row)
        size_frame = ctk.CTkFrame(self, fg_color="transparent")
        size_frame.grid(row=row, column=0, padx=20, pady=(0, 10), sticky="ew")
        size_frame.grid_columnconfigure((0, 1), weight=1)
        self.opt_ch_size = ctk.CTkComboBox(size_frame, values=FONT_SIZE_OPTIONS, fg_color="#18181B", button_color="#27272A")
        self.opt_ch_size.set(self.settings.get("ch_size_override", "跟随预设"))
        self.opt_ch_size.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        self.opt_jp_size = ctk.CTkComboBox(size_frame, values=FONT_SIZE_OPTIONS, fg_color="#18181B", button_color="#27272A")
        self.opt_jp_size.set(self.settings.get("jp_size_override", "跟随预设"))
        self.opt_jp_size.grid(row=0, column=1, padx=(5, 0), sticky="ew")
        row += 1

        row = self._add_group("目标画幅比", row)
        self.opt_aspect_ratio = self._option(
            row,
            ASPECT_RATIO_OPTIONS,
            self.settings.get("aspect_ratio", DEFAULT_ASPECT_RATIO),
            fg_color="#18181B",
            button_color="#27272A",
        )
        row += 1

        row = self._add_group("字幕出场动画", row)
        self.opt_fade_mode = self._option(
            row,
            FADE_MODE_OPTIONS,
            self.settings.get("fade_mode", DEFAULT_FADE_MODE),
            fg_color="#18181B",
            button_color="#27272A",
        )
        row += 1

        action_frame = ctk.CTkFrame(self, fg_color="transparent")
        action_frame.grid(row=row, column=0, padx=20, pady=(10, 5), sticky="ew")
        action_frame.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            action_frame,
            text="预览样式",
            height=32,
            fg_color="#18181B",
            hover_color="#27272A",
            text_color="#A1A1AA",
            border_width=1,
            border_color="#3F3F46",
            command=self.callbacks.get("preview_ass"),
        ).grid(row=0, column=0, padx=(0, 5), sticky="ew")
        ctk.CTkButton(
            action_frame,
            text="极速重绘",
            height=32,
            fg_color="#2A2A2A",
            hover_color="#3F3F46",
            text_color="#E5E5E5",
            command=self.callbacks.get("redraw_ass"),
        ).grid(row=0, column=1, padx=(5, 0), sticky="ew")
        row += 1

        ctk.CTkButton(
            self,
            text="浏览花字画廊 & AI 配色",
            height=36,
            fg_color="#059669",
            hover_color="#047857",
            text_color="#FFFFFF",
            command=self.open_style_gallery,
        ).grid(row=row, column=0, padx=20, pady=(5, 10), sticky="ew")
        row += 1

        self.check_hires = ctk.CTkSwitch(
            self,
            text="开启 B站 Hi-Res 无损封装 (.mkv)",
            text_color=ACCENT_GREEN,
            progress_color=ACCENT_GREEN,
        )
        if self.settings.get("hires_export", False):
            self.check_hires.select()
        self.check_hires.grid(row=row, column=0, padx=20, pady=(10, 10), sticky="w")
        row += 1

        ctk.CTkButton(
            self,
            text="启动底层物理压制 (硬字幕)",
            height=36,
            fg_color="#1E3A8A",
            hover_color="#0284C7",
            text_color="#FFFFFF",
            command=self.callbacks.get("burn_video"),
        ).grid(row=row, column=0, padx=20, pady=(0, 10), sticky="ew")

    def get_settings(self) -> dict[str, str | bool]:
        return {
            "ass_preset": self.opt_ass_preset.get(),
            "ch_font_override": self._clean_font_display(self.opt_ch_font.get()),
            "jp_font_override": self._clean_font_display(self.opt_jp_font.get()),
            "ch_size_override": self.opt_ch_size.get(),
            "jp_size_override": self.opt_jp_size.get(),
            "aspect_ratio": self.opt_aspect_ratio.get(),
            "fade_mode": self.opt_fade_mode.get(),
            "hires_export": self.check_hires.get() == 1,
        }

    def open_style_gallery(self) -> None:
        from ui.gallery import StyleGalleryWindow

        def on_preset_selected(preset_name: str) -> None:
            options = [ASS_DISABLED_OPTION] + list(ASS_PRESETS.keys())
            self.opt_ass_preset.configure(values=options)
            self.opt_ass_preset.set(preset_name)

        StyleGalleryWindow(
            master=self.winfo_toplevel(),
            current_preset=self.opt_ass_preset.get(),
            apply_callback=on_preset_selected,
            get_settings_cb=self.get_settings_cb,
            sync_presets_cb=self.sync_presets_cb,
        )

    def _add_group(self, text: str, row: int) -> int:
        ctk.CTkLabel(
            self,
            text=text,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#666666",
        ).grid(row=row, column=0, padx=20, pady=(15, 2), sticky="w")
        return row + 1

    def _option(
        self,
        row: int,
        values: list[str],
        value: object,
        command: Callable[[str], object] | None = None,
        fg_color: str = "#222222",
        button_color: str = "#222222",
    ) -> ctk.CTkOptionMenu:
        option = ctk.CTkOptionMenu(
            self,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            values=values,
            command=command,
            fg_color=fg_color,
            text_color=TEXT_TITLE,
            button_color=button_color,
            button_hover_color="#2A2A2A",
        )
        selected = str(value or values[0])
        option.set(selected if selected in values else values[0])
        option.grid(row=row, column=0, padx=20, pady=(0, 10), sticky="ew")
        return option

    def _format_font_display(self, font_name: object) -> str:
        name = str(font_name or "跟随预设")
        if name == "跟随预设":
            return name
        alias = FONT_ALIAS_MAP.get(name)
        return f"{alias} | {name}" if alias else name

    def _clean_font_display(self, display_name: object) -> str:
        text = str(display_name or "跟随预设")
        if text == "跟随预设" or "━━━" in text:
            return "跟随预设"
        return text.split(" | ")[-1].strip()

    def _lazy_load_system_fonts(self) -> None:
        def font_worker() -> None:
            try:
                from tkinter import font as tkfont

                system_fonts = sorted(set(tkfont.families()))
                cjk_pattern = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")
                cjk_keywords = (
                    "cjk",
                    "jp",
                    "sc",
                    "tc",
                    "chinese",
                    "japanese",
                    "hei",
                    "song",
                    "mincho",
                    "gothic",
                    "meiryo",
                    "yahei",
                    "sim",
                    "noto",
                    "source han",
                )
                preferred = [font for font in FONT_WHITELIST if font in system_fonts]
                other = []
                for font_name in system_fonts:
                    lower = font_name.lower()
                    if font_name in preferred or any(bad in lower for bad in GARBAGE_FONT_KEYWORDS):
                        continue
                    if cjk_pattern.search(font_name) or any(key in lower for key in cjk_keywords):
                        other.append(font_name)

                final_fonts = ["跟随预设"] + [self._format_font_display(font) for font in preferred + other]
                if len(final_fonts) <= 1:
                    final_fonts.extend([self._format_font_display("Microsoft YaHei"), self._format_font_display("Meiryo")])
                self.after(0, lambda: self._update_font_ui(final_fonts))
            except Exception:
                pass

        threading.Thread(target=font_worker, daemon=True).start()

    def _update_font_ui(self, font_list: list[str]) -> None:
        self.all_system_fonts = font_list
        ch_current = self.opt_ch_font.get()
        jp_current = self.opt_jp_font.get()
        self.opt_ch_font.configure(values=font_list)
        self.opt_jp_font.configure(values=font_list)
        if ch_current in font_list:
            self.opt_ch_font.set(ch_current)
        if jp_current in font_list:
            self.opt_jp_font.set(jp_current)

    def _on_font_search(self, _event: object) -> None:
        query = self.font_search_entry.get().lower().strip()
        font_list = self.all_system_fonts
        if query:
            font_list = [font for font in font_list if query in font.lower() and "━━━" not in font]
            if "跟随预设" not in font_list:
                font_list.insert(0, "跟随预设")

        ch_current = self.opt_ch_font.get()
        jp_current = self.opt_jp_font.get()
        self.opt_ch_font.configure(values=font_list)
        self.opt_jp_font.configure(values=font_list)
        if ch_current in font_list:
            self.opt_ch_font.set(ch_current)
        if jp_current in font_list:
            self.opt_jp_font.set(jp_current)

    def _load_user_presets(self) -> None:
        preset_file = Path("user_presets.json")
        if not preset_file.exists():
            return
        try:
            data = json.loads(preset_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                ASS_PRESETS.update(data)
        except Exception:
            pass
