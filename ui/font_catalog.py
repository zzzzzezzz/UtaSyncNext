"""Small, predictable user-font catalog for the Next UI.

The catalog deliberately does not enumerate the whole Windows font registry.
Users can import a font file into the app-owned ``fonts`` directory and then
select the discovered family from the two curated subtitle controls.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.cache_env import app_root


FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}
_SAFE_FILENAME = re.compile(r"[^0-9A-Za-z_.()\-\u3400-\u9fff]+")


@dataclass(frozen=True)
class UserFont:
    family: str
    path: Path


def user_fonts_dir() -> Path:
    path = app_root() / "fonts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_font_pairs_file() -> Path:
    """Return the small app-owned file used for named font pairings."""

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


def discover_user_fonts(directory: Path | None = None) -> list[UserFont]:
    root = directory or user_fonts_dir()
    if not root.exists():
        return []
    records: list[UserFont] = []
    seen: set[str] = set()
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in FONT_SUFFIXES:
            continue
        family = read_font_family(path) or path.stem
        key = family.casefold()
        if key in seen:
            continue
        seen.add(key)
        records.append(UserFont(family=family, path=path))
    return records


def import_font(source: str | Path, directory: Path | None = None) -> UserFont:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"找不到字体文件：{source_path}")
    if source_path.suffix.lower() not in FONT_SUFFIXES:
        raise ValueError("仅支持 .ttf、.otf、.ttc、.otc 字体文件")

    root = directory or user_fonts_dir()
    root.mkdir(parents=True, exist_ok=True)
    family = read_font_family(source_path) or source_path.stem
    safe_name = _SAFE_FILENAME.sub("_", source_path.name).strip("._") or "font"
    target = root / safe_name
    if target.resolve() != source_path:
        shutil.copy2(source_path, target)
    return UserFont(family=family, path=target)


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


def read_font_family(path: Path) -> str:
    """Read the family name from a normal TTF/OTF name table.

    TTC/OTC collections are accepted as files but use the filename fallback;
    selecting a collection by a single family would be ambiguous without an
    additional font-management dependency.
    """

    try:
        data = path.read_bytes()
        if len(data) < 12 or data[:4] == b"ttcf":
            return ""
        num_tables = struct.unpack_from(">H", data, 4)[0]
        name_offset = name_length = None
        for index in range(num_tables):
            record_offset = 12 + index * 16
            if record_offset + 16 > len(data):
                return ""
            tag = data[record_offset : record_offset + 4]
            offset, length = struct.unpack_from(">II", data, record_offset + 8)
            if tag == b"name":
                name_offset, name_length = offset, length
                break
        if name_offset is None or name_length is None or name_offset + name_length > len(data):
            return ""

        table = data[name_offset : name_offset + name_length]
        if len(table) < 6:
            return ""
        count, string_offset = struct.unpack_from(">HH", table, 2)
        candidates: list[tuple[int, str]] = []
        for index in range(count):
            record_offset = 6 + index * 12
            if record_offset + 12 > len(table):
                continue
            platform, encoding, language, name_id, length, offset = struct.unpack_from(">HHHHHH", table, record_offset)
            if name_id not in {1, 16}:
                continue
            start = string_offset + offset
            end = start + length
            if start < 0 or end > len(table):
                continue
            raw = table[start:end]
            try:
                text = raw.decode("utf-16-be" if platform in {0, 3} else "mac_roman", errors="ignore").strip()
            except LookupError:
                text = raw.decode("utf-8", errors="ignore").strip()
            if text:
                # Prefer typographic family (16), then English/neutral names.
                preference = (0 if name_id == 16 else 10) + (0 if language in {0, 0x0409} else 2)
                candidates.append((preference, text))
        if not candidates:
            return ""
        return min(candidates, key=lambda item: item[0])[1]
    except (OSError, struct.error, ValueError):
        return ""


def filter_font_families(records: list[UserFont]) -> list[str]:
    return [record.family for record in records]
