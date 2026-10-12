from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


# Short-video exports keep absurdly long file names, and each task writes a
# whole family of derived files next to each other. Once the combined path
# passes the Windows limit, writes fail with a misleading "No such file or
# directory". Oversized names are shortened with a stable hash suffix so the
# same media always maps to the same task folder.
MAX_TASK_NAME_LENGTH = 60


def app_root() -> Path:
    override = os.environ.get("UTASYNCNEXT_APP_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def task_base_name(media_path: str | os.PathLike[str]) -> str:
    """Return the on-disk task name used for output folders and file prefixes."""
    stem = Path(media_path).stem.strip()
    if not stem:
        return "general"
    if len(stem) <= MAX_TASK_NAME_LENGTH:
        return stem
    digest = hashlib.md5(stem.encode("utf-8")).hexdigest()[:8]
    return f"{stem[: MAX_TASK_NAME_LENGTH - len(digest) - 1]}_{digest}"


def next_cache_root() -> Path:
    return app_root() / ".cache"


def huggingface_cache_root() -> Path:
    return next_cache_root() / "huggingface"


def apply_next_cache_env() -> dict[str, str]:
    """Keep HuggingFace-related caches inside UtaSyncNext instead of the user profile."""
    hf_home = huggingface_cache_root()
    hub_cache = hf_home / "hub"
    xdg_cache = next_cache_root()

    hf_home.mkdir(parents=True, exist_ok=True)
    hub_cache.mkdir(parents=True, exist_ok=True)

    values = {
        "HF_HOME": str(hf_home),
        "HUGGINGFACE_HUB_CACHE": str(hub_cache),
        "HF_HUB_CACHE": str(hub_cache),
        "TRANSFORMERS_CACHE": str(hf_home / "transformers"),
        "XDG_CACHE_HOME": str(xdg_cache),
        "HF_HUB_DISABLE_XET": "1",
    }
    for key, value in values.items():
        os.environ[key] = value
    return values
