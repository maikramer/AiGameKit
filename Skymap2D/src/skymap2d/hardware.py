"""Detecção automática de hardware → perfil de inferência FLUX.1-dev + equirect LoRA.

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``--cpu`` ganha). Desligável com ``--no-hw-auto``
ou ``SKYMAP2D_HW_AUTO=0``.

O modelo base é sempre FLUX.1-dev (bf16 + SDNQ); o perfil decide apenas CPU
offload e clamp de resolução conforme a VRAM disponível. Resolução por defeito
2048x1024 (panorama equirectangular 2:1).
"""

from __future__ import annotations

from dataclasses import dataclass

from aigamekit_shared.hardware import GIB, HardwareProfileBase, detect_profile
from aigamekit_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "SKYMAP2D_HW_AUTO"

# Tiers (GiB da maior GPU):
#   >= 12  full GPU, sem offload, resolução livre (default 2048x1024)
#   >=  8  group offload + streams (auto via planner), clamp a 2048x1024
#   <   8  group offload + streams, clamp a 1024x512
#   <   6  group offload + streams, clamp a 1024x512 (2048x1024 é inviável em 6GB)

DEFAULT_WIDTH = 2048
DEFAULT_HEIGHT = 1024


def hw_auto_enabled() -> bool:
    """``SKYMAP2D_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Skymap2DHardwareProfile(HardwareProfileBase):
    memory_efficient: bool  # True = enable_model_cpu_offload
    max_width: int | None  # None = sem clamp; int = clamp se utilizador não explicitou
    max_height: int | None

    def summary(self) -> str:
        parts = [self.name]
        if self.memory_efficient:
            parts.append("cpu-offload")
        if self.max_width is not None:
            parts.append(f"clamp={self.max_width}x{self.max_height}")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def profile_from_specs(gpus: list[tuple[int, int]]) -> Skymap2DHardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU."""
    if not gpus:
        return Skymap2DHardwareProfile(
            name="cpu",
            device="cpu",
            memory_efficient=True,
            max_width=1024,
            max_height=512,
            gpu_ids=None,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    gpu_ids = [idx for idx, _ in gpus] if len(gpus) > 1 else None

    if largest_gib >= 12.0:
        # Full GPU, sem offload, resolução livre.
        return Skymap2DHardwareProfile(
            name=name,
            device="cuda",
            memory_efficient=False,
            max_width=None,
            max_height=None,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    if largest_gib >= 8.0:
        # group offload + streams (auto via planner), clamp a 2048x1024.
        return Skymap2DHardwareProfile(
            name=name,
            device="cuda",
            memory_efficient=True,
            max_width=2048,
            max_height=1024,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    # < 8 GiB (inclui < 6): offload + clamp a 1024x512.
    # 2048x1024 é inviável mesmo em 6GB com FLUX.1-dev.
    return Skymap2DHardwareProfile(
        name=name,
        device="cuda",
        memory_efficient=True,
        max_width=1024,
        max_height=512,
        gpu_ids=gpu_ids,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> Skymap2DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return detect_profile(profile_from_specs)
