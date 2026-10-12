from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

from ui.sidebar_options import (
    AUDIO_TYPE_OPTIONS,
    DEFAULT_AUDIO_TYPE,
    DEFAULT_DEMUCS_OPTION,
    DEFAULT_HARDWARE_MODE,
    DEFAULT_OUTPUT_MODE,
    DEFAULT_WHISPER_MODEL,
    DEMUCS_OPTIONS,
    HARDWARE_MODE_OPTIONS,
    OUTPUT_MODE_OPTIONS,
    WHISPER_MODEL_OPTIONS,
)
from ui.theme import TEXT_TITLE


class BasicSettingsPanel(ctk.CTkScrollableFrame):
    """Task-critical acoustic and pipeline controls."""

    def __init__(self, master, settings: dict[str, Any]) -> None:
        super().__init__(master, fg_color="transparent")
        self.settings = settings
        self.grid_columnconfigure(0, weight=1)
        self._build()

    def _build(self) -> None:
        row = 0
        row = self._add_group("声学模型 (Acoustic)", row)
        self.opt_audio_type = self._option(
            row,
            AUDIO_TYPE_OPTIONS,
            self.settings.get("audio_type", DEFAULT_AUDIO_TYPE),
        )
        row += 1

        row = self._add_group("人声分离算力 (Demucs Hardware)", row)
        self.opt_hardware = self._option(
            row,
            HARDWARE_MODE_OPTIONS,
            self.settings.get("hardware_mode", DEFAULT_HARDWARE_MODE),
        )
        row += 1

        row = self._add_group("分离策略 (Demucs)", row)
        self.opt_demucs = self._option(
            row,
            DEMUCS_OPTIONS,
            self.settings.get("use_demucs", DEFAULT_DEMUCS_OPTION),
            command=self._on_demucs_change,
        )
        row += 1

        self.demucs_hint_label = ctk.CTkLabel(
            self,
            text="建议先跳过，快速生成打轴结果；需要更干净的人声时再开启 Demucs。CPU 模式会明显变慢。",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            text_color="#A1A1AA",
            wraplength=270,
            justify="left",
            anchor="w",
        )
        self.demucs_hint_label.grid(row=row, column=0, padx=20, pady=(0, 10), sticky="ew")
        self.bind("<Configure>", self._fit_demucs_hint)
        row += 1
        self._on_demucs_change(self.opt_demucs.get())
        self.after_idle(self._sync_demucs_hint_width)

        row = self._add_group("引擎精度 (Whisper)", row)
        self.opt_model = self._option(
            row,
            WHISPER_MODEL_OPTIONS,
            self.settings.get("model_size", DEFAULT_WHISPER_MODEL),
        )
        row += 1

        row = self._add_group("语言排版模式 (Output)", row)
        self.opt_output_mode = self._option(
            row,
            OUTPUT_MODE_OPTIONS,
            self.settings.get("output_mode", DEFAULT_OUTPUT_MODE),
        )

    def get_settings(self) -> dict[str, str]:
        return {
            "model_size": self.opt_model.get(),
            "audio_type": self.opt_audio_type.get(),
            "output_mode": self.opt_output_mode.get(),
            "use_demucs": self.opt_demucs.get(),
            "hardware_mode": self.opt_hardware.get(),
        }

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
    ) -> ctk.CTkOptionMenu:
        option = ctk.CTkOptionMenu(
            self,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            values=values,
            fg_color="#222222",
            text_color=TEXT_TITLE,
            button_color="#222222",
            button_hover_color="#2A2A2A",
            command=command,
        )
        selected = str(value or values[0])
        option.set(selected if selected in values else values[0])
        option.grid(row=row, column=0, padx=20, pady=(0, 10), sticky="ew")
        return option

    def _on_demucs_change(self, choice: str) -> None:
        skipped = "跳过" in str(choice)
        self.opt_hardware.configure(state="disabled" if skipped else "normal")
        hint = (
            "Whisper 会自动优先使用 GPU。当前跳过人声分离，无需设置显存档位。"
            if skipped
            else "自动档会按显存选择 Demucs 分段；显存紧张时可手动选保守分离。"
        )
        self.demucs_hint_label.configure(text=hint)

    def _fit_demucs_hint(self, event: object) -> None:
        if int(getattr(event, "width", 0)) > 50:
            self._sync_demucs_hint_width()

    def _sync_demucs_hint_width(self) -> None:
        # Leave room for the frame padding and keep the label left-aligned so
        # long Chinese hints never lose their first characters in a narrow
        # sidebar.
        width = self.winfo_width()
        if width <= 50 or not self.demucs_hint_label.winfo_exists():
            return
        wraplength = max(170, width - 40)
        self.demucs_hint_label.configure(wraplength=wraplength)
