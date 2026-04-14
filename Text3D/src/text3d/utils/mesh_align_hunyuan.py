"""
Alinhamento tipo Hunyuan: cluster de normais ~+Z em baixo → chão -Y + pés em Y=0.

Usa :func:`text3d.mesh_beautify.align_largest_plus_z_face_normal_to_ground` com guarda
opcional (evita dobrar personagens quando a heurística falha).
"""

from __future__ import annotations

from pathlib import Path

import trimesh

from ..mesh_beautify import align_largest_plus_z_face_normal_to_ground
from .export import _export_glb_with_normals, _load_as_trimesh


def align_glb_plus_z_safe(
    path_in: str | Path,
    path_out: str | Path,
    *,
    min_height_ratio: float = 0.25,
) -> Path:
    """
    Aplica alinhamento +Z→chão; se a altura da AABB cair para menos de
    ``min_height_ratio`` da original, mantém a mesh de entrada (caso típico: humanoide).
    """
    path_in = Path(path_in)
    path_out = Path(path_out)

    mesh = _load_as_trimesh(path_in)
    h0 = float(mesh.bounds[1][1] - mesh.bounds[0][1])
    out = align_largest_plus_z_face_normal_to_ground(mesh)
    h1 = float(out.bounds[1][1] - out.bounds[0][1])

    if h0 > 1e-6 and h1 < h0 * float(min_height_ratio):
        out = mesh
    elif h0 <= 1e-6:
        out = mesh

    path_out.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(out, trimesh.Scene):
        geoms = list(out.geometry.values())
        out = geoms[0] if len(geoms) == 1 else trimesh.util.concatenate(geoms)
    _export_glb_with_normals(out, path_out)
    return path_out
