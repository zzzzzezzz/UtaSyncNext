from __future__ import annotations

from pathlib import Path


def compact_filename(value: object, max_chars: int = 48) -> str:
    """Keep both ends of a filename while preventing layout-breaking labels."""
    name = Path(str(value or "")).name
    if not name or len(name) <= max_chars:
        return name
    if max_chars < 9:
        return name[: max(1, max_chars - 1)] + "…"

    suffix = Path(name).suffix
    stem = name[: -len(suffix)] if suffix else name
    room = max_chars - len(suffix) - 1
    if room < 5:
        return name[: max_chars - 1] + "…"
    left = (room + 1) // 2
    right = room - left
    return f"{stem[:left]}…{stem[-right:]}{suffix}"
