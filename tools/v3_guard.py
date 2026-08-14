from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NEXT_ROOT = Path(__file__).resolve().parents[1]
V3_ROOT = NEXT_ROOT.parent.resolve()
STATE_DIR = NEXT_ROOT / ".safety"
SNAPSHOT_PATH = STATE_DIR / "v3-baseline.json"

PROTECTED_DIRS = ("core", "controllers", "ui", "utils", "packaging")
ROOT_SOURCE_SUFFIXES = {
    ".cmd",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".spec",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SKIP_DIR_NAMES = {
    ".cache",
    ".engine",
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    ".vscode",
    ".wheelhouse",
    "UtaSyncNext",
    "__pycache__",
    "build",
    "dist",
    "env",
    "env_build",
    "env_gui",
    "input",
    "models",
    "models_faster",
    "output",
    "venv",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Protect the parent UtaSync v3 repository from Next edits.")
    parser.add_argument("command", choices=["doctor", "begin", "verify"])
    args = parser.parse_args()

    try:
        _validate_layout(require_next_git=args.command == "doctor")
        if args.command == "doctor":
            return _doctor()
        if args.command == "begin":
            return _begin()
        return _verify()
    except Exception as exc:
        print(f"V3 guard failed: {exc}", file=sys.stderr)
        return 2


def _doctor() -> int:
    next_git_root = _git_output(NEXT_ROOT, "rev-parse", "--show-toplevel")
    parent_git_root = _git_output(V3_ROOT, "rev-parse", "--show-toplevel")
    if Path(next_git_root).resolve() != NEXT_ROOT:
        raise RuntimeError(f"Next Git root is not isolated: {next_git_root}")
    if Path(parent_git_root).resolve() != V3_ROOT:
        raise RuntimeError(f"Parent Git root mismatch: {parent_git_root}")

    print("PASS: UtaSyncNext is an independent Git repository.")
    print(f"- writable Next root: {NEXT_ROOT}")
    print(f"- read-only v3 reference: {V3_ROOT}")
    print(f"- guard snapshot: {SNAPSHOT_PATH}")
    return 0


def _begin() -> int:
    snapshot = _build_snapshot()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print("V3 guard baseline recorded.")
    print(f"- protected files: {len(snapshot['files'])}")
    print(f"- v3 HEAD: {snapshot['git_head']}")
    return 0


def _verify() -> int:
    if not SNAPSHOT_PATH.exists():
        print("V3 guard has no baseline. Run `tools/v3_guard.py begin` before editing.", file=sys.stderr)
        return 2

    before = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    after = _build_snapshot()
    changes = _compare_snapshots(before, after)
    if changes:
        print("V3 guard detected changes after the Next task:", file=sys.stderr)
        for change in changes:
            print(f"- {change}", file=sys.stderr)
        print("No files were reverted. Stop and inspect these paths.", file=sys.stderr)
        return 1

    print("PASS: v3 remained unchanged during the Next task.")
    print(f"- protected files: {len(after['files'])}")
    print(f"- v3 HEAD: {after['git_head']}")
    return 0


def _validate_layout(*, require_next_git: bool) -> None:
    if not (V3_ROOT / ".git").exists():
        raise RuntimeError(f"parent v3 Git repository was not found: {V3_ROOT}")
    if NEXT_ROOT.parent.resolve() != V3_ROOT:
        raise RuntimeError("Next is no longer directly inside the expected v3 reference directory")
    if require_next_git and not (NEXT_ROOT / ".git").exists():
        raise RuntimeError("UtaSyncNext has not been initialized as an independent Git repository")


def _build_snapshot() -> dict[str, Any]:
    files = _collect_protected_files()
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "v3_root": str(V3_ROOT),
        "git_head": _git_output(V3_ROOT, "rev-parse", "HEAD"),
        "files": {relative: _file_state(path) for relative, path in sorted(files.items())},
    }


def _collect_protected_files() -> dict[str, Path]:
    protected: dict[str, Path] = {}

    for relative in _git_tracked_paths():
        if _is_next_path(relative):
            continue
        protected[relative] = V3_ROOT / Path(relative)

    for child in V3_ROOT.iterdir():
        if child.name in SKIP_DIR_NAMES or child == NEXT_ROOT:
            continue
        if child.is_file() and child.suffix.lower() in ROOT_SOURCE_SUFFIXES:
            protected[_relative_name(child)] = child

    for dirname in PROTECTED_DIRS:
        source_root = V3_ROOT / dirname
        if not source_root.exists():
            continue
        for path in source_root.rglob("*"):
            if any(part in SKIP_DIR_NAMES for part in path.relative_to(V3_ROOT).parts):
                continue
            if path.is_file() and path.suffix.lower() in ROOT_SOURCE_SUFFIXES:
                protected[_relative_name(path)] = path

    return protected


def _git_tracked_paths() -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(V3_ROOT), "ls-files", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"could not list v3 tracked files: {error}")
    return [item.decode("utf-8", errors="surrogateescape") for item in completed.stdout.split(b"\0") if item]


def _file_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"state": "missing"}
    if path.is_symlink():
        return {"state": "symlink", "target": os.readlink(path)}
    if not path.is_file():
        return {"state": "other"}

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"state": "file", "size": path.stat().st_size, "sha256": digest.hexdigest()}


def _compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    changes: list[str] = []
    if before.get("v3_root") != after.get("v3_root"):
        changes.append("v3 root changed")
    if before.get("git_head") != after.get("git_head"):
        changes.append(f"v3 HEAD changed: {before.get('git_head')} -> {after.get('git_head')}")

    before_files = before.get("files") if isinstance(before.get("files"), dict) else {}
    after_files = after.get("files") if isinstance(after.get("files"), dict) else {}
    for relative in sorted(set(before_files) | set(after_files)):
        if relative not in before_files:
            changes.append(f"added: {relative}")
        elif relative not in after_files:
            changes.append(f"removed: {relative}")
        elif before_files[relative] != after_files[relative]:
            changes.append(f"changed: {relative}")
    return changes


def _git_output(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _relative_name(path: Path) -> str:
    return path.relative_to(V3_ROOT).as_posix()


def _is_next_path(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    return normalized == "UtaSyncNext" or normalized.startswith("UtaSyncNext/")


if __name__ == "__main__":
    raise SystemExit(main())
