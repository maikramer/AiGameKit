"""
Alinha o plano medio da base da malha ao chao (normal exterior ~ -Y) e assenta em Y=0.

Usa **faces** na faixa inferior ponderadas por **área** (mais estável que só vértices) e
**assentamento robusto** (mediana ponderada do contacto em Y, mais correção se algum vértice
ficar abaixo do chão).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import bpy.types


# ---------------------------------------------------------------------------
# bpy mesh-data → numpy helpers
# ---------------------------------------------------------------------------


def _vertices_world(obj: bpy.types.Object) -> np.ndarray:
    """World-space vertex positions as ``(N, 3)`` float64."""
    mw = obj.matrix_world
    return np.array([(mw @ v.co).to_tuple() for v in obj.data.vertices], dtype=np.float64)


def _face_centers_world(obj: bpy.types.Object) -> np.ndarray:
    """World-space polygon centres as ``(N, 3)`` float64."""
    mw = obj.matrix_world
    return np.array([(mw @ p.center).to_tuple() for p in obj.data.polygons], dtype=np.float64)


def _face_areas(obj: bpy.types.Object) -> np.ndarray:
    """Polygon areas as ``(N,)`` float64."""
    return np.array([p.area for p in obj.data.polygons], dtype=np.float64)


def _face_normals_world(obj: bpy.types.Object) -> np.ndarray:
    """World-space polygon normals as ``(N, 3)`` float64."""
    nm = obj.matrix_world.to_3x3().inverted().transposed()
    return np.array([(nm @ p.normal).normalized().to_tuple() for p in obj.data.polygons], dtype=np.float64)


def _apply_translation(obj: bpy.types.Object, offset: np.ndarray | list) -> None:
    """Translate *obj* in world space by modifying ``matrix_world``."""
    import mathutils

    off = tuple(float(x) for x in offset)
    obj.matrix_world = mathutils.Matrix.Translation(off) @ obj.matrix_world


def _apply_transform_matrix(obj: bpy.types.Object, matrix: np.ndarray) -> None:
    """Apply a 4x4 numpy transform to *obj* via ``matrix_world``."""
    import mathutils

    m = mathutils.Matrix(matrix.tolist())
    obj.matrix_world = m @ obj.matrix_world


def _copy_object(obj: bpy.types.Object) -> bpy.types.Object:
    """Independent copy of *obj* (separate data block) linked to current collection."""
    import bpy

    new_obj = obj.copy()
    new_obj.data = obj.data.copy()
    bpy.context.collection.objects.link(new_obj)
    return new_obj


def _get_bounds(obj: bpy.types.Object) -> tuple[np.ndarray, np.ndarray]:
    """World-space AABB as ``(min_corner, max_corner)``, each shape ``(3,)``."""
    v = _vertices_world(obj)
    if v.size == 0:
        return np.zeros(3, dtype=np.float64), np.zeros(3, dtype=np.float64)
    return v.min(axis=0), v.max(axis=0)


def _translation_matrix_np(v: np.ndarray) -> np.ndarray:
    """4x4 translation matrix (numpy) from a 3-vector."""
    m = np.eye(4, dtype=np.float64)
    m[:3, 3] = v.reshape(3)
    return m


# ---------------------------------------------------------------------------
# Rotation helpers (pure numpy, one mathutils call for 180° case)
# ---------------------------------------------------------------------------


def _rotmat_unit_a_to_b(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Matriz 3x3 R com R @ a = b (a, b nao nulos; normalizados internamente)."""
    a = np.asarray(a, dtype=np.float64).reshape(3)
    b = np.asarray(b, dtype=np.float64).reshape(3)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na < 1e-12 or nb < 1e-12:
        return np.eye(3)
    a = a / na
    b = b / nb
    v = np.cross(a, b)
    s = float(np.linalg.norm(v))
    c = float(np.dot(a, b))
    if s < 1e-10:
        if c > 0.9999:
            return np.eye(3)
        ortho = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        axis = np.cross(a, ortho)
        axis = axis / np.linalg.norm(axis)
        # 180-degree rotation around axis: R = 2 * axis·axisᵀ - I
        m = 2.0 * np.outer(axis, axis) - np.eye(3)
        return m
    vx = np.array(
        [[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]],
        dtype=np.float64,
    )
    return (np.eye(3) + vx + vx @ vx * ((1.0 - c) / (s * s))).astype(np.float64)


# ---------------------------------------------------------------------------
# Weighted statistics (unchanged)
# ---------------------------------------------------------------------------


def _weighted_median_1d(values: np.ndarray, weights: np.ndarray) -> float:
    """Mediana ponderada (1D), pesos ≥ 0."""
    v = np.asarray(values, dtype=np.float64).ravel()
    w = np.asarray(weights, dtype=np.float64).ravel()
    if v.size == 0:
        return 0.0
    if v.size == 1:
        return float(v[0])
    w = np.clip(w, 1e-15, None)
    order = np.argsort(v)
    v = v[order]
    w = w[order]
    cw = np.cumsum(w)
    half = 0.5 * float(cw[-1])
    idx = int(np.searchsorted(cw, half))
    return float(v[min(idx, v.size - 1)])


def _weighted_plane_normal(
    points: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Plano por SVD ponderado; devolve (centroide ponderado, normal unitária)."""
    pts = np.asarray(points, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64).ravel()
    if pts.shape[0] < 3:
        c = pts.mean(axis=0)
        return c, np.array([0.0, -1.0, 0.0], dtype=np.float64)
    w = np.clip(w, 1e-15, None)
    w = w / float(np.sum(w))
    c = np.sum(pts * w[:, np.newaxis], axis=0)
    x = pts - c
    sw = np.sqrt(w)[:, np.newaxis]
    xm = sw * x
    _, _, vh = np.linalg.svd(xm, full_matrices=False)
    n = vh[-1].copy()
    n = n / (np.linalg.norm(n) + 1e-15)
    return c, n


# ---------------------------------------------------------------------------
# Grounding & alignment
# ---------------------------------------------------------------------------


def _smart_reground(
    obj: bpy.types.Object,
    *,
    bottom_frac: float,
) -> None:
    """
    Coloca o patch de contacto típico em Y≈0: mediana ponderada por área dos centros de face
    na faixa inferior; depois sobe a malha se algum vértice ficar ligeiramente abaixo de 0.
    """
    v = _vertices_world(obj)
    if v.size < 9:
        return

    fc = _face_centers_world(obj)
    fa = _face_areas(obj)

    ymin = float(v[:, 1].min())
    ymax = float(v[:, 1].max())
    yr = ymax - ymin
    if yr < 1e-9:
        return

    bf = float(np.clip(bottom_frac, 0.04, 0.35))
    y_cut = ymin + bf * yr
    mask = fc[:, 1] <= y_cut
    # Preferir faces com normal virada para baixo (exterior do apoio)
    fn = _face_normals_world(obj)
    downish = fn[:, 1] < -0.12
    mask_strict = mask & downish
    if int(np.sum(mask_strict)) >= 8:
        mask = mask_strict
    elif int(np.sum(mask)) < 4:
        mask = fc[:, 1] <= float(np.percentile(fc[:, 1], 24))

    if int(np.sum(mask)) < 3:
        _apply_translation(obj, [0.0, -ymin, 0.0])
        return

    ys = fc[mask, 1]
    w = fa[mask]
    y_ref = _weighted_median_1d(ys, w)
    _apply_translation(obj, [0.0, -y_ref, 0.0])

    mny = float(_vertices_world(obj)[:, 1].min())
    if mny < -1e-5:
        _apply_translation(obj, [0.0, -mny, 0.0])


def align_mesh_base_plane_to_ground(
    obj: bpy.types.Object,
    *,
    bottom_frac: float = 0.14,
    min_points: int = 12,
    min_tilt_rad: float = 0.004,
) -> bpy.types.Object:
    """
    Ajusta um plano à base com **faces** (ponderadas por área), alinha a normal a ``(0,-1,0)``,
    depois :func:`_smart_reground`.

    Sem rotação útil (já horizontal), aplica só o assentamento robusto.
    """
    out = _copy_object(obj)
    v = _vertices_world(out)
    if v.size < 9:
        return out

    ymin = float(v[:, 1].min())
    ymax = float(v[:, 1].max())
    yr = ymax - ymin
    if yr < 1e-9:
        return out

    bf = float(np.clip(bottom_frac, 0.04, 0.35))
    y_cut = ymin + bf * yr

    fc = _face_centers_world(out)
    fa = _face_areas(out)
    fn = _face_normals_world(out)

    mask = fc[:, 1] <= y_cut
    downish = fn[:, 1] < -0.1
    mask_b = mask & downish
    if int(np.sum(mask_b)) >= 8:
        mask = mask_b
    elif int(np.sum(mask)) < 4:
        mask = fc[:, 1] <= float(np.percentile(fc[:, 1], 25))

    pts = fc[mask]
    w = fa[mask]
    if len(pts) < min_points:
        mask2 = v[:, 1] <= ymin + bf * yr
        pts = v[mask2]
        w = np.ones(len(pts), dtype=np.float64)
        if len(pts) < min_points:
            _smart_reground(out, bottom_frac=bf)
            return out

    c, n = _weighted_plane_normal(pts, w)
    n = n / (np.linalg.norm(n) + 1e-15)
    if float(n[1]) > 0:
        n = -n

    target = np.array([0.0, -1.0, 0.0], dtype=np.float64)
    cos_t = float(np.clip(abs(np.dot(n, target)), -1.0, 1.0))
    tilt = float(np.arccos(cos_t))

    if tilt >= min_tilt_rad:
        r = _rotmat_unit_a_to_b(n, target)
        if np.linalg.norm(r - np.eye(3)) >= 1e-10:
            pivot = np.array([float(c[0]), ymin, float(c[2])], dtype=np.float64)
            t = np.eye(4)
            t[:3, :3] = r
            a = _translation_matrix_np(pivot) @ t @ _translation_matrix_np(-pivot)
            _apply_transform_matrix(out, a)

    _smart_reground(out, bottom_frac=bf)
    return out
