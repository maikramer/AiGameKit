"""Detecção automática de hardware → perfil de inferência Clark Air Sana 1.6B.

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``--low-vram``/``--cpu`` ganham). Desligível com ``--no-hw-auto``
ou ``TEXT2ICON_HW_AUTO=0``.

Clark Air Sana 1.6B (transformer ternário 1.58-bit descompactado para bf16
drop-in): transformer ~3.2 GB + Gemma text encoder ~2.5 GB + VAE ~0.5 GB ≈
~6 GB fp16. GPUs de 6-7 GB ficam no limite → ``model_cpu`` offload para garantir
que o VAE decode tem espaço.
    >= 10 GiB  full GPU, sem offload, resolução livre (default 512x512)
    >=  8 GiB  full GPU, sem offload, resolução livre
    >=  6 GiB  enable_model_cpu_offload, resolução livre (VAE precisa de espaço)
    <   6 GiB  offload + clamp a 512x512
"""

from __future__ import annotations

from dataclasses import dataclass

from gamedev_shared.hardware import GIB, cuda_gpu_specs
from gamedev_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "TEXT2ICON_HW_AUTO"

# Resolução nativa do Sana 1.6B 512px (Clark Air).
DEFAULT_WIDTH = 512
DEFAULT_HEIGHT = 512


def hw_auto_enabled() -> bool:
    """``TEXT2ICON_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Text2IconHardwareProfile:
    name: str
    device: str  # "cuda" | "cpu"
    low_vram: bool  # True = enable_model_cpu_offload
    max_width: int | None  # None = sem clamp; int = clamp se utilizador não explicitou
    max_height: int | None
    gpu_ids: list[int] | None  # >1 GPU: split multi-GPU; senão None
    total_vram_gib: float

    def summary(self) -> str:
        parts = [self.name]
        if self.low_vram:
            parts.append("cpu-offload")
        if self.max_width is not None:
            parts.append(f"clamp={self.max_width}x{self.max_height}")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def profile_from_specs(gpus: list[tuple[int, int]]) -> Text2IconHardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU."""
    if not gpus:
        return Text2IconHardwareProfile(
            name="cpu",
            device="cpu",
            low_vram=True,
            max_width=512,
            max_height=512,
            gpu_ids=None,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    gpu_ids = [idx for idx, _ in gpus] if len(gpus) > 1 else None

    if largest_gib >= 8.0:
        # ~6 GB fp16 cabe folgado em 8 GiB. Full GPU, sem offload.
        return Text2IconHardwareProfile(
            name=name,
            device="cuda",
            low_vram=False,
            max_width=None,
            max_height=None,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    if largest_gib >= 6.0:
        # Limite: transformer+Gemma cabem, mas VAE decode precisa de espaço → offload.
        return Text2IconHardwareProfile(
            name=name,
            device="cuda",
            low_vram=True,
            max_width=None,
            max_height=None,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    # < 6 GiB: offload + clamp a 512 (resolução nativa do modelo).
    return Text2IconHardwareProfile(
        name=name,
        device="cuda",
        low_vram=True,
        max_width=512,
        max_height=512,
        gpu_ids=gpu_ids,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> Text2IconHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_specs())
