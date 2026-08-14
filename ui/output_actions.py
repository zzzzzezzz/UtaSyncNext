from __future__ import annotations

import os
import shutil
import webbrowser
from pathlib import Path
from tkinter import messagebox
from typing import Any

from engine.cache_env import app_root


class OutputActionRunner:
    """Output discovery, path actions, and per-media cache cleanup."""

    def __init__(self, host: Any) -> None:
        self.host = host

    def set_last_outputs(self, artifacts: dict[str, object]) -> None:
        self.host.last_outputs = {key: str(value) for key, value in artifacts.items() if value}
        self.host.workspace.update_outputs(self.host.last_outputs)
        output_dir = self.host.last_outputs.get("output_dir")
        if output_dir:
            self.host.append_log(f"\n📦 [输出结果] 已更新输出目录: {output_dir}\n")

    def open_output_dir(self) -> None:
        output_dir = self.host.last_outputs.get("output_dir")
        if not output_dir:
            self.refresh_existing_outputs()
            output_dir = self.host.last_outputs.get("output_dir")

        if not output_dir or not os.path.isdir(output_dir):
            messagebox.showwarning("提示", "暂时没有可打开的输出目录，请先完成一次处理。", parent=self.host)
            return

        try:
            if hasattr(os, "startfile"):
                os.startfile(output_dir)  # type: ignore[attr-defined]
            else:
                webbrowser.open(Path(output_dir).as_uri())
        except Exception as exc:
            messagebox.showerror("打开失败", f"无法打开输出目录：{exc}", parent=self.host)

    def copy_output_path(self, artifact_key: str) -> None:
        target_path = self.host.last_outputs.get(artifact_key)
        if not target_path:
            self.refresh_existing_outputs()
            target_path = self.host.last_outputs.get(artifact_key)

        if not target_path or not os.path.exists(target_path):
            labels = {"ass": "ASS", "srt": "SRT", "log": "日志"}
            label = labels.get(artifact_key, "输出文件")
            messagebox.showwarning("提示", f"暂时没有可复制的 {label} 输出路径。", parent=self.host)
            return

        self.host.clipboard_clear()
        self.host.clipboard_append(target_path)
        self.host.update()
        self.host.append_log(f"\n📋 [路径已复制] {target_path}\n")

    def refresh_existing_outputs(self) -> None:
        artifacts = self._find_existing_outputs()
        if artifacts:
            self.set_last_outputs(artifacts)
        elif hasattr(self.host, "workspace"):
            self.host.last_outputs = {}
            self.host.workspace.update_outputs({})

    def clear_cache(self) -> None:
        if not self.host.media_path:
            messagebox.showwarning("提示", "请先导入或处理一个媒体文件，再清理当前任务缓存。", parent=self.host)
            return

        base_name = Path(self.host.media_path).stem
        output_root = app_root() / "output" / base_name
        if not output_root.exists():
            messagebox.showinfo("清理空间", "当前媒体还没有生成 Next 输出目录，无需清理。", parent=self.host)
            return

        targets = [
            output_root / "audio",
            output_root / "htdemucs_ft",
            output_root / "separated",
        ]
        existing_targets = [path for path in targets if path.exists()]
        total_bytes = sum(self._path_size(path) for path in existing_targets)
        if not existing_targets or total_bytes <= 0:
            messagebox.showinfo(
                "清理空间",
                "没有检测到可清理的音频/分离中间缓存。SRT/ASS/成品视频会被保留。",
                parent=self.host,
            )
            return

        if not messagebox.askyesno(
            "确认清理",
            f"将清理当前媒体的中间缓存，保留字幕与成品文件。\n\n目录：{output_root}\n预计释放：{self._format_bytes(total_bytes)}\n\n是否继续？",
            parent=self.host,
        ):
            return

        removed = 0
        for path in existing_targets:
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                removed += 1
            except Exception as exc:
                self.host.append_log(f"⚠️ [缓存清理] 无法删除 {path}: {exc}\n")

        self.host.append_log(f"\n🧹 [缓存清理] 已清理 {removed} 个中间缓存目录，预计释放 {self._format_bytes(total_bytes)}。\n")
        self.refresh_existing_outputs()

    def _find_existing_outputs(self) -> dict[str, str]:
        if not self.host.media_path:
            return {}

        base_name = Path(self.host.media_path).stem
        output_root = app_root() / "output" / base_name
        subtitles_dir = output_root / "subtitles"
        log_dir = output_root / "logs"
        if not subtitles_dir.exists() and not log_dir.exists():
            return {}

        srt_candidates = (
            sorted(subtitles_dir.glob(f"{base_name}*.srt"), key=self._output_priority, reverse=True)
            if subtitles_dir.exists()
            else []
        )
        ass_candidates = (
            sorted(subtitles_dir.glob(f"{base_name}*.ass"), key=self._output_priority, reverse=True)
            if subtitles_dir.exists()
            else []
        )
        log_candidates = (
            sorted(log_dir.glob("utasyncnext_error_*.txt"), key=lambda path: path.stat().st_mtime, reverse=True)
            if log_dir.exists()
            else []
        )
        output_dir = subtitles_dir if subtitles_dir.exists() else log_dir
        artifacts: dict[str, str] = {
            "output_dir": str(output_dir.resolve()),
            "media": str(Path(self.host.media_path).resolve()),
        }
        if srt_candidates:
            artifacts["srt"] = str(srt_candidates[0].resolve())
        if ass_candidates:
            artifacts["ass"] = str(ass_candidates[0].resolve())
        if log_candidates:
            artifacts["log"] = str(log_candidates[0].resolve())
        return artifacts

    def _output_priority(self, path: Path) -> tuple[int, float]:
        name = path.name
        score = 0
        if "双语字幕" in name or "纯中文字幕" in name:
            score += 20
        if "顶级特效" in name:
            score += 5
        return score, path.stat().st_mtime

    def _path_size(self, path: Path) -> int:
        if not path.exists():
            return 0
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        total = 0
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
        return total

    def _format_bytes(self, value: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(value)
        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024
        return f"{size:.1f} TB"
