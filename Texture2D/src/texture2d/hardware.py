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

from aigamekit_shared.group_offload import (
    apply_alloc_conf_early as _apply_alloc_conf_early,
)
from aigamekit_shared.group_offload import (
    cuda_alloc_conf_for as _cuda_alloc_conf_for,
)
from aigamekit_shared.group_offload import (
    group_offload_will_engage as _shared_will_engage,
)
from aigamekit_shared.group_offload import (
    is_group_offload_enabled,
)
from aigamekit_shared.hardware import GIB, HardwareProfileBase, detect_profile
from aigamekit_shared.hardware import hw_auto_enabled as _hw_auto_enabled
from aigamekit_shared.lowvram import OFFLOAD_GROUP_STREAM, OFFLOAD_NONE, get_footprint, plan_offload

HW_AUTO_ENV = "TEXTURE2D_HW_AUTO"
GROUP_OFFLOAD_ENV = "TEXTURE2D_GROUP_OFFLOAD"

# Gate de folga comum das tools 2D (espelha o generator): full-GPU só com
# pico ≤70% do orçamento; sem folga → group offload + streams.
FULL_GPU_BUDGET_FRACTION = 0.70

# Resolução nativa do SD1.5 (referência para o summary).
DEFAULT_WIDTH = 512
DEFAULT_HEIGHT = 512


def hw_auto_enabled() -> bool:
    """``TEXTURE2D_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


def group_offload_intent(allow: bool = True) -> bool:
    """Intenção de group offload: flag ``--group-offload`` AND env kill-switch."""
    if not allow:
        return False
    return is_group_offload_enabled(tool_env_var=GROUP_OFFLOAD_ENV)


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


def _offload_mode(gpus: list[tuple[int, int]]) -> str:
    """Modo do planner para a GPU primária (mesma política do generator)."""
    if not group_offload_intent() or not gpus:
        return OFFLOAD_NONE
    primary = max(gpus, key=lambda t: t[1])
    plan = plan_offload(
        [primary],
        get_footprint("sd15-base"),
        allow_multi_gpu=False,
        allow_quant=("none",),  # SD1.5 sempre fp16 (sem quant runtime)
        full_gpu_budget_fraction=FULL_GPU_BUDGET_FRACTION,
    )
    return plan.offload


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
        offload_mode=_offload_mode(gpus),
    )


def detect_hardware_profile() -> Texture2DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return detect_profile(profile_from_specs)


def group_offload_will_engage() -> bool:
    """Réplica pura do gate: o plano para o hardware ATUAL engaja group offload?

    Usa **specs com VRAM livre** (o mesmo sinal do placement real) — numa GPU
    parcialmente ocupada o gate concorda com o planner. O ``offload_mode`` do
    perfil (specs totais) fica para display.
    """
    if not group_offload_intent():
        return False
    hwp = detect_hardware_profile()
    if hwp.device != "cuda":
        return False
    return _shared_will_engage(
        get_footprint("sd15-base"),
        full_gpu_budget_fraction=FULL_GPU_BUDGET_FRACTION,
        tool_env_var=GROUP_OFFLOAD_ENV,
        allow_quant=("none",),
    )


def cuda_alloc_conf_for(group_offload: bool = True) -> str:
    """``PYTORCH_CUDA_ALLOC_CONF`` por modo — ver aigamekit_shared.group_offload."""
    return _cuda_alloc_conf_for(group_offload_intent(group_offload) and group_offload_will_engage())


def apply_alloc_conf_early(group_offload: bool = True) -> None:
    """``setdefault`` do alloc conf no arranque do CLI (antes da 1ª alocação CUDA)."""
    _apply_alloc_conf_early(group_offload_intent(group_offload) and group_offload_will_engage())
