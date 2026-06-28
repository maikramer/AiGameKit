"""
Detecção automática de hardware → perfil de inferência Hunyuan3D.

Mapeia GPUs CUDA visíveis para um perfil (steps/octree/chunks, SDNQ, multi-GPU,
volume decoder, resolução da imagem Text2D). Soft resolution no CLI: só preenche o
que o utilizador não definiu explicitamente — flags manuais, ``--quality`` e
``--preset`` têm sempre precedência. Desligável com ``--no-hw-auto`` ou
``TEXT3D_HW_AUTO=0``.
"""

from __future__ import annotations

from dataclasses import dataclass

from gamedev_shared.hardware import GIB, cuda_gpu_specs
from gamedev_shared.hardware import hw_auto_enabled as _hw_auto_enabled

from . import defaults as _defaults

HW_AUTO_ENV = "TEXT3D_HW_AUTO"


@dataclass(frozen=True)
class HardwareProfile:
    name: str
    device: str  # "cuda" | "cpu"
    gpu_ids: list[int] | None  # >1 GPU: lista para MultiGPUPlanner; senão None
    sdnq_preset: str | None  # None = sem quantização
    steps: int
    octree: int
    chunks: int
    volume_decoder: str
    total_vram_gib: float
    image_width: int | None = None  # None = não override (usa default do CLI)
    image_height: int | None = None
    offload: bool = False  # True = CPU offload (conditioner->model->vae) em VRAM baixa

    def summary(self) -> str:
        parts = [
            f"{self.name}",
            f"steps={self.steps} octree={self.octree} chunks={self.chunks}",
            f"decoder={self.volume_decoder}",
        ]
        if self.sdnq_preset:
            parts.append(f"sdnq={self.sdnq_preset}")
        if self.offload:
            parts.append("cpu-offload")
        if self.image_width:
            parts.append(f"img={self.image_width}x{self.image_height}")
        if self.gpu_ids:
            parts.append(f"gpus={self.gpu_ids}")
        return " | ".join(parts)


def hw_auto_enabled() -> bool:
    """``TEXT3D_HW_AUTO=0`` / ``false`` / ``no`` desliga a auto-detecção."""
    return _hw_auto_enabled(HW_AUTO_ENV)


def profile_from_specs(gpus: list[tuple[int, int]]) -> HardwareProfile:
    """Resolve perfil a partir de specs (índice, bytes VRAM). Puro — testável sem GPU."""
    fast = _defaults.PRESET_HUNYUAN["fast"]
    balanced = _defaults.PRESET_HUNYUAN["balanced"]
    hq = _defaults.PRESET_HUNYUAN["hq"]

    if not gpus:
        return HardwareProfile(
            name="cpu",
            device="cpu",
            gpu_ids=None,
            sdnq_preset=None,
            steps=fast["steps"],
            octree=fast["octree"],
            chunks=fast["chunks"],
            volume_decoder="hierarchical",
            total_vram_gib=0.0,
            image_width=1024,
            image_height=1024,
        )

    total_gib = sum(mem for _, mem in gpus) / GIB
    largest_gib = max(mem for _, mem in gpus) / GIB
    multi = len(gpus) > 1
    gpu_ids = [idx for idx, _ in gpus] if multi else None
    name = f"cuda-{len(gpus)}x{largest_gib:.0f}g"

    # Capacidade efectiva: multi-GPU divide os pesos do DiT (accelerate),
    # por isso a soma conta para o tier; single-GPU usa só a própria VRAM.
    capacity_gib = total_gib if multi else largest_gib

    if capacity_gib >= 10.0:
        tier = hq
        sdnq: str | None = None
        img_w: int | None = None
        img_h: int | None = None
    elif capacity_gib >= 7.5:
        tier = balanced
        sdnq = None
        img_w = None
        img_h = None
    elif capacity_gib >= 6.5:
        tier = balanced
        sdnq = "sdnq-int4"
        img_w = 1024
        img_h = 1024
    elif capacity_gib >= 4.0:
        tier = fast
        sdnq = "sdnq-int4"
        img_w = 1024
        img_h = 1024
    else:
        tier = fast
        sdnq = "sdnq-int4"
        img_w = 1024
        img_h = 1024

    # CPU offload proativo só em GPUs muito pequenas (<5GB): em 6GB o DiT int4 cabe
    # com pipe.to(cuda) (caminho rápido validado), e o generator tem fallback OOM ->
    # enable_model_cpu_offload se a colocação estourar. Multi-GPU divide os pesos.
    offload = (not multi) and largest_gib < 5.0

    return HardwareProfile(
        name=name,
        device="cuda",
        gpu_ids=gpu_ids,
        sdnq_preset=sdnq,
        steps=tier["steps"],
        octree=tier["octree"],
        chunks=tier["chunks"],
        volume_decoder="hierarchical",
        total_vram_gib=round(total_gib, 1),
        image_width=img_w,
        image_height=img_h,
        offload=offload,
    )


def detect_hardware_profile() -> HardwareProfile:
    """Detecta GPUs CUDA e devolve o perfil correspondente."""
    return profile_from_specs(cuda_gpu_specs())
