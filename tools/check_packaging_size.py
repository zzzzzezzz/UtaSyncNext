from __future__ import annotations

import argparse
import os
import stat
from dataclasses import dataclass
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = NEXT_ROOT / "dist" / "UtaSyncNext"

HEAVY_NAMES = {
    ".engine",
    ".wheelhouse",
    "ctranslate2",
    "demucs",
    "faster_whisper",
    "models",
    "models_faster",
    "nvidia",
    "output",
    "torch",
    "torchaudio",
    "torchvision",
    "whisper",
}
HEAVY_SUFFIXES = {".bin", ".pt", ".pth", ".safetensors", ".ckpt", ".onnx"}
WARN_SIZE_MB = 180
EXTERNAL_RUNTIME_NAMES = {".engine", "models_faster", "output", ".cache", "performance", "fonts", "user_settings.json"}


@dataclass
class AuditResult:
    total_bytes: int
    file_count: int
    heavy_hits: list[Path]
    external_links: list[Path]


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit UtaSyncNext launcher packaging size and heavy payload leaks.")
    parser.add_argument("--dist", default=str(DEFAULT_DIST), help="Path to the built dist/UtaSyncNext directory.")
    parser.add_argument("--warn-size-mb", type=int, default=WARN_SIZE_MB, help="Warn if dist size exceeds this value.")
    args = parser.parse_args()

    dist = Path(args.dist).expanduser().resolve()
    if not dist.exists():
        print(f"SKIP: dist directory not found: {dist}")
        print("Build the launcher first with tools\\package_launcher.ps1.")
        return 0

    result = audit_dist(dist)
    total_mb = result.total_bytes / (1024 * 1024)
    print("UtaSyncNext packaging audit")
    print("=" * 30)
    print(f"dist: {dist}")
    print(f"files: {result.file_count}")
    print(f"size: {total_mb:.1f} MB")
    if result.external_links:
        print("external runtime links skipped:")
        for path in result.external_links:
            print(f"- {path.relative_to(dist)}")

    failed = False
    if result.heavy_hits:
        failed = True
        print("\nHeavy payload leaks detected:")
        for path in result.heavy_hits[:80]:
            print(f"- {path.relative_to(dist)}")
        if len(result.heavy_hits) > 80:
            print(f"- ... and {len(result.heavy_hits) - 80} more")
    else:
        print("heavy payload leaks: none")

    if total_mb > args.warn_size_mb:
        failed = True
        print(f"\nSize warning: launcher dist is above {args.warn_size_mb} MB.")

    return 1 if failed else 0


def audit_dist(dist: Path) -> AuditResult:
    total = 0
    file_count = 0
    heavy_hits: list[Path] = []
    external_links: list[Path] = []

    for path in _iter_dist_paths(dist):
        try:
            relative = path.relative_to(dist)
        except ValueError:
            relative = Path()
        # package_launcher.ps1 deliberately restores user/runtime state after
        # building so a local packaged run can resume. Those roots are not
        # part of the launcher payload and must not make the audit fail.
        if relative.parts and relative.parts[0].lower() in EXTERNAL_RUNTIME_NAMES:
            if len(relative.parts) == 1 and _is_reparse_point(path):
                external_links.append(path)
            continue
        name = path.name.lower()
        if _is_reparse_point(path):
            if name in HEAVY_NAMES:
                external_links.append(path)
            continue

        if path.is_file():
            file_count += 1
            try:
                total += path.stat().st_size
            except OSError:
                pass
            if any(part.lower() in HEAVY_NAMES for part in path.parts):
                heavy_hits.append(path)
            if path.suffix.lower() in HEAVY_SUFFIXES:
                heavy_hits.append(path)

    # Keep output deterministic and compact.
    unique_hits = sorted(set(heavy_hits), key=lambda item: str(item).lower())
    unique_links = sorted(set(external_links), key=lambda item: str(item).lower())
    return AuditResult(total_bytes=total, file_count=file_count, heavy_hits=unique_hits, external_links=unique_links)


def _iter_dist_paths(dist: Path):
    stack = [dist]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue

        for entry in entries:
            path = Path(entry.path)
            yield path
            if _entry_is_reparse_point(entry):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
            except OSError:
                continue


def _is_reparse_point(path: Path) -> bool:
    try:
        return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return path.is_symlink()
    except OSError:
        return False


def _entry_is_reparse_point(entry: os.DirEntry) -> bool:
    try:
        return bool(entry.stat(follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return entry.is_symlink()
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
