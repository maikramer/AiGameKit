"""
Detecção automática de hardware → perfil de inferência FLUX.2 Klein.

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``-m``/``TEXT2D_MODEL_ID``, ``--cpu`` ganham).
Desligável com ``--no-hw-auto`` ou ``TEXT2D_HW_AUTO=0``.

Perfis para os hardwares de referência:
- 2x RTX 3060 12GB → 9B SDNQ, split multi-GPU (transformer+vae / text_encoder).
- RTX 4050 6GB → 4B SDNQ int4 + group offload com streams (pico ≈ ativação).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from aigamekit_shared.group_offload import is_group_offload_enabled
from aigamekit_shared.hardware import GIB, HardwareProfileBase, detect_profile
from aigamekit_shared.hardware import hw_auto_enabled as _hw_auto_enabled
from aigamekit_shared.lowvram import OFFLOAD_GROUP_STREAM, OFFLOAD_NONE, plan_offload

from .generator import (
    FULL_GPU_BUDGET_FRACTION,
    GROUP_OFFLOAD_ENV,
    HIGH_VRAM_MODEL_ID,
    LOW_VRAM_MODEL_ID,
    model_footprint,
)

HW_AUTO_ENV = "TEXT2D_HW_AUTO"

# ---------------------------------------------------------------------------
# PYTORCH_CUDA_ALLOC_CONF por modo — a lição do Paint3D (OOM de fragmentação):
# com group offload, o streaming faz churn de milhares de onloads pequenos
# intercalados com blocos grandes de ativação; max_split_size_mb proíbe partir
# blocos grandes e o reserved explode por fragmentação. Sem GO, max_split +
# gc_threshold reduzem o pico de alocação clássico.
# ---------------------------------------------------------------------------
ALLOC_CONF_DEFAULT = "expandable_segments:True,max_split_size_mb:128,garbage_collection_threshold:0.6"
ALLOC_CONF_GROUP_OFFLOAD = "expandable_segments:True"

# needed_mib do fallback in-process quando GO+streams vai correr: pico ≈
# ativação + trânsito de grupos + VAE decode — não pesos+ativação, que
# recusava jobs que correm bem. Calibrado na RTX 4050 6 GB (2026-09-11,
# GO+streams+int4): 1024² pico ~5.5 GiB (chunks de ativação usam a VRAM
# livre pós-offload); 512² ~2.6 GiB.
GROUP_OFFLOAD_NEEDED_MIB = 5400
GROUP_OFFLOAD_NEEDED_MIB_SMALL = 3000  # lados ≤ 640 px (quality fast/low)


def group_offload_needed_mib(width: int, height: int) -> int:
    """``needed_mib`` do fallback in-process com GO, pela resolução do pedido."""
    if max(int(width), int(height)) <= 640:
        return GROUP_OFFLOAD_NEEDED_MIB_SMALL
    return GROUP_OFFLOAD_NEEDED_MIB


def hw_auto_enabled() -> bool:
    """``TEXT2D_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


def group_offload_intent(allow: bool = True) -> bool:
    """Intenção de group offload: flag ``--group-offload`` AND env kill-switch."""
    if not allow:
        return False
    return is_group_offload_enabled(tool_env_var=GROUP_OFFLOAD_ENV)


@dataclass(frozen=True)
class Text2DHardwareProfile(HardwareProfileBase):
    model_id: str  # modelo BASE sugerido (não sobrepõe -m / TEXT2D_MODEL_ID)
    memory_efficient: bool  # True = offload na colocação
    quant_preset: str  # preset SDNQ runtime ("none" | "sdnq-uint8" | ... ) por VRAM
    offload_mode: str = OFFLOAD_NONE  # modo do plano ("none" | "group_stream" | ...)

    def summary(self) -> str:
        model_tag = "9B" if self.model_id == HIGH_VRAM_MODEL_ID else "4B"
        parts = [self.name, f"base={model_tag}"]
        if self.quant_preset != "none":
            parts.append(f"quant={self.quant_preset}")
        if self.offload_mode == OFFLOAD_GROUP_STREAM:
            parts.append("group-offload+streams")
        elif self.memory_efficient:
            parts.append("cpu-offload")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def _plan_kwargs() -> dict:
    """Knobs partilhados pelo hw-profile e pelo generator (mesma política)."""
    go = group_offload_intent()
    return {
        "allow_group_offload": go,
        "full_gpu_budget_fraction": FULL_GPU_BUDGET_FRACTION if go else None,
    }


def profile_from_specs(gpus: list[tuple[int, int]]) -> Text2DHardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU.

    Escolhe o modelo BASE por VRAM (9B >=10GB senão 4B) e delega quantização (runtime
    SDNQ) + offload ao planner partilhado — o checkpoint deixou de ser pré-quantizado.
    """
    if not gpus:
        return Text2DHardwareProfile(
            name="cpu",
            device="cpu",
            model_id=LOW_VRAM_MODEL_ID,
            memory_efficient=True,
            gpu_ids=None,
            total_vram_gib=0.0,
            quant_preset="none",
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    multi = len(gpus) > 1
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    if multi and total_gib >= 16.0:
        # Split multi-GPU: pesos do 9B base divididos; quant decidido pelo planner.
        plan = plan_offload(gpus, model_footprint(HIGH_VRAM_MODEL_ID), **_plan_kwargs())
        return Text2DHardwareProfile(
            name=name,
            device="cuda",
            model_id=HIGH_VRAM_MODEL_ID,
            memory_efficient=plan.memory_efficient,
            gpu_ids=[idx for idx, _ in gpus],
            total_vram_gib=round(total_gib, 1),
            quant_preset=plan.quant_mode,
            offload_mode=plan.offload,
        )

    primary = max(gpus, key=lambda t: t[1])
    model_id = HIGH_VRAM_MODEL_ID if largest_gib >= 10.0 else LOW_VRAM_MODEL_ID
    plan = plan_offload([primary], model_footprint(model_id), allow_multi_gpu=False, **_plan_kwargs())
    return Text2DHardwareProfile(
        name=name,
        device="cuda",
        model_id=model_id,
        memory_efficient=plan.memory_efficient,
        gpu_ids=None,
        total_vram_gib=round(total_gib, 1),
        quant_preset=plan.quant_mode,
        offload_mode=plan.offload,
    )


def detect_hardware_profile() -> Text2DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return detect_profile(profile_from_specs)


def group_offload_will_engage() -> bool:
    """Réplica pura do gate: o plano para o hardware ATUAL engaja group offload?

    Usado antes do load (CLI/worker) para: (a) escolher o
    ``PYTORCH_CUDA_ALLOC_CONF`` certo; (b) reduzir o ``needed_mib`` do
    fallback in-process (com GO o pico é ≈ ativação, não pesos+ativação).
    """
    if not group_offload_intent():
        return False
    hwp = detect_hardware_profile()
    return hwp.device == "cuda" and hwp.offload_mode == OFFLOAD_GROUP_STREAM


def cuda_alloc_conf_for(group_offload: bool = True) -> str:
    """``PYTORCH_CUDA_ALLOC_CONF`` por modo — chamar ANTES da 1ª alocação CUDA.

    Args:
        group_offload: intenção (flag ``--group-offload`` + env). O conf GO só
            é devolvido quando o offload **vai correr** neste hardware — GPUs
            grandes voltam ao conf clássico (max_split reduz o pico).
    """
    if group_offload_intent(group_offload) and group_offload_will_engage():
        return ALLOC_CONF_GROUP_OFFLOAD
    return ALLOC_CONF_DEFAULT


def apply_alloc_conf_early(group_offload: bool = True) -> None:
    """``setdefault`` do alloc conf no arranque do CLI (torch lê o env na 1ª
    alocação CUDA; o override explícito do utilizador ganha sempre)."""
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", cuda_alloc_conf_for(group_offload))
