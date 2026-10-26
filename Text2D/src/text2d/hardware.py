"""
Detecção automática de hardware → perfil de inferência FLUX.3 Image Klein.

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``-m``/``TEXT2D_MODEL_ID``, ``--cpu`` ganham).
Desligável com ``--no-hw-auto`` ou ``TEXT2D_HW_AUTO=0``.

Desde a migração FLUX.3 há **um só modelo** (transformer 5.6B + Qwen3-VL 4B):
o hw-auto já não escolhe checkpoint — decide só quantização (SDNQ runtime por
VRAM) e modo de colocação (group offload + streams vs full-GPU / split
multi-GPU).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aigamekit_shared.group_offload import (
    ALLOC_CONF_DEFAULT,  # noqa: F401 — re-export (testes usam daqui)
    ALLOC_CONF_GROUP_OFFLOAD,  # noqa: F401 — re-export
    ToolOffloadPolicy,
)
from aigamekit_shared.hardware import GIB, HardwareProfileBase, detect_profile
from aigamekit_shared.hardware import hw_auto_enabled as _hw_auto_enabled
from aigamekit_shared.lowvram import OFFLOAD_GROUP_STREAM, OFFLOAD_NONE, plan_offload

from .generator import (
    DEFAULT_MODEL_ID,
    FULL_GPU_BUDGET_FRACTION,
    GROUP_OFFLOAD_ENV,
    model_footprint,
)

HW_AUTO_ENV = "TEXT2D_HW_AUTO"

# ---------------------------------------------------------------------------
# needed_mib do fallback in-process quando GO+streams vai correr (calibrado
# na RTX 4050 6 GB com FLUX.2; FLUX.3 Klein tem pesos menores mas o ladder de
# ativação é semelhante — recalibrar na fase E2E): 1024² pico ~5.5 GiB; 512²
# ~2.6 GiB. O alloc conf por modo vive no Shared (aigamekit_shared.group_offload).
# ---------------------------------------------------------------------------
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


def _footprint_for(specs: list) -> Any:
    """Footprint único do FLUX.3 Klein (a assinatura specs mantém-se na POLICY)."""
    return model_footprint(DEFAULT_MODEL_ID)


# Política GO da tool — gate (specs livres, fraction 0.70), alloc conf por modo
# e needed_mib num só objeto (aigamekit_shared.group_offload.ToolOffloadPolicy).
POLICY = ToolOffloadPolicy(
    footprint_key="flux3-klein",
    footprint_fn=_footprint_for,
    tool_env_var=GROUP_OFFLOAD_ENV,
    full_gpu_budget_fraction=FULL_GPU_BUDGET_FRACTION,
)


def group_offload_intent(allow: bool = True) -> bool:
    """Intenção de group offload: flag ``--group-offload`` AND env kill-switch."""
    return POLICY.intent(allow)


@dataclass(frozen=True)
class Text2DHardwareProfile(HardwareProfileBase):
    model_id: str  # modelo BASE (constante; override real é -m / TEXT2D_MODEL_ID)
    memory_efficient: bool  # True = offload na colocação
    quant_preset: str  # preset SDNQ runtime ("none" | "sdnq-uint8" | ... ) por VRAM
    offload_mode: str = OFFLOAD_NONE  # modo do plano ("none" | "group_stream" | ...)

    def summary(self) -> str:
        parts = [self.name, "base=flux3-klein"]
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
    return {
        "allow_group_offload": POLICY.intent(),
        "full_gpu_budget_fraction": FULL_GPU_BUDGET_FRACTION if POLICY.intent() else None,
    }


def profile_from_specs(gpus: list[tuple[int, int]]) -> Text2DHardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU.

    Modelo único FLUX.3 Klein; delega quantização (runtime SDNQ) + offload ao
    planner partilhado. Multi-GPU (>=2 GPUs, total >=16 GiB) → split acelerate.
    """
    if not gpus:
        return Text2DHardwareProfile(
            name="cpu",
            device="cpu",
            model_id=DEFAULT_MODEL_ID,
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
        # Split multi-GPU: pesos divididos; quant decidido pelo planner.
        plan = plan_offload(gpus, model_footprint(DEFAULT_MODEL_ID), **_plan_kwargs())
        return Text2DHardwareProfile(
            name=name,
            device="cuda",
            model_id=DEFAULT_MODEL_ID,
            memory_efficient=plan.memory_efficient,
            gpu_ids=[idx for idx, _ in gpus],
            total_vram_gib=round(total_gib, 1),
            quant_preset=plan.quant_mode,
            offload_mode=plan.offload,
        )

    primary = max(gpus, key=lambda t: t[1])
    plan = plan_offload([primary], model_footprint(DEFAULT_MODEL_ID), allow_multi_gpu=False, **_plan_kwargs())
    return Text2DHardwareProfile(
        name=name,
        device="cuda",
        model_id=DEFAULT_MODEL_ID,
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

    Usa **specs com VRAM livre** (o mesmo sinal do placement real) e o footprint
    do FLUX.3 Klein — ver ``POLICY``.
    """
    hwp = detect_hardware_profile()
    if hwp.device != "cuda":
        return False
    return POLICY.will_engage()


def cuda_alloc_conf_for(group_offload: bool = True) -> str:
    """``PYTORCH_CUDA_ALLOC_CONF`` por modo — delega na ``POLICY``."""
    return POLICY.cuda_alloc_conf_for(group_offload)


def apply_alloc_conf_early(group_offload: bool = True) -> None:
    """``setdefault`` do alloc conf no arranque do CLI (antes da 1ª CUDA)."""
    POLICY.apply_alloc_conf_early(group_offload)
