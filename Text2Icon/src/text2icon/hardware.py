"""Detecção automática de hardware → perfil de inferência Sana (Text2Icon).

Soft resolution no CLI: só preenche o que o utilizador não definiu (flags
explícitas, ``--low-vram``/``--cpu``/``--model``/``--quant-transformer``
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

from gamedev_shared.hardware import GIB, cuda_gpu_specs
from gamedev_shared.hardware import hw_auto_enabled as _hw_auto_enabled

from .generator import STANDARD_TRANSFORMER_ID, TERNARY_TRANSFORMER_ID

HW_AUTO_ENV = "TEXT2ICON_HW_AUTO"

# Resolução nativa do pipeline Sana 512px.
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
    transformer_id: str  # standard ou ternário (ver módulo)
    transformer_sdnq_preset: str | None  # None = sem SDNQ no transformer ("16-bit")

    def summary(self) -> str:
        parts = [self.name]
        if self.low_vram:
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
            low_vram=True,
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
            low_vram=True,
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
            low_vram=False,
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
            low_vram=False,
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
            low_vram=True,
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
        low_vram=True,
        max_width=512,
        max_height=512,
        gpu_ids=gpu_ids,
        total_vram_gib=round(total_gib, 1),
        transformer_id=STANDARD_TRANSFORMER_ID,
        transformer_sdnq_preset="sdnq-int4",
    )


def detect_hardware_profile() -> Text2IconHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_specs())
