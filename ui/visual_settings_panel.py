from __future__ import annotations

import json
import os
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox
from typing import Any, Callable

import customtkinter as ctk

from core.ass_maker import ASS_PRESETS
from ui.constants import CURATED_FONT_FAMILIES, FONT_ALIAS_MAP
from ui.font_catalog import (
    discover_user_fonts,
    filter_font_families,
    import_font,
    import_fonts_from_directory,
    user_fonts_dir,
)
from ui.sidebar_options import (
    ASPECT_RATIO_OPTIONS,
    ASS_DISABLED_OPTION,
    DEFAULT_ASPECT_RATIO,
    DEFAULT_ASS_PRESET,
    DEFAULT_FADE_MODE,
    FADE_MODE_OPTIONS,
    FONT_SIZE_OPTIONS,
)
from ui.theme import ACCENT_GREEN, TEXT_HINT, TEXT_TITLE


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
        self._user_fonts = discover_user_fonts()
        self._user_font_families = set(filter_font_families(self._user_fonts))

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

        font_options = self._font_options()

        row = self._add_group("主字体 (中文) / 副字体 (日文)", row)
        font_frame = ctk.CTkFrame(self, fg_color="transparent")
        font_frame.grid(row=row, column=0, padx=20, pady=(0, 5), sticky="ew")
        font_frame.grid_columnconfigure((0, 1), weight=1)
        self.opt_ch_font = ctk.CTkOptionMenu(
            font_frame,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            values=font_options,
            fg_color="#18181B",
            button_color="#27272A",
            button_hover_color="#3F3F46",
            text_color=TEXT_TITLE,
        )
        self.opt_ch_font.set(self._format_font_display(self.settings.get("ch_font_override", "跟随预设")))
        self.opt_ch_font.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        self.opt_jp_font = ctk.CTkOptionMenu(
            font_frame,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            values=font_options,
            fg_color="#18181B",
            button_color="#27272A",
            button_hover_color="#3F3F46",
            text_color=TEXT_TITLE,
        )
        self.opt_jp_font.set(self._format_font_display(self.settings.get("jp_font_override", "跟随预设")))
        self.opt_jp_font.grid(row=0, column=1, padx=(5, 0), sticky="ew")
        row += 1
        font_actions = ctk.CTkFrame(self, fg_color="transparent")
        font_actions.grid(row=row, column=0, padx=20, pady=(0, 8), sticky="ew")
        font_actions.grid_columnconfigure(0, weight=1)
        self.font_catalog_hint = ctk.CTkLabel(
            font_actions,
            text=self._font_catalog_hint(),
            font=ctk.CTkFont(family="Microsoft YaHei", size=10),
            text_color="#94A3B8",
            justify="left",
        )
        self.font_catalog_hint.grid(row=0, column=0, padx=(0, 8), sticky="w")
        ctk.CTkButton(
            font_actions,
            text="导入字体",
            width=74,
            height=28,
            fg_color="#1F2A3A",
            hover_color="#2F3E55",
            command=self._import_user_font,
        ).grid(row=0, column=1, padx=(0, 6))
        ctk.CTkButton(
            font_actions,
            text="批量导入",
            width=74,
            height=28,
            fg_color="#1F2A3A",
            hover_color="#2F3E55",
            command=self._import_user_fonts,
        ).grid(row=0, column=2, padx=(0, 6))
        ctk.CTkButton(
            font_actions,
            text="字体目录",
            width=74,
            height=28,
            fg_color="#1F2A3A",
            hover_color="#2F3E55",
            command=self._open_user_fonts_dir,
        ).grid(row=0, column=3)
        ctk.CTkButton(
            font_actions,
            text="刷新",
            width=54,
            height=28,
            fg_color="#1F2A3A",
            hover_color="#2F3E55",
            command=self._refresh_font_options,
        ).grid(row=0, column=4, padx=(6, 0))
        row += 1
        row = self._add_group("主字号 (中文) / 副字号 (日文)", row)
        size_frame = ctk.CTkFrame(self, fg_color="transparent")
        size_frame.grid(row=row, column=0, padx=20, pady=(0, 10), sticky="ew")
        size_frame.grid_columnconfigure((0, 1), weight=1)
        self.opt_ch_size = ctk.CTkOptionMenu(
            size_frame,
            values=FONT_SIZE_OPTIONS,
            fg_color="#18181B",
            button_color="#27272A",
            button_hover_color="#3F3F46",
            text_color=TEXT_TITLE,
        )
        self.opt_ch_size.set(self.settings.get("ch_size_override", "跟随预设"))
        self.opt_ch_size.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        self.opt_jp_size = ctk.CTkOptionMenu(
            size_frame,
            values=FONT_SIZE_OPTIONS,
            fg_color="#18181B",
            button_color="#27272A",
            button_hover_color="#3F3F46",
            text_color=TEXT_TITLE,
        )
        self.opt_jp_size.set(self.settings.get("jp_size_override", "跟随预设"))
        self.opt_jp_size.grid(row=0, column=1, padx=(5, 0), sticky="ew")
        row += 1
        ctk.CTkLabel(
            self,
            text="选具体字号会覆盖画幅安全区上限；字放大后每行字数会自动收紧，避免越出画面。",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            text_color=TEXT_HINT,
            anchor="w",
            justify="left",
        ).grid(row=row, column=0, padx=20, pady=(0, 10), sticky="ew")
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
            text="打开字幕样式工作台",
            height=36,
            fg_color="#059669",
            hover_color="#047857",
            text_color="#FFFFFF",
            command=self.open_style_gallery,
        ).grid(row=row, column=0, padx=20, pady=(5, 10), sticky="ew")
        row += 1

        self.check_hires = ctk.CTkSwitch(
            self,
            text="B站 Hi-Res 小金标（可选：MKV + 32-bit FLAC）",
            text_color=ACCENT_GREEN,
            progress_color=ACCENT_GREEN,
            command=self._notify_hires_mode,
        )
        if self.settings.get("hires_export", False):
            self.check_hires.select()
        self.check_hires.grid(row=row, column=0, padx=20, pady=(10, 10), sticky="w")
        row += 1

        self.burn_hint = ctk.CTkLabel(
            self,
            text="默认关闭，避免不必要的大文件；需要上传 B站并保留无损音频时再开启。生成 ASS 后在主界面执行压制。",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            text_color="#94A3B8",
            wraplength=300,
            justify="left",
        )
        self.burn_hint.grid(row=row, column=0, padx=20, pady=(0, 10), sticky="w")

    def _notify_hires_mode(self) -> None:
        callback = self.callbacks.get("burn_mode_changed")
        if callable(callback):
            callback(self.check_hires.get() == 1)

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
            text_color="#A1A1AA",
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
        if name in self._user_font_families:
            return f"用户字体 | {name}"
        alias = FONT_ALIAS_MAP.get(name)
        return f"{alias} | {name}" if alias else name

    def _clean_font_display(self, display_name: object) -> str:
        text = str(display_name or "跟随预设")
        if text == "跟随预设" or "━━━" in text:
            return "跟随预设"
        return text.split(" | ")[-1].strip()

    def _font_options(self) -> list[str]:
        families = self._available_curated_fonts()
        for family in filter_font_families(self._user_fonts):
            if family not in families:
                families.append(family)
        # Preserve a previously saved choice even when its file is currently
        # unavailable; silently replacing a task font is worse than showing it.
        for saved_key in ("ch_font_override", "jp_font_override"):
            saved_font = str(self.settings.get(saved_key) or "").strip()
            if saved_font and saved_font != "跟随预设" and saved_font not in families:
                families.append(saved_font)
        return ["跟随预设"] + [self._format_font_display(name) for name in families]

    def _available_curated_fonts(self) -> list[str]:
        """Keep the curated catalog small while hiding fonts absent on this PC."""
        try:
            installed = {str(name) for name in tkfont.families(self.winfo_toplevel())}
        except Exception:
            installed = set()
        available = [name for name in CURATED_FONT_FAMILIES if name in installed]
        # The four original choices remain visible if Tk cannot report the
        # registry (for example during a headless preview or test harness).
        return available or list(CURATED_FONT_FAMILIES[:4])

    def _font_catalog_hint(self) -> str:
        user_count = len(self._user_fonts)
        curated_count = len(self._available_curated_fonts())
        return f"精选可用 {curated_count} 种 + 用户字体 {user_count} 种，不扫描全部系统字体。"

    def _refresh_font_options(self, selected_family: str = "") -> None:
        current_ch = selected_family or self._clean_font_display(self.opt_ch_font.get())
        current_jp = self._clean_font_display(self.opt_jp_font.get())
        self._user_fonts = discover_user_fonts()
        self._user_font_families = set(filter_font_families(self._user_fonts))
        options = self._font_options()
        self.opt_ch_font.configure(values=options)
        self.opt_jp_font.configure(values=options)
        self.opt_ch_font.set(self._format_font_display(current_ch))
        self.opt_jp_font.set(self._format_font_display(current_jp))
        self.font_catalog_hint.configure(text=self._font_catalog_hint())

    def _import_user_font(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title="导入字幕字体",
            filetypes=[("字体文件", "*.ttf *.otf *.ttc *.otc"), ("所有文件", "*.*")],
        )
        if not selected:
            return
        try:
            record = import_font(selected)
            self._refresh_font_options(record.family)
            messagebox.showinfo(
                "字体已导入",
                f"已加入用户字体：{record.family}\n\n保存设置后即可用于 ASS 与视频压制。",
                parent=self.winfo_toplevel(),
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("字体导入失败", str(exc), parent=self.winfo_toplevel())

    def _open_user_fonts_dir(self) -> None:
        directory = user_fonts_dir()
        if hasattr(os, "startfile"):
            os.startfile(str(directory))  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("字体目录", str(directory), parent=self.winfo_toplevel())

    def _import_user_fonts(self) -> None:
        source = filedialog.askdirectory(parent=self.winfo_toplevel(), title="选择要导入的字体文件夹")
        if not source:
            return
        try:
            records = import_fonts_from_directory(source)
            if not records:
                messagebox.showinfo(
                    "没有找到字体",
                    "所选文件夹第一层没有 .ttf、.otf、.ttc 或 .otc 文件。",
                    parent=self.winfo_toplevel(),
                )
                return
            self._refresh_font_options(records[0].family)
            messagebox.showinfo(
                "字体已批量导入",
                f"已导入 {len(records)} 个字体文件。\n\n请在下拉框中选择并保存设置。",
                parent=self.winfo_toplevel(),
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("字体导入失败", str(exc), parent=self.winfo_toplevel())

    def _load_user_presets(self) -> None:
        preset_file = Path("user_presets.json")
        if not preset_file.exists():
            return
        try:
            data = json.loads(preset_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                ASS_PRESETS.update({name: value for name, value in data.items() if not str(name).startswith("AI:")})
        except Exception:
            pass
