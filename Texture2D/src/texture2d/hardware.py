"""Detecção automática de hardware → perfil de inferência pattern-diffusion (SD2-base).

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``--low-vram``/``--cpu`` ganham). Desligível com ``--no-hw-auto``
ou ``TEXTURE2D_HW_AUTO=0``.

O modelo base é pattern-diffusion (Stable Diffusion 2-base, ~870M params) —
muito mais leve que FLUX.1-dev 12B: pesos fp16 ~2 GB, pico de VRAM ~4-6 GB em
512x512 nativo, ~6-8 GB em 768x768. O perfil decide apenas CPU offload e clamp
de resolução conforme a VRAM disponível.
"""

from __future__ import annotations

from dataclasses import dataclass

from gamedev_shared.hardware import GIB, cuda_gpu_specs
from gamedev_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "TEXTURE2D_HW_AUTO"

# Tiers (GiB da maior GPU) — calibrados para pattern-diffusion (SD2-base):
#   >= 8  full GPU, sem offload, resolução livre (default 512x512 nativo)
#   >= 6  sem offload, clamp a 512x512 (nativo)
#   >= 4  low_vram (group_offload no generator), clamp a 512x512
#   <  4  low_vram + clamp a 512x512 (idealmente sequential offload)

# Resolução nativa do pattern-diffusion (SD2-base).
DEFAULT_WIDTH = 512
DEFAULT_HEIGHT = 512


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
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU.

    Args:
        gpus: Lista de tuplos ``(device_index, vram_bytes)`` detectados.

    Returns:
        Perfil calibrado para pattern-diffusion (SD2-base, ~870M params).
    """
    if not gpus:
        return Texture2DHardwareProfile(
            name="cpu",
            device="cpu",
            low_vram=True,
            max_width=512,
            max_height=512,
            gpu_ids=None,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    gpu_ids = [idx for idx, _ in gpus] if len(gpus) > 1 else None

    if largest_gib >= 8.0:
        # Full GPU, sem offload, resolução livre — SD2-base cabe com folga
        # (pico ~6-8 GB em 768x768).
        return Texture2DHardwareProfile(
            name=name,
            device="cuda",
            low_vram=False,
            max_width=None,
            max_height=None,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    if largest_gib >= 6.0:
        # Sem offload; clamp a 512x512 (resolução nativa do SD2-base).
        return Texture2DHardwareProfile(
            name=name,
            device="cuda",
            low_vram=False,
            max_width=512,
            max_height=512,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    if largest_gib >= 4.0:
        # low_vram (group_offload no generator); clamp a 512x512.
        return Texture2DHardwareProfile(
            name=name,
            device="cuda",
            low_vram=True,
            max_width=512,
            max_height=512,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    # < 4 GiB: low_vram + clamp a 512x512. Idealmente sequential offload;
    # o generator trata low_vram como group_offload, que cobre o essencial
    # mesmo nas GPUs mais pequenas.
    return Texture2DHardwareProfile(
        name=name,
        device="cuda",
        low_vram=True,
        max_width=512,
        max_height=512,
        gpu_ids=gpu_ids,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> Texture2DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_specs())
