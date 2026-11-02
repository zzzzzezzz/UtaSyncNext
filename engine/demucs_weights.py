"""Inspect and download the optional Demucs separation weights.

The Python packages live in the portable ``.engine`` directory, while the
large ``.th`` checkpoint files live in the app cache.  Keeping this small
helper in the application lets the packaged model manager prepare the
weights without relying on the developer's ``tools`` directory.
"""

from __future__ import annotations

import hashlib
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from engine.cache_env import app_root
from engine.locator import find_engine_location
from utils.network_proxy import build_urllib_opener


DEFAULT_MODEL = "htdemucs_ft"
DEFAULT_BASE_URL = "https://dl.fbaipublicfiles.com/demucs"
ProgressCallback = Callable[[str, float], None]


class DemucsWeightsError(RuntimeError):
    """Raised when Demucs metadata or checkpoint download is unavailable."""


@dataclass(frozen=True)
class DemucsWeightsStatus:
    model_name: str
    checkpoints_dir: Path
    required_files: tuple[str, ...]
    missing_files: tuple[str, ...]
    detail: str = ""

    @property
    def ready(self) -> bool:
        return not self.missing_files and bool(self.required_files)


def inspect_demucs_weights(model_name: str = DEFAULT_MODEL) -> DemucsWeightsStatus:
    """Return the local state without touching the network."""

    checkpoints = torch_checkpoints_dir()
    try:
        required = tuple(name for _root, name in resolve_required_weights(model_name))
    except DemucsWeightsError as exc:
        return DemucsWeightsStatus(model_name, checkpoints, (), (), str(exc))
    missing = tuple(name for name in required if not _is_valid_checkpoint(checkpoints / name))
    return DemucsWeightsStatus(model_name, checkpoints, required, missing)


def download_demucs_weights(
    *,
    model_name: str = DEFAULT_MODEL,
    proxy_url: str = "",
    base_url: str = DEFAULT_BASE_URL,
    progress_cb: ProgressCallback | None = None,
) -> DemucsWeightsStatus:
    """Download missing Demucs checkpoints into the app's local cache.

    Downloads use ``.part`` files and HTTP Range requests so closing the app
    leaves a resumable file.  The callback receives a human-readable status
    and a best-effort overall ratio.
    """

    required = resolve_required_weights(model_name)
    checkpoints = torch_checkpoints_dir()
    checkpoints.mkdir(parents=True, exist_ok=True)
    missing = [(root, name) for root, name in required if not _is_valid_checkpoint(checkpoints / name)]
    if not missing:
        return inspect_demucs_weights(model_name)

    opener = build_urllib_opener(proxy_url)
    total_files = len(required)
    completed = total_files - len(missing)
    for remote_root, file_name in missing:
        target = checkpoints / file_name
        url = f"{base_url.rstrip('/')}/{remote_root.strip('/').replace(os.sep, '/')}/{file_name}"
        _download_one(
            opener,
            url,
            target,
            completed=completed,
            total_files=total_files,
            progress_cb=progress_cb,
        )
        completed += 1

    status = inspect_demucs_weights(model_name)
    if not status.ready:
        missing_text = ", ".join(status.missing_files) or "未知文件"
        raise DemucsWeightsError(f"Demucs 权重下载后仍缺少：{missing_text}")
    return status


def resolve_required_weights(model_name: str = DEFAULT_MODEL) -> list[tuple[str, str]]:
    """Read the installed Demucs metadata to map signatures to filenames."""

    remote_dir = find_demucs_remote_dir()
    bag_path = remote_dir / f"{model_name}.yaml"
    files_path = remote_dir / "files.txt"
    if not bag_path.exists() or not files_path.exists():
        raise DemucsWeightsError("未找到 Demucs 模型清单，请先安装 Next 引擎")

    signatures = re.findall(r"['\"]([0-9a-f]{8})['\"]", bag_path.read_text(encoding="utf-8", errors="ignore"))
    if not signatures:
        raise DemucsWeightsError(f"Demucs 模型清单中没有找到 {model_name} 的权重签名")

    entries: dict[str, tuple[str, str]] = {}
    current_root = ""
    for raw_line in files_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("root:"):
            current_root = line.split(":", 1)[1].strip().strip("/")
            continue
        if line.endswith(".th") and "-" in line:
            entries[line.split("-", 1)[0]] = (current_root, line)

    missing_signatures = [signature for signature in signatures if signature not in entries]
    if missing_signatures:
        raise DemucsWeightsError(
            "Demucs 模型清单缺少权重文件：" + ", ".join(missing_signatures)
        )
    return [entries[signature] for signature in signatures]


def find_demucs_remote_dir() -> Path:
    # Use the same engine selection as the pipeline worker.  In particular,
    # UTASYNCNEXT_ENGINE_ROOT may point outside the application directory.
    location = find_engine_location()
    candidates = [location.site_packages / "demucs" / "remote"] if location.site_packages else []
    site_packages = os.environ.get("UTASYNC_SITE_PACKAGES", "").strip()
    if site_packages:
        candidates.append(Path(site_packages) / "demucs" / "remote")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise DemucsWeightsError("未找到 Demucs 运行组件，请先安装 Next 引擎")


def torch_checkpoints_dir() -> Path:
    torch_home = os.environ.get("TORCH_HOME", "").strip()
    if torch_home:
        return Path(torch_home) / "hub" / "checkpoints"
    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg_cache:
        return Path(xdg_cache) / "torch" / "hub" / "checkpoints"
    return app_root() / ".cache" / "torch" / "hub" / "checkpoints"


def _download_one(
    opener: urllib.request.OpenerDirector,
    url: str,
    target: Path,
    *,
    completed: int,
    total_files: int,
    progress_cb: ProgressCallback | None,
) -> None:
    part = target.with_suffix(target.suffix + ".part")
    resume_from = part.stat().st_size if part.exists() else 0
    if resume_from and _is_valid_checkpoint(part, expected_name=target.name):
        os.replace(part, target)
        if progress_cb:
            progress_cb(f"Demucs 权重已下载：{target.name}", (completed + 1) / total_files)
        return
    headers = {"User-Agent": "UtaSyncNext/DemucsWeights"}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(request, timeout=60) as response:
            status = getattr(response, "status", 200)
            if resume_from and status != 206:
                resume_from = 0
            content_length = int(response.headers.get("Content-Length") or 0)
            total_size = resume_from + content_length if content_length else 0
            written = resume_from
            mode = "ab" if resume_from else "wb"
            with part.open(mode) as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    written += len(chunk)
                    if progress_cb:
                        ratio = (completed + (written / total_size if total_size else 0.0)) / total_files
                        progress_cb(
                            f"正在下载 Demucs 权重：{target.name}（{written / 1024 / 1024:.0f} MB）",
                            max(0.0, min(ratio, 0.99)),
                        )
            if total_size and written < total_size:
                raise OSError(f"下载不完整：{written} / {total_size} bytes")
        if not _is_valid_checkpoint(part, expected_name=target.name):
            part.unlink(missing_ok=True)
            raise DemucsWeightsError(f"Demucs 权重校验失败：{target.name}，请重试下载")
        os.replace(part, target)
        if progress_cb:
            progress_cb(f"Demucs 权重已下载：{target.name}", (completed + 1) / total_files)
    except (OSError, urllib.error.URLError) as exc:
        raise DemucsWeightsError(f"下载 Demucs 权重失败：{target.name}：{exc}") from exc


def _is_non_empty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _is_valid_checkpoint(path: Path, *, expected_name: str | None = None) -> bool:
    if not _is_non_empty_file(path):
        return False
    match = re.fullmatch(r"[0-9a-f]{8}-([0-9a-f]{8})\.th", expected_name or path.name)
    if not match:
        return True
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest().startswith(match.group(1))
