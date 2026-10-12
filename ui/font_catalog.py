"""Small, predictable user-font catalog for the Next UI.

The catalog deliberately does not enumerate the whole Windows font registry.
Users can import a font file into the app-owned ``fonts`` directory and then
select the discovered family from the two curated subtitle controls.

Each record keeps two names: the family shown in the UI and the render family
that the subtitle renderer can actually match. See ``core.font_names``.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.font_names import (
    FONT_SUFFIXES,
    read_display_family,
    resolve_render_family,
)
from core.font_names import user_fonts_dir as _user_fonts_dir


_SAFE_FILENAME = re.compile(r"[^0-9A-Za-z_.()\-\u3400-\u9fff]+")


@dataclass(frozen=True)
class UserFont:
    family: str
    path: Path
    render_family: str = ""


def user_fonts_dir() -> Path:
    return _user_fonts_dir()


def user_font_pairs_file() -> Path:
    """Return the small app-owned file used for named font pairings."""

    from engine.cache_env import app_root

    return app_root() / "user_font_pairs.json"


def load_user_font_pairs(path: Path | None = None) -> dict[str, tuple[str, str]]:
    """Load named Chinese/Japanese font pairs without scanning the system registry."""

    source = path or user_font_pairs_file()
    if not source.exists():
        return {}
    try:
        data: Any = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}

    pairs: dict[str, tuple[str, str]] = {}
    for raw_name, raw_pair in data.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_pair, dict):
            continue
        ch_font = str(raw_pair.get("ch_font") or "").strip()
        jp_font = str(raw_pair.get("jp_font") or "").strip()
        if ch_font and jp_font:
            pairs[name] = (ch_font, jp_font)
    return pairs


def save_user_font_pairs(pairs: dict[str, tuple[str, str]], path: Path | None = None) -> None:
    """Persist named pairings separately from subtitle style presets."""

    target = path or user_font_pairs_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        str(name).strip(): {"ch_font": pair[0], "jp_font": pair[1]}
        for name, pair in pairs.items()
        if str(name).strip() and len(pair) == 2 and all(str(item).strip() for item in pair)
    }
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_font_family(path: Path) -> str:
    """Read the displayed family name from a normal TTF/OTF name table.

    TTC/OTC collections are accepted as files but use the filename fallback;
    selecting a collection by a single family would be ambiguous without an
    additional font-management dependency.
    """

    return read_display_family(path)


def _record_for(path: Path) -> UserFont:
    family = read_font_family(path) or path.stem
    return UserFont(family=family, path=path, render_family=resolve_render_family(family))


def discover_user_fonts(directory: Path | None = None) -> list[UserFont]:
    root = directory or user_fonts_dir()
    if not root.exists():
        return []
    records: list[UserFont] = []
    seen: set[str] = set()
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in FONT_SUFFIXES:
            continue
        record = _record_for(path)
        key = record.family.casefold()
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    return records


def import_font(source: str | Path, directory: Path | None = None) -> UserFont:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"找不到字体文件：{source_path}")
    if source_path.suffix.lower() not in FONT_SUFFIXES:
        raise ValueError("仅支持 .ttf、.otf、.ttc、.otc 字体文件")

    root = directory or user_fonts_dir()
    root.mkdir(parents=True, exist_ok=True)
    safe_name = _SAFE_FILENAME.sub("_", source_path.name).strip("._") or "font"
    target = root / safe_name
    if target.resolve() != source_path:
        shutil.copy2(source_path, target)
    record = _record_for(target)
    if not record.family:
        record = UserFont(family=source_path.stem, path=target)
    return record


def import_fonts_from_directory(source: str | Path, directory: Path | None = None) -> list[UserFont]:
    """Import supported font files from one selected folder (first level only)."""

    source_root = Path(source).expanduser().resolve()
    if not source_root.is_dir():
        raise NotADirectoryError(f"找不到字体文件夹：{source_root}")
    records: list[UserFont] = []
    for path in sorted(source_root.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_file() and path.suffix.lower() in FONT_SUFFIXES:
            records.append(import_font(path, directory))
    return records


def filter_font_families(records: list[UserFont]) -> list[str]:
    return [record.family for record in records]