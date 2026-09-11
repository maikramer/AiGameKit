"""Detecção automática de hardware → perfil de inferência Sana (Text2Icon).

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``--cpu``/``--model``/``--quant-transformer``
ganham). Desligível com ``--no-hw-auto`` ou ``TEXT2ICON_HW_AUTO=0``.

Além de offload/clamp de resolução, o planner escolhe **transformer** e
**preset SDNQ do transformer** por tier de VRAM — "4 / 8 / 16 bit":
    >= 10 GiB  standard, sem SDNQ (bf16/fp16 nativo — "16-bit")
    >=  8 GiB  standard, SDNQ uint8 ("8-bit")
    >=  6 GiB  standard, SDNQ uint8, offload (VAE decode precisa de espaço)
    >=  4 GiB  standard, SDNQ int4 ("4-bit"), offload + clamp 512x512
    <   4 GiB  ternário Clark Air 1.58-bit (já pré-comprimido, sem SDNQ),
               offload + clamp 512x512 — hardware modesto
    sem GPU    ternário Clark Air 1.58-bit, CPU, clamp 512x512
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
    group_offload_will_engage as _group_offload_will_engage,
)
from aigamekit_shared.group_offload import is_group_offload_enabled
from aigamekit_shared.hardware import GIB, HardwareProfileBase, detect_profile
from aigamekit_shared.hardware import hw_auto_enabled as _hw_auto_enabled

from .generator import FULL_GPU_BUDGET_FRACTION, GROUP_OFFLOAD_ENV, STANDARD_TRANSFORMER_ID, TERNARY_TRANSFORMER_ID

HW_AUTO_ENV = "TEXT2ICON_HW_AUTO"

# Resolução nativa do pipeline Sana 512px.
DEFAULT_WIDTH = 512
DEFAULT_HEIGHT = 512


def hw_auto_enabled() -> bool:
    """``TEXT2ICON_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Text2IconHardwareProfile(HardwareProfileBase):
    cpu_offload: bool  # True = enable_model_cpu_offload
    max_width: int | None  # None = sem clamp; int = clamp se utilizador não explicitou
    max_height: int | None
    transformer_id: str  # standard ou ternário (ver módulo)
    transformer_sdnq_preset: str | None  # None = sem SDNQ no transformer ("16-bit")

    def summary(self) -> str:
        parts = [self.name]
        if self.cpu_offload:
            parts.append("cpu-offload")
        if self.max_width is not None:
            parts.append(f"clamp={self.max_width}x{self.max_height}")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        parts.append("ternário-1.58b" if self.transformer_id == TERNARY_TRANSFORMER_ID else "standard-600M")
        parts.append(f"sdnq={self.transformer_sdnq_preset}" if self.transformer_sdnq_preset else "sdnq=none(16b)")
        return " | ".join(parts)


def profile_from_specs(gpus: list[tuple[int, int]]) -> Text2IconHardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU."""
    if not gpus:
        return Text2IconHardwareProfile(
            name="cpu",
            device="cpu",
            cpu_offload=True,
            max_width=512,
            max_height=512,
            gpu_ids=None,
            total_vram_gib=0.0,
            transformer_id=TERNARY_TRANSFORMER_ID,
            transformer_sdnq_preset=None,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    gpu_ids = [idx for idx, _ in gpus] if len(gpus) > 1 else None

    # < 4 GiB: hardware modesto — ternário Clark Air (já ~1.85 bits/weight no
    # checkpoint, cabe folgado, não vale a pena empilhar SDNQ por cima).
    if largest_gib < 4.0:
        return Text2IconHardwareProfile(
            name=name,
            device="cuda",
            cpu_offload=True,
            max_width=512,
            max_height=512,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
            transformer_id=TERNARY_TRANSFORMER_ID,
            transformer_sdnq_preset=None,
        )

    if largest_gib >= 10.0:
        # Standard fp16/bf16 nativo, sem SDNQ no transformer ("16-bit"), sem offload.
        return Text2IconHardwareProfile(
            name=name,
            device="cuda",
            cpu_offload=False,
            max_width=None,
            max_height=None,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
            transformer_id=STANDARD_TRANSFORMER_ID,
            transformer_sdnq_preset=None,
        )

    if largest_gib >= 8.0:
        # Cabe folgado, mas SDNQ uint8 ("8-bit") reduz o pico e deixa margem ao Gemma.
        return Text2IconHardwareProfile(
            name=name,
            device="cuda",
            cpu_offload=False,
            max_width=None,
            max_height=None,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
            transformer_id=STANDARD_TRANSFORMER_ID,
            transformer_sdnq_preset="sdnq-uint8",
        )

    if largest_gib >= 6.0:
        # Limite: transformer+Gemma cabem, mas VAE decode precisa de espaço → offload.
        return Text2IconHardwareProfile(
            name=name,
            device="cuda",
            cpu_offload=True,
            max_width=None,
            max_height=None,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
            transformer_id=STANDARD_TRANSFORMER_ID,
            transformer_sdnq_preset="sdnq-uint8",
        )

    # 4-6 GiB: offload + clamp a 512 + SDNQ int4 ("4-bit") no transformer.
    return Text2IconHardwareProfile(
        name=name,
        device="cuda",
        cpu_offload=True,
        max_width=512,
        max_height=512,
        gpu_ids=gpu_ids,
        total_vram_gib=round(total_gib, 1),
        transformer_id=STANDARD_TRANSFORMER_ID,
        transformer_sdnq_preset="sdnq-int4",
    )


def detect_hardware_profile() -> Text2IconHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return detect_profile(profile_from_specs)


# ---------------------------------------------------------------------------
# Group offload + CUDA streams (default ON) — réplica do gate + alloc conf.
# O gate real (VRAM) vive no planner lowvram; aqui só a intenção + kill-switch.
# ---------------------------------------------------------------------------


def group_offload_intent(allow: bool = True) -> bool:
    """Intenção de group offload: flag ``--group-offload`` AND env kill-switch."""
    if not allow:
        return False
    return is_group_offload_enabled(tool_env_var=GROUP_OFFLOAD_ENV)


def group_offload_will_engage() -> bool:
    """Réplica pura do gate: o plano para o hardware ATUAL engaja group offload?

    Usa a versão do Shared com o footprint do Sana (``sana-sprint-600m``) e o
    mesmo ``allow_quant=("none",)`` do placement (o ctor já quantizou antes do
    load — o planner planeia só colocação). Usado antes do load (CLI/worker)
    para escolher o ``PYTORCH_CUDA_ALLOC_CONF`` certo e reduzir o
    ``needed_mib`` do fallback in-process (com GO o pico é ≈ ativação).
    """
    from aigamekit_shared.lowvram import get_footprint

    return _group_offload_will_engage(
        get_footprint("sana-sprint-600m"),
        full_gpu_budget_fraction=FULL_GPU_BUDGET_FRACTION,
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
