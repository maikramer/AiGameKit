"""Pós-processo GLB pintado — smooth / upscale / preserve_origin (CLI + vramd)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def apply_paint_postprocess(
    glb_path: str | Path,
    *,
    mesh_path: str | Path | None = None,
    preserve_origin: bool = False,
    smooth: bool = False,
    smooth_passes: int | None = None,
    upscale: bool = False,
    upscale_factor: int | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Aplica pós-processos in-place no GLB.

    Returns:
        Dict com flags efectivamente aplicadas (para ``runtime_budget`` / logs).
    """
    from paint3d import defaults as _defaults
    from paint3d.painter import _fit_glb_aabb_to_reference

    path = Path(glb_path)
    applied: dict[str, Any] = {}

    if smooth:
        from paint3d.texture_smooth import smooth_trimesh_texture
        from paint3d.utils.mesh_io import load_mesh_trimesh, save_glb

        passes = int(smooth_passes if smooth_passes is not None else _defaults.DEFAULT_SMOOTH_PASSES)
        mesh = load_mesh_trimesh(path)
        mesh = smooth_trimesh_texture(
            mesh,
            passes=passes,
            diameter=_defaults.DEFAULT_SMOOTH_DIAMETER,
            sigma_color=_defaults.DEFAULT_SMOOTH_SIGMA_COLOR,
            sigma_space=_defaults.DEFAULT_SMOOTH_SIGMA_SPACE,
            verbose=verbose,
        )
        save_glb(mesh, path)
        applied["smooth"] = True
        applied["smooth_passes"] = passes

    if upscale:
        from aigamekit_shared.gpu import clear_cuda_memory
        from paint3d.texture_upscale import upscale_trimesh_texture
        from paint3d.utils.mesh_io import load_mesh_trimesh, save_glb

        factor = int(upscale_factor if upscale_factor is not None else _defaults.DEFAULT_UPSCALE_FACTOR)
        clear_cuda_memory()
        mesh = load_mesh_trimesh(path)
        mesh = upscale_trimesh_texture(mesh, scale=factor, verbose=verbose)
        save_glb(mesh, path)
        applied["upscale"] = True
        applied["upscale_factor"] = factor

    if preserve_origin and mesh_path is not None:
        _fit_glb_aabb_to_reference(path, mesh_path, verbose=verbose)
        applied["preserve_origin"] = True

    return applied
