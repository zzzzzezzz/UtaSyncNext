from __future__ import annotations

import sys
from pathlib import Path


NEXT_ROOT = Path(__file__).resolve().parents[1]
if str(NEXT_ROOT) not in sys.path:
    sys.path.insert(0, str(NEXT_ROOT))

from engine.hardware_profile import (  # noqa: E402
    HardwareProfile,
    resolve_demucs_hardware_mode,
    whisper_hardware_note,
)
from utils.config import _normalize_settings  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    cases = [
        (HardwareProfile(False), "low"),
        (HardwareProfile(True, "4GB GPU", 4 * 1024), "low"),
        (HardwareProfile(True, "6GB GPU", 6 * 1024), "low"),
        (HardwareProfile(True, "8GB GPU", 8 * 1024), "medium"),
        (HardwareProfile(True, "12GB GPU", 12 * 1024), "high"),
        (HardwareProfile(True, "24GB GPU", 24 * 1024), "high"),
    ]
    for profile, expected in cases:
        actual, reason = resolve_demucs_hardware_mode("自动匹配显存 (推荐)", profile)
        _require(actual == expected, f"{profile} should resolve to {expected}, got {actual}")
        _require("自动" in reason or "未检测" in reason, "automatic mode should explain its decision")

    profile = HardwareProfile(True, "Test GPU", 24 * 1024)
    _require(resolve_demucs_hardware_mode("保守分离 (4G~6G显存)", profile)[0] == "low", "manual low")
    _require(resolve_demucs_hardware_mode("均衡分离 (8G显存)", profile)[0] == "medium", "manual medium")
    _require(resolve_demucs_hardware_mode("快速分离 (12G+显存)", profile)[0] == "high", "manual high")

    gpu_note = whisper_hardware_note(HardwareProfile(True, "Test GPU", 8 * 1024))
    _require("CUDA" in gpu_note and "CPU" in gpu_note, "GPU note should explain CUDA fallback")
    cpu_note = whisper_hardware_note(HardwareProfile(False))
    _require("CPU" in cpu_note and "降低" in cpu_note, "CPU note should explain lower speed")

    migrated = _normalize_settings({"hardware_mode": "极限防爆 (4G~6G显存/默认)"})
    _require("自动匹配" in migrated["hardware_mode"], "legacy default should migrate to automatic VRAM mode")

    print("PASS: Demucs auto profile scales by VRAM while Whisper keeps automatic CUDA fallback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
