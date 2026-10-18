from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from engine.cache_env import app_root
from utils.network_proxy import build_urllib_opener


DEFAULT_MANIFEST_URL = (
    "https://github.com/zzzzzezzz/UtaSyncNext/releases/latest/download/"
    "utasyncnext-engine-manifest.json"
)


class EngineInstallError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class EngineAsset:
    name: str
    url: str
    sha256: str
    size: int | None = None


@dataclass(frozen=True)
class EngineManifest:
    version: str
    assets: tuple[EngineAsset, ...]
    total_size: int | None
    source_url: str


def manifest_url() -> str:
    return os.environ.get("UTASYNCNEXT_ENGINE_MANIFEST_URL", "").strip() or DEFAULT_MANIFEST_URL


def fetch_manifest(url: str | None = None, timeout: int = 30, proxy_url: str = "") -> EngineManifest:
    source_url = url or manifest_url()
    request = urllib.request.Request(source_url, headers={"User-Agent": "UtaSyncNext/engine-installer"})
    try:
        with _open_url(request, timeout=timeout, proxy_url=proxy_url) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise EngineInstallError(
            f"无法读取引擎下载清单：HTTP {exc.code} {exc.reason}",
            status_code=exc.code,
        ) from exc
    except Exception as exc:
        raise EngineInstallError(f"无法读取引擎下载清单：{exc}") from exc

    if not isinstance(payload, dict):
        raise EngineInstallError("引擎下载清单格式无效。")
    version = str(payload.get("version", "")).strip()
    raw_assets = payload.get("assets")
    if not version or not isinstance(raw_assets, list) or not raw_assets:
        raise EngineInstallError("引擎下载清单缺少 version 或 assets。")

    assets: list[EngineAsset] = []
    for raw in raw_assets:
        if not isinstance(raw, dict):
            raise EngineInstallError("引擎下载清单包含无效资产。")
        name = str(raw.get("name", "")).strip()
        asset_url = str(raw.get("url", "")).strip()
        digest = str(raw.get("sha256", "")).strip().lower()
        size = raw.get("size")
        if not name or not _safe_asset_name(name) or not asset_url or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise EngineInstallError(f"引擎资产信息无效：{name or '<unnamed>'}")
        if not isinstance(size, int) or size <= 0:
            size = None
        assets.append(EngineAsset(name=name, url=asset_url, sha256=digest, size=size))

    declared_total = payload.get("total_size")
    total_size = declared_total if isinstance(declared_total, int) and declared_total > 0 else None
    if total_size is None and all(asset.size for asset in assets):
        total_size = sum(asset.size or 0 for asset in assets)
    return EngineManifest(version=version, assets=tuple(assets), total_size=total_size, source_url=source_url)


def install_engine(
    manifest: EngineManifest,
    progress_cb: Callable[[str, float], None] | None = None,
    target_root: Path | None = None,
    proxy_url: str = "",
) -> Path:
    """Download verified engine archives and atomically replace the local engine.

    Each manifest asset is a ZIP whose contents are relative to the `.engine`
    directory. Multiple assets let a release stay below hosting limits.
    """
    root = target_root or (app_root() / ".engine")
    root = root.resolve()
    install_root = root.parent / f"{root.name}.installing"
    backup_root = root.parent / f"{root.name}.backup"
    download_root = app_root() / ".cache" / "engine_downloads"
    install_root = install_root.resolve()
    backup_root = backup_root.resolve()
    download_root.mkdir(parents=True, exist_ok=True)
    _assert_child(root.parent, install_root)
    _assert_child(root.parent, backup_root)

    if install_root.exists():
        shutil.rmtree(install_root)
    install_root.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    try:
        total = manifest.total_size or sum(asset.size or 0 for asset in manifest.assets) or None
        completed = 0
        for index, asset in enumerate(manifest.assets, start=1):
            archive = download_root / asset.name
            _download_asset(
                asset,
                archive,
                progress_cb,
                completed,
                total,
                index,
                len(manifest.assets),
                proxy_url=proxy_url,
            )
            _verify_sha256(archive, asset.sha256)
            _extract_archive(archive, install_root)
            completed += asset.size or archive.stat().st_size
            if progress_cb:
                progress_cb(f"已校验引擎分卷 {index}/{len(manifest.assets)}：{asset.name}", _ratio(completed, total))
            downloaded.append(archive)

        if not (install_root / "Scripts").exists() or not (install_root / "Lib" / "site-packages").exists():
            raise EngineInstallError("引擎包解压完成，但目录结构不完整。")
        portable_root = install_root / "python-runtime"
        if portable_root.exists() and not (
            (portable_root / "python.exe").is_file() and (portable_root / "python._pth").is_file()
        ):
            raise EngineInstallError("引擎包包含便携 Python 目录，但运行时文件不完整。")

        if backup_root.exists():
            shutil.rmtree(backup_root)
        if root.exists():
            root.rename(backup_root)
        install_root.rename(root)
        if backup_root.exists():
            shutil.rmtree(backup_root)
        if progress_cb:
            progress_cb(f"引擎 {manifest.version} 安装完成，重启软件后生效。", 1.0)
        return root
    except Exception as exc:
        if install_root.exists():
            shutil.rmtree(install_root, ignore_errors=True)
        if backup_root.exists() and not root.exists():
            backup_root.rename(root)
        if isinstance(exc, EngineInstallError):
            raise
        raise EngineInstallError(f"引擎安装失败：{exc}") from exc


def _download_asset(
    asset: EngineAsset,
    destination: Path,
    progress_cb: Callable[[str, float], None] | None,
    completed: int,
    total: int | None,
    index: int,
    count: int,
    proxy_url: str = "",
) -> None:
    if not _safe_asset_name(asset.name):
        raise EngineInstallError(f"引擎资产文件名无效：{asset.name}")
    partial = destination.with_suffix(destination.suffix + ".part")
    existing = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "UtaSyncNext/engine-installer"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    request = urllib.request.Request(asset.url, headers=headers)
    try:
        with _open_url(request, timeout=60, proxy_url=proxy_url) as response:
            status = getattr(response, "status", None)
            if existing and status != 206:
                existing = 0
                partial.unlink(missing_ok=True)
            mode = "ab" if existing else "wb"
            written = existing
            with partial.open(mode) as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    if progress_cb:
                        progress_cb(
                            f"下载引擎分卷 {index}/{count}：{asset.name} ({written / 1024 / 1024:.0f} MB)",
                            _ratio(completed + written, total),
                        )
        partial.replace(destination)
    except Exception as exc:
        raise EngineInstallError(f"下载引擎资产失败：{asset.name}：{exc}") from exc


def _open_url(request: urllib.request.Request, *, timeout: int, proxy_url: str = ""):
    """Open an installer URL with the user-selected proxy when supplied.

    Keep the empty-proxy path on ``urllib.request.urlopen`` so existing system
    proxy/environment behavior and lightweight tests remain unchanged.  A
    non-empty value uses the same normalization rules as model downloads.
    """
    if not str(proxy_url or "").strip():
        return urllib.request.urlopen(request, timeout=timeout)
    opener = build_urllib_opener(proxy_url)
    return opener.open(request, timeout=timeout)


def _verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest().lower()
    if actual != expected.lower():
        raise EngineInstallError(f"引擎资产校验失败：{path.name}（期望 {expected}，实际 {actual}）")


def _extract_archive(archive: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as zipped:
            for member in zipped.infolist():
                relative = PurePosixPath(member.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise EngineInstallError(f"引擎压缩包包含越界路径：{member.filename}")
                target = (destination / Path(*relative.parts)).resolve()
                _assert_child(destination.resolve(), target)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(member, "r") as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except zipfile.BadZipFile as exc:
        raise EngineInstallError(f"引擎资产不是有效 ZIP：{archive.name}") from exc


def _assert_child(parent: Path, child: Path) -> None:
    parent_full = parent.resolve()
    child_full = child.resolve()
    if child_full == parent_full or parent_full not in child_full.parents:
        raise EngineInstallError(f"拒绝使用工作目录外路径：{child_full}")


def _safe_asset_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and "\\" not in name
        and len(path.parts) == 1
        and path.name == name
        and path.suffix.lower() == ".zip"
    )


def _ratio(current: int, total: int | None) -> float:
    if not total or total <= 0:
        return 0.0
    return max(0.0, min(current / total, 0.999))
