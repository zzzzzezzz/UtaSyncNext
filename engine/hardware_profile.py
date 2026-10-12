from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from utils.performance_metrics import collect_gpu_info


@dataclass(frozen=True)
class HardwareProfile:
    gpu_detected: bool
    gpu_name: str = ""
    gpu_memory_total_mb: int = 0


@lru_cache(maxsize=1)
def inspect_hardware_profile() -> HardwareProfile:
    info = collect_gpu_info()
    return HardwareProfile(
        gpu_detected=bool(info.get("gpu_detected")),
        gpu_name=str(info.get("gpu_name") or ""),
        gpu_memory_total_mb=int(info.get("gpu_memory_total_mb") or 0),
    )


def resolve_demucs_hardware_mode(raw_value: object, profile: HardwareProfile | None = None) -> tuple[str, str]:
    raw = str(raw_value or "").strip()
    if "均衡" in raw:
        return "medium", "手动选择"
    if "满血" in raw or "快速分离" in raw:
        return "high", "手动选择"
    if "极限防爆" in raw or "保守分离" in raw:
        return "low", "手动选择"

    detected = profile or inspect_hardware_profile()
    memory_mb = detected.gpu_memory_total_mb if detected.gpu_detected else 0
    if memory_mb >= 12 * 1024:
        return "high", "自动匹配 12GB+ 显存"
    if memory_mb >= 8 * 1024:
        return "medium", "自动匹配 8GB+ 显存"
    if memory_mb > 0:
        return "low", f"自动匹配 {memory_mb / 1024:.1f}GB 显存"
    return "low", "未检测到 NVIDIA 显存，使用保守分离"


def whisper_hardware_note(profile: HardwareProfile) -> str:
    if profile.gpu_detected:
        memory = f"{profile.gpu_memory_total_mb / 1024:.1f}GB" if profile.gpu_memory_total_mb else "显存未知"
        name = profile.gpu_name or "NVIDIA GPU"
        return f"{name} · {memory}；Whisper 会优先尝试 CUDA，失败时自动回退 CPU。"
    return "未检测到 NVIDIA 显卡；Whisper 将使用 CPU，速度会明显降低。"
