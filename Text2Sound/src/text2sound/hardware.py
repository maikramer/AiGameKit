"""Detecção automática de hardware → perfil de inferência Stable Audio.

Mapeia GPUs CUDA visíveis para uma estratégia de memória (float16, VAE decode
em chunks, multi-GPU). Ao contrário do Text3D, os *steps* de difusão não
entram no perfil: em áudio eles custam tempo, não VRAM — a qualidade fica a
cargo do ``--quality``/preset e o hw-auto trata apenas de caber na memória.

Soft resolution no CLI: só preenche o que o utilizador não definiu
explicitamente. Desligável com ``--no-hw-auto`` ou ``TEXT2SOUND_HW_AUTO=0``.

Medição de referência (RTX 4050 Laptop 6 GB, Open 1.0 fp16, 30 s, 60 steps):
difusão completa com pico ~4.9 GiB; o OOM histórico acontecia no decode do
VAE (aloca ~1 GiB extra de ativações fp32) — resolvido com
``pretransform.chunked`` em vez de degradar o modelo.
"""

from __future__ import annotations

from dataclasses import dataclass

from aigamekit_shared.hardware import GIB, HardwareProfileBase, detect_profile
from aigamekit_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "TEXT2SOUND_HW_AUTO"


@dataclass(frozen=True)
class HardwareProfile(HardwareProfileBase):
    half: bool  # float16 no DiT/conditioner (VAE fica sempre fp32)
    chunked_vae: bool  # decode do VAE em chunks (pico de VRAM ~constante)

    def summary(self) -> str:
        parts = [self.name, f"half={'on' if self.half else 'off'}"]
        if self.chunked_vae:
            parts.append("vae=chunked")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def hw_auto_enabled() -> bool:
    """``TEXT2SOUND_HW_AUTO=0`` / ``false`` / ``no`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


def profile_from_specs(gpus: list[tuple[int, int]]) -> HardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU."""
    if not gpus:
        # CPU: fp16 em CPU é mais lento que fp32 na maioria dos backends;
        # chunked mantém o pico de RAM do decode baixo.
        return HardwareProfile(
            name="cpu",
            device="cpu",
            gpu_ids=None,
            half=False,
            chunked_vae=True,
            total_vram_gib=0.0,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    multi = len(gpus) > 1
    gpu_ids = [idx for idx, _ in gpus] if multi else None
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    # Multi-GPU divide os pesos do DiT (accelerate); a soma conta para o tier.
    capacity_gib = total_gib if multi else largest_gib

    if capacity_gib >= 12.0:
        half = False
        chunked = False
    elif capacity_gib >= 8.5:
        half = True
        chunked = False
    else:
        # Classe 6 GB (ex.: RTX 4050 laptop): fp16 obrigatório e decode do
        # VAE em chunks — a difusão do Open 1.0 cabe; o decode inteiro não.
        half = True
        chunked = True

    return HardwareProfile(
        name=name,
        device="cuda",
        gpu_ids=gpu_ids,
        half=half,
        chunked_vae=chunked,
        total_vram_gib=round(total_gib, 1),
    )


def detect_hardware_profile() -> HardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return detect_profile(profile_from_specs)
