from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from io import TextIOBase
from pathlib import Path
from typing import Callable, Iterator

from engine.cache_env import apply_next_cache_env
from engine.model_registry import find_complete_snapshot, next_models_root
from utils.network_proxy import ProxyConfigError, normalize_proxy_url


SOURCE_OFFICIAL = "HuggingFace 官方源"
SOURCE_MIRROR = "HF Mirror 镜像源"
SOURCE_CUSTOM = "自定义源"

MODEL_SOURCES = {
    SOURCE_OFFICIAL: None,
    SOURCE_MIRROR: "https://hf-mirror.com",
    SOURCE_CUSTOM: None,
}

MODEL_REPOS = {
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    # Systran does not publish a repository with this name. This is the
    # public CTranslate2 conversion used for faster-whisper turbo.
    "large-v3-turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
    "medium": "Systran/faster-whisper-medium",
    "small": "Systran/faster-whisper-small",
    "base": "Systran/faster-whisper-base",
}


class ModelDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelDownloadPreflight:
    model_name: str
    repo_id: str
    endpoint: str | None
    file_count: int
    total_size: int | None


def download_model(
    model_name: str,
    source_name: str = SOURCE_OFFICIAL,
    custom_endpoint: str = "",
    proxy_url: str = "",
    progress_cb: Callable[[str], None] | None = None,
) -> Path:
    repo_id = MODEL_REPOS.get(model_name)
    if not repo_id:
        raise ModelDownloadError(f"不支持的模型：{model_name}")

    endpoint = _resolve_endpoint(source_name, custom_endpoint)
    proxy = _normalize_proxy_url(proxy_url)

    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ModelDownloadError(_missing_huggingface_hub_message("在线下载模型")) from exc

    model_root = next_models_root()
    model_root.mkdir(parents=True, exist_ok=True)

    if progress_cb:
        source_label = endpoint or "https://huggingface.co"
        proxy_label = f" | proxy={proxy}" if proxy else ""
        progress_cb(f"正在连接模型仓库：{repo_id} | {source_label}{proxy_label}")

    try:
        snapshot_path = _snapshot_download(
            snapshot_download=snapshot_download,
            repo_id=repo_id,
            model_root=model_root,
            endpoint=endpoint,
            proxy=proxy,
        )
    except Exception as exc:
        if source_name == SOURCE_MIRROR:
            if progress_cb:
                progress_cb("HF Mirror 连接失败，正在自动切换 HuggingFace 官方源重试...")
            try:
                snapshot_path = _snapshot_download(
                    snapshot_download=snapshot_download,
                    repo_id=repo_id,
                    model_root=model_root,
                    endpoint=None,
                    proxy=proxy,
                )
            except Exception as official_exc:
                raise ModelDownloadError(f"模型下载失败：镜像源失败：{exc}；官方源重试也失败：{official_exc}") from official_exc
        else:
            raise ModelDownloadError(f"模型下载失败：{exc}") from exc

    verified = find_complete_snapshot(model_name, model_root)
    if not verified:
        raise ModelDownloadError("模型下载结束，但完整性校验未通过。请重试或清理半成品后再下载。")

    if progress_cb:
        progress_cb(f"{model_name} 下载完成")
    return verified or snapshot_path


def preflight_model_download(
    model_name: str,
    source_name: str = SOURCE_OFFICIAL,
    custom_endpoint: str = "",
    proxy_url: str = "",
    cache_dir: Path | None = None,
) -> ModelDownloadPreflight:
    repo_id = MODEL_REPOS.get(model_name)
    if not repo_id:
        raise ModelDownloadError(f"不支持的模型：{model_name}")

    endpoint = _resolve_endpoint(source_name, custom_endpoint)
    proxy = _normalize_proxy_url(proxy_url)

    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ModelDownloadError(_missing_huggingface_hub_message("预检模型下载")) from exc

    try:
        with _temporary_proxy_env(proxy):
            dry_run_files = snapshot_download(
                repo_id=repo_id,
                cache_dir=str(cache_dir or next_models_root()),
                endpoint=endpoint,
                local_files_only=False,
                dry_run=True,
            )
    except Exception as exc:
        raise ModelDownloadError(f"模型源预检失败：{exc}") from exc

    sizes = [_dry_run_file_size(item) for item in dry_run_files]
    known_sizes = [size for size in sizes if size is not None]
    total_size = sum(known_sizes) if len(known_sizes) == len(sizes) else None
    return ModelDownloadPreflight(
        model_name=model_name,
        repo_id=repo_id,
        endpoint=endpoint,
        file_count=len(dry_run_files),
        total_size=total_size,
    )


def _resolve_endpoint(source_name: str, custom_endpoint: str) -> str | None:
    if source_name == SOURCE_CUSTOM:
        endpoint = custom_endpoint.strip().rstrip("/")
        if not endpoint:
            raise ModelDownloadError("已选择自定义源，但没有填写 endpoint。")
        if not endpoint.startswith(("http://", "https://")):
            raise ModelDownloadError("自定义源必须以 http:// 或 https:// 开头。")
        return endpoint
    return MODEL_SOURCES.get(source_name)


def _normalize_proxy_url(proxy_url: str) -> str | None:
    try:
        return normalize_proxy_url(proxy_url) or None
    except ProxyConfigError as exc:
        raise ModelDownloadError(str(exc)) from exc


def _missing_huggingface_hub_message(action: str) -> str:
    return (
        f"缺少 huggingface_hub，无法{action}。\n\n"
        f"当前 Python：{sys.executable}\n"
        "请在 Next 独立环境中安装依赖：\n"
        "powershell -ExecutionPolicy Bypass -File UtaSyncNext\\tools\\bootstrap_venv.ps1\n\n"
        "如果已经创建过 .venv，也可以运行：\n"
        "UtaSyncNext\\.venv\\Scripts\\python.exe -m pip install -r UtaSyncNext\\requirements-ui.txt"
    )


def _snapshot_download(
    snapshot_download,
    repo_id: str,
    model_root: Path,
    endpoint: str | None,
    proxy: str | None,
) -> Path:
    with _temporary_proxy_env(proxy):
        return Path(
            snapshot_download(
                repo_id=repo_id,
                cache_dir=str(model_root),
                endpoint=endpoint,
                local_files_only=False,
                max_workers=2,
            )
        )


@contextmanager
def _temporary_proxy_env(proxy_url: str | None) -> Iterator[None]:
    keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "HF_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "XDG_CACHE_HOME",
    ]
    # Keep large model payloads inside the configured HuggingFace cache_dir.
    # hf-xet may stage chunks in the user-level cache first, which makes our
    # Next-local progress monitor look frozen.
    keys.extend(("HF_HUB_DISABLE_XET", "HF_HUB_DISABLE_PROGRESS_BARS"))
    previous = {key: os.environ.get(key) for key in keys}
    previous_stdout = sys.stdout
    previous_stderr = sys.stderr
    try:
        apply_next_cache_env()
        # PyInstaller's windowed bootloader sets stdout/stderr to None. The
        # Hub's tqdm/file-download path writes to stderr even when its UI is
        # hidden, which otherwise surfaces as: NoneType has no attribute write.
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        if sys.stdout is None:
            sys.stdout = _NullWriter()
        if sys.stderr is None:
            sys.stderr = _NullWriter()
        if proxy_url:
            for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
                os.environ[key] = proxy_url
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        sys.stdout = previous_stdout
        sys.stderr = previous_stderr


def _dry_run_file_size(item: object) -> int | None:
    for attr in ("file_size", "size", "expected_size"):
        value = getattr(item, attr, None)
        if isinstance(value, int):
            return value
    return None


class _NullWriter(TextIOBase):
    """File-like sink for windowed/frozen processes without console streams."""

    def write(self, text: str) -> int:
        return len(text or "")

    def flush(self) -> None:
        return None
