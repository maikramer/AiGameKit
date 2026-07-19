"""Runtime VRAM budget — camada partilhada exposta ao UMS e adapters.

O admit estático vive em :mod:`modelserver.vram_planner` (pesos+act+safety).
Este módulo é o **pós-load**: orça chunks/views/tiles pela VRAM livre para
nenhum backend cair em OOM→CPU por batch demasiado grande.

Implementação canónica: :mod:`gamedev_shared.vram_budget`. Os adapters
``text3d`` / ``paint3d`` (e futuros) devem preferir estes helpers a
reimplementar a fórmula.
"""

from __future__ import annotations

from typing import Any

from gamedev_shared.vram_budget import (
    PaintRuntimeBudget,
    budget_units,
    free_vram_bytes,
    paint_runtime_budget,
    text3d_num_chunks,
)


def suggest_text3d_chunks(
    free_bytes: int | None = None,
    *,
    device: int | None = None,
) -> dict[str, Any]:
    """Sugestão de ``num_chunks`` para o decode Omni (pós-load)."""
    free = free_bytes if free_bytes is not None else free_vram_bytes(device)
    n = text3d_num_chunks(free)
    return {
        "free_vram_bytes": free,
        "num_chunks": n,
        "auto": n is not None,
    }


def suggest_paint_budget(
    *,
    requested_views: int = 6,
    requested_resolution: int = 512,
    memory_efficient: bool = False,
    free_bytes: int | None = None,
    device: int | None = None,
    force_dino_device: str | None = None,
) -> dict[str, Any]:
    """Sugestão de knobs de activação Paint3D (pós-load / pré-denoise)."""
    free = free_bytes if free_bytes is not None else free_vram_bytes(device)
    budget: PaintRuntimeBudget = paint_runtime_budget(
        free,
        requested_views=requested_views,
        requested_resolution=requested_resolution,
        memory_efficient=memory_efficient,
        force_dino_device=force_dino_device,
    )
    return budget.as_dict()


__all__ = [
    "budget_units",
    "free_vram_bytes",
    "paint_runtime_budget",
    "suggest_paint_budget",
    "suggest_text3d_chunks",
    "text3d_num_chunks",
]
