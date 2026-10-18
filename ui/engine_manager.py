from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from tkinter import TclError, filedialog, messagebox

import customtkinter as ctk

from engine.cache_env import app_root, apply_next_cache_env, huggingface_cache_root
from engine.model_downloader import MODEL_SOURCES, SOURCE_OFFICIAL, ModelDownloadError, download_model
from engine.model_downloader import preflight_model_download
from engine.model_registry import is_complete_snapshot, next_models_root, scan_models
from engine.model_source_check import check_model_sources, format_source_check_report
from engine.runtime_installer import EngineInstallError, fetch_manifest, install_engine, manifest_url
from ui.engine_manager_support import (
    format_bytes,
    format_speed,
    guess_model_name,
    initial_proxy_value,
    model_download_size,
    persist_model_proxy,
    proxy_preview_text,
    tail_text,
    target_cache_dir_name,
)
from ui.theme import ACCENT_GREEN, BG_CARD, BG_WORKSPACE, TEXT_HINT, TEXT_MUTED, TEXT_TITLE
from ui.window_icon import apply_child_window_icon


BORDER = "#2A2A2A"


def show_engine_manager(parent, icon_photo=None, engine_ready=True) -> None:
    manager = ctk.CTkToplevel(parent)
    manager.title("UtaSyncNext - 独立模型管理")
    # Reserve enough room for the fixed settings panels and footer. The model
    # list expands between them and remains the only vertically scrolling area.
    manager.geometry("980x760")
    manager.minsize(900, 700)
    manager.configure(fg_color=BG_WORKSPACE)
    manager.transient(parent)
    manager.grab_set()
    apply_child_window_icon(manager, parent)

    model_root = next_models_root()
    model_root.mkdir(parents=True, exist_ok=True)
    cache_env = apply_next_cache_env()
    hf_cache_root = huggingface_cache_root()
    user_hf_cache = Path.home() / ".cache" / "huggingface"
    user_cache_note = "旧 C 盘 HF 缓存：存在（Next 不依赖）" if user_hf_cache.exists() else "旧 C 盘 HF 缓存：未检测到"
    nearby_source_models = None
    if getattr(sys, "frozen", False):
        candidate = app_root().parents[1] / "models_faster"
        try:
            if candidate.resolve() != model_root.resolve() and candidate.exists():
                nearby_source_models = candidate
        except OSError:
            nearby_source_models = None

    shell = ctk.CTkFrame(manager, fg_color=BG_CARD, border_color=BORDER, border_width=1, corner_radius=16)
    shell.pack(fill="both", expand=True, padx=24, pady=24)

    header = ctk.CTkFrame(shell, fg_color="transparent")
    header.pack(fill="x", padx=22, pady=(20, 10))

    ctk.CTkLabel(
        header,
        text="Next 独立模型仓库",
        font=ctk.CTkFont(family="Microsoft YaHei", size=22, weight="bold"),
        text_color=TEXT_TITLE,
    ).pack(anchor="w")
    ctk.CTkLabel(
        header,
        text=(
            f"引擎目录：{app_root() / '.engine'}（{'已检测到' if engine_ready else '未检测到'}）\n"
            f"模型目录：{model_root}"
        ),
        font=ctk.CTkFont(family="Microsoft YaHei", size=12),
        text_color=TEXT_HINT,
        wraplength=680,
    ).pack(anchor="w", pady=(6, 0))
    ctk.CTkLabel(
        header,
        text=(
            "这里不读取 V3 模型目录。缺模型时可在下方预检并下载到 Next 专属目录；网络中断后可继续复用已有缓存。"
            + (f"\n检测到另一个 Next 本地缓存：{nearby_source_models}，可用“导入本地模型”接入。" if nearby_source_models else "")
            + "\n本机体检检查本地引擎、依赖和 FFmpeg；模型源巡检只检查在线仓库清单和代理，不会下载模型。"
        ),
        font=ctk.CTkFont(family="Microsoft YaHei", size=12),
        text_color=TEXT_MUTED,
        wraplength=680,
    ).pack(anchor="w", pady=(4, 0))

    cache_panel = ctk.CTkFrame(shell, fg_color="#0B0D12", border_color=BORDER, border_width=1, corner_radius=12)
    cache_panel.pack(fill="x", padx=22, pady=(0, 12))
    ctk.CTkLabel(
        cache_panel,
        text="缓存位置",
        font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
        text_color=TEXT_TITLE,
        width=72,
        anchor="w",
    ).pack(side="left", padx=(14, 8), pady=10)
    ctk.CTkLabel(
        cache_panel,
        text=f"HF_HOME: {cache_env['HF_HOME']}\n{user_cache_note}",
        font=ctk.CTkFont(family="Consolas", size=11),
        text_color=ACCENT_GREEN,
        wraplength=650,
        anchor="w",
        justify="left",
    ).pack(side="left", fill="x", expand=True, padx=8, pady=10)

    source_bar = ctk.CTkFrame(shell, fg_color="#0B0D12", border_color=BORDER, border_width=1, corner_radius=12)
    source_bar.pack(fill="x", padx=22, pady=(0, 12))

    source_var = ctk.StringVar(value=SOURCE_OFFICIAL)
    ctk.CTkLabel(
        source_bar,
        text="下载源",
        font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
        text_color=TEXT_TITLE,
    ).pack(side="left", padx=(14, 8), pady=12)
    ctk.CTkOptionMenu(
        source_bar,
        values=list(MODEL_SOURCES.keys()),
        variable=source_var,
        width=170,
        fg_color="#1F2937",
        button_color="#263244",
    ).pack(side="left", padx=8, pady=12)

    custom_endpoint_entry = ctk.CTkEntry(
        source_bar,
        placeholder_text="自定义 endpoint，例如 https://hf-mirror.com",
        width=330,
        fg_color="#07080B",
        border_color=BORDER,
    )
    custom_endpoint_entry.pack(side="left", padx=8, pady=12)

    ctk.CTkLabel(
        source_bar,
        text="官方源已验证；镜像不可用时会自动回退官方源",
        font=ctk.CTkFont(family="Microsoft YaHei", size=11),
        text_color=TEXT_HINT,
    ).pack(side="left", padx=(8, 14), pady=12)

    proxy_bar = ctk.CTkFrame(shell, fg_color="#0B0D12", border_color=BORDER, border_width=1, corner_radius=12)
    proxy_bar.pack(fill="x", padx=22, pady=(0, 12))

    ctk.CTkLabel(
        proxy_bar,
        text="代理",
        font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
        text_color=TEXT_TITLE,
    ).pack(side="left", padx=(14, 8), pady=12)
    proxy_entry = ctk.CTkEntry(
        proxy_bar,
        placeholder_text="可选：10808 / 127.0.0.1:10808 / http://127.0.0.1:10808",
        width=420,
        fg_color="#07080B",
        border_color=BORDER,
    )
    proxy_entry.insert(0, initial_proxy_value(parent))
    proxy_entry.pack(side="left", padx=8, pady=12)
    ctk.CTkLabel(
        proxy_bar,
        text="留空则使用系统/环境默认网络",
        font=ctk.CTkFont(family="Microsoft YaHei", size=11),
        text_color=TEXT_HINT,
    ).pack(side="left", padx=(8, 14), pady=12)

    rows = ctk.CTkScrollableFrame(shell, fg_color="#090B0F", border_color=BORDER, border_width=1, corner_radius=12)
    rows.pack(fill="both", expand=True, padx=22, pady=12)

    footer = ctk.CTkFrame(shell, fg_color="transparent")
    footer.pack(fill="x", padx=22, pady=(0, 20))
    # Keep the changing download text in its own row so a long status message
    # can never squeeze the fixed action buttons next to it.
    status_row = ctk.CTkFrame(footer, fg_color="transparent")
    status_row.pack(fill="x", pady=(0, 6))
    status_label = ctk.CTkLabel(
        status_row,
        text="就绪",
        font=ctk.CTkFont(family="Microsoft YaHei", size=12),
        text_color=TEXT_HINT,
        anchor="w",
    )
    status_label.pack(side="left", fill="x", expand=True)
    download_progress = ctk.CTkProgressBar(status_row, width=170, mode="determinate")
    download_progress.set(0)
    download_progress.pack(side="right", padx=(12, 0))

    engine_resource_row = ctk.CTkFrame(footer, fg_color="#0B0D12", border_color=BORDER, border_width=1, corner_radius=10)
    engine_resource_row.pack(fill="x", pady=(0, 8))
    engine_resource_indicator = ctk.CTkLabel(
        engine_resource_row,
        text="●",
        width=18,
        font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
        text_color=TEXT_HINT,
    )
    engine_resource_indicator.pack(side="left", padx=(12, 0), pady=8)
    engine_resource_label = ctk.CTkLabel(
        engine_resource_row,
        text="正在检查引擎资源",
        font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
        text_color=TEXT_TITLE,
        anchor="w",
    )
    engine_resource_label.pack(side="left", padx=(4, 8), pady=8)
    engine_resource_detail = ctk.CTkLabel(
        engine_resource_row,
        text="正在读取发布清单…",
        font=ctk.CTkFont(family="Microsoft YaHei", size=11),
        text_color=TEXT_MUTED,
        anchor="w",
        justify="left",
        wraplength=560,
    )
    engine_resource_detail.pack(side="left", fill="x", expand=True, padx=4, pady=8)
    engine_resource_retry = ctk.CTkButton(
        engine_resource_row,
        text="重新检查",
        width=88,
        height=28,
        command=lambda: check_engine_resources(),
        fg_color="#1F2937",
    )
    engine_resource_retry.pack(side="right", padx=(8, 10), pady=5)

    common_title = ctk.CTkLabel(
        footer,
        text="常用操作",
        font=ctk.CTkFont(family="Microsoft YaHei", size=11, weight="bold"),
        text_color=TEXT_MUTED,
        anchor="w",
    )
    common_title.pack(fill="x", pady=(0, 3))
    common_row = ctk.CTkFrame(footer, fg_color="transparent")
    common_row.pack(fill="x", pady=(0, 7))

    check_title = ctk.CTkLabel(
        footer,
        text="检查与安装",
        font=ctk.CTkFont(family="Microsoft YaHei", size=11, weight="bold"),
        text_color=TEXT_MUTED,
        anchor="w",
    )
    check_title.pack(fill="x", pady=(0, 3))
    check_row = ctk.CTkFrame(footer, fg_color="transparent")
    check_row.pack(fill="x", pady=(0, 7))

    more_expanded = {"value": False}
    more_toggle = ctk.CTkButton(
        footer,
        text="更多工具  ▸",
        width=150,
        height=28,
        anchor="w",
        command=lambda: toggle_more_tools(),
        fg_color="transparent",
        hover_color="#1F2937",
        text_color=TEXT_MUTED,
    )
    more_toggle.pack(anchor="w", pady=(0, 3))
    more_row = ctk.CTkFrame(footer, fg_color="transparent")

    download_busy = {"value": False}
    action_busy = {"value": False}
    engine_probe_busy = {"value": False}
    engine_resource_state = {"kind": "checking", "detail": "正在读取发布清单…"}
    engine_button_ref: dict[str, ctk.CTkButton | None] = {"button": None}
    manager_closed = threading.Event()
    active_monitor_stop: dict[str, threading.Event | None] = {"event": None}

    def ui_alive() -> bool:
        if manager_closed.is_set():
            return False
        try:
            return bool(manager.winfo_exists())
        except Exception:
            return False

    def safe_ui(callback) -> None:
        try:
            if ui_alive():
                callback()
        except (TclError, RuntimeError):
            pass

    def safe_after(callback) -> None:
        try:
            if ui_alive():
                manager.after(0, lambda: safe_ui(callback))
        except (TclError, RuntimeError):
            pass

    def set_engine_resource_state(kind: str, detail: str) -> None:
        engine_resource_state["kind"] = kind
        engine_resource_state["detail"] = detail
        if kind == "available":
            label = "引擎资源可用"
            color = ACCENT_GREEN
            button_text = "安装/修复引擎"
            button_state = "normal"
        elif kind == "unavailable":
            label = "引擎资源未发布"
            color = "#F59E0B"
            button_text = "资源未发布"
            button_state = "disabled"
        elif kind == "error":
            label = "暂时无法读取引擎资源"
            color = "#F59E0B"
            button_text = "重新检查资源"
            button_state = "disabled"
        else:
            label = "正在检查引擎资源"
            color = TEXT_HINT
            button_text = "检查中…"
            button_state = "disabled"

        panel_border = {
            "available": "#1B7A4A",
            "unavailable": "#7A5A24",
            "error": "#7A5A24",
        }.get(kind, BORDER)
        engine_resource_row.configure(border_color=panel_border)
        engine_resource_indicator.configure(text_color=color)
        engine_resource_label.configure(text=label, text_color=TEXT_TITLE)
        engine_resource_detail.configure(text=detail)
        engine_resource_retry.configure(
            state="disabled" if kind == "checking" else "normal",
            text="检查中…" if kind == "checking" else "重新检查",
        )
        button = engine_button_ref.get("button")
        if button is not None:
            button.configure(
                text=button_text,
                state=button_state,
                fg_color=ACCENT_GREEN if kind == "available" else "#1F2937",
                text_color="#000000" if kind == "available" else TEXT_HINT,
            )

    def check_engine_resources() -> None:
        if engine_probe_busy["value"]:
            return
        if download_busy["value"] or action_busy["value"]:
            messagebox.showinfo("正在忙碌", "已有下载或检查任务正在进行，请稍后再检查引擎资源。", parent=manager)
            return

        engine_probe_busy["value"] = True
        set_engine_resource_state("checking", "正在读取发布清单…")
        status_label.configure(text="正在检查引擎资源…")

        def worker() -> None:
            try:
                manifest = fetch_manifest()
                size_text = f" · 总量约 {format_bytes(manifest.total_size)}" if manifest.total_size else ""
                detail = f"清单 {manifest.version} 已读取{size_text}，可以安装或修复外置引擎。"
                safe_after(lambda: set_engine_resource_state("available", detail))
                safe_after(lambda: status_label.configure(text="引擎资源检查完成"))
            except EngineInstallError as exc:
                error_text = str(exc)
                status_code = getattr(exc, "status_code", None)
                if status_code == 404 or "404" in error_text:
                    detail = (
                        "当前发布没有引擎清单（HTTP 404），本地已有引擎仍可继续使用；"
                        "发布资源后可重新检查。"
                    )
                    safe_after(lambda: set_engine_resource_state("unavailable", detail))
                    safe_after(lambda: status_label.configure(text="引擎资源尚未发布"))
                else:
                    detail = f"{error_text}。请检查网络或代理后重试。"
                    safe_after(lambda: set_engine_resource_state("error", detail))
                    safe_after(lambda: status_label.configure(text="引擎资源检查失败"))
            except Exception as exc:
                error_text = f"{exc}。请检查网络或代理后重试。"
                safe_after(lambda: set_engine_resource_state("error", error_text))
                safe_after(lambda: status_label.configure(text="引擎资源检查失败"))
            finally:
                engine_probe_busy["value"] = False

        threading.Thread(target=worker, daemon=True).start()

    def toggle_more_tools() -> None:
        expanded = not more_expanded["value"]
        more_expanded["value"] = expanded
        if expanded:
            more_row.pack(fill="x", pady=(0, 3))
            more_toggle.configure(text="更多工具  ▾")
        else:
            more_row.pack_forget()
            more_toggle.configure(text="更多工具  ▸")

    def close_manager() -> None:
        manager_closed.set()
        monitor_stop = active_monitor_stop.get("event")
        if monitor_stop is not None:
            monitor_stop.set()
        try:
            manager.grab_release()
        except (TclError, RuntimeError):
            pass
        try:
            manager.destroy()
        except (TclError, RuntimeError):
            pass

    manager.protocol("WM_DELETE_WINDOW", close_manager)

    def refresh() -> None:
        for child in rows.winfo_children():
            child.destroy()
        for item in scan_models(model_root):
            row = ctk.CTkFrame(rows, fg_color="#111318", corner_radius=10)
            row.pack(fill="x", padx=10, pady=6)

            state_color = {
                "完整": ACCENT_GREEN,
                "未完整": "#F59E0B",
                "未安装": TEXT_HINT,
            }.get(item.state, TEXT_HINT)
            snapshot_text = str(item.snapshot) if item.snapshot else "未检测到完整 snapshot"

            ctk.CTkLabel(
                row,
                text=item.name,
                width=130,
                anchor="w",
                font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"),
                text_color=TEXT_TITLE,
            ).pack(side="left", padx=(14, 8), pady=12)
            ctk.CTkLabel(
                row,
                text=item.state,
                width=80,
                anchor="w",
                font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
                text_color=state_color,
            ).pack(side="left", padx=8)
            ctk.CTkLabel(
                row,
                text=snapshot_text,
                anchor="w",
                font=ctk.CTkFont(family="Consolas", size=11),
                text_color=TEXT_MUTED,
                wraplength=420,
            ).pack(side="left", fill="x", expand=True, padx=8)

            if item.state == "完整":
                ctk.CTkButton(
                    row,
                    text="已就绪",
                    width=86,
                    state="disabled",
                    fg_color="#1F2937",
                    text_color=TEXT_HINT,
                ).pack(side="right", padx=(8, 14), pady=12)
            else:
                ctk.CTkButton(
                    row,
                    text="下载/修复",
                    width=86,
                    command=lambda name=item.name: start_download(name),
                    fg_color=ACCENT_GREEN,
                    text_color="#000000",
                ).pack(side="right", padx=(8, 14), pady=12)
        status_label.configure(text="扫描完成")

    def open_model_dir() -> None:
        model_root.mkdir(parents=True, exist_ok=True)
        os.startfile(model_root)

    def open_cache_dir() -> None:
        hf_cache_root.mkdir(parents=True, exist_ok=True)
        os.startfile(hf_cache_root)

    def open_user_cache_dir() -> None:
        if not user_hf_cache.exists():
            messagebox.showinfo("旧缓存目录", "未检测到用户目录下的 HuggingFace 缓存。", parent=manager)
            return
        os.startfile(user_hf_cache)

    def install_runtime_engine() -> None:
        if download_busy["value"] or action_busy["value"]:
            messagebox.showinfo("正在忙碌", "已有下载或检查任务正在进行，请稍后再试。", parent=manager)
            return
        resource_kind = engine_resource_state.get("kind")
        if resource_kind == "checking":
            messagebox.showinfo("正在检查", "正在确认引擎发布资源，请稍等片刻后再试。", parent=manager)
            return
        if resource_kind == "unavailable":
            messagebox.showinfo(
                "引擎资源未发布",
                "当前版本的外置引擎清单尚未发布（HTTP 404）。\n\n"
                "这不会影响已经安装的本地引擎；发布资源后点击“重新检查”即可。",
                parent=manager,
            )
            return
        if resource_kind != "available":
            check_engine_resources()
            return
        action = "修复/更新" if engine_ready else "安装"
        if not messagebox.askyesno(
            f"{action} Next 引擎",
            "软件将从已配置的发布清单下载并校验外置 AI 引擎。\n\n"
            "引擎可能占用数 GB 磁盘空间，下载完成后需要重启软件。\n"
            f"下载清单：{manifest_url()}\n\n是否开始？",
            parent=manager,
        ):
            return

        download_busy["value"] = True
        status_label.configure(text="正在读取引擎下载清单...")
        download_progress.set(0)

        def report(text: str, ratio: float) -> None:
            safe_after(lambda: (status_label.configure(text=text), download_progress.set(ratio)))

        def worker() -> None:
            try:
                manifest = fetch_manifest()
                if manifest.total_size:
                    report(f"已读取引擎清单：约 {format_bytes(manifest.total_size)}", 0.01)
                install_engine(manifest, progress_cb=report)
                safe_after(lambda: status_label.configure(text=f"引擎 {manifest.version} 安装完成，请重启软件"))
                safe_after(
                    lambda: messagebox.showinfo(
                        "引擎安装完成",
                        f"Next 引擎 {manifest.version} 已安装。请关闭并重新打开 UtaSyncNext 后再开始任务。",
                        parent=manager,
                    )
                )
            except EngineInstallError as exc:
                error_text = str(exc)
                status_code = getattr(exc, "status_code", None)
                if status_code == 404 or "404" in error_text:
                    detail = "引擎资源尚未发布（HTTP 404），请稍后重新检查。"
                    safe_after(lambda: set_engine_resource_state("unavailable", detail))
                    safe_after(lambda: status_label.configure(text="引擎资源尚未发布"))
                    safe_after(
                        lambda: messagebox.showinfo(
                            "引擎资源未发布",
                            "当前发布没有可下载的引擎清单，软件没有改动现有引擎。",
                            parent=manager,
                        )
                    )
                else:
                    safe_after(lambda: status_label.configure(text="引擎安装失败"))
                    safe_after(lambda text=error_text: messagebox.showerror("引擎安装失败", text, parent=manager))
            except Exception as exc:
                error_text = f"未知错误：{exc}"
                safe_after(lambda: status_label.configure(text="引擎安装失败"))
                safe_after(lambda text=error_text: messagebox.showerror("引擎安装失败", text, parent=manager))
            finally:
                download_busy["value"] = False

        threading.Thread(target=worker, daemon=True).start()

    def import_model() -> None:
        source = filedialog.askdirectory(
            title="选择完整 faster-whisper 缓存目录或 snapshot 目录",
            initialdir=str(nearby_source_models or model_root),
            parent=manager,
        )
        if not source:
            return
        source_path = Path(source)
        model_name = guess_model_name(source_path)
        if not model_name:
            messagebox.showwarning(
                "无法识别模型",
                "请选择名称中包含 large-v2 / large-v3 / small / base 等字样的模型缓存目录。",
                parent=manager,
            )
            return

        target_cache = model_root / target_cache_dir_name(source_path, model_name)
        status_label.configure(text=f"正在导入 {model_name}，大模型文件较大，请稍等...")

        def worker() -> None:
            try:
                if is_complete_snapshot(source_path):
                    target_snapshot = target_cache / "snapshots" / source_path.name
                    shutil.copytree(source_path, target_snapshot, dirs_exist_ok=True)
                else:
                    shutil.copytree(source_path, target_cache, dirs_exist_ok=True)
                safe_after(lambda: status_label.configure(text=f"{model_name} 导入完成"))
                safe_after(refresh)
            except Exception as exc:
                error_text = str(exc)
                safe_after(lambda: status_label.configure(text="导入失败"))
                safe_after(lambda text=error_text: messagebox.showerror("导入失败", text, parent=manager))

        threading.Thread(target=worker, daemon=True).start()

    def start_download(model_name: str) -> None:
        if download_busy["value"]:
            messagebox.showinfo("正在下载", "已有模型下载任务正在进行，请等待完成。", parent=manager)
            return
        source_name = source_var.get()
        custom_endpoint = custom_endpoint_entry.get().strip()
        proxy_url = proxy_entry.get().strip()
        persist_model_proxy(parent, proxy_url)
        proxy_text = proxy_preview_text(proxy_url)
        if not messagebox.askyesno(
            "下载模型",
            f"将通过「{source_name}」下载 {model_name} 到 Next 独立模型目录。{proxy_text}\n\n"
            "模型文件较大，网络不稳定时可以关闭后重新点击，缓存会尽量断点续传。\n\n"
            "是否开始下载？",
            parent=manager,
        ):
            return

        download_busy["value"] = True
        status_label.configure(text=f"正在下载/修复 {model_name}...")
        download_progress.set(0)
        monitor_stop = threading.Event()
        active_monitor_stop["event"] = monitor_stop
        download_state: dict[str, int | None] = {"total_size": None}

        def report(text: str) -> None:
            safe_after(lambda: status_label.configure(text=text))

        def monitor_download() -> None:
            last_size = model_download_size(model_root, model_name)
            last_time = time.monotonic()
            while not monitor_stop.wait(1.0):
                if not ui_alive():
                    break
                current_size = model_download_size(model_root, model_name)
                current_time = time.monotonic()
                elapsed = max(current_time - last_time, 0.001)
                speed = max(current_size - last_size, 0) / elapsed
                total_size = download_state.get("total_size")

                if isinstance(total_size, int) and total_size > 0:
                    ratio = max(0.0, min(current_size / total_size, 0.999))
                    label = (
                        f"正在下载 {model_name} | {format_bytes(current_size)} / {format_bytes(total_size)} "
                        f"| {ratio * 100:.1f}% | {format_speed(speed)}"
                    )
                else:
                    ratio = 0.0
                    label = f"正在下载 {model_name} | 已缓存 {format_bytes(current_size)} | {format_speed(speed)}"

                safe_after(
                    lambda progress=ratio, text=label: (
                        download_progress.set(progress),
                        status_label.configure(text=text),
                    )
                )
                last_size = current_size
                last_time = current_time

        def worker() -> None:
            try:
                try:
                    preflight = preflight_model_download(
                        model_name,
                        source_name=source_name,
                        custom_endpoint=custom_endpoint,
                        proxy_url=proxy_url,
                    )
                    download_state["total_size"] = preflight.total_size
                    if preflight.total_size:
                        report(f"准备下载 {model_name} | 总量约 {format_bytes(preflight.total_size)}")
                    else:
                        report(f"准备下载 {model_name} | 已获取文件清单，共 {preflight.file_count} 个文件")
                except ModelDownloadError as exc:
                    report(f"无法预估总大小，继续直接下载：{exc}")

                download_model(
                    model_name,
                    source_name=source_name,
                    custom_endpoint=custom_endpoint,
                    proxy_url=proxy_url,
                    progress_cb=report,
                )
                safe_after(lambda: download_progress.set(1))
                safe_after(lambda: status_label.configure(text=f"{model_name} 下载完成"))
                safe_after(refresh)
            except ModelDownloadError as exc:
                error_text = str(exc)
                safe_after(lambda: status_label.configure(text="下载失败"))
                safe_after(lambda text=error_text: messagebox.showerror("下载失败", text, parent=manager))
            except Exception as exc:
                error_text = f"未知错误：{exc}"
                safe_after(lambda: status_label.configure(text="下载失败"))
                safe_after(lambda text=error_text: messagebox.showerror("下载失败", text, parent=manager))
            finally:
                monitor_stop.set()
                if active_monitor_stop.get("event") is monitor_stop:
                    active_monitor_stop["event"] = None
                download_busy["value"] = False
                if not download_state.get("total_size"):
                    safe_after(lambda: download_progress.set(0))

        threading.Thread(target=monitor_download, daemon=True).start()
        threading.Thread(target=worker, daemon=True).start()

    def run_health_check() -> None:
        if download_busy["value"] or action_busy["value"]:
            messagebox.showinfo("正在忙碌", "当前已有任务在运行，请稍后再执行本机体检。", parent=manager)
            return

        action_busy["value"] = True
        status_label.configure(text="正在执行本机体检...")
        download_progress.set(0.15)

        def worker() -> None:
            started = time.monotonic()
            command = [
                sys.executable,
                "tools/verify_next_stack.py",
                "--profile",
                "health",
                "--json-report",
                "test_logs/health.json",
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=app_root(),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=120,
                )
                elapsed = time.monotonic() - started
                output = tail_text(completed.stdout, 1800)
                if completed.returncode == 0:
                    safe_after(lambda: download_progress.set(1))
                    safe_after(lambda: status_label.configure(text=f"本机体检通过，用时 {elapsed:.1f}s"))
                    safe_after(lambda: messagebox.showinfo("本机体检通过", output or "health profile passed.", parent=manager))
                else:
                    safe_after(lambda: download_progress.set(0))
                    safe_after(lambda: status_label.configure(text="本机体检失败"))
                    safe_after(lambda: messagebox.showerror("本机体检失败", output, parent=manager))
            except subprocess.TimeoutExpired as exc:
                output = tail_text(exc.stdout or "", 1800)
                safe_after(lambda: download_progress.set(0))
                safe_after(lambda: status_label.configure(text="本机体检超时"))
                safe_after(lambda: messagebox.showerror("本机体检超时", output or "health check timed out.", parent=manager))
            except Exception as exc:
                error_text = str(exc)
                safe_after(lambda: download_progress.set(0))
                safe_after(lambda: status_label.configure(text="本机体检失败"))
                safe_after(lambda: messagebox.showerror("本机体检失败", error_text, parent=manager))
            finally:
                action_busy["value"] = False

        threading.Thread(target=worker, daemon=True).start()

    def run_model_source_check() -> None:
        if download_busy["value"] or action_busy["value"]:
            messagebox.showinfo("正在忙碌", "当前已有任务在运行，请稍后再执行模型源巡检。", parent=manager)
            return

        action_busy["value"] = True
        status_label.configure(text="正在巡检官方源和镜像源文件清单...")
        download_progress.set(0.1)

        def worker() -> None:
            try:
                payload = check_model_sources(
                    proxy_url=proxy_entry.get().strip(),
                    report_path=app_root() / "test_logs" / "model_sources-latest.json",
                )
                summary = format_source_check_report(payload)
                if payload.get("ok"):
                    safe_after(lambda: download_progress.set(1))
                    safe_after(lambda: status_label.configure(text="模型源巡检通过"))
                    safe_after(lambda: messagebox.showinfo("模型源巡检通过", summary, parent=manager))
                else:
                    safe_after(lambda: download_progress.set(0))
                    safe_after(lambda: status_label.configure(text="模型源巡检发现错误"))
                    safe_after(lambda: messagebox.showerror("模型源巡检发现错误", summary, parent=manager))
            except Exception as exc:
                error_text = str(exc)
                safe_after(lambda: download_progress.set(0))
                safe_after(lambda: status_label.configure(text="模型源巡检失败"))
                safe_after(lambda: messagebox.showerror("模型源巡检失败", error_text, parent=manager))
            finally:
                action_busy["value"] = False

        threading.Thread(target=worker, daemon=True).start()

    def show_gpu_upgrade_guide() -> None:
        proxy_value = proxy_entry.get().strip() or "10808"
        prepare_cmd = (
            "powershell -ExecutionPolicy Bypass -File tools\\prepare_engine_gpu_wheels.ps1 "
            f"-Proxy {proxy_value} -Scope TorchCuda"
        )
        install_cmd = "powershell -ExecutionPolicy Bypass -File tools\\install_engine_gpu_wheels.ps1 -Scope TorchCuda"
        check_cmd = ".venv\\Scripts\\python.exe tools\\check_engine_runtime.py --deep"
        guide = (
            "GPU 引擎升级建议分两步走，避免再次卡死电脑：\n\n"
            "1. 先只下载 CUDA wheel 到 .wheelhouse，不修改 .engine：\n"
            f"{prepare_cmd}\n\n"
            "2. 下载完整后，再从本地 wheelhouse 安装到 Next .engine：\n"
            f"{install_cmd}\n\n"
            "3. 最后深度检查 CUDA 是否可用：\n"
            f"{check_cmd}\n\n"
            "这三个命令都只作用于 UtaSyncNext，不会修改 V3。"
        )

        dialog = ctk.CTkToplevel(manager)
        dialog.title("GPU 引擎安全升级")
        dialog.geometry("720x430")
        dialog.configure(fg_color=BG_WORKSPACE)
        dialog.transient(manager)
        dialog.grab_set()

        frame = ctk.CTkFrame(dialog, fg_color=BG_CARD, border_color=BORDER, border_width=1, corner_radius=14)
        frame.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(
            frame,
            text="GPU 引擎安全升级",
            font=ctk.CTkFont(family="Microsoft YaHei", size=20, weight="bold"),
            text_color=TEXT_TITLE,
        ).pack(anchor="w", padx=20, pady=(18, 8))

        textbox = ctk.CTkTextbox(
            frame,
            height=250,
            fg_color="#07080B",
            border_color=BORDER,
            border_width=1,
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=TEXT_MUTED,
        )
        textbox.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        textbox.insert("1.0", guide)
        textbox.configure(state="disabled")

        button_bar = ctk.CTkFrame(frame, fg_color="transparent")
        button_bar.pack(fill="x", padx=20, pady=(0, 18))

        def copy_commands() -> None:
            manager.clipboard_clear()
            manager.clipboard_append(f"cd /d {app_root()}\n{prepare_cmd}\n{install_cmd}\n{check_cmd}")
            status_label.configure(text="GPU 升级命令已复制")

        ctk.CTkButton(
            button_bar,
            text="复制命令",
            command=copy_commands,
            fg_color=ACCENT_GREEN,
            text_color="#000000",
            width=110,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(button_bar, text="关闭", command=dialog.destroy, fg_color="#1F2937", width=90).pack(
            side="right", padx=(8, 0)
        )

    def add_action_button(
        parent,
        *,
        text: str,
        width: int,
        command,
        primary: bool = False,
        state: str = "normal",
    ) -> ctk.CTkButton:
        button = ctk.CTkButton(
            parent,
            text=text,
            width=width,
            height=34,
            command=command,
            state=state,
            fg_color=ACCENT_GREEN if primary else "#1F2937",
            text_color="#000000" if primary else TEXT_TITLE,
        )
        button.pack(side="left", padx=(0, 6))
        return button

    add_action_button(common_row, text="导入本地模型", width=118, command=import_model, primary=True)
    add_action_button(common_row, text="刷新", width=80, command=refresh)
    add_action_button(common_row, text="打开模型目录", width=110, command=open_model_dir)
    add_action_button(common_row, text="打开缓存目录", width=110, command=open_cache_dir)

    add_action_button(check_row, text="模型源巡检", width=94, command=run_model_source_check)
    add_action_button(check_row, text="本机体检", width=84, command=run_health_check)
    engine_button = add_action_button(
        check_row,
        text="检查中…",
        width=110,
        command=install_runtime_engine,
        state="disabled",
    )
    engine_button_ref["button"] = engine_button

    add_action_button(more_row, text="旧缓存位置", width=94, command=open_user_cache_dir)
    add_action_button(more_row, text="GPU 升级指南", width=100, command=show_gpu_upgrade_guide)

    refresh()
    check_engine_resources()
