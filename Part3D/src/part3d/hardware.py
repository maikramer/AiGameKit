"""
Detecção automática de hardware → perfil de inferência Hunyuan3D-Part.

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``--quantization``, ``--no-cpu-offload`` e
``--quality`` ganham sempre). Desligável com ``--no-hw-auto`` ou
``PART3D_HW_AUTO=0``.

Perfis por tier de VRAM:

- >= 10 GiB (single ou multi-GPU): FP16, sem CPU offload, sem quantização.
- 8.0 - 10.0 GiB: FP16 com CPU offload sequencial (sem SDNQ).
- < 8.0 GiB (ex: RTX 4050 6GB): memory-efficient — SDNQ uint8 + CPU offload
  + attention slicing. Pico medido ~5.2 GB em FP16; SDNQ reduz DiT.
- CPU (sem GPU): memory-efficient conservador.

Hardware de referência:
- RTX 4050 6GB → memory-efficient (SDNQ uint8 + CPU offload).
- RTX 4060 8GB → mid-tier (FP16 + CPU offload).
- RTX 3060 12GB / multi-GPU → FP16 full.
"""

from __future__ import annotations

from dataclasses import dataclass

from gamedev_shared.hardware import GIB, HardwareProfileBase, detect_profile
from gamedev_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "PART3D_HW_AUTO"

# >= 10 GiB: FP16 sem offload.
FULL_PROFILE_MIN_GIB = 10.0

# 8-10 GiB: FP16 com CPU offload.
MID_TIER_MIN_GIB = 8.0


def hw_auto_enabled() -> bool:
    """``PART3D_HW_AUTO=0`` / ``false`` / ``no`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Part3DHardwareProfile(HardwareProfileBase):
    memory_efficient: bool  # True = SDNQ + CPU offload + attention slicing
    cpu_offload: bool
    sdnq_preset: str | None  # e.g. "sdnq-uint8"; None = sem SDNQ runtime

    def summary(self) -> str:
        parts = [self.name]
        if self.memory_efficient:
            label = f"memory-efficient ({self.sdnq_preset})" if self.sdnq_preset else "memory-efficient"
            parts.append(label)
        elif self.cpu_offload:
            parts.append("FP16+offload")
        else:
            parts.append("FP16")
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
            cpu_offload=True,
            sdnq_preset="sdnq-uint8",
            gpu_ids=None,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    multi = len(gpus) > 1
    gpu_ids = [idx for idx, _ in gpus] if multi else None
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    # Capacidade efectiva: multi-GPU divide os pesos, por isso a soma conta;
    # single-GPU usa só a própria VRAM.
    capacity_gib = total_gib if multi else largest_gib

    if capacity_gib >= FULL_PROFILE_MIN_GIB:
        return Part3DHardwareProfile(
            name=name,
            device="cuda",
            memory_efficient=False,
            cpu_offload=False,
            sdnq_preset=None,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    if capacity_gib >= MID_TIER_MIN_GIB:
        return Part3DHardwareProfile(
            name=name,
            device="cuda",
            memory_efficient=False,
            cpu_offload=True,
            sdnq_preset=None,
            gpu_ids=None,
            total_vram_gib=round(total_gib, 1),
        )

    # < 8 GiB: SDNQ uint8 + CPU offload sequencial.
    return Part3DHardwareProfile(
        name=name,
        device="cuda",
        memory_efficient=True,
        cpu_offload=True,
        sdnq_preset="sdnq-uint8",
        gpu_ids=None,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> Part3DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return detect_profile(profile_from_specs)
