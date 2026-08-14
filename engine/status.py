from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from pathlib import Path

from engine.locator import find_engine_location, next_root


@dataclass(frozen=True)
class LightweightEngineStatus:
    label: str
    detail: str
    ready: bool
    runtime: dict[str, str] | None = None
    accelerator: str = "unknown"


def inspect_engine_lightweight() -> LightweightEngineStatus:
    """Check engine presence without importing Torch, Whisper, or Demucs."""
    location = find_engine_location()
    if not location.root:
        return LightweightEngineStatus(
            label="需要安装 Next 引擎",
            detail=f"没有找到 {next_root() / '.engine'} 或可用的 Next .venv。",
            ready=False,
        )

    runtime = _read_runtime_versions(location.site_packages)
    accelerator = _detect_accelerator(runtime)

    if location.missing_modules:
        missing = "、".join(location.missing_modules)
        return LightweightEngineStatus(
            label="Next 引擎依赖未完整",
            detail=f"{location.source_label}: {location.root}，缺少 {missing}；{_format_runtime(runtime, accelerator)}",
            ready=False,
            runtime=runtime,
            accelerator=accelerator,
        )

    scripts_note = "Scripts 已找到" if location.scripts else "Scripts 缺失"
    legacy_note = "，旧环境兼容模式" if location.is_legacy else ""
    return LightweightEngineStatus(
        label="Next 独立引擎就绪",
        detail=f"{location.source_label}: {location.root} ({scripts_note}{legacy_note})；{_format_runtime(runtime, accelerator)}",
        ready=True,
        runtime=runtime,
        accelerator=accelerator,
    )


def _read_runtime_versions(site_packages: Path | None) -> dict[str, str]:
    if not site_packages or not site_packages.exists():
        return {}

    wanted = {
        "torch": "torch",
        "torchaudio": "torchaudio",
        "ctranslate2": "ctranslate2",
        "faster-whisper": "faster-whisper",
        "demucs": "demucs",
    }
    found: dict[str, str] = {}
    try:
        distributions = importlib.metadata.distributions(path=[str(site_packages)])
        for distribution in distributions:
            name = (distribution.metadata.get("Name") or "").lower().replace("_", "-")
            if name in wanted:
                found[wanted[name]] = distribution.version
    except Exception:
        return {}
    return found


def _detect_accelerator(runtime: dict[str, str]) -> str:
    torch_version = runtime.get("torch", "")
    if "+cu" in torch_version:
        return "CUDA wheel"
    if "+cpu" in torch_version or torch_version:
        return "CPU wheel"
    return "unknown"


def _format_runtime(runtime: dict[str, str], accelerator: str) -> str:
    if not runtime:
        return f"运行时: {accelerator}"
    parts = [f"运行时: {accelerator}"]
    for name in ("torch", "torchaudio", "ctranslate2", "faster-whisper", "demucs"):
        version = runtime.get(name)
        if version:
            parts.append(f"{name} {version}")
    return " | ".join(parts)
