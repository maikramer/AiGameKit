"""Hardware profile detection for Motion3D (T2M-GPT + CLIP)."""

from __future__ import annotations

from dataclasses import dataclass

from aigamekit_shared.hardware import GIB, HardwareProfileBase, detect_profile
from aigamekit_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "MOTION3D_HW_AUTO"

# Reference peak for Motius T2M-GPT HumanML3D on fp32 (~2.5-3.2 GiB depending on CLIP backend).
ESTIMATED_PEAK_MIB = 3000


@dataclass(frozen=True)
class HardwareProfile(HardwareProfileBase):
    half: bool

    def summary(self) -> str:
        parts = [self.name, f"half={'on' if self.half else 'off'}"]
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        parts.append(f"peak~{ESTIMATED_PEAK_MIB}MiB")
        return " | ".join(parts)


def hw_auto_enabled() -> bool:
    return _hw_auto_enabled(HW_AUTO_ENV)


def profile_from_specs(gpus: list[tuple[int, int]]) -> HardwareProfile:
    if not gpus:
        return HardwareProfile(
            name="cpu",
            device="cpu",
            gpu_ids=None,
            half=False,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    multi = len(gpus) > 1
    gpu_ids = [idx for idx, _ in gpus] if multi else None
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"
    capacity_gib = total_gib if multi else largest_gib
    half = capacity_gib < 8.0

    return HardwareProfile(
        name=name,
        device="cuda",
        gpu_ids=gpu_ids,
        half=half,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> HardwareProfile:
    return detect_profile(profile_from_specs)


def estimate_peak_mib(*, half: bool = False) -> int:
    """Conservative UMS admit hint for motion3d backend."""
    if half:
        return max(1800, int(ESTIMATED_PEAK_MIB * 0.65))
    return ESTIMATED_PEAK_MIB
