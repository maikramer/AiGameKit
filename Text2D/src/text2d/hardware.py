"""
Detecção automática de hardware → perfil de inferência FLUX.2 Klein.

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``-m``/``TEXT2D_MODEL_ID``, ``--low-vram``/``--cpu`` ganham).
Desligável com ``--no-hw-auto`` ou ``TEXT2D_HW_AUTO=0``.

Perfis para os hardwares de referência:
- 2x RTX 3060 12GB → 9B SDNQ, split multi-GPU (transformer+vae / text_encoder).
- RTX 4050 6GB → 4B SDNQ (tier decide full-GPU vs CPU offload pela VRAM).
"""

from __future__ import annotations

from dataclasses import dataclass

from gamedev_shared.hardware import GIB, cuda_gpu_specs
from gamedev_shared.hardware import hw_auto_enabled as _hw_auto_enabled
from gamedev_shared.lowvram import plan_offload

from .generator import HIGH_VRAM_MODEL_ID, LOW_VRAM_MODEL_ID, model_footprint

HW_AUTO_ENV = "TEXT2D_HW_AUTO"


def hw_auto_enabled() -> bool:
    """``TEXT2D_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Text2DHardwareProfile:
    name: str
    device: str  # "cuda" | "cpu"
    model_id: str  # modelo BASE sugerido (não sobrepõe -m / TEXT2D_MODEL_ID)
    low_vram: bool  # True = CPU offload na colocação
    gpu_ids: list[int] | None  # >1 GPU: split multi-GPU; senão None
    total_vram_gib: float
    quant_preset: str  # preset SDNQ runtime ("none" | "sdnq-uint8" | ... ) por VRAM

    def summary(self) -> str:
        model_tag = "9B" if self.model_id == HIGH_VRAM_MODEL_ID else "4B"
        parts = [self.name, f"base={model_tag}"]
        if self.quant_preset != "none":
            parts.append(f"quant={self.quant_preset}")
        if self.low_vram:
            parts.append("cpu-offload")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


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
            low_vram=True,
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
        plan = plan_offload(gpus, model_footprint(HIGH_VRAM_MODEL_ID))
        return Text2DHardwareProfile(
            name=name,
            device="cuda",
            model_id=HIGH_VRAM_MODEL_ID,
            low_vram=False,
            gpu_ids=[idx for idx, _ in gpus],
            total_vram_gib=round(total_gib, 1),
            quant_preset=plan.quant_mode,
        )

    primary = max(gpus, key=lambda t: t[1])
    model_id = HIGH_VRAM_MODEL_ID if largest_gib >= 10.0 else LOW_VRAM_MODEL_ID
    plan = plan_offload([primary], model_footprint(model_id), allow_multi_gpu=False)
    return Text2DHardwareProfile(
        name=name,
        device="cuda",
        model_id=model_id,
        low_vram=plan.low_vram,
        gpu_ids=None,
        total_vram_gib=round(total_gib, 1),
        quant_preset=plan.quant_mode,
    )


def detect_hardware_profile() -> Text2DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_specs())
