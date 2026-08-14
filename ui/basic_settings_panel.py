from __future__ import annotations

from typing import Any

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

        row = self._add_group("算力调度 (Hardware)", row)
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
        )
        row += 1

        self.demucs_hint_label = ctk.CTkLabel(
            self,
            text="建议先跳过，快速生成打轴结果；需要更干净的人声时再开启 Demucs。CPU 模式会明显变慢。",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            text_color="#A1A1AA",
            wraplength=300,
            justify="left",
        )
        self.demucs_hint_label.grid(row=row, column=0, padx=20, pady=(0, 10), sticky="w")
        row += 1

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

    def _option(self, row: int, values: list[str], value: object) -> ctk.CTkOptionMenu:
        option = ctk.CTkOptionMenu(
            self,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            values=values,
            fg_color="#222222",
            text_color=TEXT_TITLE,
            button_color="#222222",
            button_hover_color="#2A2A2A",
        )
        selected = str(value or values[0])
        option.set(selected if selected in values else values[0])
        option.grid(row=row, column=0, padx=20, pady=(0, 10), sticky="ew")
        return option
