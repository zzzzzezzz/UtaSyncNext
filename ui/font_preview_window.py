"""Reusable real-ASS font preview window.

The style workbench and the advanced settings panel both need to show the same
thing: the selected Chinese and Japanese families rendered by the export
renderer. One window keeps the two entry points from drifting apart.
"""

from __future__ import annotations

import threading
from typing import Any

import customtkinter as ctk
from PIL import Image, ImageOps

from ui.ass_preview_frame import render_ass_preview_frame
from ui.design_tokens import UTASYNC_TOKENS
from ui.window_icon import apply_child_window_icon

# One sample line shared by every font preview, so two entry points cannot show
# different text for the same pair.
SAMPLE_CHINESE = "在这光芒万丈的舞台上"
SAMPLE_JAPANESE = "この光り輝くステージの上で"


class FontPreviewWindow(ctk.CTkToplevel):
    """Singleton window: 中文/日文字体 + 样例字幕, rendered by libass."""

    _instance: "FontPreviewWindow | None" = None

    @classmethod
    def show(
        cls,
        master: Any,
        *,
        config: dict[str, Any],
        ch_font: str,
        jp_font: str,
        preset_name: str = "",
        chinese: str = "",
        japanese: str = "",
        sample_label: str = "",
        source_note: str = "",
    ) -> "FontPreviewWindow":
        window = cls._instance
        if window is None or not window.winfo_exists():
            window = cls(master)
            cls._instance = window
        window._render(
            config,
            ch_font=ch_font,
            jp_font=jp_font,
            preset_name=preset_name,
            chinese=chinese or SAMPLE_CHINESE,
            japanese=japanese or SAMPLE_JAPANESE,
            sample_label=sample_label,
            source_note=source_note,
        )
        window.deiconify()
        window.lift()
        window.focus_force()
        return window

    def __init__(self, master: Any) -> None:
        super().__init__(master)
        self._generation = 0
        self._pending: dict[str, Any] = {}
        self._image: ctk.CTkImage | None = None
        self._source: Image.Image | None = None
        self._resize_after: str | None = None

        self.title("UtaSyncNext · 字体预览")
        self.transient(master)
        apply_child_window_icon(self, master)
        self.configure(fg_color=UTASYNC_TOKENS.surface_app)
        self.geometry("880x640")
        self.minsize(720, 520)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=22, pady=(18, 8))
        ctk.CTkLabel(
            header,
            text="字体预览",
            anchor="w",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_display, size=20, weight="bold"),
            text_color=UTASYNC_TOKENS.text_primary,
        ).pack(side="left")
        self._status = ctk.CTkLabel(
            header,
            text="ASS 渲染中…",
            anchor="e",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            text_color=UTASYNC_TOKENS.text_hint,
        )
        self._status.pack(side="right", pady=(4, 0))

        self._meta = ctk.CTkLabel(
            self,
            text="",
            anchor="w",
            justify="left",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_mono, size=10),
            text_color=UTASYNC_TOKENS.text_secondary,
        )
        self._meta.pack(fill="x", padx=22, pady=(0, 10))

        self._stage = ctk.CTkFrame(
            self,
            fg_color=UTASYNC_TOKENS.surface_preview,
            border_color=UTASYNC_TOKENS.border_cool,
            border_width=1,
            corner_radius=UTASYNC_TOKENS.radius_panel,
        )
        self._stage.pack(fill="both", expand=True, padx=22, pady=(0, 12))
        self._stage.pack_propagate(False)
        self._frame_label = ctk.CTkLabel(self._stage, text="ASS 渲染中…")
        self._frame_label.pack(fill="both", expand=True, padx=12, pady=12)

        self._sample = ctk.CTkLabel(
            self,
            text="",
            anchor="w",
            justify="left",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            text_color=UTASYNC_TOKENS.text_hint,
        )
        self._sample.pack(fill="x", padx=22, pady=(0, 16))

        self._stage.bind("<Configure>", self._on_stage_resize)
        self.protocol("WM_DELETE_WINDOW", self.close)

    @classmethod
    def close_for(cls, master: Any) -> None:
        """Close the shared window when ``master`` owns it."""

        window = cls._instance
        if window is None:
            return
        try:
            if not window.winfo_exists():
                cls._instance = None
                return
            if window.master is master:
                window.close()
        except Exception:
            cls._instance = None

    # --- rendering ----------------------------------------------------------
    def _render(
        self,
        config: dict[str, Any],
        *,
        ch_font: str,
        jp_font: str,
        preset_name: str,
        chinese: str,
        japanese: str,
        sample_label: str,
        source_note: str,
    ) -> None:
        self._generation += 1
        generation = self._generation
        render_config = dict(config)
        render_config["ch_font"] = ch_font
        render_config["jp_font"] = jp_font

        meta = [f"中文：{ch_font}", f"日文：{jp_font}"]
        if preset_name:
            meta.append(f"预设：{preset_name}")
        if source_note:
            meta.append(source_note)
        self._meta.configure(text="  ·  ".join(meta))
        self._sample.configure(text="  ".join(part for part in (sample_label, f"{chinese}  /  {japanese}") if part))
        self._status.configure(text="ASS 渲染中…")
        if self._image is None:
            self._frame_label.configure(text="ASS 渲染中…")

        # The worker never touches Tk: the UI thread collects the frame, which
        # also keeps the window usable before an event loop is running.
        self._pending = {}

        def worker() -> None:
            try:
                self._pending["image"] = render_ass_preview_frame(render_config, japanese, chinese)
            except Exception as exc:
                self._pending["error"] = exc

        threading.Thread(target=worker, daemon=True).start()
        self.after(60, lambda: self._poll_render(generation))

    def _poll_render(self, generation: int) -> None:
        if generation != self._generation or not self.winfo_exists():
            return
        error = self._pending.get("error")
        if error is not None:
            self._status.configure(text="预览失败")
            self._frame_label.configure(text=f"ASS 预览暂不可用：{error}")
            return
        image = self._pending.get("image")
        if image is None:
            self.after(60, lambda: self._poll_render(generation))
            return
        self._pending = {}
        self._source = image
        self._fit_frame()
        self._status.configure(text="真实 ASS 预览")

    # --- view state ---------------------------------------------------------
    def _fit_frame(self) -> None:
        source = self._source
        if source is None or not self._frame_label.winfo_exists():
            return
        width = max(320, self._stage.winfo_width() - 24)
        height = max(200, self._stage.winfo_height() - 24)
        frame = ImageOps.contain(source, (width, height), method=Image.Resampling.LANCZOS)
        previous = self._image
        self._image = ctk.CTkImage(light_image=frame, dark_image=frame, size=frame.size)
        self._frame_label.configure(image=self._image, text="")
        del previous

    def _on_stage_resize(self, _event: Any) -> None:
        if self._resize_after is not None:
            try:
                self.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.after(120, self._after_resize)

    def _after_resize(self) -> None:
        self._resize_after = None
        self._fit_frame()

    def close(self) -> None:
        if FontPreviewWindow._instance is self:
            FontPreviewWindow._instance = None
        if self._resize_after is not None:
            try:
                self.after_cancel(self._resize_after)
            except Exception:
                pass
            self._resize_after = None
        self._generation += 1
        try:
            self.destroy()
        except Exception:
            pass
