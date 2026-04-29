"""
Alinhamento tipo Hunyuan: cluster de normais ~+Z em baixo → chão -Y + pés em Y=0.

Usa :func:`align_largest_plus_z_face_normal_to_ground` com guarda
opcional (evita dobrar personagens quando a heurística falha).
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import numpy as np

from gamedev_shared.bpy_mesh import load_glb, save_glb

from .mesh_base_plane import (
    _apply_transform_matrix,
    _apply_translation,
    _copy_object,
    _face_centers_world,
    _face_normals_world,
    _get_bounds,
    _rotmat_unit_a_to_b,
)


def _join_mesh_objects(objs: list) -> object:
    """Join multiple mesh objects into a single object via ``bpy.ops.object.join``."""
    import bpy

    if len(objs) <= 1:
        return objs[0] if objs else None
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    return bpy.context.active_object


def _delete_loose_verts(obj) -> None:
    """Remove loose vertices/edges via ``bpy.ops.mesh.delete_loose``."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.delete_loose()
    bpy.ops.object.mode_set(mode="OBJECT")


def _recalc_normals_outside(obj) -> None:
    """Recalculate normals to point outward via ``bpy.ops.mesh.normals_make_consistent``."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def align_largest_plus_z_face_normal_to_ground(
    obj,
    *,
    dot_min: float = 0.82,
    min_faces: int = 60,
    bottom_percentile: float = 48.0,
):
    """
    Hunyuan costuma pôr a face de corte de cristais com normais ~ +Z (virada "pr'a frente");
    só consideramos triângulos na **metade inferior** do modelo (percentil de Y) com n·Z alto,
    para não misturar facetas laterais. Alinha a média dessas normais a **-Y** e recentra.
    """
    fn = _face_normals_world(obj)
    c = _face_centers_world(obj)
    y_cut = float(np.percentile(c[:, 1], float(bottom_percentile)))
    mask = (fn[:, 2] > float(dot_min)) & (c[:, 1] <= y_cut + 1e-4)
    if int(np.sum(mask)) < int(min_faces):
        mask = fn[:, 2] > float(dot_min)
    if int(np.sum(mask)) < int(min_faces):
        return obj
    bn = fn[mask].mean(axis=0)
    nrm = float(np.linalg.norm(bn))
    if nrm < 1e-9:
        return obj
    bn = bn / nrm
    target = np.array([0.0, -1.0, 0.0], dtype=np.float64)
    r3 = _rotmat_unit_a_to_b(bn, target)
    tf = np.eye(4, dtype=np.float64)
    tf[:3, :3] = r3
    m = _copy_object(obj)
    _apply_transform_matrix(m, tf)
    bmin, bmax = _get_bounds(m)
    _apply_translation(
        m,
        [
            -0.5 * (float(bmin[0]) + float(bmax[0])),
            -float(bmin[1]),
            -0.5 * (float(bmin[2]) + float(bmax[2])),
        ],
    )
    # Manter textura / vertex-colors: nao reconstruir so geometria.
    with contextlib.suppress(Exception):
        _delete_loose_verts(m)
    with contextlib.suppress(Exception):
        _recalc_normals_outside(m)
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

    objs = load_glb(path_in)
    if not objs:
        raise ValueError(f"No mesh objects found in: {path_in}")
    obj = _join_mesh_objects(objs)

    bmin0, bmax0 = _get_bounds(obj)
    h0 = float(bmax0[1] - bmin0[1])

    aligned = align_largest_plus_z_face_normal_to_ground(obj)

    bmin1, bmax1 = _get_bounds(aligned)
    h1 = float(bmax1[1] - bmin1[1])

    out = obj if (h0 > 1e-6 and h1 < h0 * float(min_height_ratio)) or h0 <= 1e-6 else aligned

    path_out.parent.mkdir(parents=True, exist_ok=True)
    save_glb(out, path_out)
    return path_out
