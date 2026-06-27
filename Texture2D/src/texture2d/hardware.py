"""Detecção automática de hardware → perfil de inferência FLUX.1-dev + seamless LoRA.

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``--low-vram``/``--cpu`` ganham). Desligável com ``--no-hw-auto``
ou ``TEXTURE2D_HW_AUTO=0``.

O modelo base é sempre FLUX.1-dev com SDNQ quantized matmul aplicado; o perfil
decide apenas CPU offload e clamp de resolução conforme a VRAM disponível.
"""

from __future__ import annotations

from dataclasses import dataclass

from gamedev_shared.hardware import GIB, cuda_gpu_specs
from gamedev_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "TEXTURE2D_HW_AUTO"

# Tiers (GiB da maior GPU):
#   >= 12  full GPU, sem offload, resolução livre (default 1024x1024)
#   >=  8  enable_model_cpu_offload, resolução livre
#   <   8  offload + clamp a 1024 (se o utilizador pediu mais alto)
#   <   6  offload + clamp a 768x768

# Resolução por defeito do Texture2D (mantida como referência para o summary).
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 1024


def hw_auto_enabled() -> bool:
    """``TEXTURE2D_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Texture2DHardwareProfile:
    name: str
    device: str  # "cuda" | "cpu"
    low_vram: bool  # True = enable_model_cpu_offload
    max_width: int | None  # None = sem clamp; int = clamp se utilizador não explicitou
    max_height: int | None
    gpu_ids: list[int] | None  # >1 GPU: split multi-GPU; senão None
    total_vram_gib: float

    def summary(self) -> str:
        parts = [self.name]
        if self.low_vram:
            parts.append("cpu-offload")
        if self.max_width is not None:
            parts.append(f"clamp={self.max_width}x{self.max_height}")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def profile_from_specs(gpus: list[tuple[int, int]]) -> Texture2DHardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU."""
    if not gpus:
        return Texture2DHardwareProfile(
            name="cpu",
            device="cpu",
            low_vram=True,
            max_width=768,
            max_height=768,
            gpu_ids=None,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    gpu_ids = [idx for idx, _ in gpus] if len(gpus) > 1 else None

    if largest_gib >= 12.0:
        # Full GPU, sem offload, resolução livre.
        return Texture2DHardwareProfile(
            name=name,
            device="cuda",
            low_vram=False,
            max_width=None,
            max_height=None,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    if largest_gib >= 8.0:
        # Offload módulo-a-módulo, resolução livre.
        return Texture2DHardwareProfile(
            name=name,
            device="cuda",
            low_vram=True,
            max_width=None,
            max_height=None,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    if largest_gib >= 6.0:
        # Offload + clamp a 1024.
        return Texture2DHardwareProfile(
            name=name,
            device="cuda",
            low_vram=True,
            max_width=1024,
            max_height=1024,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    # < 6 GiB: offload + clamp a 768x768.
    return Texture2DHardwareProfile(
        name=name,
        device="cuda",
        low_vram=True,
        max_width=768,
        max_height=768,
        gpu_ids=gpu_ids,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> Texture2DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_specs())
