"""Create GitHub-Release-sized ZIP parts for the external Next engine.

The launcher deliberately keeps the heavy runtime outside its PyInstaller
directory.  GitHub Release assets have a per-file size limit, so a prepared
engine is published as several ZIPs whose paths merge into one ``.engine``
directory when extracted by :mod:`engine.runtime_installer`.

This tool never edits the source ``.engine``.  It filters development-only
files into a staging list and writes deterministic, ZIP64-compatible parts.
Use ``tools/build_engine_manifest.py`` afterwards to create the signed-size
manifest uploaded alongside the parts.
"""

from __future__ import annotations

import argparse
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MAX_BYTES = 1_450_000_000
PART_PREFIX = "utasyncnext-engine-part-"


@dataclass(frozen=True)
class EngineFile:
    source: Path
    relative: str
    size: int


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Prepared .engine directory")
    parser.add_argument("output", type=Path, help="Directory for ZIP parts")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--clean", action="store_true", help="Remove existing generated parts first")
    parser.add_argument("--deflate", action="store_true", help="Use ZIP_DEFLATED instead of fast ZIP_STORED")
    parser.add_argument(
        "--portable-python-root",
        type=Path,
        help="Python 3.10 installation used to add a small self-contained runtime",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    if not source.is_dir():
        raise SystemExit(f"Engine directory not found: {source}")
    if args.max_bytes <= 0:
        raise SystemExit("--max-bytes must be positive")
    output.mkdir(parents=True, exist_ok=True)
    if args.clean:
        for path in output.glob(f"{PART_PREFIX}*.zip"):
            path.unlink()

    with tempfile.TemporaryDirectory(prefix="utasync-engine-metadata-") as metadata_dir:
        files = collect_files(source, portable_python_root=args.portable_python_root, metadata_dir=Path(metadata_dir))
        if not files:
            raise SystemExit(f"No runtime files found in {source}")
        parts = partition(files, args.max_bytes)
        compression = zipfile.ZIP_DEFLATED if args.deflate else zipfile.ZIP_STORED
        generated: list[Path] = []
        total_bytes = 0
        for index, part in enumerate(parts, start=1):
            target = output / f"{PART_PREFIX}{index:02d}.zip"
            if target.exists():
                target.unlink()
            with zipfile.ZipFile(target, "w", compression=compression, allowZip64=True) as archive:
                for item in part:
                    archive.write(item.source, item.relative)
            generated.append(target)
            part_bytes = sum(item.size for item in part)
            total_bytes += part_bytes
            print(
                f"{target.name}: {len(part):,} files, "
                f"{part_bytes / 1024 / 1024 / 1024:.3f} GiB source, "
                f"{target.stat().st_size / 1024 / 1024 / 1024:.3f} GiB ZIP"
            )

    print(
        f"Wrote {len(generated)} parts, {len(files):,} files, "
        f"{total_bytes / 1024 / 1024 / 1024:.3f} GiB source to {output}"
    )
    return 0


def collect_files(
    source: Path,
    *,
    portable_python_root: Path | None = None,
    metadata_dir: Path | None = None,
) -> list[EngineFile]:
    portable_root = portable_python_root.resolve() if portable_python_root else None
    if portable_root and not portable_root.is_dir():
        raise SystemExit(f"Portable Python root not found: {portable_root}")
    items: list[EngineFile] = []
    for path in source.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative_path = path.relative_to(source)
        relative = relative_path.as_posix()
        if should_exclude(relative_path, portable_python=bool(portable_root)):
            continue
        items.append(EngineFile(path, relative, path.stat().st_size))

    if portable_root:
        if metadata_dir is None:
            raise ValueError("metadata_dir is required when adding portable Python")
        if any(item.relative.startswith("python-runtime/") for item in items):
            raise SystemExit("Source already contains python-runtime; pass the original .engine directory")
        items.extend(collect_portable_python(portable_root, metadata_dir))

    # Keep the bootstrap files in the first part.  The installer validates the
    # merged directory only after all parts are present, but this makes a
    # partially downloaded cache easier to inspect and repair.
    items.sort(key=lambda item: (0 if item.relative in {"pyvenv.cfg"} or item.relative.startswith("Scripts/") else 1, item.relative.lower()))
    return items


def should_exclude(relative: Path, *, portable_python: bool = False) -> bool:
    parts = {part.lower() for part in relative.parts}
    lowered = relative.as_posix().lower()
    if "__pycache__" in parts or relative.suffix.lower() in {".pyc", ".pyo", ".lib", ".pdb"}:
        return True
    if portable_python and relative.as_posix().lower() == "pyvenv.cfg":
        return True
    if "~ympy" in parts:
        return True
    if lowered.startswith("lib/site-packages/torch/include/"):
        return True
    if lowered.startswith("lib/site-packages/numba/tests/"):
        return True
    if lowered.startswith("lib/site-packages/numpy/tests/"):
        return True
    if lowered.startswith("lib/site-packages/pip/") or lowered.startswith("lib/site-packages/setuptools/"):
        return True
    return False


def collect_portable_python(base: Path, metadata_dir: Path) -> list[EngineFile]:
    """Overlay the small Python runtime needed by the Demucs subprocess.

    The development venv's ``pyvenv.cfg`` points at the maintainer's Python
    installation.  A normal Python root plus ``python._pth`` makes the
    downloaded engine runnable on another Windows machine while still using
    the engine's site-packages.  We intentionally omit the base installation's
    site-packages: those are the large, separately verified engine payload.
    """
    items: list[EngineFile] = []
    root_names = {
        "python.exe",
        "python3.dll",
        "python310.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "LICENSE.txt",
    }
    for name in sorted(root_names):
        path = base / name
        if path.is_file():
            items.append(EngineFile(path, f"python-runtime/{name}", path.stat().st_size))

    for top_name in ("DLLs", "Lib", "libs"):
        top = base / top_name
        if not top.is_dir():
            continue
        for path in top.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(top)
            if top_name == "Lib" and any(part.lower() in {"site-packages", "__pycache__", "test", "tkinter", "idlelib", "turtledemo"} for part in rel.parts):
                continue
            if rel.suffix.lower() in {".pyc", ".pyo"} or "__pycache__" in {part.lower() for part in rel.parts}:
                continue
            items.append(EngineFile(path, f"python-runtime/{top_name}/{rel.as_posix()}", path.stat().st_size))

    metadata_dir.mkdir(parents=True, exist_ok=True)
    pth = metadata_dir / "python._pth"
    pth.write_text(
        ".\nLib\nDLLs\n..\\Lib\\site-packages\nimport site\n",
        encoding="ascii",
    )
    cfg = metadata_dir / "pyvenv.cfg"
    cfg.write_text(
        "home = .engine\\python-runtime\ninclude-system-site-packages = false\nversion = 3.10.11\n",
        encoding="ascii",
    )
    items.append(EngineFile(pth, "python-runtime/python._pth", pth.stat().st_size))
    items.append(EngineFile(cfg, "pyvenv.cfg", cfg.stat().st_size))
    return items


def partition(files: list[EngineFile], max_bytes: int) -> list[list[EngineFile]]:
    parts: list[list[EngineFile]] = []
    current: list[EngineFile] = []
    current_size = 0
    for item in files:
        if item.size > max_bytes:
            raise SystemExit(f"Single file exceeds --max-bytes: {item.relative} ({item.size:,} bytes)")
        if current and current_size + item.size > max_bytes:
            parts.append(current)
            current = []
            current_size = 0
        current.append(item)
        current_size += item.size
    if current:
        parts.append(current)
    return parts


if __name__ == "__main__":
    raise SystemExit(main())
