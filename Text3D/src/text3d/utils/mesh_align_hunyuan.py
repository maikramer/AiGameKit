"""
Alinhamento tipo Hunyuan: cluster de normais ~+Z em baixo → chão -Y + pés em Y=0.

Usa :func:`align_largest_plus_z_face_normal_to_ground` com guarda
opcional (evita dobrar personagens quando a heurística falha).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from .export import _export_glb_with_normals, _load_as_trimesh
from .mesh_base_plane import _rotmat_unit_a_to_b


def align_largest_plus_z_face_normal_to_ground(
    mesh: trimesh.Trimesh,
    *,
    dot_min: float = 0.82,
    min_faces: int = 60,
    bottom_percentile: float = 48.0,
) -> trimesh.Trimesh:
    """
    Hunyuan costuma pôr a face de corte de cristais com normais ~ +Z (virada "pr'a frente");
    só consideramos triângulos na **metade inferior** do modelo (percentil de Y) com n·Z alto,
    para não misturar facetas laterais. Alinha a média dessas normais a **-Y** e recentra.
    """
    fn = mesh.face_normals
    c = mesh.triangles_center
    y_cut = float(np.percentile(c[:, 1], float(bottom_percentile)))
    mask = (fn[:, 2] > float(dot_min)) & (c[:, 1] <= y_cut + 1e-4)
    if int(np.sum(mask)) < int(min_faces):
        mask = fn[:, 2] > float(dot_min)
    if int(np.sum(mask)) < int(min_faces):
        return mesh
    bn = fn[mask].mean(axis=0)
    nrm = float(np.linalg.norm(bn))
    if nrm < 1e-9:
        return mesh
    bn = bn / nrm
    target = np.array([0.0, -1.0, 0.0], dtype=np.float64)
    r3 = _rotmat_unit_a_to_b(bn, target)
    tf = np.eye(4, dtype=np.float64)
    tf[:3, :3] = r3
    m = mesh.copy()
    m.apply_transform(tf)
    b = m.bounds
    m.apply_translation(
        [
            -0.5 * (float(b[0][0]) + float(b[1][0])),
            -float(b[0][1]),
            -0.5 * (float(b[0][2]) + float(b[1][2])),
        ]
    )
    # Manter textura / vertex-colors: não reconstruir só geometria.
    try:
        m.remove_unreferenced_vertices()
    except Exception:
        pass
    try:
        m.fix_normals()
    except Exception:
        _ = m.vertex_normals
    return m


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
