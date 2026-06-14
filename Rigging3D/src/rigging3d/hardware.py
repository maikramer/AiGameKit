"""
Detecção automática de hardware → escolha de GPU para o UniRig.

O UniRig corre single-GPU em subprocessos (CUDA_VISIBLE_DEVICES). Em rigs
multi-GPU, o hw-auto escolhe a placa com mais VRAM LIVRE (a GPU 0 costuma
estar ocupada pelo desktop). ``--gpu-ids`` explícito ganha sempre.
Desligável com ``--no-hw-auto`` ou ``RIGGING3D_HW_AUTO=0``.

Hardwares de referência:
- 2x RTX 3060 12GB → pina o UniRig na placa mais livre.
- RTX 4050 6GB → única GPU; aviso (UniRig pede ~6-8GB em meshes densas).
"""

from __future__ import annotations

from dataclasses import dataclass

from gamedev_shared.hardware import GIB, cuda_gpu_free_specs
from gamedev_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "RIGGING3D_HW_AUTO"

# Abaixo disto (VRAM total da placa escolhida) avisar: UniRig precisa ~6-8GB.
LOW_VRAM_WARN_GIB = 6.5


def hw_auto_enabled() -> bool:
    """``RIGGING3D_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Rigging3DHardwareProfile:
    name: str
    device: str  # "cuda" | "cpu"
    gpu_ids: list[int] | None  # GPU escolhida p/ CUDA_VISIBLE_DEVICES (1 elem)
    free_gib: float  # VRAM livre da GPU escolhida
    low_vram_warning: bool
    low_vram: bool

    def summary(self) -> str:
        parts = [self.name]
        if self.gpu_ids is not None:
            parts.append(f"gpu={self.gpu_ids[0]} ({self.free_gib:.1f}GiB livre)")
        if self.low_vram_warning:
            parts.append("aviso: <6.5GiB — meshes densas podem dar OOM")
        return " | ".join(parts)


def profile_from_specs(gpus: list[tuple[int, int, int]]) -> Rigging3DHardwareProfile:
    """Resolve perfil a partir de (índice, livre, total) bytes. Puro — testável sem GPU."""
    if not gpus:
        return Rigging3DHardwareProfile(
            name="cpu",
            device="cpu",
            gpu_ids=None,
            free_gib=0.0,
            low_vram_warning=True,
            low_vram=True,
        )

    largest_total = max(total for _, _, total in gpus) / GIB
    best_idx, best_free, best_total = max(gpus, key=lambda s: s[1])
    multi = len(gpus) > 1
    name = f"cuda-{len(gpus)}x{largest_total:.0f}g"

    return Rigging3DHardwareProfile(
        name=name,
        device="cuda",
        # Só pina em multi-GPU; single-GPU não precisa de CUDA_VISIBLE_DEVICES.
        gpu_ids=[best_idx] if multi else None,
        free_gib=round(best_free / GIB, 1),
        low_vram_warning=(best_total / GIB) < LOW_VRAM_WARN_GIB,
        low_vram=(best_total / GIB) < LOW_VRAM_WARN_GIB,
    )


def detect_hardware_profile() -> Rigging3DHardwareProfile:
    """Detecta GPUs CUDA (com VRAM livre) e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_free_specs())
