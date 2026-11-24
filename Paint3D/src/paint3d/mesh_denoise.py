"""Pré-processamento geométrico e normais suavizadas com preservação de arestas para Paint3D.

Malhas vindas do Marching Cubes / Hi3D / decimação QEM frequentemente chegam ao
Paint3D com três problemas que geram ruído na pintura e fragmentam o atlas UV:

1. **Cascas internas invisíveis** (ex.: cavidades oclusas dentro de edifícios
   que consomem 20-30% do atlas UV em milhares de ilhas minúsculas sem 1 único
   pixel visível do exterior).
2. **Micro-ruído diedral** (facetas irregulares em paredes/telhados/roupas que
   criam mapas normais tipo "folha amassada" e quebram o ``xatlas`` em milhares
   de charts).
3. **Normais planas por triângulo (``shader_type="face"``)** na renderização de
   condicionamento — o UNet 2.5D literalmente pinta os triângulos planos na
   pele, rosto, roupas e telhados.

Este módulo resolve os três passos preservando os volumes chanfrados nítidos
(*Volume Talhado*):

- :func:`cull_occluded_components`: remove componentes secundários desconexos
  completamente oclusos sob 34 câmaras exteriores.
- :func:`bilateral_normal_denoise`: filtragem bilateral de normais + ajuste de
  vértices (Sun et al. 2007 / Zheng et al. 2011) com corte rígido em arestas
  vivas (``hard_cutoff_deg=55°``) e limite de deslocamento (``0.6%`` da
  diagonal AABB) — alisa micro-ruído mantendo chanfros arquitetónicos intactos.
- :func:`compute_welded_crease_normals`: normais interpoladas suavizadas através
  de seams UV (vértices soldados por posição), mantendo creases ``>= crease_deg``
  nítidas.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def bilateral_normal_denoise(
    verts: np.ndarray,
    faces: np.ndarray,
    *,
    normal_iters: int = 6,
    vertex_iters: int = 12,
    sigma_deg: float = 28.0,
    hard_cutoff_deg: float = 55.0,
    max_disp_frac: float = 0.006,
) -> np.ndarray:
    """Denoising geométrico *feature-preserving* (Sun et al. 2007 / Zheng et al. 2011).

    Filtra normais de face com kernel bilateral sobre faces adjacentes (peso 0
    acima de ``hard_cutoff_deg`` para nunca arredondar chanfros/arestas vivas) e
    atualiza posições de vértices para se alinharem aos planos filtrados,
    limitando o deslocamento máximo a ``max_disp_frac`` da diagonal do AABB.

    Returns:
        Array ``float32`` ``(V, 3)`` com as novas posições dos vértices.
    """
    V = np.asarray(verts, dtype=np.float64).copy()
    F_idx = np.asarray(faces, dtype=np.int64)
    n_v, n_f = len(V), len(F_idx)
    if n_v < 3 or n_f < 2:
        return V.astype(np.float32)

    diag = float(np.linalg.norm(V.max(axis=0) - V.min(axis=0)))
    if diag <= 1e-12:
        return V.astype(np.float32)
    max_disp = diag * float(max_disp_frac)
    V0 = V.copy()

    # Adjacência face-face por arestas partilhadas (vetorizado O(F log F)).
    edges = np.vstack(
        [
            np.sort(F_idx[:, [0, 1]], axis=1),
            np.sort(F_idx[:, [1, 2]], axis=1),
            np.sort(F_idx[:, [2, 0]], axis=1),
        ]
    )
    f_ids = np.tile(np.arange(n_f, dtype=np.int64), 3)
    keys = edges[:, 0] * n_v + edges[:, 1]
    order = np.argsort(keys)
    keys_s, f_s = keys[order], f_ids[order]
    same = keys_s[:-1] == keys_s[1:]
    if not same.any():
        return V.astype(np.float32)

    fa, fb = f_s[:-1][same], f_s[1:][same]
    adj_i = np.concatenate([fa, fb])
    adj_j = np.concatenate([fb, fa])

    v0, v1, v2 = V[F_idx[:, 0]], V[F_idx[:, 1]], V[F_idx[:, 2]]
    cr = np.cross(v1 - v0, v2 - v0)
    ln = np.linalg.norm(cr, axis=1, keepdims=True)
    area = 0.5 * ln[:, 0]
    n = cr / np.maximum(ln, 1e-12)

    cos_cut = float(np.cos(np.radians(hard_cutoff_deg)))
    sigma_d = float(1.0 - np.cos(np.radians(sigma_deg)))
    inv_2s2 = 1.0 / max(2.0 * sigma_d * sigma_d, 1e-8)

    for _ in range(int(normal_iters)):
        dots = np.einsum("ij,ij->i", n[adj_i], n[adj_j])
        d = np.maximum(0.0, 1.0 - dots)
        w = np.where(dots >= cos_cut, area[adj_j] * np.exp(-d * d * inv_2s2), 0.0)
        acc = n * area[:, None]
        np.add.at(acc, adj_i, n[adj_j] * w[:, None])
        n = acc / np.maximum(np.linalg.norm(acc, axis=1, keepdims=True), 1e-12)

    v_deg = np.maximum(np.bincount(F_idx.ravel(), minlength=n_v).astype(np.float64), 1.0)
    for _ in range(int(vertex_iters)):
        c = (V[F_idx[:, 0]] + V[F_idx[:, 1]] + V[F_idx[:, 2]]) / 3.0
        disp = np.zeros_like(V)
        for k in range(3):
            proj = np.einsum("ij,ij->i", n, c - V[F_idx[:, k]])
            np.add.at(disp, F_idx[:, k], n * proj[:, None])
        V += 0.5 * disp / v_deg[:, None]
        delta = V - V0
        dlen = np.linalg.norm(delta, axis=1, keepdims=True)
        V = V0 + delta * np.minimum(1.0, max_disp / np.maximum(dlen, 1e-12))

    return V.astype(np.float32)


def compute_welded_crease_normals(
    vtx_pos: np.ndarray,
    pos_idx: np.ndarray,
    face_normals: np.ndarray,
    *,
    crease_deg: float = 55.0,
) -> np.ndarray:
    """Normais suaves ponderadas pela área com soldagem por posição e corte de crease.

    Devolve array ``float32`` de shape ``(n_faces, 3, 3)`` (uma normal por canto
    de triângulo):

    - Regiões suaves (ângulo diedral ``< crease_deg``) são soldadas mesmo através
      de splits de seams UV, eliminando linhas de costura nas normais.
    - Arestas vivas (``>= crease_deg``) preservam normais separadas por lado,
      mantendo chanfros e silhuetas arquitetónicas nítidas.
    """
    V = np.asarray(vtx_pos, dtype=np.float64)
    F_idx = np.asarray(pos_idx, dtype=np.int64)
    FN = np.asarray(face_normals, dtype=np.float64)
    n_f = len(F_idx)
    if n_f == 0:
        return np.zeros((0, 3, 3), dtype=np.float32)

    v0, v1, v2 = V[F_idx[:, 0]], V[F_idx[:, 1]], V[F_idx[:, 2]]
    areas = np.maximum(0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1), 1e-12)

    # Soldagem por posição (1e-5) une vértices duplicados em seams UV.
    key = np.round(V, 5)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    n_w = int(inv.max()) + 1
    F_w = inv[F_idx]

    vn_all = np.zeros((n_w, 3), dtype=np.float64)
    wf = FN * areas[:, None]
    for k in range(3):
        np.add.at(vn_all, F_w[:, k], wf)
    vn_all /= np.maximum(np.linalg.norm(vn_all, axis=1, keepdims=True), 1e-12)

    if crease_deg >= 179.0:
        return vn_all[F_w].astype(np.float32)

    cos_thr = float(np.cos(np.radians(crease_deg)))
    corner_n = vn_all[F_w].copy()
    dots = np.einsum("ij,ikj->ik", FN, corner_n)
    bad_verts = np.unique(F_w[dots < float(np.cos(np.radians(crease_deg * 0.55)))])
    if len(bad_verts) > 0:
        flat_w = F_w.ravel()
        flat_f = np.repeat(np.arange(n_f, dtype=np.int64), 3)
        order = np.argsort(flat_w)
        w_s = flat_w[order]
        f_s = flat_f[order]
        split_pts = np.flatnonzero(np.diff(w_s)) + 1
        starts = np.concatenate([[0], split_pts])
        ends = np.concatenate([split_pts, [len(w_s)]])
        v_starts = np.zeros(n_w, dtype=np.int64)
        v_ends = np.zeros(n_w, dtype=np.int64)
        uniq_w = w_s[starts]
        v_starts[uniq_w] = starts
        v_ends[uniq_w] = ends

        corner_n_flat = corner_n.reshape(-1, 3)
        for wv in bad_verts:
            s0, e0 = int(v_starts[wv]), int(v_ends[wv])
            if e0 - s0 <= 1:
                continue
            orig_slots = order[s0:e0]
            fids = f_s[s0:e0]
            fns = FN[fids]
            fas = areas[fids]
            mask = ((fns @ fns.T) >= cos_thr).astype(np.float64)
            acc = (mask * fas[None, :]) @ fns
            acc /= np.maximum(np.linalg.norm(acc, axis=1, keepdims=True), 1e-12)
            corner_n_flat[orig_slots] = acc

    return corner_n.astype(np.float32)


def _face_connected_components(n_verts: int, faces: np.ndarray) -> list[np.ndarray]:
    """Componentes conexos de faces via arestas partilhadas (pure NumPy/SciPy)."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components

    n_f = len(faces)
    if n_f == 0:
        return []
    edges = np.vstack(
        [
            np.sort(faces[:, [0, 1]], axis=1),
            np.sort(faces[:, [1, 2]], axis=1),
            np.sort(faces[:, [2, 0]], axis=1),
        ]
    )
    f_ids = np.tile(np.arange(n_f, dtype=np.int64), 3)
    keys = edges[:, 0].astype(np.int64) * n_verts + edges[:, 1].astype(np.int64)
    order = np.argsort(keys)
    keys_s, f_s = keys[order], f_ids[order]
    same = keys_s[:-1] == keys_s[1:]
    if not same.any():
        return [np.array([i], dtype=np.int64) for i in range(n_f)]

    fa, fb = f_s[:-1][same], f_s[1:][same]
    row = np.concatenate([fa, fb])
    col = np.concatenate([fb, fa])
    graph = csr_matrix((np.ones(len(row), dtype=np.int8), (row, col)), shape=(n_f, n_f))
    n_cc, labels = connected_components(graph, directed=False)
    order_l = np.argsort(labels)
    l_s = labels[order_l]
    splits = np.flatnonzero(np.diff(l_s)) + 1
    groups = np.split(order_l, splits) if n_cc > 1 else [order_l]
    groups.sort(key=len, reverse=True)
    return groups


def cull_occluded_and_orient(
    verts: np.ndarray,
    faces: np.ndarray,
    render: Any,
    *,
    min_visible_px: int = 30,
    resolution: int = 384,
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    """Remove cascas internas invisíveis e orienta faces visíveis para fora usando ``render``.

    Mantém sempre o componente principal (``groups[0]``) e todos os componentes
    secundários com ``>= min_visible_px`` pixéis visíveis em 34 câmaras
    ortográficas exteriores.
    """
    import torch
    import torch.nn.functional as F

    from .hy3dpaint.DifferentiableRenderer.camera_utils import get_mv_matrix, transform_pos

    V = np.asarray(verts, dtype=np.float32)
    F_idx = np.asarray(faces, dtype=np.int32).copy()
    stats = {"dropped_faces": 0, "dropped_components": 0, "flipped_faces": 0}
    if render is None or len(F_idx) < 4:
        return V, F_idx, stats

    groups = _face_connected_components(len(V), F_idx)
    face_comp = np.zeros(len(F_idx), dtype=np.int32)
    for cid, grp in enumerate(groups):
        face_comp[grp] = cid

    c0 = (V.min(axis=0) + V.max(axis=0)) * 0.5
    ext = float((V.max(axis=0) - V.min(axis=0)).max())
    if ext <= 1e-8:
        return V, F_idx, stats
    v_norm = (V - c0) / ext

    device = render.device
    v_t = torch.from_numpy(v_norm).to(device=device, dtype=torch.float32)
    f_t = torch.from_numpy(F_idx).to(device=device, dtype=torch.int32)
    tris = v_t[f_t.long()]
    fn_w = F.normalize(torch.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0], dim=-1), dim=-1)

    votes = torch.zeros(len(F_idx), device=device, dtype=torch.float32)
    comp_px = np.zeros(len(groups), dtype=np.int64)
    cams = [(e, a) for a in range(0, 360, 45) for e in (-15, 0, 25, 55)] + [(90, 0), (-90, 0)]
    res = (int(resolution), int(resolution))
    proj = render.camera_proj_mat

    with torch.no_grad():
        for e, a in cams:
            r_mv = get_mv_matrix(elev=e, azim=a, camera_distance=render.camera_distance)
            pc = transform_pos(r_mv, v_t, keepdim=True)
            pclip = transform_pos(proj, pc)
            rast, _ = render.raster_rasterize(pclip, f_t, resolution=res)
            tid = rast[0, ..., 3].long()
            fg = tid > 0
            if not fg.any():
                continue
            fids = tid[fg] - 1
            counts = torch.bincount(fids, minlength=len(F_idx)).float()
            to_cam = torch.from_numpy(r_mv[2, :3]).to(device=device, dtype=torch.float32)
            votes += counts * (fn_w * to_cam).sum(dim=-1)
            if len(groups) > 1:
                cids, cnts = np.unique(face_comp[fids.cpu().numpy()], return_counts=True)
                for cid, cnt in zip(cids, cnts, strict=False):
                    comp_px[int(cid)] += int(cnt)

    flip = (votes < 0).cpu().numpy()
    if flip.any():
        F_idx[flip] = F_idx[flip][:, [0, 2, 1]]
        stats["flipped_faces"] = int(flip.sum())

    if len(groups) > 1:
        keep_mask = np.zeros(len(F_idx), dtype=bool)
        dropped_c = 0
        for cid, grp in enumerate(groups):
            if cid == 0 or comp_px[cid] >= int(min_visible_px):
                keep_mask[grp] = True
            else:
                dropped_c += 1
        if dropped_c > 0 and keep_mask.any():
            stats["dropped_faces"] = int((~keep_mask).sum())
            stats["dropped_components"] = dropped_c
            F_keep = F_idx[keep_mask]
            used_v, inv = np.unique(F_keep.ravel(), return_inverse=True)
            V = V[used_v]
            F_idx = inv.reshape(-1, 3).astype(np.int32)

    return V, F_idx, stats


def prepare_mesh_for_uv_and_paint(
    mesh_objects: Any,
    *,
    render: Any = None,
    logger: Any = None,
) -> Any:
    """Limpa geometria (casca interna + orientação + denoise bilateral) ANTES do xatlas.

    Desligar via ``PAINT3D_MESH_DENOISE=0``; só o cull via ``PAINT3D_CULL_OCCLUDED=0``.
    """
    do_denoise = _env_enabled("PAINT3D_MESH_DENOISE", True)
    do_cull = _env_enabled("PAINT3D_CULL_OCCLUDED", True)
    if not do_denoise and not do_cull:
        return mesh_objects

    import bpy

    from .hy3dpaint.utils.uvwrap_utils import _triangulate_in_place, _weld_positions

    if isinstance(mesh_objects, list):
        if not mesh_objects:
            return mesh_objects
        if len(mesh_objects) > 1:
            bpy.ops.object.select_all(action="DESELECT")
            for obj in mesh_objects:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = mesh_objects[0]
            bpy.ops.object.join()
            mesh_obj = bpy.context.active_object
        else:
            mesh_obj = mesh_objects[0]
    else:
        mesh_obj = mesh_objects

    mesh = mesh_obj.data
    if len(mesh.polygons) < 4:
        return mesh_obj
    if len(mesh.loops) != 3 * len(mesh.polygons):
        _triangulate_in_place(mesh_obj)
        mesh = mesh_obj.data

    verts = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", verts)
    verts = verts.reshape(-1, 3)
    faces = np.empty(len(mesh.polygons) * 3, dtype=np.int32)
    mesh.polygons.foreach_get("vertices", faces)
    faces = faces.reshape(-1, 3)

    verts, faces, _ = _weld_positions(verts, faces)
    orig_f = len(faces)

    cull_stats = {"dropped_faces": 0, "dropped_components": 0, "flipped_faces": 0}
    if do_cull and render is not None:
        try:
            verts, faces, cull_stats = cull_occluded_and_orient(verts, faces, render)
        except Exception as exc:
            if logger is not None:
                logger.warn(f"Mesh prep cull saltado ({exc})")

    if do_denoise:
        sigma_deg = float(os.environ.get("PAINT3D_DENOISE_SIGMA_DEG", "28.0") or 28.0)
        cutoff_deg = float(os.environ.get("PAINT3D_DENOISE_CUTOFF_DEG", "55.0") or 55.0)
        iters = int(os.environ.get("PAINT3D_DENOISE_ITERS", "6") or 6)
        verts = bilateral_normal_denoise(
            verts,
            faces,
            normal_iters=iters,
            vertex_iters=max(6, iters * 2),
            sigma_deg=sigma_deg,
            hard_cutoff_deg=cutoff_deg,
        )

    mesh.clear_geometry()
    mesh.from_pydata(verts.tolist(), [], faces.tolist())
    mesh.update()

    if logger is not None:
        parts = []
        if cull_stats["dropped_faces"]:
            dropped = cull_stats["dropped_faces"]
            shells = cull_stats["dropped_components"]
            parts.append(f"-{dropped:,}/{orig_f:,} faces internas ({shells} cascas)")
        if cull_stats["flipped_faces"]:
            parts.append(f"{cull_stats['flipped_faces']:,} faces reorientadas")
        if do_denoise:
            parts.append(f"bilateral feature-preserving (crease {cutoff_deg:g}°)")
        if parts:
            logger.info("Mesh prep pré-UV: " + ", ".join(parts))

    return mesh_obj
