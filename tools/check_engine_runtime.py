from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.locator import find_engine_location


def main() -> int:
    parser = argparse.ArgumentParser(description="Check UtaSyncNext engine runtime without loading heavy models.")
    parser.add_argument("--deep", action="store_true", help="Import torch/ctranslate2 and query CUDA. Slower.")
    args = parser.parse_args()

    location = find_engine_location()
    report: dict[str, object] = {
        "engine_root": str(location.root) if location.root else "",
        "engine_source": location.source_label,
        "engine_ready": location.ready,
        "engine_legacy": location.is_legacy,
        "missing_modules": list(location.missing_modules),
    }

    if location.site_packages:
        site.addsitedir(str(location.site_packages))
    if location.scripts:
        os.environ["PATH"] = str(location.scripts) + os.pathsep + os.environ.get("PATH", "")

    _register_cuda_dll_dirs(location.site_packages)
    report["asr_cuda_runtime_ready"] = _cuda_runtime_available()
    if not report["asr_cuda_runtime_ready"]:
        report["asr_runtime_note"] = "CUDA ASR DLLs are incomplete; Faster-Whisper will use CPU fallback."

    distribution_names = {
        "torch": "torch",
        "torchaudio": "torchaudio",
        "ctranslate2": "ctranslate2",
        "faster_whisper": "faster-whisper",
        "demucs": "demucs",
    }
    for module_name, distribution_name in distribution_names.items():
        try:
            report[module_name] = importlib.metadata.version(distribution_name)
        except Exception as exc:
            report[module_name] = f"ERROR: {type(exc).__name__}: {exc}"

    if not args.deep:
        report["torch_cuda_available"] = "skipped; pass --deep to import torch"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if location.ready else 1

    try:
        import torch

        report["torch_cuda_available"] = bool(torch.cuda.is_available())
        report["torch_cuda_version"] = getattr(torch.version, "cuda", "")
        report["torch_device_count"] = int(torch.cuda.device_count())
    except Exception as exc:
        report["torch_cuda_error"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if location.ready else 1


def _cuda_runtime_available() -> bool:
    if sys.platform != "win32":
        return True

    try:
        import ctypes

        dll_name = "cublas" + "64_12.dll"
        getattr(ctypes, "WinDLL")(dll_name)
        return True
    except Exception:
        return False


def _register_cuda_dll_dirs(site_packages: Path | None) -> None:
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return

    candidates: list[Path] = []
    if site_packages:
        candidates.extend(
            [
                site_packages / "torch" / "lib",
                site_packages / "ctranslate2",
                site_packages / "nvidia" / "cublas" / "bin",
                site_packages / "nvidia" / "cudnn" / "bin",
                site_packages / "nvidia" / "cufft" / "bin",
                site_packages / "nvidia" / "curand" / "bin",
                site_packages / "nvidia" / "cusolver" / "bin",
                site_packages / "nvidia" / "cusparse" / "bin",
                site_packages / "nvidia" / "nvtx" / "bin",
            ]
        )

    for candidate in candidates:
        if not candidate.exists():
            continue
        os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(str(candidate))
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
