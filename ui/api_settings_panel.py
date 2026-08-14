from __future__ import annotations

from typing import Any, Callable

import customtkinter as ctk

from ui.sidebar_options import (
    DEFAULT_API_PRESET,
    DEFAULT_DESIGN_API_PRESET,
    DESIGN_API_PRESETS,
    TRANSLATION_API_PRESETS,
)
from ui.theme import TEXT_HINT, TEXT_TITLE


class ApiSettingsPanel(ctk.CTkScrollableFrame):
    """OpenAI-compatible translation, design, and proxy settings."""

    def __init__(self, master, settings: dict[str, Any]) -> None:
        super().__init__(master, fg_color="transparent")
        self.settings = settings
        self.grid_columnconfigure(0, weight=1)
        self._build()

    def _build(self) -> None:
        row = 0

        row = self._add_group("核心翻译引擎", row)
        self.opt_api_preset = self._option(
            row,
            list(TRANSLATION_API_PRESETS.keys()),
            self.settings.get("api_preset", DEFAULT_API_PRESET),
            command=self.on_api_preset_change,
        )
        row += 1

        default_translation = TRANSLATION_API_PRESETS[DEFAULT_API_PRESET]
        row = self._field_label("Base URL (接口地址):", row)
        self.api_url_entry = self._entry(row, self.settings.get("api_url", default_translation.base_url))
        row += 1
        row = self._field_label("Model ID (模型代号):", row)
        self.api_model_entry = self._entry(row, self.settings.get("api_model", default_translation.model))
        row += 1
        row = self._field_label("API Key (您的密钥):", row)
        self.api_key_entry = self._entry(row, self.settings.get("api_key", ""), show="*")
        row += 1

        self._divider(row)
        row += 1

        row = self._add_group("视觉设计引擎", row)
        self.opt_api_preset_design = self._option(
            row,
            list(DESIGN_API_PRESETS.keys()),
            self.settings.get("api_preset_design", DEFAULT_DESIGN_API_PRESET),
            command=self.on_design_api_preset_change,
        )
        row += 1
        row = self._field_label("视觉 API URL:", row)
        self.api_url_entry_design = self._entry(row, self.settings.get("api_url_design", ""))
        row += 1
        row = self._field_label("视觉 Model ID:", row)
        self.api_model_entry_design = self._entry(row, self.settings.get("api_model_design", ""))
        row += 1
        row = self._field_label("视觉 API Key:", row)
        self.api_key_entry_design = self._entry(row, self.settings.get("api_key_design", ""), show="*")
        row += 1
        if "与翻译引擎一致" in self.opt_api_preset_design.get():
            for entry in [self.api_url_entry_design, self.api_model_entry_design, self.api_key_entry_design]:
                entry.configure(state="disabled", fg_color="#0A0A0A")

        self._divider(row)
        row += 1
        self.proxy_entry = ctk.CTkEntry(
            self,
            font=ctk.CTkFont(family="Consolas", size=12),
            placeholder_text="本地代理，如 10808 / 127.0.0.1:10808；留空直连",
            height=32,
            fg_color="#121212",
            text_color=TEXT_TITLE,
            border_width=0,
            corner_radius=8,
        )
        self.proxy_entry.insert(0, self.settings.get("proxy_url", ""))
        self.proxy_entry.grid(row=row, column=0, padx=20, pady=(15, 10), sticky="ew")

    def get_settings(self) -> dict[str, str]:
        preset_design = self.opt_api_preset_design.get()
        if "与翻译引擎一致" in preset_design:
            url_design = self.api_url_entry.get().strip()
            model_design = self.api_model_entry.get().strip()
            key_design = self.api_key_entry.get().strip()
        else:
            url_design = self.api_url_entry_design.get().strip()
            model_design = self.api_model_entry_design.get().strip()
            key_design = self.api_key_entry_design.get().strip()

        return {
            "api_key": self.api_key_entry.get().strip(),
            "api_preset": self.opt_api_preset.get(),
            "api_url": self.api_url_entry.get().strip(),
            "api_model": self.api_model_entry.get().strip(),
            "api_preset_design": preset_design,
            "api_url_design": url_design,
            "api_model_design": model_design,
            "api_key_design": key_design,
            "proxy_url": self.proxy_entry.get().strip(),
        }

    def on_api_preset_change(self, choice: str) -> None:
        self.api_url_entry.delete(0, "end")
        self.api_model_entry.delete(0, "end")
        preset = TRANSLATION_API_PRESETS.get(choice)
        self.api_url_entry.insert(0, preset.base_url if preset else "")
        self.api_model_entry.insert(0, preset.model if preset else "")

    def on_design_api_preset_change(self, choice: str) -> None:
        self._sync_design_fields(choice)

    def _sync_design_fields(self, choice: str | None = None) -> None:
        selected = choice or self.opt_api_preset_design.get()
        for entry in [self.api_url_entry_design, self.api_model_entry_design, self.api_key_entry_design]:
            entry.configure(state="normal", fg_color="#121212")

        self.api_url_entry_design.delete(0, "end")
        self.api_model_entry_design.delete(0, "end")
        if "与翻译引擎一致" in selected:
            for entry in [self.api_url_entry_design, self.api_model_entry_design, self.api_key_entry_design]:
                entry.configure(state="disabled", fg_color="#0A0A0A")
            return

        preset = DESIGN_API_PRESETS.get(selected)
        self.api_url_entry_design.insert(0, preset.base_url if preset else "")
        self.api_model_entry_design.insert(0, preset.model if preset else "")

    def _add_group(self, text: str, row: int) -> int:
        ctk.CTkLabel(
            self,
            text=text,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#666666",
        ).grid(row=row, column=0, padx=20, pady=(15, 2), sticky="w")
        return row + 1

    def _divider(self, row: int) -> None:
        ctk.CTkFrame(self, height=1, fg_color="#1E1E1E").grid(row=row, column=0, padx=20, pady=(5, 5), sticky="ew")

    def _field_label(self, text: str, row: int) -> int:
        ctk.CTkLabel(self, text=text, font=ctk.CTkFont(size=11), text_color=TEXT_HINT).grid(
            row=row, column=0, padx=20, pady=(0, 2), sticky="w"
        )
        return row + 1

    def _entry(self, row: int, value: object, show: str | None = None) -> ctk.CTkEntry:
        entry = ctk.CTkEntry(
            self,
            font=ctk.CTkFont(family="Consolas", size=12),
            show=show,
            height=32,
            fg_color="#121212",
            text_color=TEXT_TITLE,
            border_width=0,
            corner_radius=8,
        )
        entry.insert(0, str(value or ""))
        entry.grid(row=row, column=0, padx=20, pady=(0, 10), sticky="ew")
        return entry

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
            command=command,
            fg_color="#222222",
            text_color=TEXT_TITLE,
            button_color="#222222",
            button_hover_color="#2A2A2A",
        )
        selected = str(value or values[0])
        option.set(selected if selected in values else values[0])
        option.grid(row=row, column=0, padx=20, pady=(0, 10), sticky="ew")
        return option
