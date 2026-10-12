"""Resolve the font family names libass can actually match.

A TTF/OTF usually carries two family names: the typographic family (name ID 16,
for example ``Zen Kaku Gothic New``) and the legacy family (name ID 1, for
example ``Zen Kaku Gothic New Medium``). The font matcher behind libass indexes
fonts by the legacy name, so a style that asks for the typographic name renders
in a silent fallback face while the UI keeps showing the selected family.

The catalog therefore displays the typographic family and renders the legacy
one. Lookups are read-only and cached per directory signature, so callers may
query them for every generated ASS line.
"""

from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}

_HEADER_SIZE = 12
_DIRECTORY_ENTRY_SIZE = 16
_MAX_NAME_TABLE_BYTES = 1 << 20
_REGULAR_STYLES = {"regular", "book", "normal", "medium regular"}


def user_fonts_dir() -> Path:
    """Return the app-owned font directory, creating it when missing."""

    from engine.cache_env import app_root

    path = app_root() / "fonts"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def app_font_dir() -> Path:
    """Return the app-owned font directory without creating it.

    ``resolve_render_family`` runs once per subtitle line, so it must not touch
    the filesystem beyond a cheap change check.
    """

    from engine.cache_env import app_root

    return app_root() / "fonts"


def _per_user_fonts_dir() -> Path | None:
    """Return the per-user Windows font folder when the platform has one."""

    try:
        import os

        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return None
        path = Path(local_app_data) / "Microsoft" / "Windows" / "Fonts"
        return path if path.is_dir() else None
    except (OSError, ValueError):
        return None


def read_name_table(path: Path) -> bytes:
    """Read only the ``name`` table of a font file."""

    try:
        with open(path, "rb") as handle:
            header = handle.read(4)
            if len(header) < 4:
                return b""
            if header == b"ttcf":
                # A collection stores face offsets before the file table
                # directory. Without a font-management dependency the first
                # face is a deterministic choice.
                handle.seek(12)
                first_offset = struct.unpack(">I", handle.read(4))[0]
                handle.seek(first_offset)
                if len(handle.read(4)) < 4:
                    return b""
            handle.seek(4)
            num_tables = struct.unpack(">H", handle.read(2))[0]
            # The table directory always starts at byte 12, after the header.
            handle.seek(_HEADER_SIZE)
            directory = handle.read(num_tables * _DIRECTORY_ENTRY_SIZE)
            for index in range(num_tables):
                entry = directory[index * _DIRECTORY_ENTRY_SIZE : (index + 1) * _DIRECTORY_ENTRY_SIZE]
                if len(entry) < _DIRECTORY_ENTRY_SIZE or entry[:4] != b"name":
                    continue
                offset, length = struct.unpack_from(">II", entry, 8)
                if length <= 0 or length > _MAX_NAME_TABLE_BYTES:
                    return b""
                handle.seek(offset)
                return handle.read(length)
    except (OSError, struct.error, ValueError):
        return b""
    return b""


def read_name_records(path: Path) -> dict[int, str]:
    """Return ``{name_id: best_text}`` using neutral/English names first."""

    table = read_name_table(path)
    if len(table) < 6:
        return {}
    try:
        count, string_offset = struct.unpack_from(">HH", table, 2)
    except struct.error:
        return {}

    candidates: dict[int, list[tuple[int, str]]] = {}
    for index in range(count):
        record_offset = 6 + index * 12
        if record_offset + 12 > len(table):
            break
        platform, _encoding, language, name_id, length, offset = struct.unpack_from(
            ">HHHHHH", table, record_offset
        )
        start = string_offset + offset
        end = start + length
        if start < 0 or end > len(table):
            continue
        raw = table[start:end]
        try:
            text = raw.decode("utf-16-be" if platform in {0, 3} else "mac_roman", errors="ignore").strip()
        except LookupError:
            continue
        if not text:
            continue
        preference = 0 if language in {0, 0x0409, 1033} else 1
        candidates.setdefault(name_id, []).append((preference, text))

    return {
        name_id: min(options, key=lambda item: item[0])[1]
        for name_id, options in candidates.items()
    }


def _preferred(records: dict[int, str], name_ids: tuple[int, ...]) -> str:
    for name_id in name_ids:
        value = str(records.get(name_id) or "").strip()
        if value:
            return value
    return ""


def read_display_family(path: Path) -> str:
    """Return the family name shown in the UI (typographic family first)."""

    return _preferred(read_name_records(path), (16, 1))


def read_render_family(path: Path) -> str:
    """Return the family name the font matcher resolves (legacy family first)."""

    return _preferred(read_name_records(path), (1, 16))


def _directory_signature(root: Path) -> tuple[tuple[str, int, int], ...]:
    entries: list[tuple[str, int, int]] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in FONT_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(entries)


@lru_cache(maxsize=8)
def _cached_map(root_text: str, signature: tuple[tuple[str, int, int], ...]) -> dict[str, str]:
    root = Path(root_text)
    faces: list[tuple[str, str, str]] = []
    for name, _mtime, _size in signature:
        path = root / name
        records = read_name_records(path)
        display = _preferred(records, (16, 1))
        render = _preferred(records, (1, 16))
        if not display or not render:
            continue
        faces.append((display, render, _preferred(records, (17, 2))))

    def rank(face: tuple[str, str, str]) -> tuple[int, int]:
        display, render, style = face
        return (
            0 if style.casefold() in _REGULAR_STYLES else 1,
            0 if render.casefold() == display.casefold() else 1,
        )

    mapping: dict[str, str] = {}
    # A family folder can hold several weights; the regular face is the one the
    # family name should resolve to.
    for display, render, _style in sorted(faces, key=rank):
        mapping.setdefault(display.casefold(), render)
    for _display, render, _style in faces:
        mapping.setdefault(render.casefold(), render)
    return mapping


def _directory_stamp(root: Path) -> int:
    """Cheap change detector: a directory's own timestamp moves when fonts land."""

    try:
        return int(root.stat().st_mtime_ns)
    except OSError:
        return -1


_name_map_cache: dict[tuple[str, ...], tuple[tuple[int, ...], dict[str, str]]] = {}


def _name_map(*roots: Path | None) -> dict[str, str]:
    active = [root for root in roots if root is not None]
    if not active:
        return {}
    key = tuple(str(root) for root in active)
    stamps = tuple(_directory_stamp(root) for root in active)
    cached = _name_map_cache.get(key)
    if cached is not None and cached[0] == stamps:
        return cached[1]

    mapping: dict[str, str] = {}
    for root in active:
        if not root.is_dir():
            continue
        try:
            signature = _directory_signature(root)
        except OSError:
            continue
        mapping.update(_cached_map(str(root), signature))
    _name_map_cache[key] = (stamps, mapping)
    return mapping


def resolve_render_family(family: str, extra_roots: tuple[Path, ...] = ()) -> str:
    """Translate a displayed family into the name the font matcher can find.

    Unknown families are returned unchanged so system fonts keep working exactly
    as they do today.
    """

    name = str(family or "").strip()
    if not name:
        return name
    roots: tuple[Path | None, ...] = (app_font_dir(), _per_user_fonts_dir()) + tuple(extra_roots)
    return _name_map(*roots).get(name.casefold(), name)