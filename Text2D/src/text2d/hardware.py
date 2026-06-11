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

from .generator import HIGH_VRAM_MODEL_ID, LOW_VRAM_MODEL_ID

HW_AUTO_ENV = "TEXT2D_HW_AUTO"

# Mínimo (GiB) para correr o 4B SDNQ inteiro na GPU sem CPU offload.
# Validado no RTX 4050 6GB: pesos+activações@1024² pedem ~5.4GB de 5.64
# utilizáveis → OOM; offload corre com pico ~4.6GB. 6GB fica em offload.
LOW_VRAM_FULL_GPU_MIN_GIB = 7.5


def hw_auto_enabled() -> bool:
    """``TEXT2D_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Text2DHardwareProfile:
    name: str
    device: str  # "cuda" | "cpu"
    model_id: str  # checkpoint SDNQ sugerido (não sobrepõe -m / TEXT2D_MODEL_ID)
    low_vram: bool  # True = enable_model_cpu_offload
    gpu_ids: list[int] | None  # >1 GPU: split multi-GPU; senão None
    total_vram_gib: float

    def summary(self) -> str:
        model_tag = "9B" if self.model_id == HIGH_VRAM_MODEL_ID else "4B"
        parts = [self.name, f"modelo={model_tag}"]
        if self.low_vram:
            parts.append("cpu-offload")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def profile_from_specs(gpus: list[tuple[int, int]]) -> Text2DHardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU."""
    if not gpus:
        return Text2DHardwareProfile(
            name="cpu",
            device="cpu",
            model_id=LOW_VRAM_MODEL_ID,
            low_vram=True,
            gpu_ids=None,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    multi = len(gpus) > 1
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    if multi and total_gib >= 16.0:
        # Split: transformer+vae na primária, text_encoder na secundária.
        return Text2DHardwareProfile(
            name=name,
            device="cuda",
            model_id=HIGH_VRAM_MODEL_ID,
            low_vram=False,
            gpu_ids=[idx for idx, _ in gpus],
            total_vram_gib=round(total_gib, 1),
        )

    if largest_gib >= 10.0:
        return Text2DHardwareProfile(
            name=name,
            device="cuda",
            model_id=HIGH_VRAM_MODEL_ID,
            low_vram=False,
            gpu_ids=None,
            total_vram_gib=round(total_gib, 1),
        )

    # 4B SDNQ: <10GB. CPU offload abaixo do limiar onde o 4B inteiro não cabe.
    return Text2DHardwareProfile(
        name=name,
        device="cuda",
        model_id=LOW_VRAM_MODEL_ID,
        low_vram=largest_gib < LOW_VRAM_FULL_GPU_MIN_GIB,
        gpu_ids=None,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> Text2DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_specs())
