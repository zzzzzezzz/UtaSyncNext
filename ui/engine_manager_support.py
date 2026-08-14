from __future__ import annotations

import socket
from pathlib import Path

from engine.model_downloader import ModelDownloadError
from engine.model_downloader import _normalize_proxy_url
from engine.model_registry import MODEL_ORDER
from utils.config import save_settings


def guess_model_name(path: Path) -> str | None:
    haystack = " ".join(part.lower() for part in path.parts)
    for model_name in sorted(MODEL_ORDER, key=len, reverse=True):
        if model_name in haystack or f"faster-whisper-{model_name}" in haystack:
            return model_name
    return None


def target_cache_dir_name(source_path: Path, model_name: str) -> str:
    if source_path.name.startswith("models--") and model_name in source_path.name.lower():
        return source_path.name
    return f"models--Systran--faster-whisper-{model_name}"


def model_download_size(model_root: Path, model_name: str) -> int:
    return folder_size(model_cache_root(model_root, model_name)) + folder_size(model_lock_root(model_root, model_name))


def folder_size(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def model_cache_root(model_root: Path, model_name: str) -> Path:
    return model_root / f"models--Systran--faster-whisper-{model_name}"


def model_lock_root(model_root: Path, model_name: str) -> Path:
    return model_root / ".locks" / f"models--Systran--faster-whisper-{model_name}"


def format_bytes(value: int | None) -> str:
    if not value:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def format_speed(bytes_per_second: float) -> str:
    return f"{format_bytes(int(bytes_per_second))}/s"


def tail_text(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return "...<trimmed>...\n" + text[-limit:]


def proxy_preview_text(proxy_url: str) -> str:
    if not proxy_url:
        return "\n代理：留空，使用系统/环境默认网络"
    try:
        normalized = _normalize_proxy_url(proxy_url)
    except ModelDownloadError as exc:
        return f"\n代理：{proxy_url}\n代理格式提示：{exc}"
    if normalized and normalized != proxy_url:
        return f"\n代理：{proxy_url}（等同 {normalized}）"
    return f"\n代理：{proxy_url}"


def initial_proxy_value(parent) -> str:
    settings = getattr(parent, "settings", {})
    if isinstance(settings, dict):
        for key in ("model_download_proxy", "proxy_url"):
            value = str(settings.get(key) or "").strip()
            if value:
                return value

    detected = detect_local_proxy_port()
    return detected or ""


def persist_model_proxy(parent, proxy_url: str) -> None:
    settings = getattr(parent, "settings", None)
    if not isinstance(settings, dict):
        return
    settings["model_download_proxy"] = proxy_url
    try:
        save_settings(settings)
    except Exception:
        pass


def detect_local_proxy_port() -> str | None:
    for port in (10808, 7890, 7897):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                return str(port)
        except OSError:
            continue
    return None
