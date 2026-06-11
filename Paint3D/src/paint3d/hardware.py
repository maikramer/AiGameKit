"""
Detecção automática de hardware → perfil Hunyuan3D-Paint 2.1.

Soft resolution: só liga ``low_vram`` quando o utilizador não pediu nada
explícito; ``--low-vram-mode``, ``--gpu-ids``, ``--quality`` e flags de
resolução ganham sempre. Desligável com ``--no-hw-auto`` ou ``PAINT3D_HW_AUTO=0``.

Perfis para os hardwares de referência:
- 2x RTX 3060 12GB → FP16, split multi-GPU (painter já auto-detecta ≥2 GPUs).
- RTX 4050 6GB → low-VRAM (SDNQ uint8, 4 views @ 384px, render 1024, tex 2048).
"""

from __future__ import annotations

from dataclasses import dataclass

from gamedev_shared.hardware import GIB, cuda_gpu_specs
from gamedev_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "PAINT3D_HW_AUTO"

# Mínimo (GiB) por GPU para o perfil FP16 padrão (6 views @ 640, render 2048,
# texture 4096) — afinado para single 12GB (ver defaults.py). Abaixo: low-VRAM.
FULL_PROFILE_MIN_GIB = 10.0


def hw_auto_enabled() -> bool:
    """``PAINT3D_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Paint3DHardwareProfile:
    name: str
    device: str  # "cuda" | "cpu"
    low_vram: bool  # True = SDNQ uint8 + 4 views @ 384 + render/tex reduzidos
    gpu_ids: list[int] | None  # informativo; painter auto-split com ≥2 GPUs
    total_vram_gib: float

    def summary(self) -> str:
        parts = [self.name, "low-vram (SDNQ uint8, 4v@384)" if self.low_vram else "FP16 (6v@640)"]
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def profile_from_specs(gpus: list[tuple[int, int]]) -> Paint3DHardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU."""
    if not gpus:
        # Paint3D requer CUDA (nvdiffrast); perfil cpu é só informativo.
        return Paint3DHardwareProfile(
            name="cpu",
            device="cpu",
            low_vram=True,
            gpu_ids=None,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    multi = len(gpus) > 1
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    # Multi-GPU divide UNet/VAE entre placas — perfil FP16 com VRAM agregada.
    if multi and total_gib >= FULL_PROFILE_MIN_GIB:
        return Paint3DHardwareProfile(
            name=name,
            device="cuda",
            low_vram=False,
            gpu_ids=[idx for idx, _ in gpus],
            total_vram_gib=round(total_gib, 1),
        )

    return Paint3DHardwareProfile(
        name=name,
        device="cuda",
        low_vram=largest_gib < FULL_PROFILE_MIN_GIB,
        gpu_ids=None,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> Paint3DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_specs())
