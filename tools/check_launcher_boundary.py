from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
HEAVY_MODULES = {
    "ctranslate2",
    "demucs",
    "faster_whisper",
    "torch",
    "torchaudio",
    "torchvision",
    "whisper",
}
SCAN_DIRS = ["app.py", "controllers", "engine", "ui", "utils", "core"]
SKIP_DIRS = {"__pycache__", ".venv", ".engine", ".cache", ".wheelhouse", "models_faster", "output", "test_logs"}


@dataclass
class Finding:
    path: Path
    line: int
    module: str


def main() -> int:
    findings = _scan_top_level_heavy_imports()
    spec_errors = _check_spec_excludes()
    import_errors = _check_startup_imports()
    duplicate_errors = _check_duplicate_class_methods()

    if findings or spec_errors or import_errors or duplicate_errors:
        print("Launcher boundary check failed.")
        for finding in findings:
            rel = finding.path.relative_to(NEXT_ROOT)
            print(f"- Top-level heavy import: {rel}:{finding.line} -> {finding.module}")
        for error in spec_errors + import_errors + duplicate_errors:
            print(f"- {error}")
        return 1

    print("Launcher boundary check passed.")
    print("- No top-level heavy imports in launcher/source startup tree.")
    print("- PyInstaller spec excludes heavy AI packages.")
    print("- Importing app does not load torch/faster_whisper/demucs.")
    print("- No duplicate class method definitions in launcher/source tree.")
    return 0


def _scan_top_level_heavy_imports() -> list[Finding]:
    findings: list[Finding] = []
    for path in _iter_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        for node in tree.body:
            module = _top_level_import_name(node)
            if module and module in HEAVY_MODULES:
                findings.append(Finding(path=path, line=getattr(node, "lineno", 0), module=module))
    return findings


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for item in SCAN_DIRS:
        path = NEXT_ROOT / item
        if path.is_file() and path.suffix == ".py":
            files.append(path)
            continue
        if path.is_dir():
            for child in path.rglob("*.py"):
                if any(part in SKIP_DIRS for part in child.parts):
                    continue
                files.append(child)
    return sorted(files)


def _top_level_import_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in HEAVY_MODULES:
                return root
    if isinstance(node, ast.ImportFrom) and node.module:
        root = node.module.split(".", 1)[0]
        if root in HEAVY_MODULES:
            return root
    return None


def _check_spec_excludes() -> list[str]:
    spec_path = NEXT_ROOT / "packaging" / "launcher.spec"
    if not spec_path.exists():
        return [f"Missing PyInstaller spec: {spec_path}"]

    text = spec_path.read_text(encoding="utf-8", errors="ignore")
    errors: list[str] = []
    for module in sorted(HEAVY_MODULES):
        if f'"{module}"' not in text and f"'{module}'" not in text:
            errors.append(f"launcher.spec does not explicitly exclude {module}")
    return errors


def _check_startup_imports() -> list[str]:
    script = (
        "import sys, pathlib;"
        f"sys.path.insert(0, {str(NEXT_ROOT)!r});"
        "import app;"
        f"heavy={sorted(HEAVY_MODULES)!r};"
        "loaded=[name for name in heavy if name in sys.modules];"
        "print(','.join(loaded));"
        "raise SystemExit(1 if loaded else 0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=NEXT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    output = (completed.stdout or "").strip()
    if completed.returncode == 0:
        return []
    return [f"Importing app loaded heavy modules or failed: {output}"]


def _check_duplicate_class_methods() -> list[str]:
    errors: list[str] = []
    for path in _iter_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            seen: dict[str, int] = {}
            for child in node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                previous_line = seen.get(child.name)
                if previous_line is not None:
                    rel = path.relative_to(NEXT_ROOT)
                    errors.append(
                        f"Duplicate method definition: {rel}:{child.lineno} "
                        f"{node.name}.{child.name} already defined at line {previous_line}"
                    )
                else:
                    seen[child.name] = child.lineno
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
