"""Detecção automática de hardware → perfil de inferência SD1.5 + circular padding.

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``--cpu`` ganha). Desligável com ``--no-hw-auto``
ou ``TEXTURE2D_HW_AUTO=0``.

SD1.5 fp16 ocupa ~2.5 GB de VRAM e cabe inteiro em qualquer GPU CUDA moderna
(≥4 GiB) — **não há offloads, vae slicing, group offload nem clamp de resolução**.
O perfil deteta apenas device (cuda/cpu), multi-GPU (para display) e VRAM total.
"""

from __future__ import annotations

from dataclasses import dataclass

from aigamekit_shared.hardware import GIB, HardwareProfileBase, detect_profile
from aigamekit_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "TEXTURE2D_HW_AUTO"

# Resolução nativa do SD1.5 (referência para o summary).
DEFAULT_WIDTH = 512
DEFAULT_HEIGHT = 512


def hw_auto_enabled() -> bool:
    """``TEXTURE2D_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Texture2DHardwareProfile(HardwareProfileBase):
    max_width: int | None  # Sempre None (SD1.5 não precisa de clamp).
    max_height: int | None

    def summary(self) -> str:
        parts = [self.name]
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def profile_from_specs(gpus: list[tuple[int, int]]) -> Texture2DHardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU.

    SD1.5 fp16 (~2.5 GB) cabe em qualquer GPU CUDA — não há tiers de offload/clamp.
    A deteção serve apenas para: escolher device (cuda/cpu), multi-GPU (display) e
    reportar VRAM total.
    """
    if not gpus:
        return Texture2DHardwareProfile(
            name="cpu",
            device="cpu",
            max_width=None,
            max_height=None,
            gpu_ids=None,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    gpu_ids = [idx for idx, _ in gpus] if len(gpus) > 1 else None

    return Texture2DHardwareProfile(
        name=name,
        device="cuda",
        max_width=None,
        max_height=None,
        gpu_ids=gpu_ids,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> Texture2DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return detect_profile(profile_from_specs)
