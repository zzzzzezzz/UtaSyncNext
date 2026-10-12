from __future__ import annotations

import customtkinter as ctk

from ui.engine_manager import show_engine_manager
from ui.theme import ACCENT_GREEN, BG_CARD, BG_WORKSPACE, TEXT_MUTED, TEXT_TITLE
from ui.window_icon import apply_child_window_icon


BORDER = "#2A2A2A"


def show_help_dialog(parent, icon_photo=None) -> None:
    dialog = ctk.CTkToplevel(parent)
    dialog.title("UtaSyncNext - 使用说明")
    dialog.geometry("620x420")
    dialog.configure(fg_color=BG_WORKSPACE)
    dialog.transient(parent)
    dialog.grab_set()
    apply_child_window_icon(dialog, parent)

    frame = ctk.CTkFrame(dialog, fg_color=BG_CARD, border_color=BORDER, border_width=1, corner_radius=14)
    frame.pack(fill="both", expand=True, padx=24, pady=24)

    ctk.CTkLabel(
        frame,
        text="UtaSyncNext 使用说明",
        font=ctk.CTkFont(family="Microsoft YaHei", size=22, weight="bold"),
        text_color=TEXT_TITLE,
    ).pack(anchor="w", padx=22, pady=(20, 8))

    body = (
        "UtaSyncNext 用来把视频或音频制作成带时间轴的字幕、花字和成片。\n\n"
        "基本流程：\n"
        "1. 选择视频 / 音频，可选填歌词。\n"
        "2. 在左侧选择识别模型和分离策略。\n"
        "3. 点击“启动引擎”，等待识别、翻译或压制完成。\n"
        "4. 完成后可复制 SRT / ASS，或直接压制 MP4。\n\n"
        "模型和引擎不会自动混用旧版目录：模型管理中显示“完整”后即可使用；已有本地模型也可以通过“导入本地模型”接入。"
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
