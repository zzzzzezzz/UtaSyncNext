from __future__ import annotations

import os
import tempfile
import webbrowser
from typing import Any
from tkinter import messagebox

from core.ass_config import build_ass_config, ensure_hex
from core.ass_maker import ASSMaker, ASS_PRESETS
from core.subtitle_policy import apply_layout_policy, policy_log_line, resolve_subtitle_layout
from engine.cache_env import app_root, task_base_name


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
        media_path = str(getattr(self.host, "video_path", "") or getattr(self.host, "media_path", "") or "")
        if media_path and os.path.exists(media_path):
            preview_policy = resolve_subtitle_layout(media_path, settings)
            config = apply_layout_policy(config, preview_policy, settings)
        c_ch = ensure_hex(config.get("ch_color"), "#FFFFFF")
        c_jp = ensure_hex(config.get("jp_color"), "#CCCCCC")
        o_ch = ensure_hex(config.get("ch_outline", config.get("outline_color")), "#18181B")
        o_jp = ensure_hex(config.get("jp_outline", config.get("outline_color")), "#18181B")
        shadow = config.get("shadow_size", 2)
        res_x = max(1, int(config.get("resolution_x", 1920)))
        res_y = max(1, int(config.get("resolution_y", 1080)))
        ch_size = int(config.get("ch_size", 55))
        jp_size = int(config.get("jp_size", 35))
        # cqw keeps the preview honest: the sample text scales with the preview box
        # the same way the ASS font scales with the export canvas.
        ch_cqw = ch_size / res_x * 100
        jp_cqw = jp_size / res_x * 100
        size_note = f"中文 {ch_size} · 日文 {jp_size} · 画布 {res_x}x{res_y}"

        html_content = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>UtaSyncNext 预览 - {ass_choice}</title>
<style>
body {{ background:#050505; display:flex; justify-content:center; align-items:center; height:100vh; margin:0; font-family:sans-serif; }}
.box {{ width:min(92vw, calc(80vh * {res_x} / {res_y})); aspect-ratio:{res_x} / {res_y}; container-type:inline-size; background:radial-gradient(circle at center,#1E293B 0%,#020617 100%); border-radius:12px; display:flex; flex-direction:column; justify-content:flex-end; align-items:center; padding-bottom:5cqw; border:1px solid #1e293b; box-shadow:0 20px 50px rgba(0,0,0,.8); position:relative; }}
.badge {{ position:absolute; top:20px; left:20px; background:rgba(29,185,84,.2); color:#1DB954; padding:4px 10px; border-radius:6px; font-size:12px; border:1px solid rgba(29,185,84,.4); font-weight:bold; }}
.ch {{ font-family:"{config.get('ch_font', 'Microsoft YaHei')}"; font-size:{ch_cqw:.3f}cqw; color:{c_ch}; text-shadow:-2px -2px 0 {o_ch},2px -2px 0 {o_ch},-2px 2px 0 {o_ch},2px 2px 0 {o_ch},{shadow}px {shadow}px {shadow}px {o_ch}; font-weight:bold; margin-bottom:0.6cqw; letter-spacing:0.15cqw; }}
.jp {{ font-family:"{config.get('jp_font', 'Meiryo')}"; font-size:{jp_cqw:.3f}cqw; color:{c_jp}; text-shadow:-2px -2px 0 {o_jp},2px -2px 0 {o_jp},-2px 2px 0 {o_jp},2px 2px 0 {o_jp},{shadow}px {shadow}px {shadow}px {o_jp}; font-weight:bold; letter-spacing:0.08cqw; }}
</style></head><body><div class="box"><div class="badge">当前预设: {ass_choice} · {size_note}</div><div class="ch">在这光芒万丈的舞台上</div><div class="jp">この光り輝くステージの上で</div></div></body></html>"""

        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as file:
            file.write(html_content)
            preview_path = file.name
        webbrowser.open(f"file://{preview_path}")

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
