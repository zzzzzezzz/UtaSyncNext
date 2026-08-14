from __future__ import annotations

import customtkinter as ctk

from ui.engine_manager import show_engine_manager
from ui.theme import ACCENT_GREEN, BG_CARD, BG_WORKSPACE, TEXT_MUTED, TEXT_TITLE


BORDER = "#2A2A2A"


def show_help_dialog(parent, icon_photo=None) -> None:
    dialog = ctk.CTkToplevel(parent)
    dialog.title("UtaSyncNext - 使用说明")
    dialog.geometry("620x420")
    dialog.configure(fg_color=BG_WORKSPACE)
    dialog.transient(parent)
    dialog.grab_set()
    if icon_photo:
        dialog.iconphoto(True, icon_photo)

    frame = ctk.CTkFrame(dialog, fg_color=BG_CARD, border_color=BORDER, border_width=1, corner_radius=14)
    frame.pack(fill="both", expand=True, padx=24, pady=24)

    ctk.CTkLabel(
        frame,
        text="UtaSyncNext 使用说明",
        font=ctk.CTkFont(family="Microsoft YaHei", size=22, weight="bold"),
        text_color=TEXT_TITLE,
    ).pack(anchor="w", padx=22, pady=(20, 8))

    body = (
        "Next 的目标是把重型 AI 引擎隔离到独立 worker，主界面保持轻量和可取消。\n\n"
        "当前规则：\n"
        "1. 模型只读取 UtaSyncNext/models_faster，不再 fallback 到 V3 目录。\n"
        "2. 如果模型未完整，任务会停止并提示修复，而不是偷偷联网下载或借用旧版。\n"
        "3. 可以在“引擎与模型管理”里导入本地已有的完整 faster-whisper 缓存。"
    )
    ctk.CTkLabel(
        frame,
        text=body,
        justify="left",
        wraplength=540,
        font=ctk.CTkFont(family="Microsoft YaHei", size=14),
        text_color=TEXT_MUTED,
    ).pack(anchor="w", padx=22, pady=12)

    ctk.CTkButton(frame, text="知道了", command=dialog.destroy, fg_color=ACCENT_GREEN, text_color="#000000").pack(
        anchor="e", padx=22, pady=(12, 20)
    )
