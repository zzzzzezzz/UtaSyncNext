from __future__ import annotations

import re
import os
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".cache",
    ".engine",
    ".venv",
    ".wheelhouse",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
    "models_faster",
    "output",
    "smoke_media",
    "test_logs",
}
MAX_TEXT_BYTES = 2 * 1024 * 1024
SKIP_FILES = {
    "user_settings.json",
    "user_settings.example.json",
}
TEXT_SUFFIXES = {
    ".cmd",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".spec",
    ".txt",
    ".yaml",
    ".yml",
}

PATTERNS = [
    re.compile(r'"api_key(?:_design)?"\s*:\s*"(?!(?:|\*+|fake-key|your-api-key|YOUR_API_KEY)")([^"]{12,})"', re.I),
    re.compile(r"\bAuthorization\s*:\s*Bearer\s+(?!(?:fake-key|YOUR_API_KEY)\b)([A-Za-z0-9._\-]{20,})", re.I),
    re.compile(r"\b(?:sk|sf|hf)_[A-Za-z0-9_\-]{24,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{24,}\b"),
]


def main() -> int:
    findings: list[tuple[Path, int, str]] = []
    for path in _iter_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append((path, line, _mask(match.group(0))))

    if findings:
        print("Secret check failed. Remove private values before committing:")
        for path, line, sample in findings:
            print(f"- {path.relative_to(NEXT_ROOT)}:{line}: {sample}")
        return 1

    print("Secret check passed. No obvious API keys found in commit-safe files.")
    return 0


def _iter_text_files() -> list[Path]:
    files: list[Path] = []
    for root, dirnames, filenames in os.walk(NEXT_ROOT):
        dirnames[:] = [dirname for dirname in dirnames if dirname not in SKIP_DIRS]
        root_path = Path(root)
        for filename in filenames:
            path = root_path / filename
            if path.name in SKIP_FILES:
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                if path.stat().st_size > MAX_TEXT_BYTES:
                    continue
            except OSError:
                continue
            files.append(path)
    return sorted(files)


def _mask(value: str) -> str:
    compact = value.replace("\n", "\\n")
    if len(compact) <= 16:
        return "***"
    return f"{compact[:8]}...{compact[-4:]}"


if __name__ == "__main__":
    raise SystemExit(main())
