from __future__ import annotations

import importlib.machinery
import os
from dataclasses import dataclass
from pathlib import Path

from engine.cache_env import app_root


REQUIRED_ENGINE_MODULES = ("faster_whisper", "demucs", "torch", "torchaudio")


@dataclass(frozen=True)
class EngineLocation:
    root: Path | None
    site_packages: Path | None
    scripts: Path | None
    python: Path | None
    source_label: str = ""
    is_legacy: bool = False
    missing_modules: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return bool(self.root and self.site_packages and not self.missing_modules)


def next_root() -> Path:
    return app_root()


def find_engine_location() -> EngineLocation:
    """Find the Next-owned heavy engine environment without importing it."""
    candidates = _next_engine_candidates()

    existing: list[EngineLocation] = []
    for candidate, label, is_legacy in candidates:
        location = _inspect_candidate(candidate, label, is_legacy)
        if not location.root:
            continue
        if location.ready:
            return location
        existing.append(location)

    if existing:
        return existing[0]
    return EngineLocation(root=None, site_packages=None, scripts=None, python=None)


def _next_engine_candidates() -> list[tuple[Path, str, bool]]:
    root = next_root()
    candidates: list[tuple[Path, str, bool]] = []

    explicit_root = os.environ.get("UTASYNCNEXT_ENGINE_ROOT", "").strip()
    if explicit_root:
        candidates.append((Path(explicit_root), "Next explicit engine", False))

    candidates.extend(
        [
            (root / ".engine", "Next .engine", False),
            (root / ".venv", "Next .venv", False),
        ]
    )
    return candidates


def _inspect_candidate(root: Path, label: str, is_legacy: bool) -> EngineLocation:
    site_packages = _find_site_packages(root)
    scripts = root / "Scripts"
    python = _find_engine_python(root)
    if not site_packages:
        return EngineLocation(
            root=root if root.exists() else None,
            site_packages=None,
            scripts=scripts if scripts.exists() else None,
            python=python if python.exists() else None,
            source_label=label,
            is_legacy=is_legacy,
            missing_modules=REQUIRED_ENGINE_MODULES if root.exists() else (),
        )

    missing_modules = tuple(module for module in REQUIRED_ENGINE_MODULES if not _module_exists(site_packages, module))
    return EngineLocation(
        root=root,
        site_packages=site_packages,
        scripts=scripts if scripts.exists() else None,
        python=python if python.exists() else None,
        source_label=label,
        is_legacy=is_legacy,
        missing_modules=missing_modules,
    )


def _find_engine_python(root: Path) -> Path:
    """Prefer the portable release runtime, then legacy venv locations."""
    for candidate in (
        root / "python-runtime" / "python.exe",
        root / "python.exe",
        root / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return candidate
    return root / "Scripts" / "python.exe"


def _find_site_packages(root: Path) -> Path | None:
    windows_site = root / "Lib" / "site-packages"
    if windows_site.exists():
        return windows_site

    lib_root = root / "lib"
    if lib_root.exists():
        for candidate in lib_root.glob("python*/site-packages"):
            if candidate.exists():
                return candidate
    return None


def _module_exists(site_packages: Path, module_name: str) -> bool:
    return importlib.machinery.PathFinder.find_spec(module_name, [str(site_packages)]) is not None
