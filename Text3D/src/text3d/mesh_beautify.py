"""Geometria: fusão de vértices por distância (Hunyuan/espedanços), decimar e Taubin — típico antes de pintura.

Por defeito a fusão por distância usa :func:`suggest_smart_weld_params` (ratio ~aresta média/diagonal).
"""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

import numpy as np
import trimesh

from .utils.export import _export_glb_with_normals, _load_as_trimesh
from .utils.mesh_repair import (
    _mean_edge_length,
    _pymeshlab_close_holes,
    isotropic_remesh,
    pymeshlab_repair_non_manifold,
    taubin_smooth,
)

# Fusão adaptativa — ver ``suggest_smart_weld_params``.
# ``K × (mean_edge / diagonal) × aggressiveness`` ≈ ratio; Hunyuan típico mel/diag ~0,008.
WELD_SMART_K_EDGE = 1.08
WELD_SMART_AGGRESSIVENESS_DEFAULT = 1.14
WELD_SMART_RATIO_MIN = 0.0058
WELD_SMART_RATIO_MAX = 0.0125
WELD_SMART_SECONDARY_FACTOR = 1.2
WELD_SMART_ITER_BASE = 8
WELD_SMART_ITER_PER_VERT = 12000.0
WELD_SMART_ITER_MIN = 8
WELD_SMART_ITER_MAX = 14
WELD_SMART_SECONDARY_CAP_DIAG_RATIO = 0.021


def _bbox_diagonal(mesh: trimesh.Trimesh) -> float:
    e = mesh.bounds[1] - mesh.bounds[0]
    return float(np.sqrt(np.dot(e, e)))


def suggest_smart_weld_params(
    mesh: trimesh.Trimesh,
    *,
    aggressiveness: float = WELD_SMART_AGGRESSIVENESS_DEFAULT,
) -> tuple[float, int, float | None]:
    """
    Deriva ``weld_diagonal_ratio``, repetições e factor secundário a partir da geometria.

    Ideia: o limiar absoluto de pymeshlab deve ser da ordem de **várias** arestas médias;
    normalizado pela diagonal da AABB obtém-se um ratio estável entre escalas de prop.

    Devolve ``(ratio_diagonal, iterations, secondary_factor | None)``.
    """
    diag = _bbox_diagonal(mesh)
    mel = float(_mean_edge_length(mesh))
    if diag <= 1e-12 or mel <= 1e-12:
        ratio = 0.0085
    else:
        ratio = (WELD_SMART_K_EDGE * mel / diag) * float(aggressiveness)
        ratio = float(np.clip(ratio, WELD_SMART_RATIO_MIN, WELD_SMART_RATIO_MAX))
    nv = len(mesh.vertices)
    it = int(
        np.clip(
            WELD_SMART_ITER_BASE + nv / WELD_SMART_ITER_PER_VERT,
            WELD_SMART_ITER_MIN,
            WELD_SMART_ITER_MAX,
        )
    )
    thr_abs = ratio * diag
    cap = diag * WELD_SMART_SECONDARY_CAP_DIAG_RATIO
    sec = (
        WELD_SMART_SECONDARY_FACTOR
        if thr_abs * WELD_SMART_SECONDARY_FACTOR <= cap
        else None
    )
    return ratio, it, sec


def _pymeshlab_merge_close_vertices_once(
    mesh: trimesh.Trimesh,
    threshold_abs: float,
) -> trimesh.Trimesh | None:
    """Uma passagem ``meshing_merge_close_vertices``; devolve ``None`` se pymeshlab falhar."""
    if threshold_abs <= 0:
        return mesh
    try:
        import pymeshlab
    except ImportError:
        return None

    with tempfile.TemporaryDirectory(prefix="t3d_weld_") as tmp:
        td = Path(tmp)
        in_p = td / "in.ply"
        out_p = td / "out.ply"
        mesh.export(str(in_p))
        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(str(in_p))
        ms.meshing_merge_close_vertices(threshold=pymeshlab.PureValue(float(threshold_abs)))
        ms.save_current_mesh(str(out_p))
        m2 = trimesh.load(str(out_p), force="mesh")
        if isinstance(m2, trimesh.Trimesh) and len(m2.faces) > 0:
            return m2
    return None


def merge_close_vertices_trimesh(
    mesh: trimesh.Trimesh,
    *,
    threshold_abs: float,
    iterations: int = 5,
    secondary_factor: float | None = None,
    max_threshold_diagonal_ratio: float = 0.022,
) -> trimesh.Trimesh:
    """
    Solda vértices cuja distância é **<= threshold_abs** (unidades do modelo).

    Repete ``meshing_merge_close_vertices`` várias vezes com o **mesmo** limiar: após cada
    passo os vértices aproximam-se, permitindo fundir pares que antes estavam logo acima do
    limiar (típico em meshes Hunyuan espelhuchadas). Opcionalmente, uma última passagem com
    ``threshold_abs * secondary_factor``, limitada a
    ``max_threshold_diagonal_ratio``×diagonal (evita colapso; ratios primários >~0,008 são
    perigosos em props pequenos).
    """
    if threshold_abs <= 0:
        return mesh
    m: trimesh.Trimesh = mesh
    n_prev = -1
    for _ in range(max(1, int(iterations))):
        out = _pymeshlab_merge_close_vertices_once(m, threshold_abs)
        if out is None:
            return m
        m = out
        m = trimesh.Trimesh(
            vertices=np.asarray(m.vertices, dtype=np.float64),
            faces=np.asarray(m.faces, dtype=np.int64),
            process=True,
        )
        nv = len(m.vertices)
        if nv == n_prev:
            break
        n_prev = nv

    if secondary_factor is not None and float(secondary_factor) > 1.0:
        cap = _bbox_diagonal(m) * float(max_threshold_diagonal_ratio)
        thr2 = min(float(threshold_abs) * float(secondary_factor), cap)
        if thr2 > threshold_abs:
            out2 = _pymeshlab_merge_close_vertices_once(m, thr2)
            if out2 is not None:
                m = out2

    return m


def _apply_post_euler_deg(
    mesh: trimesh.Trimesh,
    *,
    rx_deg: float = 0.0,
    ry_deg: float = 0.0,
    rz_deg: float = 0.0,
) -> trimesh.Trimesh:
    """Rotação extrínseca XYZ (graus) em torno da origem — corrige orientação pós‑pipeline."""
    if abs(rx_deg) < 1e-9 and abs(ry_deg) < 1e-9 and abs(rz_deg) < 1e-9:
        return mesh
    from trimesh.transformations import euler_matrix

    T = euler_matrix(
        np.radians(rx_deg),
        np.radians(ry_deg),
        np.radians(rz_deg),
        axes="sxyz",
    )
    m = mesh.copy()
    m.apply_transform(T)
    return m


def _seam_cleanup_after_weld(
    mesh: trimesh.Trimesh,
    *,
    close_holes_max_edges: int | None,
    repair_non_manifold: bool,
) -> trimesh.Trimesh:
    m = mesh
    if close_holes_max_edges is not None and int(close_holes_max_edges) > 0:
        m = _pymeshlab_close_holes(m, max_hole_edges=int(close_holes_max_edges))
    if repair_non_manifold:
        m = pymeshlab_repair_non_manifold(m)
    m = trimesh.Trimesh(
        vertices=np.asarray(m.vertices, dtype=np.float64),
        faces=np.asarray(m.faces, dtype=np.int64),
        process=True,
    )
    with contextlib.suppress(Exception):
        m.merge_vertices(merge_tex=True, merge_norm=True)
        m.remove_unreferenced_vertices()
    return m


def _rotmat_unit_a_to_b(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Matriz 3×3 R com R @ a = b (a, b não nulos; normalizados internamente)."""
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
        if abs(a[0]) < 0.9:
            ortho = np.array([1.0, 0.0, 0.0])
        else:
            ortho = np.array([0.0, 1.0, 0.0])
        axis = np.cross(a, ortho)
        axis = axis / np.linalg.norm(axis)
        from trimesh.transformations import rotation_matrix

        return rotation_matrix(np.pi, axis)[:3, :3].astype(np.float64)
    vx = np.array(
        [[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]],
        dtype=np.float64,
    )
    return (np.eye(3) + vx + vx @ vx * ((1.0 - c) / (s * s))).astype(np.float64)


def align_largest_plus_z_face_normal_to_ground(
    mesh: trimesh.Trimesh,
    *,
    dot_min: float = 0.82,
    min_faces: int = 60,
    bottom_percentile: float = 48.0,
) -> trimesh.Trimesh:
    """
    Hunyuan costuma pôr a face de corte de cristais com normais ~ +Z (virada “pr’a frente”);
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


def beautify_geometry(
    mesh: trimesh.Trimesh,
    *,
    skip_distance_weld: bool = False,
    weld_diagonal_ratio: float | None = None,
    weld_smart_aggressiveness: float = WELD_SMART_AGGRESSIVENESS_DEFAULT,
    weld_iterations: int | None = None,
    weld_secondary_factor: float | None = None,
    close_holes_max_edges: int | None = None,
    repair_non_manifold_after_weld: bool = True,
    post_rotate_x_deg: float = 0.0,
    post_rotate_y_deg: float = 0.0,
    post_rotate_z_deg: float = 0.0,
    align_plus_z_cluster_to_ground: bool = False,
    align_plus_z_dot_min: float = 0.82,
    align_plus_z_min_faces: int = 60,
    align_plus_z_bottom_percentile: float = 48.0,
    isotropic_remesh_resolution: int | None = None,
    weld_only: bool = False,
    face_count: int | None = None,
    face_ratio: float | None = 0.45,
    taubin_steps: int = 10,
    taubin_lambda: float = 0.33,
    taubin_mu: float = -0.33,
) -> trimesh.Trimesh:
    """
    ``weld_diagonal_ratio`` — fração da diagonal AABB para ``meshing_merge_close_vertices``.
    ``None`` (defeito) activa :func:`suggest_smart_weld_params`. Um **float** > 0 fixa o
    ratio; ``<= 0`` ou ``skip_distance_weld=True`` desliga a fusão pymeshlab (só
    ``merge_vertices`` trimesh abaixo).

    ``weld_iterations`` / ``weld_secondary_factor`` em ``None`` acompanham o modo inteligente;
    com ratio fixo, iterações por defeito **10** e secundária **None**.
    """
    m = trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices, dtype=np.float64),
        faces=np.asarray(mesh.faces, dtype=np.int64),
        process=True,
    )

    resolved_ratio: float | None = None
    resolved_iter = 10
    resolved_sec: float | None = None

    if not skip_distance_weld:
        if weld_diagonal_ratio is None:
            ar, ait, asec = suggest_smart_weld_params(
                m, aggressiveness=float(weld_smart_aggressiveness)
            )
            resolved_ratio = ar
            resolved_iter = ait if weld_iterations is None else int(weld_iterations)
            resolved_sec = asec if weld_secondary_factor is None else weld_secondary_factor
        elif float(weld_diagonal_ratio) > 0:
            resolved_ratio = float(weld_diagonal_ratio)
            resolved_iter = int(weld_iterations) if weld_iterations is not None else 10
            resolved_sec = weld_secondary_factor

    if resolved_ratio is not None:
        thr = _bbox_diagonal(m) * float(resolved_ratio)
        m = merge_close_vertices_trimesh(
            m,
            threshold_abs=thr,
            iterations=int(resolved_iter),
            secondary_factor=resolved_sec,
        )

    m.merge_vertices(merge_tex=True, merge_norm=True)

    m = _seam_cleanup_after_weld(
        m,
        close_holes_max_edges=close_holes_max_edges,
        repair_non_manifold=repair_non_manifold_after_weld,
    )

    if align_plus_z_cluster_to_ground:
        m = align_largest_plus_z_face_normal_to_ground(
            m,
            dot_min=float(align_plus_z_dot_min),
            min_faces=int(align_plus_z_min_faces),
            bottom_percentile=float(align_plus_z_bottom_percentile),
        )

    if isotropic_remesh_resolution is not None and int(isotropic_remesh_resolution) > 0:
        m = isotropic_remesh(
            m,
            resolution=int(isotropic_remesh_resolution),
            iterations=5,
            adaptive=True,
            max_surf_dist_factor=0.4,
            close_holes=True,
            close_holes_max_edges=220,
            taubin_steps=4,
            taubin_lambda=float(taubin_lambda),
            taubin_mu=float(taubin_mu),
        )
        if isinstance(m, trimesh.Trimesh):
            m = trimesh.Trimesh(
                vertices=np.asarray(m.vertices, dtype=np.float64),
                faces=np.asarray(m.faces, dtype=np.int64),
                process=True,
            )

    if weld_only:
        m = taubin_smooth(
            m,
            iterations=max(0, int(taubin_steps)),
            lambda_=float(taubin_lambda),
            mu=float(taubin_mu),
        )
        m = trimesh.Trimesh(
            vertices=np.asarray(m.vertices, dtype=np.float64),
            faces=np.asarray(m.faces, dtype=np.int64),
            process=True,
        )
        m.merge_vertices(merge_tex=True, merge_norm=True)
        m.remove_unreferenced_vertices()
        try:
            m.fix_normals()
        except Exception:
            _ = m.vertex_normals
        m = _apply_post_euler_deg(
            m,
            rx_deg=post_rotate_x_deg,
            ry_deg=post_rotate_y_deg,
            rz_deg=post_rotate_z_deg,
        )
        try:
            m.fix_normals()
        except Exception:
            _ = m.vertex_normals
        return m

    nfaces = len(m.faces)
    target = face_count
    if target is None and face_ratio is not None:
        target = max(800, int(nfaces * float(face_ratio)))
    if target is not None and nfaces > target:
        m = m.simplify_quadric_decimation(face_count=int(target))

    m = taubin_smooth(
        m,
        iterations=max(0, int(taubin_steps)),
        lambda_=float(taubin_lambda),
        mu=float(taubin_mu),
    )
    m = trimesh.Trimesh(
        vertices=np.asarray(m.vertices, dtype=np.float64),
        faces=np.asarray(m.faces, dtype=np.int64),
        process=True,
    )
    m.merge_vertices(merge_tex=True, merge_norm=True)
    m.remove_unreferenced_vertices()
    try:
        m.fix_normals()
    except Exception:
        _ = m.vertex_normals
    m = _apply_post_euler_deg(
        m,
        rx_deg=post_rotate_x_deg,
        ry_deg=post_rotate_y_deg,
        rz_deg=post_rotate_z_deg,
    )
    try:
        m.fix_normals()
    except Exception:
        _ = m.vertex_normals
    return m


def beautify_glb_file(path_in: str | Path, path_out: str | Path, **kwargs) -> Path:
    """Carrega GLB (cena fundida), ``beautify_geometry``, exporta GLB só geometria."""
    path_in = Path(path_in)
    path_out = Path(path_out)
    mesh = _load_as_trimesh(path_in)
    out = beautify_geometry(mesh, **kwargs)
    path_out.parent.mkdir(parents=True, exist_ok=True)
    _export_glb_with_normals(out, path_out)
    return path_out
