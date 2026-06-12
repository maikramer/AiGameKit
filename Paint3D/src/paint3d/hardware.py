"""
Detecção automática de hardware → perfil Hunyuan3D-Paint 2.1.

Soft resolution: só preenche parâmetros quando o utilizador não pediu nada
explícito; ``--low-vram-mode``, ``--gpu-ids``, ``--quality`` e flags de
resolução ganham sempre. Desligável com ``--no-hw-auto`` ou ``PAINT3D_HW_AUTO=0``.

Perfis por tier de VRAM:

- >= 10 GiB (single ou multi-GPU): FP16, sem overrides (usa defaults CLI:
  6 views @ 640px, render 2048, texture 4096).
- 8.0 – 10.0 GiB (ex: RTX 4060 8GB): FP16, 6v@512, render 1536, tex 3072.
- < 8.0 GiB (ex: RTX 4050 6GB): SDNQ uint8, 4v@384, render 1024, tex 2048.
- CPU (sem GPU): mesmo que low-VRAM.

Hardware de referência:
- 2x RTX 3060 12GB → FP16, split multi-GPU (painter já auto-detecta ≥2 GPUs).
- RTX 4060 8GB → FP16, mid-tier resolutions.
- RTX 4050 6GB → low-VRAM (SDNQ uint8, 4 views @ 384px, render 1024, tex 2048).
"""

from __future__ import annotations

from dataclasses import dataclass

from gamedev_shared.hardware import GIB, cuda_gpu_specs
from gamedev_shared.hardware import hw_auto_enabled as _hw_auto_enabled

HW_AUTO_ENV = "PAINT3D_HW_AUTO"

# Mínimo (GiB) para o perfil FP16 sem overrides — 6 views @ 640, render 2048,
# texture 4096 (ver defaults.py).
FULL_PROFILE_MIN_GIB = 10.0

# Mid-tier: FP16 mas com resoluções reduzidas.
MID_TIER_MIN_GIB = 8.0


def hw_auto_enabled() -> bool:
    """``PAINT3D_HW_AUTO=0`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


@dataclass(frozen=True)
class Paint3DHardwareProfile:
    name: str
    device: str  # "cuda" | "cpu"
    low_vram: bool  # True = SDNQ uint8 (backward compat)
    gpu_ids: list[int] | None  # informativo; painter auto-split com ≥2 GPUs
    total_vram_gib: float
    max_views: int | None = None  # None = don't override (use CLI default)
    view_resolution: int | None = None
    render_size: int | None = None
    texture_size: int | None = None

    def summary(self) -> str:
        parts = [self.name, "low-vram (SDNQ uint8)" if self.low_vram else "FP16"]
        if self.max_views is not None:
            parts.append(f"views={self.max_views}@{self.view_resolution}px")
        if self.render_size is not None:
            parts.append(f"render={self.render_size}")
        if self.texture_size is not None:
            parts.append(f"tex={self.texture_size}")
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
            max_views=4,
            view_resolution=384,
            render_size=1024,
            texture_size=2048,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    multi = len(gpus) > 1
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    # Capacidade efectiva: multi-GPU divide os pesos (accelerate),
    # por isso a soma conta para o tier; single-GPU usa só a própria VRAM.
    capacity_gib = total_gib if multi else largest_gib

    # >= 10 GiB: FP16, sem overrides (CLI defaults: 6v@640, render 2048, tex 4096)
    if capacity_gib >= FULL_PROFILE_MIN_GIB:
        gpu_ids = [idx for idx, _ in gpus] if multi else None
        return Paint3DHardwareProfile(
            name=name,
            device="cuda",
            low_vram=False,
            gpu_ids=gpu_ids,
            total_vram_gib=round(total_gib, 1),
        )

    # 8.0 – 10.0 GiB: FP16, mid-tier resolutions
    if capacity_gib >= MID_TIER_MIN_GIB:
        return Paint3DHardwareProfile(
            name=name,
            device="cuda",
            low_vram=False,
            gpu_ids=None,
            total_vram_gib=round(total_gib, 1),
            max_views=6,
            view_resolution=512,
            render_size=1536,
            texture_size=3072,
        )

    # < 8.0 GiB: SDNQ uint8, low-VRAM resolutions
    return Paint3DHardwareProfile(
        name=name,
        device="cuda",
        low_vram=True,
        gpu_ids=None,
        total_vram_gib=round(total_gib, 1),
        max_views=4,
        view_resolution=384,
        render_size=1024,
        texture_size=2048,
    )


def detect_hardware_profile() -> Paint3DHardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_specs())
