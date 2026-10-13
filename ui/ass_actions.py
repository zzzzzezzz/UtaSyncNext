from __future__ import annotations

import os
from typing import Any
from tkinter import messagebox

from core.ass_config import build_ass_config
from core.ass_maker import ASSMaker, ASS_PRESETS
from core.subtitle_policy import apply_layout_policy, policy_log_line, resolve_subtitle_layout
from engine.cache_env import app_root, task_base_name
from ui.ass_preview_window import AssPreviewWindow

PREVIEW_SAMPLE_CHINESE = "在这光芒万丈的舞台上"
PREVIEW_SAMPLE_JAPANESE = "この光り輝くステージの上で"


class AssActionRunner:
    """ASS preview and redraw actions kept outside the main window shell."""

    def __init__(self, host: Any) -> None:
        self.host = host

    def preview_ass_style(self) -> None:
        settings = self.host.sidebar.get_current_settings()
        ass_choice = settings["ass_preset"]
        if ass_choice == "不生成 (仅SRT)":
            messagebox.showinfo("提示", "请先选择一个特效预设。", parent=self.host)
            return

        config = build_ass_config(ASS_PRESETS, settings, preset_name=str(ass_choice))
        source_note = ""
        media_path = str(getattr(self.host, "video_path", "") or getattr(self.host, "media_path", "") or "")
        if media_path and os.path.exists(media_path):
            preview_policy = resolve_subtitle_layout(media_path, settings)
            config = apply_layout_policy(config, preview_policy, settings)
            source_note = policy_log_line(preview_policy).rstrip("。")
        # 高级设置是模态弹窗：预览窗口必须挂在它下面，模态抓取才不会冻住新窗口。
        dialog = getattr(self.host.sidebar, "_advanced_dialog", None)
        if dialog is not None and not dialog.winfo_exists():
            dialog = None
        AssPreviewWindow.show(
            dialog or self.host,
            config=config,
            preset_name=str(ass_choice),
            chinese=PREVIEW_SAMPLE_CHINESE,
            japanese=PREVIEW_SAMPLE_JAPANESE,
            source_note=source_note,
        )

    def quick_redraw_ass(self) -> None:
        if not self.host.video_path:
            messagebox.showwarning("提示", "请先导入刚才处理过的媒体文件。", parent=self.host)
            return

        base_name = task_base_name(self.host.video_path)
        output_dir = str(app_root() / "output" / base_name / "subtitles")
        target_srt = None
        for suffix in ["_极致打轴_双语字幕.srt", "_极致打轴_纯中文字幕.srt", "_极致打轴.srt"]:
            candidate = os.path.join(output_dir, f"{base_name}{suffix}")
            if os.path.exists(candidate):
                target_srt = candidate
                break

        if not target_srt:
            messagebox.showwarning("提示", "未找到已生成的 SRT 字幕库，请先完成一次处理流程。", parent=self.host)
            return

        settings = self.host.sidebar.get_current_settings()
        config = build_ass_config(ASS_PRESETS, settings)
        layout_policy = resolve_subtitle_layout(self.host.video_path, settings)
        config = apply_layout_policy(config, layout_policy, settings)
        try:
            maker = ASSMaker(target_srt)
            ass_path = maker.generate_ass(config)
            self.host.set_last_outputs(
                {"output_dir": output_dir, "srt": target_srt, "ass": ass_path, "media": self.host.video_path}
            )
            self.host.append_log(policy_log_line(layout_policy, config) + "\n")
            self.host.append_log(f"\n⚡ [极速重绘] 新特效已保存: {ass_path}\n")
            messagebox.showinfo("重绘成功", f"花字皮肤已更新！\n\n{os.path.basename(ass_path)}", parent=self.host)
        except Exception as exc:
            messagebox.showerror("错误", f"重绘失败: {exc}", parent=self.host)
