"""Persistence helpers for subtitle style presets.

Built-in presets live in :mod:`core.ass_maker`. The user file stores their edits
plus a small metadata block, because a plain merge would silently restore every
built-in preset the user deleted and undo every rename on the next launch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

PRESET_META_KEY = "__meta__"
AI_PRESET_PREFIX = "AI:"
# Every built-in used to ship with this same pair, which is why nine presets
# looked identical, and saving settings wrote a full copy of the catalog to
# disk. Those copies keep winning over the built-ins, so they are dropped.
LEGACY_FONT_PAIR = ("Microsoft YaHei", "Meiryo")


def preset_file_path() -> Path:
    """Return the app-owned preset file, next to the app (not the cwd)."""

    from engine.cache_env import app_root

    return app_root() / "user_presets.json"


def _empty_meta() -> dict[str, Any]:
    return {"removed": [], "renamed": {}}


def normalize_meta(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only well-formed tombstones and rename records."""

    if not isinstance(raw, Mapping):
        return _empty_meta()
    removed = [str(name).strip() for name in (raw.get("removed") or []) if str(name).strip()]
    renamed: dict[str, str] = {}
    raw_renamed = raw.get("renamed")
    if isinstance(raw_renamed, Mapping):
        for old, new in raw_renamed.items():
            old_name, new_name = str(old).strip(), str(new).strip()
            if old_name and new_name and old_name != new_name:
                renamed[old_name] = new_name
    return {"removed": removed, "renamed": renamed}


def _clean_presets(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Drop non-style entries and the retired AI-generated palettes."""

    return {
        str(name): dict(value)
        for name, value in data.items()
        if isinstance(value, dict) and not str(name).startswith(AI_PRESET_PREFIX)
    }


def read_preset_document(path: Path | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return ``(saved_presets, meta)``; a missing or broken file reads as empty."""

    source = path or preset_file_path()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}, _empty_meta()
    if not isinstance(data, Mapping):
        return {}, _empty_meta()
    saved = _clean_presets({name: value for name, value in data.items() if name != PRESET_META_KEY})
    return saved, normalize_meta(data.get(PRESET_META_KEY))


def merge_presets(
    defaults: Mapping[str, dict[str, Any]],
    saved: Mapping[str, dict[str, Any]],
    meta: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Layer renames, deletions and user edits on the pristine built-ins."""

    merged = {str(name): dict(config) for name, config in defaults.items()}
    for old, new in (meta.get("renamed") or {}).items():
        if old in merged and new not in merged:
            merged[new] = merged.pop(old)
    for name in meta.get("removed") or []:
        merged.pop(str(name), None)
    merged.update(
        {
            str(name): dict(config)
            for name, config in saved.items()
            if not (name in defaults and is_legacy_builtin_snapshot(config, defaults[name]))
        }
    )
    return merged


def is_legacy_builtin_snapshot(config: Mapping[str, Any], default: Mapping[str, Any]) -> bool:
    """True when a saved entry is an old, font-identical copy of a built-in.

    Distinguishing them matters: the pre-Next catalog shipped every built-in
    with the same 雅黑 + Meiryo fonts and no ``description``, so a copy on disk
    would silently keep the nine presets looking alike forever.
    """

    if str(config.get("description") or "").strip():
        return False
    saved_pair = (str(config.get("ch_font") or ""), str(config.get("jp_font") or ""))
    if saved_pair != LEGACY_FONT_PAIR:
        return False
    default_pair = (str(default.get("ch_font") or ""), str(default.get("jp_font") or ""))
    return default_pair != saved_pair


def load_presets_into(
    target: dict[str, dict[str, Any]],
    *,
    defaults: Mapping[str, dict[str, Any]],
    path: Path | None = None,
) -> dict[str, Any]:
    """Replace ``target`` contents in place and return the applied metadata."""

    saved, meta = read_preset_document(path)
    merged = merge_presets(defaults, saved, meta)
    target.clear()
    target.update(merged)
    return meta


def restore_builtin_presets(
    defaults: Mapping[str, dict[str, Any]],
    current: Mapping[str, dict[str, Any]],
    meta: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Rebuild the built-ins while keeping presets the user authored themselves."""

    renamed_targets = {str(name) for name in (meta.get("renamed") or {}).values()}
    keep = {
        str(name): dict(config)
        for name, config in current.items()
        if str(name) not in defaults and str(name) not in renamed_targets
    }
    restored = {str(name): dict(config) for name, config in defaults.items()}
    restored.update(keep)
    return restored


def write_preset_document(
    presets: Mapping[str, dict[str, Any]],
    *,
    removed: list[str] | tuple[str, ...] = (),
    renamed: Mapping[str, str] | None = None,
    path: Path | None = None,
) -> None:
    """Write presets plus the tombstones and renames needed to replay the state."""

    target = path or preset_file_path()
    payload: dict[str, Any] = dict(_clean_presets(presets))
    payload[PRESET_META_KEY] = {
        "removed": sorted({str(name).strip() for name in removed if str(name).strip()}),
        "renamed": {
            str(old).strip(): str(new).strip()
            for old, new in (renamed or {}).items()
            if str(old).strip() and str(new).strip() and str(old) != str(new)
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
