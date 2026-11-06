"""Sondagem de capacidade GPU e política de resolução/offload.

O card do modelo **não publica um mínimo de VRAM**: a única configuração
medida foi 1536³ num H20 de 96 GB. Os limites daqui são, por isso, heurística
conservadora do monorepo — e o `hi3d doctor --measure` existe para os
substituir pelo número real em cada máquina.

Regras (provisórias, documentadas em README):

* compute capability >= 8.0 (Ampere) — requisito duro do upstream;
* 1536³ só com >= 40 GiB livres (o upstream não mediu abaixo de 96 GiB);
* 1024³ com >= 16 GiB livres;
* 512³ (1 passe) com >= 4.8 GiB livres;
* offload sequencial de CPU sempre que livres < 12 GiB.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from .perf import STAGING_MIN_FREE_MIB

#: VRAM livre mínima para o passe único de 512³ com offload sequencial.
MIN_FREE_MIB_512 = 4800

#: (livre_MiB, resolução) — primeira entrada que couber ganha.
RESOLUTION_BY_FREE_MIB: tuple[tuple[int, int], ...] = (
    (40 * 1024, 1536),
    (16 * 1024, 1024),
    (0, 512),
)

#: Abaixo disto o pipeline inteiro cabe na GPU e o offload só atrasaria.
OFFLOAD_BELOW_FREE_MIB = 12 * 1024

#: Compute capability mínima (Ampere+). O upstream não tem caminho de fallback
#: de atenção (flash-attn) nem kernels CPU.
MIN_COMPUTE_CAP = (8, 0)


@dataclass(frozen=True)
class GpuInfo:
    """Estado de uma GPU NVIDIA visível."""

    index: int
    name: str
    total_mib: int
    free_mib: int
    compute_cap: tuple[int, int] | None

    @property
    def supported(self) -> bool:
        """True quando a CC satisfaz o requisito do flash-attn/spconv."""
        return self.compute_cap is not None and self.compute_cap >= MIN_COMPUTE_CAP


@dataclass(frozen=True)
class RefinePlan:
    """Decisão de execução derivada do hardware e do ambiente instalado."""

    available: bool
    reason: str = ""
    resolution: int = 512
    offload: bool = True
    #: Residência por fase (default quando a VRAM chega) — ver ``perf.py``.
    staging: bool = False
    gpu: GpuInfo | None = None


def _parse_cap(raw: str) -> tuple[int, int] | None:
    parts = raw.strip().split(".")
    if len(parts) < 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def query_gpus() -> list[GpuInfo]:
    """Lê ``nvidia-smi`` (sem NVML) e devolve as GPUs visíveis."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return []
    try:
        out = subprocess.run(
            [
                exe,
                "--query-gpu=index,name,memory.total,memory.free,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    gpus: list[GpuInfo] = []
    for line in (out.stdout or "").splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 5:
            continue
        try:
            gpus.append(
                GpuInfo(
                    index=int(cols[0]),
                    name=cols[1],
                    total_mib=int(float(cols[2])),
                    free_mib=int(float(cols[3])),
                    compute_cap=_parse_cap(cols[4]),
                )
            )
        except ValueError:
            continue
    return gpus


def pick_gpu(gpus: list[GpuInfo]) -> GpuInfo | None:
    """Escolhe a GPU com mais VRAM (o vramd garante exclusividade)."""
    if not gpus:
        return None
    return max(gpus, key=lambda g: (g.total_mib, -g.index))


def resolution_for_free_mib(free_mib: int) -> int:
    """Resolução nominal que cabe em ``free_mib`` (política conservadora)."""
    for threshold, resolution in RESOLUTION_BY_FREE_MIB:
        if free_mib >= threshold:
            return resolution
    return 512


def wants_offload(free_mib: int) -> bool:
    """True quando o offload sequencial de CPU é o modo seguro."""
    return free_mib < OFFLOAD_BELOW_FREE_MIB


def plan_for_gpu(gpu: GpuInfo | None, *, stack_ready: bool = True) -> RefinePlan:
    """Combina requisitos do upstream com o estado da GPU e da stack."""
    if gpu is None:
        return RefinePlan(available=False, reason="nenhuma GPU NVIDIA visível (nvidia-smi)")
    if not gpu.supported:
        cc = "desconhecida" if gpu.compute_cap is None else f"{gpu.compute_cap[0]}.{gpu.compute_cap[1]}"
        return RefinePlan(
            available=False,
            reason=f"compute capability {cc} < 8.0 (flash-attn/spconv exigem Ampere+)",
            gpu=gpu,
        )
    if gpu.free_mib < MIN_FREE_MIB_512:
        return RefinePlan(
            available=False,
            reason=(f"VRAM livre {gpu.free_mib} MiB < {MIN_FREE_MIB_512} MiB (mínimo do 512³ com offload)"),
            gpu=gpu,
        )
    if not stack_ready:
        return RefinePlan(available=False, reason="stack sparc3d/torch/flash-attn ausente", gpu=gpu)
    return RefinePlan(
        available=True,
        resolution=resolution_for_free_mib(gpu.free_mib),
        offload=wants_offload(gpu.free_mib),
        # Com residência por fase o "offload" é entre fases, não por camada.
        staging=gpu.free_mib >= STAGING_MIN_FREE_MIB,
        gpu=gpu,
    )
