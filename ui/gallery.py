from __future__ import annotations

import json
import re
import threading
import urllib.request
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable

import customtkinter as ctk

from core.ass_maker import ASS_PRESETS
from ui.theme import ACCENT_GREEN, ACCENT_WARNING, BG_CARD, BG_WORKSPACE, TEXT_MUTED, TEXT_TITLE


class StyleGalleryWindow(ctk.CTkToplevel):
    """Next-native ASS preset gallery and AI color workshop."""

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
    ) -> None:
        if getattr(self, "_initialized", False):
            self.apply_callback = apply_callback
            self.get_settings_cb = get_settings_cb
            self.sync_presets_cb = sync_presets_cb
            self._select_preset_silently(current_preset)
            self._show_window()
            return

        super().__init__(master)
        self._initialized = True
        self.withdraw()
        self.title("UtaSyncNext - 视觉特效画廊 & AI 工坊")
        self.geometry("1000x780")
        self.minsize(820, 650)
        self.configure(fg_color=BG_WORKSPACE)
        self.protocol("WM_DELETE_WINDOW", self.close_window)

        self.apply_callback = apply_callback
        self.get_settings_cb = get_settings_cb
        self.sync_presets_cb = sync_presets_cb
        self.current_preset = current_preset
        self.card_widgets: list[ctk.CTkFrame] = []
        self._current_cols = -1
        self._last_width = 1000

        self._load_presets()
        self._build_ui()
        self._populate_cards()
        self.bind("<Configure>", self._on_resize)
        self.after(150, self._show_window)

    def _show_window(self) -> None:
        self.deiconify()
        self.lift()
        self.focus()
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))

    def _build_ui(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=40, pady=(30, 10))
        ctk.CTkLabel(
            header,
            text="视觉特效画廊",
            font=ctk.CTkFont(family="Microsoft YaHei", size=24, weight="bold"),
            text_color=TEXT_TITLE,
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="选择、删除和生成字幕视觉预设；所有改动会写入 user_presets.json",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=ACCENT_WARNING,
        ).pack(side="left", padx=20, pady=(8, 0))

        self.scroll_area = ctk.CTkScrollableFrame(self, fg_color=BG_WORKSPACE, corner_radius=0)
        self.scroll_area.pack(fill="both", expand=True, padx=30, pady=10)
        try:
            self.scroll_area._parent_canvas.configure(bg=BG_WORKSPACE)
        except Exception:
            pass

        self._build_ai_panel()

    def _build_ai_panel(self) -> None:
        self.ai_panel = ctk.CTkFrame(
            self,
            fg_color="#0A0B10",
            border_color="#1E293B",
            border_width=1,
            corner_radius=15,
        )
        self.ai_panel.pack(fill="x", padx=40, pady=(10, 30))

        header = ctk.CTkFrame(self.ai_panel, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(15, 5))
        ctk.CTkLabel(
            header,
            text="AI 视觉灵感",
            font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"),
            text_color="#38BDF8",
        ).pack(side="left")
        ctk.CTkLabel(
            header,
            text="描述曲风或歌手，让模型返回 ASS 字幕配色 JSON",
            font=ctk.CTkFont(size=11),
            text_color="#64748B",
        ).pack(side="left", padx=15)

        tags_frame = ctk.CTkFrame(self.ai_panel, fg_color="transparent")
        tags_frame.pack(fill="x", padx=20, pady=(0, 5))
        quick_prompts = {
            "Aimer/樋口爱": "Aimer/樋口爱，空灵抒情，莫兰迪低饱和，文艺电影感",
            "majiko 爆发": "majiko 撕裂爆发摇滚，高对比度，情绪宣泄",
            "yama/iri 都市": "yama/iri/eill，都市R&B夜游感，霓虹微光，现代利落",
            "Saucy Dog 青春": "Saucy Dog，青春校园，遗憾与热血交织，清新暖调",
        }
        for label, prompt in quick_prompts.items():
            ctk.CTkButton(
                tags_frame,
                text=label,
                font=ctk.CTkFont(family="Microsoft YaHei", size=11),
                fg_color="#1E293B",
                hover_color="#334155",
                text_color="#94A3B8",
                height=26,
                corner_radius=6,
                command=lambda value=prompt: self._fill_prompt(value),
            ).pack(side="left", padx=(0, 8))

        self.btn_enhance = ctk.CTkButton(
            tags_frame,
            text="魔法扩写",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11, weight="bold"),
            fg_color="#1E3A8A",
            hover_color="#1E40AF",
            text_color="#93C5FD",
            height=26,
            corner_radius=6,
            command=self._on_enhance_clicked,
        )
        self.btn_enhance.pack(side="left", padx=(8, 8))

        input_frame = ctk.CTkFrame(self.ai_panel, fg_color="transparent")
        input_frame.pack(fill="x", padx=20, pady=(5, 15))
        self.prompt_entry = ctk.CTkEntry(
            input_frame,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            placeholder_text="输入你喜欢的歌手、曲风或点击上方标签...",
            height=40,
            fg_color="#121212",
            border_color="#333333",
            text_color="#FFFFFF",
        )
        self.prompt_entry.pack(side="left", fill="x", expand=True, padx=(0, 15))
        self.generate_btn = ctk.CTkButton(
            input_frame,
            text="立即生成",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
            width=120,
            height=40,
            fg_color="#059669",
            hover_color="#047857",
            text_color="#FFFFFF",
            command=self._on_generate_clicked,
        )
        self.generate_btn.pack(side="right")

    def _populate_cards(self) -> None:
        for widget in self.card_widgets:
            widget.destroy()
        self.card_widgets.clear()
        self._current_cols = -1

        for name, config in ASS_PRESETS.items():
            self.card_widgets.append(self._create_preset_card(self.scroll_area, name, config))
        self._regrid_cards()

    def _create_preset_card(self, parent, name: str, config: dict[str, Any]) -> ctk.CTkFrame:
        is_active = name == self.current_preset
        bg_color = "#112016" if is_active else BG_CARD
        border_color = ACCENT_GREEN if is_active else "#252525"
        card = ctk.CTkFrame(
            parent,
            fg_color=bg_color,
            border_color=border_color,
            border_width=2,
            corner_radius=8,
            cursor="hand2",
            width=310,
            height=210,
        )
        card.preset_name = name
        card.pack_propagate(False)
        card.grid_propagate(False)

        def select(_event: object | None = None, preset_name: str = name) -> None:
            self._select_preset(preset_name)

        card.bind("<Button-1>", select)
        preview = ctk.CTkFrame(card, height=100, fg_color="#050505", corner_radius=0)
        preview.pack(fill="x", padx=8, pady=(8, 0))
        preview.pack_propagate(False)
        preview.bind("<Button-1>", select)

        ctk.CTkLabel(
            preview,
            text="不再升起的太阳",
            font=ctk.CTkFont(family=str(config.get("ch_font", "Microsoft YaHei")), size=20, weight="bold"),
            text_color=_ensure_hex(config.get("ch_color"), "#FFFFFF"),
        ).pack(pady=(20, 0))
        ctk.CTkLabel(
            preview,
            text="太陽が昇らなくなった",
            font=ctk.CTkFont(family=str(config.get("jp_font", "Meiryo")), size=12),
            text_color=_ensure_hex(config.get("jp_color"), "#CCCCCC"),
        ).pack(pady=(2, 0))

        info_frame = ctk.CTkFrame(card, fg_color=bg_color, corner_radius=0)
        card.info_frame = info_frame
        info_frame.pack(fill="x", padx=12, pady=12)
        info_frame.bind("<Button-1>", select)

        text_frame = ctk.CTkFrame(info_frame, fg_color=bg_color, corner_radius=0)
        card.text_frame = text_frame
        text_frame.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            text_frame,
            text=name,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
            text_color=TEXT_TITLE,
            justify="left",
            wraplength=210,
        ).pack(anchor="w")
        ctk.CTkLabel(
            text_frame,
            text=str(config.get("description", "自定义排版预设")),
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            justify="left",
            wraplength=210,
        ).pack(anchor="w", pady=(2, 0))

        ctk.CTkButton(
            info_frame,
            text="删",
            width=32,
            height=28,
            fg_color="#7F1D1D",
            hover_color="#991B1B",
            corner_radius=6,
            command=lambda preset_name=name, widget=card: self._delete_preset(preset_name, widget),
        ).pack(side="right", padx=(5, 0))
        return card

    def _regrid_cards(self) -> None:
        if not self.card_widgets:
            return

        current_width = self.winfo_width()
        if current_width < 100:
            current_width = 1000
        cols = max(2, int((current_width - 80) / 330))
        if cols == self._current_cols:
            return

        self._current_cols = cols
        for column in range(10):
            self.scroll_area.grid_columnconfigure(column, weight=1 if column < cols else 0)

        row = 0
        col = 0
        for card in self.card_widgets:
            card.grid(row=row, column=col, padx=10, pady=10, sticky="n")
            col += 1
            if col >= cols:
                col = 0
                row += 1

    def _on_resize(self, event) -> None:
        if event.widget != self:
            return
        if abs(event.width - self._last_width) <= 60:
            return
        self._last_width = event.width
        if hasattr(self, "_resize_timer"):
            self.after_cancel(self._resize_timer)
        self._resize_timer = self.after(150, self._regrid_cards)

    def _select_preset_silently(self, name: str) -> None:
        self.current_preset = name
        for card in self.card_widgets:
            active = getattr(card, "preset_name", "") == name
            bg = "#112016" if active else BG_CARD
            card.configure(border_color=ACCENT_GREEN if active else "#252525", fg_color=bg)
            if hasattr(card, "info_frame"):
                card.info_frame.configure(fg_color=bg)
            if hasattr(card, "text_frame"):
                card.text_frame.configure(fg_color=bg)

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
        self._save_presets()
        self.sync_presets_cb()
        if card in self.card_widgets:
            self.card_widgets.remove(card)
        card.destroy()
        self._current_cols = -1
        self._regrid_cards()
        if self.current_preset == preset_name:
            self._select_preset(next(iter(ASS_PRESETS.keys())))

    def _load_presets(self) -> None:
        preset_file = Path("user_presets.json")
        if not preset_file.exists():
            return
        try:
            data = json.loads(preset_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                ASS_PRESETS.update(data)
        except Exception as exc:
            print(f"读取本地花字预设失败: {exc}")

    def _save_presets(self) -> None:
        try:
            Path("user_presets.json").write_text(json.dumps(ASS_PRESETS, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"保存花字预设失败: {exc}")

    def _fill_prompt(self, text: str) -> None:
        self.prompt_entry.delete(0, "end")
        self.prompt_entry.insert(0, text)

    def _on_enhance_clicked(self) -> None:
        current = self.prompt_entry.get().strip()
        if len(current) < 2:
            return
        sys_prompt = "你是日音MV视觉提示词扩写助手。请将输入扩写为一句30字内的视觉提示词，包含色彩和氛围。"
        self._run_ai_task(
            sys_prompt,
            f"请扩写这个概念：{current}",
            lambda result: self._fill_prompt(result.strip()),
            lambda exc: messagebox.showerror("API 错误", str(exc), parent=self),
            self.btn_enhance,
        )

    def _on_generate_clicked(self) -> None:
        prompt = self.prompt_entry.get().strip()
        if not prompt:
            return

        sys_prompt = """你是一位日系音乐字幕视觉设计师。
请根据用户描述，输出严格 JSON，不要 Markdown。
字段必须包含:
{
  "name": "创意名",
  "ch_color": "#FFFFFF",
  "ch_outline": "#18181B",
  "ch_font": "Microsoft YaHei",
  "jp_color": "#CCCCCC",
  "jp_outline": "#18181B",
  "jp_font": "Meiryo",
  "description": "简短美学描述"
}
颜色必须带 #，不要尾随逗号。"""

        self._run_ai_task(
            sys_prompt,
            prompt,
            self._install_ai_preset,
            lambda exc: messagebox.showerror("API 错误", str(exc), parent=self),
            self.generate_btn,
        )

    def _install_ai_preset(self, json_text: str) -> None:
        try:
            match = re.search(r"\{.*\}", json_text, re.DOTALL)
            content = match.group(0) if match else json_text
            content = re.sub(r",\s*\}", "}", content)
            data = json.loads(content)
            preset_name = f"AI: {data.get('name', '定制幻色')}"
            ASS_PRESETS[preset_name] = {
                "ch_font": data.get("ch_font", "Microsoft YaHei"),
                "ch_size": 55,
                "ch_color": _ensure_hex(data.get("ch_color"), "#FFFFFF"),
                "ch_outline": _ensure_hex(data.get("ch_outline"), "#18181B"),
                "jp_font": data.get("jp_font", "Meiryo"),
                "jp_size": 35,
                "jp_color": _ensure_hex(data.get("jp_color"), "#CCCCCC"),
                "jp_outline": _ensure_hex(data.get("jp_outline"), "#18181B"),
                "outline_size": 3,
                "shadow_size": 1,
                "description": data.get("description", ""),
            }
            self._save_presets()
            self.sync_presets_cb()
            self._populate_cards()
            self._select_preset(preset_name)
            self.prompt_entry.delete(0, "end")
        except Exception as exc:
            print(f"解析失败的原始数据:\n{json_text}")
            messagebox.showerror("解析失败", f"AI 返回的 JSON 不可用：{exc}", parent=self)

    def _run_ai_task(
        self,
        sys_prompt: str,
        user_prompt: str,
        on_success: Callable[[str], object],
        on_fail: Callable[[Exception], object],
        active_btn: ctk.CTkButton,
    ) -> None:
        settings = self.get_settings_cb()
        api_key = settings.get("api_key_design") or settings.get("api_key", "")
        api_url = settings.get("api_url_design") or settings.get("api_url", "")
        api_model = settings.get("api_model_design") or settings.get("api_model", "")

        if not api_key:
            messagebox.showwarning("缺少配置", "请先在主界面【大模型API】中填写 Key。", parent=self)
            return

        original_text = active_btn.cget("text")
        active_btn.configure(text="思考中...", state="disabled")

        def worker() -> None:
            try:
                endpoint = str(api_url).rstrip("/") + "/chat/completions"
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
                payload = {
                    "model": api_model,
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.8,
                }
                request = urllib.request.Request(
                    endpoint,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    content = data["choices"][0]["message"]["content"]
                    self.after(0, lambda: on_success(content))
            except Exception as exc:
                self.after(0, lambda: on_fail(exc))
            finally:
                self.after(0, lambda: active_btn.configure(text=original_text, state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def close_window(self) -> None:
        self.withdraw()


def _ensure_hex(value: object, default: str) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "none":
        return default
    return text if text.startswith("#") else f"#{text}"
