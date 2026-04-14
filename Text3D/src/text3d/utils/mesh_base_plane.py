"""
Alinha o plano médio da base da malha ao chão (normal exterior ≈ −Y) e assenta em Y=0.

Usa **faces** na faixa inferior ponderadas por **área** (mais estável que só vértices) e
**assentamento robusto** (mediana ponderada do contacto em Y, mais correção se algum vértice
ficar abaixo do chão).
"""

from __future__ import annotations

import numpy as np
import trimesh
from trimesh.transformations import translation_matrix

from ..mesh_beautify import _rotmat_unit_a_to_b


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


def _smart_reground(
    mesh: trimesh.Trimesh,
    *,
    bottom_frac: float,
) -> None:
    """
    Coloca o patch de contacto típico em Y≈0: mediana ponderada por área dos centros de face
    na faixa inferior; depois sobe a malha se algum vértice ficar ligeiramente abaixo de 0.
    """
    v = np.asarray(mesh.vertices, dtype=np.float64)
    if v.size < 9:
        return

    fc = np.asarray(mesh.triangles_center, dtype=np.float64)
    fa = np.asarray(mesh.area_faces, dtype=np.float64)

    ymin = float(v[:, 1].min())
    ymax = float(v[:, 1].max())
    yr = ymax - ymin
    if yr < 1e-9:
        return

    bf = float(np.clip(bottom_frac, 0.04, 0.35))
    y_cut = ymin + bf * yr
    mask = fc[:, 1] <= y_cut
    # Preferir faces com normal virada para baixo (exterior do apoio)
    fn = np.asarray(mesh.face_normals, dtype=np.float64)
    downish = fn[:, 1] < -0.12
    mask_strict = mask & downish
    if int(np.sum(mask_strict)) >= 8:
        mask = mask_strict
    elif int(np.sum(mask)) < 4:
        mask = fc[:, 1] <= float(np.percentile(fc[:, 1], 24))

    if int(np.sum(mask)) < 3:
        mesh.apply_translation([0.0, -ymin, 0.0])
        return

    ys = fc[mask, 1]
    w = fa[mask]
    y_ref = _weighted_median_1d(ys, w)
    mesh.apply_translation([0.0, -y_ref, 0.0])

    mny = float(mesh.vertices[:, 1].min())
    if mny < -1e-5:
        mesh.apply_translation([0.0, -mny, 0.0])


def align_mesh_base_plane_to_ground(
    mesh: trimesh.Trimesh,
    *,
    bottom_frac: float = 0.14,
    min_points: int = 12,
    min_tilt_rad: float = 0.004,
) -> trimesh.Trimesh:
    """
    Ajusta um plano à base com **faces** (ponderadas por área), alinha a normal a ``(0,-1,0)``,
    depois :func:`_smart_reground`.

    Sem rotação útil (já horizontal), aplica só o assentamento robusto.
    """
    out = mesh.copy()
    v = np.asarray(out.vertices, dtype=np.float64)
    if v.size < 9:
        return out

    ymin = float(v[:, 1].min())
    ymax = float(v[:, 1].max())
    yr = ymax - ymin
    if yr < 1e-9:
        return out

    bf = float(np.clip(bottom_frac, 0.04, 0.35))
    y_cut = ymin + bf * yr

    fc = np.asarray(out.triangles_center, dtype=np.float64)
    fa = np.asarray(out.area_faces, dtype=np.float64)
    fn = np.asarray(out.face_normals, dtype=np.float64)

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
            a = translation_matrix(pivot) @ t @ translation_matrix(-pivot)
            out.apply_transform(a)

    _smart_reground(out, bottom_frac=bf)
    return out
