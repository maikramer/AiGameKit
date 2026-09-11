"""Detecção automática de hardware → perfil de inferência SD1.5 + circular padding.

Soft resolution no CLI: só preenche o que o utilizador não define (flags
explícitas, ``--cpu`` ganha). Desligável com ``--no-hw-auto``
ou ``TEXTURE2D_HW_AUTO=0``.

SD1.5 fp16 (~2.5 GB) cabe inteiro na maioria das GPUs CUDA (≥4 GiB); em GPUs
apertadas (ou ocupadas) o planner engaja **group offload + CUDA streams**
(pico ≈ ativação) em vez de full-GPU sem margem — o mesmo padrão das tools 2D.
"""

from __future__ import annotations

from dataclasses import dataclass

from aigamekit_shared.group_offload import ToolOffloadPolicy
from aigamekit_shared.hardware import GIB, HardwareProfileBase, detect_profile
from aigamekit_shared.hardware import hw_auto_enabled as _hw_auto_enabled
from aigamekit_shared.lowvram import OFFLOAD_GROUP_STREAM, OFFLOAD_NONE

HW_AUTO_ENV = "TEXTURE2D_HW_AUTO"
GROUP_OFFLOAD_ENV = "TEXTURE2D_GROUP_OFFLOAD"

# Gate de folga comum das tools 2D (espelha o generator): full-GPU só com
# pico ≤70% do orçamento; sem folga → group offload + streams.
FULL_GPU_BUDGET_FRACTION = 0.70

# Resolução nativa do SD1.5 (referência para o summary).
DEFAULT_WIDTH = 512
DEFAULT_HEIGHT = 512

# Política GO da tool — gate (specs livres, fraction 0.70), alloc conf por modo,
# needed_mib do fallback in-process e offload_mode do perfil, num só objeto
# (aigamekit_shared.group_offload.ToolOffloadPolicy).
POLICY = ToolOffloadPolicy(
    footprint_key="sd15-base",
    tool_env_var=GROUP_OFFLOAD_ENV,
    full_gpu_budget_fraction=FULL_GPU_BUDGET_FRACTION,
    allow_quant=("none",),  # SD1.5 sempre fp16 (sem quant runtime)
)


def hw_auto_enabled() -> bool:
    """``TEXTURE2D_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Texture2DHardwareProfile(HardwareProfileBase):
    max_width: int | None  # Sempre None (SD1.5 não precisa de clamp).
    max_height: int | None
    offload_mode: str = OFFLOAD_NONE  # "none" | "group_stream" | ...

    def summary(self) -> str:
        parts = [self.name]
        if self.offload_mode == OFFLOAD_GROUP_STREAM:
            parts.append("group-offload+streams")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def profile_from_specs(gpus: list[tuple[int, int]]) -> Texture2DHardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU.

    SD1.5 fp16 (~2.5 GB) cabe na maioria das GPUs; o ``offload_mode`` reporta se
    o planner iria engajar group offload (GPU apertada/ocupada).
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
        offload_mode=POLICY.plan_offload_mode(gpus),
    )


def detect_hardware_profile() -> Texture2DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return detect_profile(profile_from_specs)
