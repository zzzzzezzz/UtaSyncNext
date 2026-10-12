from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.cache_env import app_root


MODEL_ORDER = ["large-v2", "large-v3", "large-v3-turbo", "medium", "small", "base"]


@dataclass(frozen=True)
class ModelStatus:
    name: str
    state: str
    root: Path
    snapshot: Path | None = None


def next_models_root() -> Path:
    return app_root() / "models_faster"


def scan_models(root: Path | None = None) -> list[ModelStatus]:
    model_root = root or next_models_root()
    return [_status_for_model(model, model_root) for model in MODEL_ORDER]


def _status_for_model(model_name: str, root: Path) -> ModelStatus:
    snapshot = find_complete_snapshot(model_name, root)
    if snapshot:
        return ModelStatus(model_name, "完整", root, snapshot)
    if has_cache_dir(model_name, root):
        return ModelStatus(model_name, "未完整", root, None)
    return ModelStatus(model_name, "未安装", root, None)


def find_complete_snapshot(model_name: str, root: Path) -> Path | None:
    if not root.exists():
        return None
    for child in root.iterdir():
        if not child.is_dir() or not _matches_model(child.name, model_name):
            continue
        snapshots_dir = child / "snapshots"
        if not snapshots_dir.exists():
            continue
        for snapshot in snapshots_dir.iterdir():
            if snapshot.is_dir() and is_complete_snapshot(snapshot):
                return snapshot
    return None


def has_cache_dir(model_name: str, root: Path) -> bool:
    if not root.exists():
        return False
    return any(child.is_dir() and _matches_model(child.name, model_name) for child in root.iterdir())


def is_complete_snapshot(snapshot: Path) -> bool:
    required_files = ["config.json", "model.bin"]
    if not all((snapshot / name).exists() for name in required_files):
        return False
    tokenizers = ["tokenizer.json", "vocabulary.txt", "vocabulary.json"]
    if not any((snapshot / name).exists() for name in tokenizers):
        return False
    try:
        return (snapshot / "model.bin").stat().st_size > 1024 * 1024
    except Exception:
        return False


def _matches_model(cache_name: str, model_name: str) -> bool:
    name = cache_name.lower()
    aliases = {
        "large-v3-turbo": [
            "large-v3-turbo",
            "faster-whisper-large-v3-turbo",
            "faster-whisper-large-v3-turbo-ct2",
        ],
        "large-v3": ["large-v3", "faster-whisper-large-v3"],
        "large-v2": ["large-v2", "faster-whisper-large-v2"],
        "medium": ["medium", "faster-whisper-medium"],
        "small": ["small", "faster-whisper-small"],
        "base": ["base", "faster-whisper-base"],
    }.get(model_name, [model_name, f"faster-whisper-{model_name}"])
    normalized = name.replace("models--systran--", "")
    return any(normalized == alias or name.endswith(f"--{alias}") for alias in aliases)
