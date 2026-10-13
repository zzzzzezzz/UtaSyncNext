"""In-app preview of the real ASS output.

The previous preview wrote an HTML approximation and opened a browser tab: the
outline was faked with CSS and the canvas size was a guess. This window renders
the frame through the same libass path the export uses, so what is on screen is
what gets burned into the video.
"""

from __future__ import annotations

import threading
from typing import Any

import customtkinter as ctk
from PIL import Image

from ui.ass_preview_frame import render_ass_preview_frame
from ui.design_tokens import UTASYNC_TOKENS
from ui.window_icon import apply_child_window_icon

# Subtitles sit near the bottom of the frame, so zooming keeps that band in view
# instead of drifting to the geometric centre of the picture.
_FOCUS_X = 0.5
_FOCUS_Y = 0.86
_MAX_ZOOM_MULTIPLIER = 3.0
_WHEEL_STEP = 1.15
_CHROME_HEIGHT = 152


class AssPreviewWindow(ctk.CTkToplevel):
    """Reusable window that shows one real ASS frame for the current settings."""

    _instance: "AssPreviewWindow | None" = None

    @classmethod
    def show(
        cls,
        master: Any,
        *,
        config: dict[str, Any],
        preset_name: str,
        chinese: str,
        japanese: str,
        source_note: str = "",
    ) -> "AssPreviewWindow":
        window = cls._instance
        if window is None or not window.winfo_exists():
            window = cls(master)
            cls._instance = window
        window._render(
            config,
            preset_name=preset_name,
            chinese=chinese,
            japanese=japanese,
            source_note=source_note,
        )
        window.deiconify()
        window.lift()
        window.focus_force()
        return window

    def __init__(self, master: Any) -> None:
        super().__init__(master)
        self._generation = 0
        self._source: Image.Image | None = None
        self._image: ctk.CTkImage | None = None
        self._zoom = 1.0
        self._fit_scale = 1.0
        self._geometry_ratio: float | None = None
        self._resize_after: str | None = None
        self._pending: dict[str, Any] = {}

        self.title("UtaSyncNext · 字幕预览")
        self.transient(master)
        apply_child_window_icon(self, master)
        self.configure(fg_color=UTASYNC_TOKENS.surface_app)
        self.minsize(420, 340)
        self.geometry(self._geometry_for(16 / 9))

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=22, pady=(16, 6))
        ctk.CTkLabel(
            header,
            text="真实 ASS 预览",
            anchor="w",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=17, weight="bold"),
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
        self._meta.pack(fill="x", padx=22, pady=(0, 8))

        self._stage = ctk.CTkFrame(
            self,
            fg_color=UTASYNC_TOKENS.surface_preview,
            border_color=UTASYNC_TOKENS.border_cool,
            border_width=1,
            corner_radius=UTASYNC_TOKENS.radius_panel,
        )
        self._stage.pack(fill="both", expand=True, padx=22, pady=(0, 10))
        self._stage.pack_propagate(False)
        self._frame_label = ctk.CTkLabel(
            self._stage,
            text="ASS 渲染中…",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=11),
            text_color=UTASYNC_TOKENS.text_hint,
        )
        self._frame_label.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(
            self,
            text="滚轮缩放 · 双击 1:1 看边缘细节 · 与压制共用同一条 libass 渲染路径",
            anchor="w",
            font=ctk.CTkFont(family=UTASYNC_TOKENS.font_ui, size=10),
            text_color=UTASYNC_TOKENS.text_hint,
        ).pack(fill="x", padx=22, pady=(0, 14))

        for widget in (self._stage, self._frame_label):
            widget.bind("<MouseWheel>", self._on_wheel)
            widget.bind("<Double-Button-1>", self._on_double_click)
        self._stage.bind("<Configure>", self._on_stage_resize)
        self.protocol("WM_DELETE_WINDOW", self.close)

    # --- geometry -----------------------------------------------------------
    def _geometry_for(self, ratio: float) -> str:
        """Size the window so the canvas matches the export aspect ratio."""

        screen_w = max(800, int(self.winfo_screenwidth()))
        screen_h = max(600, int(self.winfo_screenheight()))
        max_w = int(screen_w * 0.72)
        max_h = int(screen_h * 0.78) - _CHROME_HEIGHT
        stage_w = float(max_w)
        stage_h = stage_w / ratio if ratio else stage_w * 9 / 16
        if stage_h > max_h:
            stage_h = float(max_h)
            stage_w = stage_h * ratio
        width = int(max(420, stage_w + 56))
        height = int(max(340, stage_h + _CHROME_HEIGHT))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        return f"{width}x{height}+{x}+{y}"

    def _apply_geometry(self, ratio: float) -> None:
        # Keep a manual resize; only a different aspect ratio re-frames the window.
        if self._geometry_ratio is not None and abs(self._geometry_ratio - ratio) < 0.01:
            return
        self._geometry_ratio = ratio
        self.geometry(self._geometry_for(ratio))

    # --- rendering ----------------------------------------------------------
    def _render(
        self,
        config: dict[str, Any],
        *,
        preset_name: str,
        chinese: str,
        japanese: str,
        source_note: str = "",
    ) -> None:
        self._generation += 1
        generation = self._generation
        res_x = int(config.get("resolution_x") or 1920)
        res_y = int(config.get("resolution_y") or 1080)
        self._apply_geometry(res_x / res_y if res_y else 16 / 9)

        parts = [
            f"中文 {config.get('ch_font', 'Microsoft YaHei')}",
            f"日文 {config.get('jp_font', 'Meiryo')}",
            f"画幅 {res_x}x{res_y}",
            f"预设 {preset_name}",
        ]
        if source_note:
            parts.append(source_note)
        self._meta.configure(text="  ·  ".join(parts))
        self._status.configure(text="ASS 渲染中…")
        # 上一帧继续留在屏幕上：把它从标签上摘掉会让 Tk 留着已释放的图片名，
        # 下一次换图时就会抛 "image ... doesn't exist"。
        self._show_loading_placeholder()
        self._source = None
        self._zoom = 1.0

        render_config = dict(config)
        # The render runs on a worker thread, but the result is collected by a
        # poller owned by the UI thread: calling ``after`` from the worker is not
        # safe before the main loop is running (for example in smoke tests).
        self._pending = {}

        def worker() -> None:
            try:
                image = render_ass_preview_frame(
                    render_config, japanese, chinese, output_size=(res_x, res_y)
                )
            except Exception as exc:
                self._pending["error"] = exc
                return
            self._pending["image"] = image

        threading.Thread(target=worker, daemon=True).start()
        self.after(60, lambda: self._poll_render(generation))

    def _poll_render(self, generation: int) -> None:
        if generation != self._generation or not self.winfo_exists():
            return
        error = self._pending.get("error")
        if error is not None:
            self._render_failed(generation, error)
            return
        image = self._pending.get("image")
        if image is None:
            self.after(60, lambda: self._poll_render(generation))
            return
        self._pending = {}
        self._render_done(generation, image)

    def _render_failed(self, generation: int, exc: Exception) -> None:
        if generation != self._generation or not self.winfo_exists():
            return
        self._status.configure(text=f"预览失败：{exc}"[:72])
        if self._image is None:
            self._frame_label.configure(text=f"ASS 预览暂不可用：{exc}")

    def _show_loading_placeholder(self) -> None:
        """Show the loading text only while no frame is being displayed."""

        if self._image is None:
            self._frame_label.configure(text="ASS 渲染中…")

    def _render_done(self, generation: int, image: Image.Image) -> None:
        if generation != self._generation or not self.winfo_exists():
            return
        self._source = image
        self._status.configure(text="真实 ASS 预览")
        self._draw()

    # --- view state ---------------------------------------------------------
    def _max_zoom(self) -> float:
        if self._fit_scale <= 0:
            return 1.0
        return max(1.0, (1.0 / self._fit_scale) * _MAX_ZOOM_MULTIPLIER)

    def _set_zoom(self, value: float) -> None:
        self._zoom = min(max(value, 1.0), self._max_zoom())
        self._draw()

    def _draw(self) -> None:
        source = self._source
        if source is None or not self._frame_label.winfo_exists():
            return
        view_w = max(120, self._stage.winfo_width() - 20)
        view_h = max(80, self._stage.winfo_height() - 20)
        src_w, src_h = source.size
        self._fit_scale = min(view_w / src_w, view_h / src_h)
        scale = self._fit_scale * self._zoom
        crop_w = min(float(src_w), view_w / scale)
        crop_h = min(float(src_h), view_h / scale)
        left = min(max(src_w * _FOCUS_X - crop_w / 2, 0.0), src_w - crop_w)
        top = min(max(src_h * _FOCUS_Y - crop_h / 2, 0.0), src_h - crop_h)
        box = (
            int(round(left)),
            int(round(top)),
            int(round(left + crop_w)),
            int(round(top + crop_h)),
        )
        region = source if box == (0, 0, src_w, src_h) else source.crop(box)
        size = (max(1, int(round(crop_w * scale))), max(1, int(round(crop_h * scale))))
        frame = region.resize(size, Image.Resampling.LANCZOS)
        previous = self._image
        self._image = ctk.CTkImage(light_image=frame, dark_image=frame, size=size)
        self._frame_label.configure(image=self._image, text="")
        del previous
        self._status.configure(text=f"真实 ASS 预览 · {int(round(scale * 100))}%")

    def _on_wheel(self, event: Any) -> None:
        if self._source is None:
            return
        delta = int(getattr(event, "delta", 0) or 0)
        if not delta:
            return
        self._set_zoom(self._zoom * (_WHEEL_STEP if delta > 0 else 1 / _WHEEL_STEP))

    def _on_double_click(self, _event: Any) -> None:
        if self._source is None:
            return
        if self._zoom > 1.001:
            self._set_zoom(1.0)
            return
        self._set_zoom(1.0 / self._fit_scale if self._fit_scale else 1.0)

    def _on_stage_resize(self, _event: Any) -> None:
        if self._resize_after is not None:
            try:
                self.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.after(120, self._after_resize)

    def _after_resize(self) -> None:
        self._resize_after = None
        self._draw()

    def close(self) -> None:
        if AssPreviewWindow._instance is self:
            AssPreviewWindow._instance = None
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
