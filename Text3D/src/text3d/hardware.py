"""
Detecção automática de hardware → perfil de inferência Hunyuan3D.

Mapeia GPUs CUDA visíveis para um perfil (steps/octree/chunks, SDNQ, multi-GPU,
volume decoder). Soft resolution no CLI: só preenche o que o utilizador não
definiu explicitamente — flags manuais, ``--quality`` e ``--preset`` têm sempre
precedência. Desligável com ``--no-hw-auto`` ou ``TEXT3D_HW_AUTO=0``.

Perfis validados nos dois hardwares de referência:
- 2x RTX 3060 12GB (24GB total) → multi-GPU dispatch, hq, sem quantização.
- RTX 4050 6GB → balanced + SDNQ INT4 + CPU offload (defeito do Text2D).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import torch

from . import defaults as _defaults

GIB = 1024**3


@dataclass(frozen=True)
class HardwareProfile:
    name: str
    device: str  # "cuda" | "cpu"
    gpu_ids: list[int] | None  # >1 GPU: lista para MultiGPUPlanner; senão None
    sdnq_preset: str | None  # None = sem quantização
    steps: int
    octree: int
    chunks: int
    volume_decoder: str
    total_vram_gib: float

    def summary(self) -> str:
        parts = [
            f"{self.name}",
            f"steps={self.steps} octree={self.octree} chunks={self.chunks}",
            f"decoder={self.volume_decoder}",
        ]
        if self.sdnq_preset:
            parts.append(f"sdnq={self.sdnq_preset}")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def hw_auto_enabled() -> bool:
    """``TEXT3D_HW_AUTO=0`` / ``false`` / ``no`` desliga a auto-detecção."""
    return os.environ.get("TEXT3D_HW_AUTO", "1").strip().lower() not in ("0", "false", "no")


def cuda_gpu_specs() -> list[tuple[int, int]]:
    """Lista (índice, VRAM total em bytes) das GPUs CUDA visíveis."""
    if not torch.cuda.is_available():
        return []
    specs: list[tuple[int, int]] = []
    for i in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(i)
        specs.append((i, int(props.total_memory)))
    return specs


def profile_from_specs(gpus: list[tuple[int, int]]) -> HardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU."""
    fast = _defaults.PRESET_HUNYUAN["fast"]
    balanced = _defaults.PRESET_HUNYUAN["balanced"]
    hq = _defaults.PRESET_HUNYUAN["hq"]

    if not gpus:
        return HardwareProfile(
            name="cpu",
            device="cpu",
            gpu_ids=None,
            sdnq_preset=None,
            steps=fast["steps"],
            octree=fast["octree"],
            chunks=fast["chunks"],
            volume_decoder="hierarchical",
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    multi = len(gpus) > 1
    gpu_ids = [idx for idx, _ in gpus] if multi else None
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    # Capacidade efectiva: multi-GPU divide os pesos do DiT (accelerate),
    # por isso a soma conta para o tier; single-GPU usa só a própria VRAM.
    capacity_gib = total_gib if multi else largest_gib

    if capacity_gib >= 10.0:
        tier = hq
        sdnq: str | None = None
    elif capacity_gib >= 7.5:
        tier = balanced
        sdnq = None
    elif capacity_gib >= 5.0:
        # ex.: RTX 4050 6GB — balanced só fecha com DiT quantizado INT4
        tier = balanced
        sdnq = "sdnq-int4"
    else:
        tier = fast
        sdnq = "sdnq-int4"

    return HardwareProfile(
        name=name,
        device="cuda",
        gpu_ids=gpu_ids,
        sdnq_preset=sdnq,
        steps=tier["steps"],
        octree=tier["octree"],
        chunks=tier["chunks"],
        volume_decoder="hierarchical",
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> HardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_specs())
