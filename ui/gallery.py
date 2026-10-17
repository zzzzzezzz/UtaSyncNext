from __future__ import annotations

import threading
from tkinter import colorchooser, font as tkfont, messagebox, simpledialog
from typing import Any, Callable

import customtkinter as ctk
from PIL import Image, ImageOps

from core.ass_maker import ASS_PRESETS, BUILTIN_PRESETS
from core.subtitle_policy import apply_layout_policy, resolve_subtitle_layout
from ui.ass_preview_frame import render_ass_preview_frame
from ui.design_tokens import UTASYNC_TOKENS
from ui.font_catalog import discover_user_fonts, load_user_font_pairs, save_user_font_pairs
from ui.font_preview_window import FontPreviewWindow
from ui.preset_store import (
    load_presets_into,
    restore_builtin_presets,
    write_preset_document,
)
from ui.theme import ACCENT_GREEN, ACCENT_WARNING, BG_WORKSPACE, TEXT_TITLE
from ui.window_icon import apply_child_window_icon

# Compact style editor heights: the collapsed header row and the header's own
# height, used to size the panel from its content instead of a fixed guess.
EDITOR_COLLAPSED_HEIGHT = 48
EDITOR_HEADER_HEIGHT = 44

# The preview card letterboxes the real frame, so a very tall portrait frame is
# capped instead of pushing everything else off screen.
PREVIEW_STAGE_MIN_HEIGHT = 150
PREVIEW_STAGE_MAX_HEIGHT = 460

FONT_PAIR_OPTIONS: dict[str, tuple[str, str]] = {
    "雅黑 + Meiryo（默认）": ("Microsoft YaHei", "Meiryo"),
    "思源黑体 CN + JP（同族黑体）": ("Source Han Sans CN", "Source Han Sans JP"),
    "思源黑体 + Zen Kaku（日系轻快）": ("Source Han Sans CN", "Zen Kaku Gothic New"),
    "思源黑体 + M PLUS（圆黑冰感）": ("Source Han Sans CN", "M PLUS 1p"),
    "思源黑体 + Meiryo（高可读）": ("Source Han Sans CN", "Meiryo"),
    "雅黑 + Zen Kaku（稳重日系）": ("Microsoft YaHei", "Zen Kaku Gothic New"),
    "雅黑 + Meiryo（B站烤肉）": ("Microsoft YaHei", "Meiryo"),
    "雅黑 + 思源黑体 JP（最高可读）": ("Microsoft YaHei", "Source Han Sans JP"),
    "霞鹜文楷 + 游明朝（抒情衬线）": ("LXGW WenKai", "Yu Mincho"),
    "霞鹜文楷 + Zen Kaku（楷体中文）": ("LXGW WenKai", "Zen Kaku Gothic New"),
    "等线 + M PLUS（梦幻轻飘）": ("DengXian", "M PLUS 1p"),
    "等线 + 游ゴシック（清爽细黑）": ("DengXian", "Yu Gothic"),
    "黑体 + 游ゴシック（现场硬朗）": ("SimHei", "Yu Gothic"),
    "宋体 + 游明朝（电影衬线）": ("SimSun", "Yu Mincho"),
    "M PLUS + Zen Kaku（轻快日系）": ("M PLUS 1p", "Zen Kaku Gothic New"),
}

# A small local palette catalog keeps the style workflow predictable.  These
# palettes change only the Chinese/Japanese colors and the shared outline;
# fonts, sizes, and shadows remain the user's current choices.
COLOR_PALETTES: dict[str, dict[str, str]] = {
    "柔和统一": {
        "ch_color": "#F0F8FF",
        "jp_color": "#BAE6FD",
        "outline": "#0C4A6E",
        "description": "当前 yama 风格 · 同色系低对比",
    },
    "明显区分": {
        "ch_color": "#FFF7ED",
        "jp_color": "#7DD3FC",
        "outline": "#0B1F3A",
        "description": "推荐 · 中文暖白、日文天蓝",
    },
    "强对比": {
        "ch_color": "#FFFFFF",
        "jp_color": "#F59E0B",
        "outline": "#111827",
        "description": "复杂背景和现场画面",
    },
}

# Short labels for the narrow preset cards; a full family name would be cut off.
SHORT_FONT_NAMES: dict[str, str] = {
    "Source Han Sans CN": "思源CN",
    "Source Han Sans JP": "思源JP",
    "LXGW WenKai": "霞鹜文楷",
    "Zen Kaku Gothic New": "Zen Kaku",
    "M PLUS 1p": "M PLUS",
    "Yu Mincho": "游明朝",
    "Yu Gothic": "游ゴシック",
    "DengXian": "等线",
    "SimHei": "黑体",
    "SimSun": "宋体",
    "Microsoft YaHei": "雅黑",
}

TIMELINE_LINES: tuple[tuple[str, str, str], ...] = (
    ("00:12.4", "不再升起的太阳", "太陽が昇らなくなった"),
    ("00:16.8", "留下的光还在", "残った光はまだ消えない"),
    ("00:21.1", "我仍然听见你的声音", "まだ君の声が聞こえる"),
)


class StyleGalleryWindow(ctk.CTkToplevel):
    """Lightweight ASS preset gallery with deterministic manual controls."""

    _instance: "StyleGalleryWindow | None" = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is not None and cls._instance.winfo_exists():
            return cls._instance
        cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        master,
        current_preset: str,
        apply_callback: Callable[[str], object],
        get_settings_cb: Callable[[], dict[str, Any]],
        sync_presets_cb: Callable[[], object],
        refresh_presets_cb: Callable[[str | None], object] | None = None,
    ) -> None:
        if getattr(self, "_initialized", False):
            self._owner_window = master
            self.transient(master)
            apply_child_window_icon(self, master)
            self.apply_callback = apply_callback
            self.get_settings_cb = get_settings_cb
            self.sync_presets_cb = sync_presets_cb
            self.refresh_presets_cb = refresh_presets_cb
            self._refresh_font_catalog()
            fallback = next(iter(ASS_PRESETS), current_preset)
            self._select_preset_silently(
                current_preset if current_preset in ASS_PRESETS else fallback
            )
            self._show_window()
            return

        super().__init__(master)
        self._initialized = True
        self._owner_window = master
        self.withdraw()
        self.title("UtaSyncNext - 字幕样式工作台")
        self.transient(master)
        apply_child_window_icon(self, master)
        self.geometry("1000x860")
        self.minsize(820, 720)
        self.configure(fg_color=BG_WORKSPACE)
        self.protocol("WM_DELETE_WINDOW", self.close_window)

        self.apply_callback = apply_callback
        self.get_settings_cb = get_settings_cb
        self.sync_presets_cb = sync_presets_cb
        self.refresh_presets_cb = refresh_presets_cb
        self.current_preset = current_preset
        self.card_widgets: list[ctk.CTkFrame] = []
        self._preview_generation = 0
        self._preview_image: ctk.CTkImage | None = None
        self._preview_source: Image.Image | None = None
        self._preview_aspect = 16 / 9
        self._preview_stage_width = 0
        self._preset_removed: list[str] = []
        self._preset_renamed: dict[str, str] = {}
        self.font_pair_options = self._available_font_pair_options()

        self._load_presets()
        self._build_ui()
        self._populate_cards()
        self.after(150, self._show_window)

    def _show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus()
        try:
            self.grab_set()
        except Exception:
            pass
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))

    def _available_font_pair_options(self) -> dict[str, tuple[str, str]]:
        """Expose only pairs whose families are known to the local UI."""
        try:
            installed = {str(name) for name in tkfont.families(self)}
        except Exception:
            installed = set()
        user_families = [record.family for record in discover_user_fonts()]
        known_families = installed | set(user_families)
        available = {
            label: pair
            for label, pair in FONT_PAIR_OPTIONS.items()
            if pair[0] in known_families and pair[1] in known_families
        }
        available.update(self._user_font_pair_options(user_families))
        available.update({f"我的：{name}": pair for name, pair in load_user_font_pairs().items()})
        # A headless test harness may not expose Tk families.  Keep the
        # original options in that case; libass will still validate the final
        # font during the real preview/burn step.
        return available or dict(FONT_PAIR_OPTIONS)

    def _available_font_families(self) -> list[str]:
        """Offer a curated catalog plus imported and already-used families."""

        try:
            installed = {str(name) for name in tkfont.families(self)}
        except Exception:
            installed = set()
        imported = [record.family for record in discover_user_fonts()]
        featured = ("Microsoft YaHei", "Meiryo", "SimHei", "Yu Gothic", "DengXian")
        names = [name for name in featured if name in installed]
        names.extend(name for name in imported if name not in names)
        for ch_font, jp_font in self.font_pair_options.values():
            for name in (ch_font, jp_font):
                if name not in names:
                    names.append(name)
        for config in ASS_PRESETS.values():
            for key in ("ch_font", "jp_font"):
                name = str(config.get(key) or "").strip()
                if name and name not in names:
                    names.append(name)
        return names or ["Microsoft YaHei", "Meiryo"]

    @staticmethod
    def _user_font_pair_options(families: list[str]) -> dict[str, tuple[str, str]]:
        """Add a small set of explicit user-font pairs without growing a font matrix."""
        if not families:
            return {}
        curated_pairs = set(FONT_PAIR_OPTIONS.values())
        lower = {name: name.casefold() for name in families}
        cn = [name for name in families if any(token in lower[name] for token in (" cn", " sc", "chinese", "中文"))]
        jp = [name for name in families if any(token in lower[name] for token in (" jp", " japanese", "日", "gothic"))]
        if not cn:
            cn = families[:2]
        if not jp:
            jp = families[:2]
        pairs: dict[str, tuple[str, str]] = {}
        preferred_pairs = (
            ("M PLUS 1p", "Zen Kaku Gothic New", "用户：M PLUS + Zen Kaku"),
            ("Source Han Sans CN", "Source Han Sans JP", "用户：思源黑体 CN + JP"),
        )
        for ch_font, jp_font, label in preferred_pairs:
            if ch_font in families and jp_font in families and (ch_font, jp_font) not in curated_pairs:
                pairs[label] = (ch_font, jp_font)
        for ch_font, jp_font in zip(cn[:2], jp[:2]):
            if (ch_font, jp_font) in curated_pairs:
                continue
            label = f"用户：{ch_font} + {jp_font}"
            pairs.setdefault(label, (ch_font, jp_font))
        return pairs

    def _build_ui(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=28, pady=(24, 8))
        ctk.CTkLabel(
            header,
            text="字幕样式工作台",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_display, size=24, weight="bold"),
            text_color=TEXT_TITLE,
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="选择预设，按需编辑字体、颜色、描边和阴影；不做视频剪辑",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=12),
            text_color=ACCENT_WARNING,
        ).pack(side="left", padx=18, pady=(7, 0))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=28, pady=(4, 18))

        self.sidebar = ctk.CTkFrame(
            body,
            width=260,
            fg_color=UTASYNC_TOKENS.surface_sidebar,
            border_color=UTASYNC_TOKENS.border_cool,
            border_width=1,
            corner_radius=UTASYNC_TOKENS.radius_panel,
        )
        self.sidebar.pack(side="left", fill="y", padx=(0, 14))
        self.sidebar.pack_propagate(False)
        sidebar_header = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        sidebar_header.pack(fill="x", padx=14, pady=(14, 8))
        ctk.CTkLabel(
            sidebar_header,
            text="字幕预设",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=14, weight="bold"),
            text_color=UTASYNC_TOKENS.text_primary,
        ).pack(side="left")
        self.preset_count_label = ctk.CTkLabel(
            sidebar_header,
            text="",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_mono, size=10),
            text_color=UTASYNC_TOKENS.text_muted,
        )
        self.preset_count_label.pack(side="right", pady=(2, 0))
        ctk.CTkLabel(
            self.sidebar,
            text="点击预设即可应用；删除前请确认，改动会写入 user_presets.json。",
            justify="left",
            wraplength=220,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            text_color=UTASYNC_TOKENS.text_hint,
        ).pack(fill="x", padx=14, pady=(0, 8))
        self.restore_presets_btn = ctk.CTkButton(
            self.sidebar,
            text="恢复默认预设",
            height=26,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.surface_control_hover,
            text_color=UTASYNC_TOKENS.text_secondary,
            command=self._restore_default_presets,
        )
        self.restore_presets_btn.pack(fill="x", padx=14, pady=(0, 8))
        self.preset_scroll = ctk.CTkScrollableFrame(
            self.sidebar,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=UTASYNC_TOKENS.surface_control,
            scrollbar_button_hover_color=UTASYNC_TOKENS.surface_control_hover,
        )
        self.preset_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        self.font_panel = ctk.CTkFrame(
            self.sidebar,
            fg_color=UTASYNC_TOKENS.surface_panel,
            border_color=UTASYNC_TOKENS.border_cool,
            border_width=1,
            corner_radius=UTASYNC_TOKENS.radius_control,
        )
        self.font_panel.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(
            self.font_panel,
            text="字体搭配",
            anchor="w",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=12, weight="bold"),
            text_color=UTASYNC_TOKENS.text_primary,
        ).pack(fill="x", padx=10, pady=(9, 2))
        ctk.CTkLabel(
            self.font_panel,
            text="选中日文字体，预览后可命名搭配或新建风格",
            anchor="w",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=9),
            text_color=UTASYNC_TOKENS.text_hint,
        ).pack(fill="x", padx=10, pady=(0, 6))
        self.font_pair_option = ctk.CTkOptionMenu(
            self.font_panel,
            values=list(self.font_pair_options),
            height=30,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            fg_color=UTASYNC_TOKENS.surface_control,
            button_color=UTASYNC_TOKENS.surface_control,
            button_hover_color=UTASYNC_TOKENS.surface_control_hover,
            dropdown_fg_color=UTASYNC_TOKENS.surface_panel,
            dropdown_hover_color=UTASYNC_TOKENS.surface_control_hover,
            command=self._on_font_pair_preview,
        )
        self.font_pair_option.pack(fill="x", padx=10, pady=(0, 8))
        font_row = ctk.CTkFrame(self.font_panel, fg_color="transparent")
        font_row.pack(fill="x", padx=10, pady=(0, 7))
        font_families = self._available_font_families()
        self.ch_font_option = self._font_family_menu(font_row, "中文", font_families)
        self.jp_font_option = self._font_family_menu(font_row, "日文", font_families)
        self.font_pair_hint = ctk.CTkLabel(
            self.font_panel,
            text=self._font_pair_hint(),
            anchor="w",
            justify="left",
            wraplength=220,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=9),
            text_color=UTASYNC_TOKENS.text_hint,
        )
        self.font_pair_hint.pack(fill="x", padx=10, pady=(0, 6))
        pair_actions = ctk.CTkFrame(self.font_panel, fg_color="transparent")
        pair_actions.pack(fill="x", padx=10, pady=(0, 6))
        ctk.CTkButton(
            pair_actions,
            text="保存命名搭配",
            height=28,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.surface_control_hover,
            command=self._save_named_font_pair,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            pair_actions,
            text="刷新字体",
            width=70,
            height=28,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.surface_control_hover,
            command=self._refresh_font_catalog,
        ).pack(side="left")
        self.font_preview_btn = ctk.CTkButton(
            self.font_panel,
            text="预览当前字体",
            height=28,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.surface_control_hover,
            command=self._open_font_preview,
        )
        self.font_preview_btn.pack(fill="x", padx=10, pady=(0, 7))
        pair_manage_actions = ctk.CTkFrame(self.font_panel, fg_color="transparent")
        pair_manage_actions.pack(fill="x", padx=10, pady=(0, 6))
        self.rename_pair_btn = ctk.CTkButton(
            pair_manage_actions,
            text="重命名我的搭配",
            height=26,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=9),
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.surface_control_hover,
            command=self._rename_named_font_pair,
        )
        self.rename_pair_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.delete_pair_btn = ctk.CTkButton(
            pair_manage_actions,
            text="删除我的搭配",
            height=26,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=9),
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.accent_danger_hover,
            command=self._delete_named_font_pair,
        )
        self.delete_pair_btn.pack(side="left", fill="x", expand=True)
        self.apply_font_btn = ctk.CTkButton(
            self.font_panel,
            text="覆盖当前预设的字体",
            height=29,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10, weight="bold"),
            fg_color=UTASYNC_TOKENS.accent_primary,
            hover_color=UTASYNC_TOKENS.accent_primary_hover,
            text_color="#000000",
            command=self._apply_font_overrides,
        )
        self.apply_font_btn.pack(fill="x", padx=10, pady=(0, 10))
        self._update_pair_management_state()

        self.content_scroll = ctk.CTkScrollableFrame(
            body,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=UTASYNC_TOKENS.surface_control,
            scrollbar_button_hover_color=UTASYNC_TOKENS.surface_control_hover,
        )
        self.content_scroll.pack(side="left", fill="both", expand=True)
        self.content = ctk.CTkFrame(self.content_scroll, fg_color="transparent")
        self.content.pack(fill="x", expand=False)
        self._build_preview_panel()
        self._build_manual_editor_panel()
        self._build_workspace_summary()

    def _font_family_menu(self, parent: ctk.CTkFrame, title: str, families: list[str]) -> ctk.CTkOptionMenu:
        group = ctk.CTkFrame(parent, fg_color="transparent")
        group.pack(side="left", fill="x", expand=True, padx=(0, 4 if title == "中文" else 0))
        ctk.CTkLabel(
            group,
            text=f"{title}字体",
            anchor="w",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=9),
            text_color=UTASYNC_TOKENS.text_muted,
        ).pack(fill="x", pady=(0, 2))
        option = ctk.CTkOptionMenu(
            group,
            values=families,
            height=28,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=9),
            fg_color=UTASYNC_TOKENS.surface_control,
            button_color=UTASYNC_TOKENS.surface_control,
            button_hover_color=UTASYNC_TOKENS.surface_control_hover,
            dropdown_fg_color=UTASYNC_TOKENS.surface_panel,
            dropdown_hover_color=UTASYNC_TOKENS.surface_control_hover,
            command=lambda _value: self._preview_selected_fonts(),
        )
        option.pack(fill="x")
        return option

    def _build_preview_panel(self) -> None:
        self.preview_panel = ctk.CTkFrame(
            self.content,
            fg_color=UTASYNC_TOKENS.surface_panel,
            border_color=UTASYNC_TOKENS.border_cool,
            border_width=1,
            corner_radius=UTASYNC_TOKENS.radius_panel,
        )
        self.preview_panel.pack(fill="x", expand=False)
        panel_header = ctk.CTkFrame(self.preview_panel, fg_color="transparent")
        panel_header.pack(fill="x", padx=18, pady=(14, 8))
        ctk.CTkLabel(
            panel_header,
            text="当前预览",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=14, weight="bold"),
            text_color=UTASYNC_TOKENS.text_primary,
        ).pack(side="left")
        self.preview_badge = ctk.CTkLabel(
            panel_header,
            text="",
            width=80,
            height=24,
            corner_radius=6,
            fg_color=UTASYNC_TOKENS.surface_control,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            text_color=UTASYNC_TOKENS.text_secondary,
        )
        self.preview_badge.pack(side="right")

        self.preview_stage = ctk.CTkFrame(
            self.preview_panel,
            fg_color=UTASYNC_TOKENS.surface_preview,
            border_color=UTASYNC_TOKENS.border_subtle,
            border_width=1,
            corner_radius=UTASYNC_TOKENS.radius_card,
            height=130,
        )
        self.preview_stage.pack(fill="x", padx=18, pady=(0, 12))
        self.preview_stage.pack_propagate(False)
        self.preview_ch = ctk.CTkLabel(
            self.preview_stage,
            text="不再升起的太阳",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=29, weight="bold"),
        )
        self.preview_ch.pack(pady=(18, 0))
        self.preview_jp = ctk.CTkLabel(
            self.preview_stage,
            text="太陽が昇らなくなった",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=16),
        )
        self.preview_jp.pack(pady=(8, 0))
        self.preview_frame_label = ctk.CTkLabel(self.preview_stage, text="")
        self.preview_frame_label.pack_forget()
        self.preview_stage.bind("<Configure>", self._on_preview_stage_resize)
        self._preview_aspect = self._settings_aspect()
        self._sync_preview_stage_height()

        meta = ctk.CTkFrame(self.preview_panel, fg_color="transparent")
        meta.pack(fill="x", padx=18, pady=(0, 10))
        self.preview_title = ctk.CTkLabel(
            meta,
            text="",
            anchor="w",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=15, weight="bold"),
            text_color=UTASYNC_TOKENS.text_primary,
        )
        self.preview_title.pack(fill="x")
        self.preview_desc = ctk.CTkLabel(
            meta,
            text="",
            anchor="w",
            justify="left",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=11),
            text_color=UTASYNC_TOKENS.text_muted,
        )
        self.preview_desc.pack(fill="x", pady=(3, 0))
        self.preview_font_meta = ctk.CTkLabel(
            meta,
            text="",
            anchor="w",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_mono, size=10),
            text_color=UTASYNC_TOKENS.text_hint,
        )
        self.preview_font_meta.pack(fill="x", pady=(6, 0))

        actions = ctk.CTkFrame(self.preview_panel, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(
            actions,
            text="重新渲染",
            height=32,
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.surface_control_hover,
            text_color=UTASYNC_TOKENS.text_secondary,
            command=self._rerender_preview,
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            actions,
            text="应用到当前任务",
            height=32,
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.surface_control_hover,
            text_color=UTASYNC_TOKENS.text_secondary,
            command=lambda: self._select_preset(self.current_preset),
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.rename_current_btn = ctk.CTkButton(
            actions,
            text="重命名预设",
            width=100,
            height=32,
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.surface_control_hover,
            text_color=UTASYNC_TOKENS.text_secondary,
            command=self._rename_current_preset,
        )
        self.rename_current_btn.pack(side="right", padx=(0, 8))
        self.delete_current_btn = ctk.CTkButton(
            actions,
            text="删除预设",
            width=100,
            height=32,
            fg_color=UTASYNC_TOKENS.accent_danger,
            hover_color=UTASYNC_TOKENS.accent_danger_hover,
            command=self._delete_current_preset,
        )
        self.delete_current_btn.pack(side="right")

        # 逐句时间轴保留为轻量检查区；复杂候选比较不进入默认流程。
        extras = ctk.CTkFrame(self.preview_panel, fg_color="transparent")
        extras.pack(fill="x", expand=False, padx=18, pady=(0, 14))

        timeline_panel = ctk.CTkFrame(
            extras,
            fg_color=UTASYNC_TOKENS.surface_card,
            border_color=UTASYNC_TOKENS.border_subtle,
            border_width=1,
            corner_radius=UTASYNC_TOKENS.radius_control,
            height=136,
        )
        timeline_panel.pack(fill="both", expand=True)
        timeline_panel.pack_propagate(False)
        timeline_header = ctk.CTkFrame(timeline_panel, fg_color="transparent")
        timeline_header.pack(fill="x", padx=10, pady=(7, 3))
        ctk.CTkLabel(
            timeline_header,
            text="逐句时间轴",
            anchor="w",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=11, weight="bold"),
            text_color=UTASYNC_TOKENS.text_secondary,
        ).pack(side="left")
        self.timeline_status = ctk.CTkLabel(
            timeline_header,
            text="第 1 / 3 句",
            anchor="e",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_mono, size=9),
            text_color=UTASYNC_TOKENS.text_hint,
        )
        self.timeline_status.pack(side="right")
        self.timeline_rows = ctk.CTkFrame(timeline_panel, fg_color="transparent")
        self.timeline_rows.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.timeline_buttons: list[ctk.CTkButton] = []
        for index, (timestamp, chinese, japanese) in enumerate(TIMELINE_LINES):
            line_button = ctk.CTkButton(
                self.timeline_rows,
                text=f"{timestamp}   {chinese}   /   {japanese}",
                anchor="w",
                height=28,
                fg_color=UTASYNC_TOKENS.surface_sidebar,
                hover_color=UTASYNC_TOKENS.surface_control_hover,
                text_color=UTASYNC_TOKENS.text_muted,
                font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
                command=lambda value=index: self._select_timeline_line(value),
            )
            line_button.pack(fill="x", pady=2)
            self.timeline_buttons.append(line_button)
        self._select_timeline_line(0)

        try:
            self.preset_scroll._parent_canvas.configure(bg=UTASYNC_TOKENS.surface_sidebar)
        except Exception:
            pass

    def _build_manual_editor_panel(self) -> None:
        """Compact, deterministic style controls; this is not a video editor."""
        self.editor_panel = ctk.CTkFrame(
            self.content,
            fg_color=UTASYNC_TOKENS.surface_panel,
            border_color=UTASYNC_TOKENS.border_cool,
            border_width=1,
            corner_radius=UTASYNC_TOKENS.radius_panel,
            height=EDITOR_COLLAPSED_HEIGHT,
        )
        self.editor_panel.pack(fill="x", pady=(12, 0))
        self.editor_panel.pack_propagate(False)
        editor_header = ctk.CTkFrame(self.editor_panel, fg_color="transparent")
        editor_header.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(
            editor_header,
            text="样式编辑",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=12, weight="bold"),
            text_color=UTASYNC_TOKENS.text_primary,
        ).pack(side="left")
        ctk.CTkLabel(
            editor_header,
            text="手动控制颜色、字号、描边和阴影；保留原版请另存为新预设",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            text_color=UTASYNC_TOKENS.text_muted,
        ).pack(side="left", padx=12)
        self.editor_toggle_btn = ctk.CTkButton(
            editor_header,
            text="展开编辑",
            width=92,
            height=28,
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.surface_control_hover,
            text_color=UTASYNC_TOKENS.text_secondary,
            command=self._toggle_editor,
        )
        self.editor_toggle_btn.pack(side="right")

        self.editor_body = ctk.CTkFrame(self.editor_panel, fg_color="transparent")
        self._build_palette_picker(self.editor_body)
        self._build_color_row(self.editor_body, "中文颜色", "ch_color")
        self._build_color_row(self.editor_body, "日文颜色", "jp_color")
        self._build_color_row(self.editor_body, "描边颜色", "ch_outline")
        settings_row = ctk.CTkFrame(self.editor_body, fg_color="transparent")
        settings_row.pack(fill="x", padx=16, pady=(4, 8))
        self.ch_size_option = self._editor_option(settings_row, "中文字号", ["48", "55", "60"], 0)
        self.jp_size_option = self._editor_option(settings_row, "日文字号", ["30", "35", "38"], 1)
        self.outline_size_option = self._editor_option(settings_row, "描边粗细", ["1", "2", "3", "4"], 2)
        self.shadow_size_option = self._editor_option(settings_row, "阴影距离", ["0", "1", "2", "3"], 3)
        actions = ctk.CTkFrame(self.editor_body, fg_color="transparent")
        actions.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(
            actions,
            text="预览当前修改",
            height=30,
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.surface_control_hover,
            text_color=UTASYNC_TOKENS.text_secondary,
            command=self._preview_manual_style,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            actions,
            text="另存为新预设",
            height=30,
            fg_color=UTASYNC_TOKENS.accent_primary,
            hover_color=UTASYNC_TOKENS.accent_primary_hover,
            text_color="#000000",
            command=self._save_as_new_preset,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            actions,
            text="应用到当前预设",
            height=30,
            fg_color=UTASYNC_TOKENS.surface_control,
            hover_color=UTASYNC_TOKENS.surface_control_hover,
            text_color=UTASYNC_TOKENS.text_secondary,
            command=self._apply_manual_style,
        ).pack(side="left", fill="x", expand=True)

    def _build_workspace_summary(self) -> None:
        """Use remaining space for a useful status panel instead of blank fill."""
        self.summary_panel = ctk.CTkFrame(
            self.content,
            fg_color=UTASYNC_TOKENS.surface_sidebar,
            border_color=UTASYNC_TOKENS.border_cool,
            border_width=1,
            corner_radius=UTASYNC_TOKENS.radius_panel,
            height=116,
        )
        self.summary_panel.pack(fill="x", pady=(12, 0))
        self.summary_panel.pack_propagate(False)
        summary_header = ctk.CTkFrame(self.summary_panel, fg_color="transparent")
        summary_header.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(
            summary_header,
            text="当前样式摘要",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=12, weight="bold"),
            text_color=UTASYNC_TOKENS.text_primary,
        ).pack(side="left")
        self.summary_state = ctk.CTkLabel(
            summary_header,
            text="已同步到当前任务",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            text_color=UTASYNC_TOKENS.accent_primary,
        )
        self.summary_state.pack(side="right")
        self.summary_text = ctk.CTkLabel(
            self.summary_panel,
            text="",
            anchor="w",
            justify="left",
            wraplength=660,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            text_color=UTASYNC_TOKENS.text_muted,
        )
        self.summary_text.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(
            self.summary_panel,
            text="提示：预览使用同一套 ASS 生成逻辑；确认后再回到主界面执行压制。",
            anchor="w",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=9),
            text_color=UTASYNC_TOKENS.text_hint,
        ).pack(fill="x", padx=16)

    def _build_palette_picker(self, parent: ctk.CTkFrame) -> None:
        panel = ctk.CTkFrame(parent, fg_color="transparent")
        panel.pack(fill="x", padx=16, pady=(4, 2))
        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="中日配色",
            anchor="w",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10, weight="bold"),
            text_color=UTASYNC_TOKENS.text_secondary,
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="只改变颜色，先预览再保存",
            anchor="e",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=9),
            text_color=UTASYNC_TOKENS.text_hint,
        ).pack(side="right")

        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.pack(fill="x", pady=(5, 0))
        self.palette_cards: dict[str, ctk.CTkFrame] = {}
        for name, palette in COLOR_PALETTES.items():
            card = ctk.CTkFrame(
                row,
                fg_color=UTASYNC_TOKENS.surface_control,
                border_color=UTASYNC_TOKENS.border_subtle,
                border_width=1,
                corner_radius=8,
                height=48,
            )
            card.pack(side="left", fill="x", expand=True, padx=(0, 5))
            card.pack_propagate(False)
            title = ctk.CTkLabel(
                card,
                text=name,
                anchor="w",
                font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=9, weight="bold"),
                text_color=UTASYNC_TOKENS.text_secondary,
            )
            title.pack(fill="x", padx=8, pady=(6, 0))
            swatches = ctk.CTkFrame(card, fg_color="transparent")
            swatches.pack(fill="x", padx=8, pady=(3, 0))
            for key in ("ch_color", "jp_color", "outline"):
                swatch = ctk.CTkFrame(
                    swatches,
                    width=22,
                    height=7,
                    fg_color=palette[key],
                    corner_radius=3,
                )
                swatch.pack(side="left", padx=(0, 3))
                swatch.pack_propagate(False)
            self.palette_cards[name] = card
            self._bind_palette_card(card, name)
        self._refresh_palette_cards()

    def _bind_palette_card(self, widget: Any, name: str) -> None:
        widget.bind("<Button-1>", lambda _event, value=name: self._select_color_palette(value))
        for child in widget.winfo_children():
            self._bind_palette_card(child, name)

    def _refresh_palette_cards(self) -> None:
        selected = getattr(self, "selected_palette_name", "")
        for name, card in getattr(self, "palette_cards", {}).items():
            active = name == selected
            card.configure(
                border_color=UTASYNC_TOKENS.accent_primary if active else UTASYNC_TOKENS.border_subtle,
                fg_color=UTASYNC_TOKENS.surface_control_hover if active else UTASYNC_TOKENS.surface_control,
            )

    def _select_color_palette(self, name: str) -> None:
        palette = COLOR_PALETTES.get(name)
        if not palette:
            return
        self.selected_palette_name = name
        for key, value in (
            ("ch_color", palette["ch_color"]),
            ("jp_color", palette["jp_color"]),
            ("ch_outline", palette["outline"]),
        ):
            entry = getattr(self, f"{key}_entry")
            entry.delete(0, "end")
            entry.insert(0, value)
            self._refresh_color_swatch(entry)
        self._refresh_palette_cards()
        config = dict(ASS_PRESETS.get(self.current_preset, {}))
        config.update(self._read_manual_style())
        self._render_preview(config)
        self._request_ass_preview(config)
        self.preview_badge.configure(text="未保存预览")
        self.summary_state.configure(text="仅预览，尚未保存", text_color=UTASYNC_TOKENS.accent_warning)

    def _build_color_row(self, parent: ctk.CTkFrame, label: str, key: str) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(4, 0))
        ctk.CTkLabel(row, text=label, width=70, anchor="w", font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10), text_color=UTASYNC_TOKENS.text_secondary).pack(side="left")
        entry = ctk.CTkEntry(row, width=110, height=28, font=ctk.CTkFont(family=UTASYNC_TOKENS.font_mono, size=10), fg_color=UTASYNC_TOKENS.surface_input, border_color=UTASYNC_TOKENS.border_subtle)
        entry.pack(side="left", padx=(0, 6))
        swatch = ctk.CTkButton(row, text="", width=28, height=28, corner_radius=5, command=lambda item=entry: self._pick_color(item))
        swatch.pack(side="left")
        setattr(self, f"{key}_entry", entry)
        setattr(self, f"{key}_swatch", swatch)

    def _editor_option(self, parent: ctk.CTkFrame, label: str, values: list[str], column: int) -> ctk.CTkOptionMenu:
        group = ctk.CTkFrame(parent, fg_color="transparent")
        group.pack(side="left", fill="x", expand=True, padx=(0, 6 if column < 3 else 0))
        ctk.CTkLabel(group, text=label, anchor="w", font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=9), text_color=UTASYNC_TOKENS.text_muted).pack(fill="x")
        option = ctk.CTkOptionMenu(group, values=values, height=28, fg_color=UTASYNC_TOKENS.surface_control, button_color=UTASYNC_TOKENS.surface_control, button_hover_color=UTASYNC_TOKENS.surface_control_hover)
        option.pack(fill="x")
        return option

    def _populate_cards(self) -> None:
        for widget in self.card_widgets:
            widget.destroy()
        self.card_widgets.clear()
        self.preset_count_label.configure(text=f"{len(ASS_PRESETS)} 套")
        for name, config in ASS_PRESETS.items():
            self.card_widgets.append(self._create_preset_card(self.preset_scroll, name, config))
        self._select_preset_silently(self.current_preset)

    @staticmethod
    def _short_font_name(family: str) -> str:
        name = str(family or "").strip()
        return SHORT_FONT_NAMES.get(name, name)

    def _preset_card_subtitle(self, config: dict[str, Any]) -> str:
        """Show the font pair: nine presets need to be told apart at a glance."""

        ch_font = self._short_font_name(str(config.get("ch_font") or ""))
        jp_font = self._short_font_name(str(config.get("jp_font") or ""))
        pair = " + ".join(part for part in (ch_font, jp_font) if part)
        # The tone already sits in the preset name and in the line under the
        # preview, so the narrow card spends its single line on the pair.
        return pair or str(config.get("description") or "").strip() or "自定义排版预设"

    def _card_subtitle_font(self) -> tkfont.Font:
        font = getattr(self, "_subtitle_font", None)
        if font is None:
            font = tkfont.Font(family=UTASYNC_TOKENS.font_ui, size=9)
            self._subtitle_font = font
        return font

    def _fit_text_to_width(self, text: str, available: int) -> str:
        """Shorten text to the pixels it really gets, instead of guessing.

        The sidebar can be widened and the family names differ in length, so a
        fixed character cap either clips a long pair or wastes space on a short
        one. An ellipsis keeps it obvious that something was shortened.
        """

        font = self._card_subtitle_font()
        limit = max(24, available - 4)
        if font.measure(text) <= limit:
            return text
        trimmed = text
        while trimmed and font.measure(trimmed + "…") > limit:
            trimmed = trimmed[:-1]
        return f"{trimmed}…" if trimmed else "…"

    def _bind_card_subtitle(self, label: ctk.CTkLabel, subtitle: str) -> None:
        """Trim the card subtitle to the space the card really gives it.

        Measuring the label itself would be circular: shortening the text shrinks
        the label request, which then stops reporting a usable width. The frame
        that holds it is sized by the card, so that is what gets measured.
        """

        holder = label.master

        def fit(_event: object | None = None) -> None:
            available = holder.winfo_width()
            if available <= 40:
                return
            fitted = self._fit_text_to_width(subtitle, available)
            if str(label.cget("text")) != fitted:
                label.configure(text=fitted)

        holder.bind("<Configure>", fit, add="+")
        label.bind("<Configure>", fit, add="+")

    def _create_preset_card(self, parent, name: str, config: dict[str, Any]) -> ctk.CTkFrame:
        is_active = name == self.current_preset
        bg_color = "#112016" if is_active else UTASYNC_TOKENS.surface_sidebar
        border_color = ACCENT_GREEN if is_active else UTASYNC_TOKENS.border_subtle
        card = ctk.CTkFrame(
            parent,
            fg_color=bg_color,
            border_color=border_color,
            border_width=1,
            corner_radius=UTASYNC_TOKENS.radius_control,
            cursor="hand2",
            height=58,
        )
        card.preset_name = name
        card.pack_propagate(False)
        card.pack(fill="x", padx=2, pady=4)

        def select(_event: object | None = None, preset_name: str = name) -> None:
            self._select_preset(preset_name)

        card.bind("<Button-1>", select)
        chip = ctk.CTkFrame(card, width=6, height=32, fg_color=_ensure_hex(config.get("ch_color"), "#FFFFFF"), corner_radius=3)
        chip.pack(side="left", padx=(10, 8), pady=12)
        chip.bind("<Button-1>", select)
        text_frame = ctk.CTkFrame(card, fg_color="transparent")
        text_frame.pack(side="left", fill="both", expand=True, pady=8)
        text_frame.bind("<Button-1>", select)
        title_label = ctk.CTkLabel(
            text_frame,
            text=name,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=11, weight="bold"),
            text_color=UTASYNC_TOKENS.text_primary,
            justify="left",
            anchor="w",
        )
        title_label.pack(anchor="w")
        # CTkLabel owns its own canvas, so clicks on the visible title do not
        # bubble to the parent card. Bind it explicitly to keep the whole card
        # equally responsive, including the text itself.
        title_label.bind("<Button-1>", select)
        subtitle = self._preset_card_subtitle(config)
        subtitle_label = ctk.CTkLabel(
            text_frame,
            text=subtitle,
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=9),
            text_color=UTASYNC_TOKENS.text_muted,
            justify="left",
            anchor="w",
        )
        subtitle_label.pack(anchor="w", pady=(2, 0))
        self._bind_card_subtitle(subtitle_label, subtitle)
        subtitle_label.bind("<Button-1>", select)
        card.bind("<Button-1>", select)
        return card

    def _render_preview(self, config: dict[str, Any], *, ch_font: str | None = None, jp_font: str | None = None) -> None:
        self.preview_ch.configure(
            text_color=_ensure_hex(config.get("ch_color"), "#FFFFFF"),
            font=ctk.CTkFont(
                family=ch_font or str(config.get("ch_font", UTASYNC_TOKENS.font_ui)),
                size=29,
                weight="bold",
            ),
        )
        self.preview_jp.configure(
            text_color=_ensure_hex(config.get("jp_color"), "#CCCCCC"),
            font=ctk.CTkFont(
                family=jp_font or str(config.get("jp_font", UTASYNC_TOKENS.font_ui)),
                size=16,
            ),
        )

    def _show_text_preview(self) -> None:
        self._preview_source = None
        self.preview_frame_label.pack_forget()
        self.preview_ch.pack(pady=(18, 0))
        self.preview_jp.pack(pady=(8, 0))

    def _settings_aspect(self) -> float:
        """Frame aspect the export will use, so the card shows the same shape."""

        try:
            settings = dict(self.get_settings_cb() or {})
        except Exception:
            settings = {}
        policy = resolve_subtitle_layout(None, settings)
        if policy.resolution_x and policy.resolution_y:
            return policy.resolution_x / policy.resolution_y
        return 16 / 9

    def _preview_layout_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Apply the same safe-area policy the export applies."""

        try:
            settings = dict(self.get_settings_cb() or {})
        except Exception:
            settings = {}
        return apply_layout_policy(config, resolve_subtitle_layout(None, settings), settings)

    def _request_ass_preview(self, config: dict[str, Any]) -> None:
        if not hasattr(self, "preview_frame_label"):
            return
        self._preview_generation += 1
        generation = self._preview_generation
        japanese = TIMELINE_LINES[getattr(self, "active_timeline_index", 0)][2]
        chinese = TIMELINE_LINES[getattr(self, "active_timeline_index", 0)][1]
        self.preview_badge.configure(text="ASS 渲染中…")
        render_config = self._preview_layout_config(config)
        result: dict[str, Any] = {}

        def worker() -> None:
            try:
                result["image"] = render_ass_preview_frame(render_config, japanese, chinese)
            except Exception as exc:
                result["error"] = exc

        # The worker never touches Tk; the UI thread collects the frame. This
        # keeps the card working when no event loop is running (smoke tests).
        threading.Thread(target=worker, daemon=True).start()
        self.after(60, lambda: self._poll_ass_preview(generation, result))

    def _poll_ass_preview(self, generation: int, result: dict[str, Any]) -> None:
        if generation != self._preview_generation or not self.winfo_exists():
            return
        error = result.get("error")
        if error is not None:
            self._ass_preview_failed(generation, error)
            return
        image = result.get("image")
        if image is None:
            self.after(60, lambda: self._poll_ass_preview(generation, result))
            return
        self._display_ass_preview(generation, image)

    def _display_ass_preview(self, generation: int, image: Image.Image) -> None:
        if generation != self._preview_generation or not self.winfo_exists():
            return
        self._preview_source = image
        if image.height:
            self._preview_aspect = image.width / image.height
        self._sync_preview_stage_height()
        self._refit_preview_frame()
        self.preview_badge.configure(text="真实 ASS 预览")

    def _refit_preview_frame(self) -> None:
        """Letterbox the frame into the card instead of cropping it.

        The old centre crop kept only a thin band of the frame, which pushed the
        subtitle line out of the card: the render had succeeded but the card
        looked empty.
        """

        source = self._preview_source
        if source is None or not hasattr(self, "preview_frame_label"):
            return
        width = max(220, self.preview_stage.winfo_width() - 16)
        height = max(90, self.preview_stage.winfo_height() - 10)
        frame = ImageOps.contain(source, (width, height), method=Image.Resampling.LANCZOS)
        self._preview_image = ctk.CTkImage(light_image=frame, dark_image=frame, size=frame.size)
        self.preview_ch.pack_forget()
        self.preview_jp.pack_forget()
        self.preview_frame_label.configure(image=self._preview_image, text="")
        self.preview_frame_label.pack(fill="both", expand=True)

    def _sync_preview_stage_height(self, width: int | None = None) -> None:
        """Keep the card at the export aspect so the frame is not squashed."""

        ratio = self._preview_aspect if self._preview_aspect > 0 else 16 / 9
        current_width = int(width or self.preview_stage.winfo_width())
        if current_width <= 1:
            return
        target = int(
            max(PREVIEW_STAGE_MIN_HEIGHT, min(current_width / ratio, PREVIEW_STAGE_MAX_HEIGHT))
        )
        if abs(target - int(self.preview_stage.cget("height"))) > 2:
            self.preview_stage.configure(height=target)

    def _on_preview_stage_resize(self, event: Any) -> None:
        if event.width == self._preview_stage_width:
            return
        self._preview_stage_width = event.width
        self._sync_preview_stage_height(event.width)
        self._refit_preview_frame()

    def _ass_preview_failed(self, generation: int, exc: Exception) -> None:
        if generation != self._preview_generation or not self.winfo_exists():
            return
        self._show_text_preview()
        self.preview_badge.configure(text="近似预览")
        self.preview_desc.configure(text=f"ASS 预览暂不可用：{exc}")

    def _on_font_pair_preview(self, label: str) -> None:
        pair = self.font_pair_options.get(label)
        self._update_pair_management_state()
        config = ASS_PRESETS.get(self.current_preset, {})
        if pair and hasattr(self, "preview_ch"):
            self._set_font_family_menus(pair)
            self._render_preview(config, ch_font=pair[0], jp_font=pair[1])
            preview_config = dict(config)
            preview_config["ch_font"], preview_config["jp_font"] = pair
            self._request_ass_preview(preview_config)
            self.preview_font_meta.configure(
                text=f"中文 {pair[0]}  ·  日文 {pair[1]}  ·  描边 {config.get('outline_size', 3)}px  ·  未保存预览"
            )

    def _set_font_family_menus(self, pair: tuple[str, str]) -> None:
        for option, family in zip((getattr(self, "ch_font_option", None), getattr(self, "jp_font_option", None)), pair):
            if option is None:
                continue
            values = list(option.cget("values") or [])
            if family not in values:
                values.append(family)
                option.configure(values=values)
            option.set(family)

    def _preview_selected_fonts(self) -> None:
        ch_font = self.ch_font_option.get().strip()
        jp_font = self.jp_font_option.get().strip()
        if not ch_font or not jp_font:
            return
        self._update_pair_management_state()
        config = ASS_PRESETS.get(self.current_preset, {})
        pair = (ch_font, jp_font)
        self._render_preview(config, ch_font=ch_font, jp_font=jp_font)
        preview_config = dict(config)
        preview_config["ch_font"], preview_config["jp_font"] = pair
        self._request_ass_preview(preview_config)
        self.preview_font_meta.configure(
            text=f"中文 {ch_font}  ·  日文 {jp_font}  ·  描边 {config.get('outline_size', 3)}px  ·  未保存预览"
        )
        self.preview_badge.configure(text="未保存预览")

    def _open_font_preview(self) -> None:
        """Show the selected pair through the shared font preview window."""

        pair = self._current_font_pair()
        if not pair:
            return
        config = dict(ASS_PRESETS.get(self.current_preset, {}))
        try:
            config.update(self._read_manual_style())
        except (AttributeError, ValueError):
            pass
        timestamp, chinese, japanese = TIMELINE_LINES[getattr(self, "active_timeline_index", 0)]
        FontPreviewWindow.show(
            self,
            config=config,
            ch_font=pair[0],
            jp_font=pair[1],
            preset_name=self.current_preset,
            chinese=chinese,
            japanese=japanese,
            sample_label=f"样例 {timestamp}",
        )

    def _current_font_pair(self) -> tuple[str, str] | None:
        if hasattr(self, "ch_font_option") and hasattr(self, "jp_font_option"):
            ch_font = self.ch_font_option.get().strip()
            jp_font = self.jp_font_option.get().strip()
            if ch_font and jp_font:
                return ch_font, jp_font
        return self.font_pair_options.get(self.font_pair_option.get())

    def _selected_named_pair_name(self) -> str | None:
        label = self.font_pair_option.get().strip() if hasattr(self, "font_pair_option") else ""
        prefix = "我的："
        if label.startswith(prefix):
            name = label[len(prefix) :].strip()
            return name or None
        return None

    def _update_pair_management_state(self) -> None:
        """Only named user pairs can be renamed or deleted."""

        enabled = self._selected_named_pair_name() is not None
        state = "normal" if enabled else "disabled"
        for button in (getattr(self, "rename_pair_btn", None), getattr(self, "delete_pair_btn", None)):
            if button is not None:
                button.configure(state=state)

    def _refresh_font_catalog(self) -> None:
        """Refresh imported fonts and named pairs without changing any preset."""

        self.user_font_pairs = load_user_font_pairs()
        if not hasattr(self, "font_pair_options"):
            return
        current = self._current_font_pair()
        self.font_pair_options = self._available_font_pair_options()
        if hasattr(self, "font_pair_option"):
            self.font_pair_option.configure(values=list(self.font_pair_options))
        if hasattr(self, "ch_font_option"):
            families = self._available_font_families()
            self.ch_font_option.configure(values=families)
            self.jp_font_option.configure(values=families)
        if current:
            self._set_pair_option_for_pair(current)
        elif self.font_pair_options and hasattr(self, "font_pair_option"):
            first_label = next(iter(self.font_pair_options))
            self.font_pair_option.set(first_label)
            self._set_font_family_menus(self.font_pair_options[first_label])
        if hasattr(self, "font_pair_hint"):
            self.font_pair_hint.configure(text=self._font_pair_hint())
        self._update_pair_management_state()

    def _set_pair_option_for_pair(self, pair: tuple[str, str]) -> None:
        if not hasattr(self, "font_pair_option"):
            return
        label = next((name for name, value in self.font_pair_options.items() if value == pair), "")
        if not label:
            label = f"当前：{pair[0]} + {pair[1]}"
            self.font_pair_options[label] = pair
            self.font_pair_option.configure(values=list(self.font_pair_options))
        self.font_pair_option.set(label)
        self._set_font_family_menus(pair)

    def _save_named_font_pair(self) -> None:
        pair = self._current_font_pair()
        if not pair:
            return
        name = simpledialog.askstring(
            "保存字体搭配",
            "给这组中文 + 日文字体起个名字（例如：majiko）：",
            parent=self,
        )
        name = (name or "").strip()
        if not name:
            return
        if len(name) > 24:
            messagebox.showwarning("名称太长", "搭配名称请控制在 24 个字符以内。", parent=self)
            return
        pairs = load_user_font_pairs()
        if name in pairs and not messagebox.askyesno("覆盖搭配", f"“{name}”已经存在，要覆盖它吗？", parent=self):
            return
        pairs[name] = pair
        try:
            save_user_font_pairs(pairs)
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法保存字体搭配：{exc}", parent=self)
            return
        self._refresh_font_catalog()
        label = f"我的：{name}"
        if label in self.font_pair_options:
            self.font_pair_option.set(label)
        self.font_pair_hint.configure(text=f"已保存“{name}”：{pair[0]} + {pair[1]}。原有预设未改变。")
        self.preview_badge.configure(text="字体搭配已保存")
        self._update_pair_management_state()

    def _rename_named_font_pair(self) -> None:
        old_name = self._selected_named_pair_name()
        if not old_name:
            return
        pairs = load_user_font_pairs()
        pair = pairs.get(old_name)
        if pair is None:
            self._refresh_font_catalog()
            return
        new_name = simpledialog.askstring(
            "重命名字体搭配",
            f"给“{old_name}”改名（最多 24 个字符）：",
            initialvalue=old_name,
            parent=self,
        )
        new_name = (new_name or "").strip()
        if not new_name or new_name == old_name:
            return
        if len(new_name) > 24:
            messagebox.showwarning("名称太长", "搭配名称请控制在 24 个字符以内。", parent=self)
            return
        if new_name in pairs:
            messagebox.showwarning("名称已存在", "请换一个未使用的搭配名称。", parent=self)
            return
        pairs.pop(old_name)
        pairs[new_name] = pair
        try:
            save_user_font_pairs(pairs)
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法保存字体搭配：{exc}", parent=self)
            return
        self._refresh_font_catalog()
        self.font_pair_option.set(f"我的：{new_name}")
        self._set_font_family_menus(pair)
        self.font_pair_hint.configure(text=f"已将“{old_name}”重命名为“{new_name}”。")
        self._update_pair_management_state()

    def _delete_named_font_pair(self) -> None:
        name = self._selected_named_pair_name()
        if not name:
            return
        pairs = load_user_font_pairs()
        pair = pairs.get(name)
        if pair is None:
            self._refresh_font_catalog()
            return
        users = [
            preset_name
            for preset_name, config in ASS_PRESETS.items()
            if (str(config.get("ch_font") or ""), str(config.get("jp_font") or "")) == pair
        ]
        usage_hint = ""
        if users:
            usage_hint = (
                f"\n\n已有 {len(users)} 个预设正在使用这组字体：{'、'.join(users[:3])}"
                + (" 等" if len(users) > 3 else "")
                + "。删除只会移除快捷搭配，不会改动这些预设。"
            )
        if not messagebox.askyesno(
            "删除字体搭配",
            f"确定删除“{name}”吗？{usage_hint}\n\n字体文件本身不会删除。",
            parent=self,
        ):
            return
        pairs.pop(name, None)
        try:
            save_user_font_pairs(pairs)
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法保存字体搭配：{exc}", parent=self)
            return
        self._refresh_font_catalog()
        self.font_pair_hint.configure(text=f"已删除“{name}”。字体文件和已有预设未改变。")
        self._update_pair_management_state()

    def _apply_font_overrides(self) -> None:
        pair = self._current_font_pair()
        if not pair or self.current_preset not in ASS_PRESETS:
            return
        ASS_PRESETS[self.current_preset]["ch_font"] = pair[0]
        ASS_PRESETS[self.current_preset]["jp_font"] = pair[1]
        self._save_presets()
        self._notify_presets_changed()
        self._select_preset(self.current_preset)

    def _toggle_editor(self) -> None:
        """Show or hide the manual controls in a single step.

        The old version animated the panel height in 16 ms steps, which made the
        scroll area jitter and clipped the controls for a few frames.
        """
        if self.editor_body.winfo_manager():
            self.editor_body.pack_forget()
            self.editor_panel.configure(height=EDITOR_COLLAPSED_HEIGHT)
            self.editor_toggle_btn.configure(text="展开编辑")
            return
        self.editor_body.pack(fill="x", expand=False)
        self._load_editor_values()
        self.editor_panel.configure(height=self._editor_expanded_height())
        self.editor_toggle_btn.configure(text="收起编辑")
        self.after_idle(self._reveal_editor)

    def _editor_expanded_height(self) -> int:
        """Size the panel from its real content instead of a magic number."""

        self.update_idletasks()
        body = int(self.editor_body.winfo_reqheight())
        return max(EDITOR_COLLAPSED_HEIGHT + 24, body + EDITOR_HEADER_HEIGHT)

    def _reveal_editor(self) -> None:
        try:
            self.content_scroll._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _font_pair_hint(self) -> str:
        user_count = len(discover_user_fonts())
        pair_count = len(load_user_font_pairs())
        return f"可用搭配 {len(self.font_pair_options)} 组 · 我的搭配 {pair_count} 组 · 用户字体 {user_count} 个"

    def _pick_color(self, entry: ctk.CTkEntry) -> None:
        current = entry.get().strip() or "#FFFFFF"
        picked = colorchooser.askcolor(color=current, parent=self)
        if picked and picked[1]:
            entry.delete(0, "end")
            entry.insert(0, picked[1].upper())
            self._refresh_color_swatch(entry)

    def _refresh_color_swatch(self, entry: ctk.CTkEntry) -> None:
        value = _safe_hex(entry.get(), "#FFFFFF")
        key = next((item for item in ("ch_color", "jp_color", "ch_outline") if getattr(self, f"{item}_entry", None) is entry), None)
        if key:
            getattr(self, f"{key}_swatch").configure(fg_color=value)

    def _load_editor_values(self) -> None:
        config = ASS_PRESETS.get(self.current_preset, {})
        for key in ("ch_color", "jp_color", "ch_outline"):
            entry = getattr(self, f"{key}_entry")
            value = _ensure_hex(config.get(key), "#FFFFFF")
            entry.delete(0, "end")
            entry.insert(0, value)
            self._refresh_color_swatch(entry)
        self.selected_palette_name = next(
            (
                name
                for name, palette in COLOR_PALETTES.items()
                if _safe_hex(config.get("ch_color"), "#FFFFFF") == palette["ch_color"]
                and _safe_hex(config.get("jp_color"), "#CCCCCC") == palette["jp_color"]
                and _safe_hex(config.get("ch_outline"), "#18181B") == palette["outline"]
            ),
            "",
        )
        self._refresh_palette_cards()
        self.ch_size_option.set(str(config.get("ch_size", 55)))
        self.jp_size_option.set(str(config.get("jp_size", 35)))
        self.outline_size_option.set(str(config.get("outline_size", 3)))
        self.shadow_size_option.set(str(config.get("shadow_size", 1)))

    def _read_manual_style(self) -> dict[str, Any]:
        return {
            "ch_color": _safe_hex(self.ch_color_entry.get(), "#FFFFFF"),
            "jp_color": _safe_hex(self.jp_color_entry.get(), "#CCCCCC"),
            "ch_outline": _safe_hex(self.ch_outline_entry.get(), "#18181B"),
            "jp_outline": _safe_hex(self.ch_outline_entry.get(), "#18181B"),
            "ch_size": int(self.ch_size_option.get()),
            "jp_size": int(self.jp_size_option.get()),
            "outline_size": int(self.outline_size_option.get()),
            "shadow_size": int(self.shadow_size_option.get()),
        }

    def _apply_manual_style(self) -> None:
        if self.current_preset not in ASS_PRESETS:
            return
        ASS_PRESETS[self.current_preset].update(self._read_manual_style())
        self._save_presets()
        self._notify_presets_changed()
        self._select_preset(self.current_preset)
        self.preview_badge.configure(text="已保存")
        self.summary_state.configure(text="已保存并同步", text_color=UTASYNC_TOKENS.accent_primary)

    def _preview_manual_style(self) -> None:
        config = dict(ASS_PRESETS.get(self.current_preset, {}))
        config.update(self._read_manual_style())
        self._render_preview(config)
        self._request_ass_preview(config)
        self.preview_badge.configure(text="未保存预览")
        self.summary_state.configure(text="仅预览，尚未保存", text_color=UTASYNC_TOKENS.accent_warning)

    def _save_as_new_preset(self) -> None:
        name = simpledialog.askstring("保存预设", "给这套字幕样式起个名字：", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in ASS_PRESETS:
            messagebox.showwarning("名称已存在", "请换一个预设名称。", parent=self)
            return
        base = dict(ASS_PRESETS.get(self.current_preset, {}))
        pair = self._current_font_pair()
        if pair:
            base["ch_font"], base["jp_font"] = pair
        base.update(self._read_manual_style())
        base["description"] = "手动编辑的字幕样式"
        ASS_PRESETS[name] = base
        self._save_presets()
        self._notify_presets_changed()
        self._populate_cards()
        self._select_preset(name)
        self.preview_badge.configure(text="新预设已保存")

    def _select_timeline_line(self, index: int) -> None:
        if not 0 <= index < len(TIMELINE_LINES):
            return
        self.active_timeline_index = index
        timestamp, chinese, japanese = TIMELINE_LINES[index]
        if hasattr(self, "preview_ch"):
            self.preview_ch.configure(text=chinese)
            self.preview_jp.configure(text=japanese)
            self._request_ass_preview(ASS_PRESETS.get(self.current_preset, {}))
        if hasattr(self, "timeline_status"):
            self.timeline_status.configure(text=f"第 {index + 1} / {len(TIMELINE_LINES)} 句 · {timestamp}")
        for row_index, button in enumerate(getattr(self, "timeline_buttons", [])):
            button.configure(
                fg_color=UTASYNC_TOKENS.surface_control if row_index == index else UTASYNC_TOKENS.surface_sidebar,
                text_color=UTASYNC_TOKENS.text_secondary if row_index == index else UTASYNC_TOKENS.text_muted,
            )

    def _rerender_preview(self) -> None:
        self._request_ass_preview(dict(ASS_PRESETS.get(self.current_preset, {})))

    def _select_preset_silently(self, name: str) -> None:
        self.current_preset = name
        config = ASS_PRESETS.get(name, {})
        for card in self.card_widgets:
            active = getattr(card, "preset_name", "") == name
            bg = "#112016" if active else UTASYNC_TOKENS.surface_sidebar
            card.configure(border_color=ACCENT_GREEN if active else UTASYNC_TOKENS.border_subtle, fg_color=bg)
        if hasattr(self, "preview_ch"):
            self._render_preview(config)
            if hasattr(self, "font_pair_option"):
                configured_pair = (str(config.get("ch_font") or ""), str(config.get("jp_font") or ""))
                current_pair = next((label for label, pair in self.font_pair_options.items() if pair == configured_pair), "")
                if not current_pair and all(configured_pair):
                    current_pair = f"当前：{configured_pair[0]} + {configured_pair[1]}"
                    self.font_pair_options[current_pair] = configured_pair
                    self.font_pair_option.configure(values=list(self.font_pair_options))
                current_pair = current_pair or list(self.font_pair_options)[0]
                self.font_pair_option.set(current_pair)
                if all(configured_pair):
                    self._set_font_family_menus(configured_pair)
                if hasattr(self, "font_pair_hint"):
                    self.font_pair_hint.configure(text=self._font_pair_hint())
            self.preview_badge.configure(text="已选中")
            self.preview_title.configure(text=name)
            self.preview_desc.configure(text=str(config.get("description", "自定义排版预设")))
            self.preview_font_meta.configure(
                text=f"中文 {config.get('ch_font', 'Microsoft YaHei')}  ·  日文 {config.get('jp_font', 'Meiryo')}  ·  描边 {config.get('outline_size', 3)}px"
            )
            self._request_ass_preview(config)
            if hasattr(self, "summary_text"):
                self.summary_text.configure(
                    text=(
                        f"{name}\n"
                        f"中文 {config.get('ch_font', 'Microsoft YaHei')} {config.get('ch_size', 55)}px  ·  "
                        f"日文 {config.get('jp_font', 'Meiryo')} {config.get('jp_size', 35)}px  ·  "
                        f"描边 {config.get('outline_size', 3)}px  ·  阴影 {config.get('shadow_size', 1)}px"
                    )
                )
            if hasattr(self, "editor_body") and self.editor_body.winfo_manager():
                self._load_editor_values()

    def _delete_current_preset(self) -> None:
        card = next((item for item in self.card_widgets if getattr(item, "preset_name", "") == self.current_preset), None)
        if card is not None:
            self._delete_preset(self.current_preset, card)

    def _select_preset(self, name: str) -> None:
        self._select_preset_silently(name)
        self.apply_callback(name)

    def _delete_preset(self, preset_name: str, card: ctk.CTkFrame) -> None:
        if len(ASS_PRESETS) <= 1:
            messagebox.showwarning("警告", "必须至少保留一个特效预设。", parent=self)
            return
        if not messagebox.askyesno("删除确认", f"确定要永久删除【{preset_name}】吗？", parent=self):
            return
        ASS_PRESETS.pop(preset_name, None)
        # 内置预设要留一条"已删除"记录，否则下次启动会被默认值合并回来。
        if preset_name in BUILTIN_PRESETS and preset_name not in self._preset_removed:
            self._preset_removed.append(preset_name)
        self._reset_rename_for(preset_name)
        self._save_presets()
        if card in self.card_widgets:
            self.card_widgets.remove(card)
        card.destroy()
        self.preset_count_label.configure(text=f"{len(ASS_PRESETS)} 套")
        if self.current_preset == preset_name:
            self._select_preset(next(iter(ASS_PRESETS.keys())))
        self._notify_presets_changed()

    def _rename_current_preset(self) -> None:
        """Rename the selected preset; built-in renames must survive a restart."""

        old_name = self.current_preset
        if old_name not in ASS_PRESETS:
            return
        new_name = simpledialog.askstring(
            "重命名预设", "新的预设名称：", initialvalue=old_name, parent=self
        )
        if not new_name:
            return
        new_name = str(new_name).strip()
        if not new_name or new_name == old_name:
            return
        if new_name in ASS_PRESETS:
            messagebox.showwarning("名称已存在", "请换一个预设名称。", parent=self)
            return
        ASS_PRESETS[new_name] = ASS_PRESETS.pop(old_name)
        self._reset_rename_for(old_name)
        if old_name in BUILTIN_PRESETS:
            self._preset_renamed[old_name] = new_name
        self.current_preset = new_name
        self._save_presets()
        self._populate_cards()
        self._select_preset(new_name)
        self.preview_badge.configure(text="已重命名")

    def _restore_default_presets(self) -> None:
        """Bring back deleted or renamed built-ins, keeping user-authored presets."""

        if not messagebox.askyesno(
            "恢复默认预设",
            "将恢复被删除或改名的内置预设（连同自定义颜色一起还原），并保留你自己另存的预设。继续吗？",
            parent=self,
        ):
            return
        restored = restore_builtin_presets(
            BUILTIN_PRESETS,
            ASS_PRESETS,
            {"removed": self._preset_removed, "renamed": self._preset_renamed},
        )
        ASS_PRESETS.clear()
        ASS_PRESETS.update(restored)
        self._preset_removed = []
        self._preset_renamed = {}
        self._save_presets()
        self._populate_cards()
        self._select_preset(next(iter(ASS_PRESETS)))
        self.preview_badge.configure(text="已恢复默认")

    def _reset_rename_for(self, name: str) -> None:
        """Drop rename records touching ``name`` so reloads cannot duplicate it."""

        for old, new in list(self._preset_renamed.items()):
            if old == name or new == name:
                self._preset_renamed.pop(old, None)

    def _notify_presets_changed(self) -> None:
        self.sync_presets_cb()
        refresh = self.refresh_presets_cb
        if callable(refresh):
            refresh(self.current_preset if self.current_preset in ASS_PRESETS else None)

    def _load_presets(self) -> None:
        meta = load_presets_into(ASS_PRESETS, defaults=BUILTIN_PRESETS)
        self._preset_removed = list(meta.get("removed") or [])
        self._preset_renamed = dict(meta.get("renamed") or {})
        if self.current_preset not in ASS_PRESETS:
            self.current_preset = next(iter(ASS_PRESETS))

    def _save_presets(self) -> None:
        try:
            write_preset_document(
                ASS_PRESETS,
                removed=self._preset_removed,
                renamed=self._preset_renamed,
            )
        except OSError as exc:
            print(f"保存花字预设失败: {exc}")

    def close_window(self) -> None:
        FontPreviewWindow.close_for(self)
        try:
            self.grab_release()
        except Exception:
            pass
        self.withdraw()
        owner = getattr(self, "_owner_window", None)
        if owner is not None:
            try:
                owner.grab_set()
                owner.lift()
                owner.focus_force()
            except Exception:
                pass


def _ensure_hex(value: object, default: str) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "none":
        return default
    return text if text.startswith("#") else f"#{text}"


def _safe_hex(value: object, default: str) -> str:
    text = _ensure_hex(value, default).upper()
    if len(text) != 7 or text[0] != "#":
        return default
    try:
        int(text[1:], 16)
    except ValueError:
        return default
    return text
