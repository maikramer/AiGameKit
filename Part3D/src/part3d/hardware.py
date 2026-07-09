"""
Detecção automática de hardware → perfil de inferência Hunyuan3D-Part.

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``--quantization``, ``--no-cpu-offload`` e
``--quality`` ganham sempre). Desligável com ``--no-hw-auto`` ou
``PART3D_HW_AUTO=0``.

Perfis por tier de VRAM:

- >= 6 GiB (single ou multi-GPU): defaults do CLI (CPU offload já ON por
  defeito, quantização ``auto``). Sem activar o modo memory-efficient.
- < 6 GiB (ex: RTX 4050 6GB): activa o modo memory-efficient (quantização auto +
  CPU offload sequencial + attention slicing). Pico medido ~5.2 GB em FP16.
- CPU (sem GPU): modo memory-efficient (conservador).

Hardware de referência:
- RTX 4050 6GB → modo memory-efficient (CPU offload + quantização auto).
- RTX 3060 12GB / 4060 8GB → defaults (sem modo memory-efficient).
"""

from __future__ import annotations

from dataclasses import dataclass

from gamedev_shared.hardware import GIB, cuda_gpu_specs
from gamedev_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "PART3D_HW_AUTO"

# Mínimo (GiB) para correr sem activar o bundle memory-efficient. Abaixo deste
# limiar o DiT (~3.3 GB FP16) + conditioner + VAE + ativações pedem mais do que
# a GPU oferece sem CPU offload sequencial + quantização.
MEMORY_EFFICIENT_MIN_GIB = 6.0


def hw_auto_enabled() -> bool:
    """``PART3D_HW_AUTO=0`` / ``false`` / ``no`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Part3DHardwareProfile:
    name: str
    device: str  # "cuda" | "cpu"
    memory_efficient: bool  # True = quantização auto + CPU offload sequencial
    gpu_ids: list[int] | None  # >1 GPU: split multi-GPU; senão None
    total_vram_gib: float

    def summary(self) -> str:
        parts = [self.name]
        if self.memory_efficient:
            parts.append("memory-efficient")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def profile_from_specs(gpus: list[tuple[int, int]]) -> Part3DHardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU."""
    if not gpus:
        return Part3DHardwareProfile(
            name="cpu",
            device="cpu",
            memory_efficient=True,
            gpu_ids=None,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    multi = len(gpus) > 1
    gpu_ids = [idx for idx, _ in gpus] if multi else None
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    # Capacidade efectiva: multi-GPU divide os pesos (accelerate), por isso a
    # soma conta para o tier; single-GPU usa só a própria VRAM.
    capacity_gib = total_gib if multi else largest_gib

    # < 6 GiB: activa o modo memory-efficient (CPU offload + quantização auto).
    return Part3DHardwareProfile(
        name=name,
        device="cuda",
        memory_efficient=capacity_gib < MEMORY_EFFICIENT_MIN_GIB,
        gpu_ids=gpu_ids,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> Part3DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_specs())
