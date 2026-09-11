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

from aigamekit_shared.group_offload import (
    ALLOC_CONF_DEFAULT,  # noqa: F401 — re-export (testes/CLI usam daqui)
    ALLOC_CONF_GROUP_OFFLOAD,  # noqa: F401 — re-export
    is_group_offload_enabled,
)
from aigamekit_shared.group_offload import (
    apply_alloc_conf_early as _apply_alloc_conf_early,
)
from aigamekit_shared.group_offload import (
    cuda_alloc_conf_for as _cuda_alloc_conf_for,
)
from aigamekit_shared.group_offload import (
    group_offload_will_engage as _shared_group_offload_will_engage,
)
from aigamekit_shared.hardware import GIB, HardwareProfileBase, detect_profile
from aigamekit_shared.hardware import hw_auto_enabled as _hw_auto_enabled
from aigamekit_shared.lowvram import OFFLOAD_GROUP_STREAM, OFFLOAD_NONE, get_footprint, plan_offload

from .generator import SkymapGenerator

HW_AUTO_ENV = "SKYMAP2D_HW_AUTO"

# Kill-switch por tool do group offload (precedência: tool > global AIGAMEKIT_GROUP_OFFLOAD).
GROUP_OFFLOAD_ENV = "SKYMAP2D_GROUP_OFFLOAD"

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


def group_offload_intent(allow: bool = True) -> bool:
    """Intenção de group offload: flag ``--group-offload`` AND env kill-switch."""
    if not allow:
        return False
    return is_group_offload_enabled(tool_env_var=GROUP_OFFLOAD_ENV)


@dataclass(frozen=True)
class Skymap2DHardwareProfile(HardwareProfileBase):
    memory_efficient: bool  # True = enable_model_cpu_offload
    max_width: int | None  # None = sem clamp; int = clamp se utilizador não explicitou
    max_height: int | None
    offload_mode: str = OFFLOAD_NONE  # modo do plano ("none" | "group_stream" | ...)

    def summary(self) -> str:
        parts = [self.name]
        if self.offload_mode == OFFLOAD_GROUP_STREAM:
            parts.append("group-offload+streams")
        elif self.memory_efficient:
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

    # offload_mode: réplica do gate GO do generator sobre o single-GPU principal
    # (fonte única: mesmos knobs de folga/kill-switch). O caminho dos clamp
    # tiers mantém-se — o offload_mode é observabilidade para o summary.
    _go_intent = group_offload_intent()
    plan = plan_offload(
        [max(gpus, key=lambda t: t[1])],
        get_footprint("flux-dev-uint4"),
        allow_quant=("none",),
        allow_group_offload=_go_intent,
        full_gpu_budget_fraction=SkymapGenerator.FULL_GPU_BUDGET_FRACTION if _go_intent else None,
    )

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
            offload_mode=plan.offload,
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
            offload_mode=plan.offload,
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
        offload_mode=plan.offload,
    )


def detect_hardware_profile() -> Skymap2DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return detect_profile(profile_from_specs)


def group_offload_will_engage() -> bool:
    """Réplica pura do gate: o plano para o hardware ATUAL engaja group offload?

    Usado antes do load (CLI/worker) para: (a) escolher o
    ``PYTORCH_CUDA_ALLOC_CONF`` certo; (b) reduzir o ``needed_mib`` do
    fallback in-process (com GO o pico é ≈ ativação, não pesos+ativação).
    """
    return _shared_group_offload_will_engage(
        get_footprint("flux-dev-uint4"),
        full_gpu_budget_fraction=SkymapGenerator.FULL_GPU_BUDGET_FRACTION,
        tool_env_var=GROUP_OFFLOAD_ENV,
        allow_quant=("none",),
    )


def cuda_alloc_conf_for(group_offload: bool = True) -> str:
    """``PYTORCH_CUDA_ALLOC_CONF`` por modo — chamar ANTES da 1ª alocação CUDA.

    Args:
        group_offload: intenção (flag ``--group-offload`` + env). O conf GO só
            é devolvido quando o offload **vai correr** neste hardware — GPUs
            grandes voltam ao conf clássico (max_split reduz o pico).
    """
    return _cuda_alloc_conf_for(group_offload_intent(group_offload) and group_offload_will_engage())


def apply_alloc_conf_early(group_offload: bool = True) -> None:
    """``setdefault`` do alloc conf no arranque do CLI (torch lê o env na 1ª
    alocação CUDA; o override explícito do utilizador ganha sempre)."""
    _apply_alloc_conf_early(group_offload_intent(group_offload) and group_offload_will_engage())
