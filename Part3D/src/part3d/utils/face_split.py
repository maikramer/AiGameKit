"""Split a mesh into per-part geometries using P3-SAM face IDs."""

from __future__ import annotations

from typing import Any

import numpy as np
import trimesh


def label_ids_ordered(face_ids: np.ndarray) -> np.ndarray:
    """Unique non-negative labels in ascending order (matches AABB build order)."""
    ids = np.asarray(face_ids)
    return np.asarray(sorted(int(u) for u in np.unique(ids) if u >= 0), dtype=np.int64)


def _aabb_thinness(pts: np.ndarray) -> tuple[float, float]:
    """Return ``(thin_ratio, aspect)`` for a point cloud AABB.

    ``thin_ratio`` = min_extent / max_extent (↓ = mais fina).
    ``aspect`` = max_extent / min_extent (↑ = mais alongada).
    """
    mn = pts.min(axis=0)
    mx = pts.max(axis=0)
    ext = np.maximum(mx - mn, 1e-12)
    thin = float(ext.min() / ext.max())
    aspect = float(ext.max() / ext.min())
    return thin, aspect


def thin_part_mask(
    mesh: trimesh.Trimesh,
    face_ids: np.ndarray,
    *,
    max_thin_ratio: float = 0.20,
    min_aspect: float = 5.0,
    soft_thin_ratio: float = 0.35,
    soft_max_area_frac: float = 0.05,
    tiny_max_area_frac: float = 0.02,
    tiny_max_thin_ratio: float = 0.65,
) -> np.ndarray:
    """Boolean mask over ``label_ids_ordered`` — True = demasiado fina para X-Part.

    X-Part/MC derrete escadas, bandeiras, antenas e painéis: a topologia
    original (face-split) preserva a silhueta. Critérios gerais (sem
    heurísticas de asset):

    * AABB muito plana (``thin_ratio ≤ max_thin_ratio``) ou alongada
      (``aspect ≥ min_aspect``);
    * **ou** feature pequena e relativamente fina
      (``area_frac ≤ soft_max_area_frac`` e ``thin_ratio ≤ soft_thin_ratio``);
    * **ou** feature minúscula (``area_frac ≤ tiny_max_area_frac`` e
      ``thin_ratio ≤ tiny_max_thin_ratio``) — bandeira/pano, ornamentos.
    """
    labels = label_ids_ordered(face_ids)
    if len(labels) == 0:
        return np.zeros(0, dtype=bool)
    verts = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    ids = np.asarray(face_ids)
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    total_area = float(areas.sum()) + 1e-12
    mask = np.zeros(len(labels), dtype=bool)
    for i, uid in enumerate(labels):
        sel = ids == uid
        pts = verts[faces[sel].reshape(-1)]
        if pts.size == 0:
            continue
        thin, aspect = _aabb_thinness(pts)
        area_frac = float(areas[sel].sum()) / total_area
        hard = thin <= float(max_thin_ratio) or aspect >= float(min_aspect)
        soft = area_frac <= float(soft_max_area_frac) and thin <= float(soft_thin_ratio)
        tiny = area_frac <= float(tiny_max_area_frac) and thin <= float(tiny_max_thin_ratio)
        if hard or soft or tiny:
            mask[i] = True
    return mask


def xpart_candidate_mask(
    mesh: trimesh.Trimesh,
    face_ids: np.ndarray,
    *,
    max_area_frac: float = 0.10,
) -> np.ndarray:
    """Boolean mask over ``label_ids_ordered`` — True = compact (full octree).

    False = large slab: still runs X-Part, but with reduced octree. Never skips
    the phase — face-split is only for true decode failures / thin parts.
    """
    labels = label_ids_ordered(face_ids)
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    total = float(areas.sum()) + 1e-12
    ids = np.asarray(face_ids)
    mask = np.ones(len(labels), dtype=bool)
    for i, uid in enumerate(labels):
        frac = float(areas[ids == uid].sum()) / total
        if frac > max_area_frac:
            mask[i] = False
    return mask


def split_mesh_by_face_ids(
    mesh: trimesh.Trimesh,
    face_ids: np.ndarray,
    *,
    min_faces: int = 8,
    cap_holes: bool = False,
    only_labels: np.ndarray | set[int] | None = None,
) -> trimesh.Scene:
    """Extract one submesh per face-id label (original topology — no X-Part regen).

    Args:
        mesh: Clean mesh from P3-SAM (same face count as ``face_ids``).
        face_ids: Per-face part labels (negative = ignore).
        min_faces: Skip tiny fragments below this face count.
        cap_holes: Close boundary loops per part via bpy ``fill_holes`` so
            removing a part (e.g. a door) leaves closed, usable geometry.
        only_labels: If set, only emit these label ids.

    Returns:
        Scene with nodes ``part_{id}`` (same naming as X-Part for merge).
    """
    if len(mesh.faces) != len(face_ids):
        raise ValueError(f"face_ids length {len(face_ids)} != mesh faces {len(mesh.faces)}")

    scene = trimesh.Scene()
    ids = np.asarray(face_ids)
    allow = None if only_labels is None else {int(x) for x in only_labels}
    for uid in sorted(int(u) for u in np.unique(ids) if u >= 0):
        if allow is not None and uid not in allow:
            continue
        face_idx = np.flatnonzero(ids == uid)
        if face_idx.size < min_faces:
            continue
        try:
            sub = mesh.submesh([face_idx], append=True, repair=False)
        except Exception:
            # Fallback: rebuild from face subset
            faces = mesh.faces[face_idx]
            sub = trimesh.Trimesh(vertices=mesh.vertices.copy(), faces=faces, process=False)
            sub.remove_unreferenced_vertices()
        if isinstance(sub, list):
            sub = sub[0] if sub else None
        if sub is None or len(getattr(sub, "faces", [])) == 0:
            continue
        if cap_holes:
            try:
                from .mesh_bpy import cap_boundary_holes

                sub = cap_boundary_holes(sub)
            except Exception:
                pass  # parte fica aberta mas utilizável; bpy pode faltar em CI
        scene.add_geometry(sub, geom_name=f"part_{uid}", node_name=f"part_{uid}")
    return scene


def carve_meshes_outside_aabbs(
    geom: trimesh.Trimesh,
    carve_boxes: list[tuple[np.ndarray, np.ndarray]],
    *,
    margin_frac: float = 0.08,
    thin_axis_pad_scale: float = 0.0,
) -> trimesh.Trimesh:
    """Remove vertices (e faces) de ``geom`` que caem dentro de AABBs preservadas.

    Evita que o X-Part “alucine” escada/bandeira derretida em cima da
    topologia original que vamos colar a seguir.

    Com ``thin_axis_pad_scale > 0``, o eixo mais fino da AABB recebe pad
    extra (slab) — remove o casco derretido à frente/atrás da feature sem
    expandir tanto para dentro do volume grosso.
    """
    if not carve_boxes or len(geom.vertices) == 0:
        return geom
    verts = np.asarray(geom.vertices, dtype=np.float64)
    kill = np.zeros(len(verts), dtype=bool)
    for mn, mx in carve_boxes:
        mn = np.asarray(mn, dtype=np.float64)
        mx = np.asarray(mx, dtype=np.float64)
        ext = np.maximum(mx - mn, 1e-12)
        pad = np.maximum(ext * float(margin_frac), 1e-4)
        if float(thin_axis_pad_scale) > 0:
            thin_i = int(np.argmin(ext))
            pad = pad.copy()
            pad[thin_i] = max(pad[thin_i], ext[thin_i] * float(thin_axis_pad_scale))
        lo, hi = mn - pad, mx + pad
        kill |= np.all((verts >= lo) & (verts <= hi), axis=1)
    if not kill.any():
        return geom
    keep_faces = ~kill[np.asarray(geom.faces)].any(axis=1)
    if not keep_faces.any():
        return geom
    out = trimesh.Trimesh(vertices=verts.copy(), faces=np.asarray(geom.faces)[keep_faces], process=False)
    out.remove_unreferenced_vertices()
    return out if len(out.faces) > 0 else geom


def carve_meshes_near_points(
    geom: trimesh.Trimesh,
    preserve_points: np.ndarray,
    *,
    radius: float,
    outward_from: np.ndarray | None = None,
    outward_only: bool = False,
) -> trimesh.Trimesh:
    """Remove verts de ``geom`` a distância ≤ ``radius`` de ``preserve_points``.

    Com ``outward_only`` + ``outward_from``, só remove verts do lado de fora
    da feature (longe do centro do mesh) — evita furar a parede atrás da
    escada/bandeira enquanto limpa o “fantasma” à frente.
    """
    pts = np.asarray(preserve_points, dtype=np.float64).reshape(-1, 3)
    if pts.size == 0 or len(geom.vertices) == 0 or radius <= 0:
        return geom
    verts = np.asarray(geom.vertices, dtype=np.float64)
    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(pts)
        dist, _ = tree.query(verts, k=1, workers=-1)
        kill = dist <= float(radius)
    except Exception:
        # Fallback O(n*m) só para meshes pequenos
        # (evita dependência dura de scipy em ambientes mínimos)
        d2 = ((verts[:, None, :] - pts[None, :, :]) ** 2).sum(axis=2).min(axis=1)
        kill = d2 <= float(radius) ** 2
    if outward_only and outward_from is not None and kill.any():
        origin = np.asarray(outward_from, dtype=np.float64).reshape(3)
        feat_c = pts.mean(axis=0)
        outward = feat_c - origin
        nrm = float(np.linalg.norm(outward))
        if nrm > 1e-12:
            outward = outward / nrm
            # Meio-espaço exterior da feature (proj≥0). Margem mínima só
            # para ruído numérico — margem larga voltava a comer a parede.
            proj = (verts - feat_c) @ outward
            kill &= proj >= -1e-6
    if not kill.any():
        return geom
    keep_faces = ~kill[np.asarray(geom.faces)].any(axis=1)
    if not keep_faces.any():
        return geom
    out = trimesh.Trimesh(vertices=verts.copy(), faces=np.asarray(geom.faces)[keep_faces], process=False)
    out.remove_unreferenced_vertices()
    return out if len(out.faces) > 0 else geom


def carve_thin_components_near_points(
    geom: trimesh.Trimesh,
    preserve_points: np.ndarray,
    *,
    radius: float,
    max_thin_ratio: float = 0.35,
    min_aspect: float = 3.5,
    max_area_frac: float = 0.20,
) -> trimesh.Trimesh:
    """Remove componentes conectados finos/pequenos perto de ``preserve_points``.

    Não toca no corpo grosso mesmo que partilhe a vizinhança da escada —
    só blobs/cascos desligados (alucinações X-Part).
    """
    pts = np.asarray(preserve_points, dtype=np.float64).reshape(-1, 3)
    if pts.size == 0 or len(geom.vertices) == 0 or radius <= 0:
        return geom
    try:
        comps = geom.split(only_watertight=False)
    except Exception:
        return geom
    if len(comps) <= 1:
        return geom
    total_area = float(sum(float(c.area) for c in comps)) + 1e-12
    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(pts)
    except Exception:
        tree = None
    keep: list[trimesh.Trimesh] = []
    removed = False
    for comp in comps:
        if len(comp.faces) == 0:
            continue
        verts = np.asarray(comp.vertices, dtype=np.float64)
        if tree is not None:
            dist, _ = tree.query(verts, k=1, workers=-1)
            near = bool(np.min(dist) <= float(radius))
        else:
            d2 = ((verts[:, None, :] - pts[None, :, :]) ** 2).sum(axis=2).min()
            near = bool(d2 <= float(radius) ** 2)
        if not near:
            keep.append(comp)
            continue
        thin, aspect = _aabb_thinness(verts)
        area_frac = float(comp.area) / total_area
        drop = thin <= float(max_thin_ratio) or aspect >= float(min_aspect) or area_frac <= float(max_area_frac)
        if drop:
            removed = True
            continue
        keep.append(comp)
    if not removed or not keep:
        return geom
    if len(keep) == 1:
        return keep[0]
    return trimesh.util.concatenate(keep)


def _label_aabb(mesh: trimesh.Trimesh, face_ids: np.ndarray, uid: int) -> tuple[np.ndarray, np.ndarray] | None:
    ids = np.asarray(face_ids)
    sel = ids == int(uid)
    if not sel.any():
        return None
    pts = np.asarray(mesh.vertices)[np.asarray(mesh.faces)[sel].reshape(-1)]
    if pts.size == 0:
        return None
    return pts.min(axis=0), pts.max(axis=0)


def _label_points(mesh: trimesh.Trimesh, face_ids: np.ndarray, uids: set[int]) -> np.ndarray:
    """Vértices únicos das faces com labels em ``uids``."""
    ids = np.asarray(face_ids)
    faces = np.asarray(mesh.faces)
    sel = np.isin(ids, list(uids))
    if not sel.any():
        return np.zeros((0, 3), dtype=np.float64)
    idx = np.unique(faces[sel].reshape(-1))
    return np.asarray(mesh.vertices, dtype=np.float64)[idx]


def merge_xpart_with_face_fallback(
    mesh: trimesh.Trimesh,
    face_ids: np.ndarray,
    xpart_scene: trimesh.Scene | None,
    succeeded_labels: set[int] | list[int] | None,
    *,
    cap_holes: bool = False,
    prefer_face_labels: set[int] | list[int] | None = None,
    carve_xpart_for_face: bool = False,
    carve_radius_frac: float = 0.08,
) -> trimesh.Scene:
    """Prefer X-Part meshes; fill missing / thin labels from original face topology.

    ``prefer_face_labels`` força face-split mesmo quando o X-Part “sucesso”
    (estruturas finas regeneradas ficam derretidas).

    ``carve_xpart_for_face`` está OFF por defeito: em features coladas ao
    volume (escada na parede) o carve remove paredes/plataformas. Opt-in
    só quando a feature está isolada no espaço.
    """
    ok = {int(x) for x in (succeeded_labels or [])}
    prefer_face = {int(x) for x in (prefer_face_labels or [])}
    # X-Part em labels finas não conta — substituímos pela topologia original.
    ok -= prefer_face

    carve_boxes: list[tuple[np.ndarray, np.ndarray]] = []
    preserve_pts = np.zeros((0, 3), dtype=np.float64)
    carve_radius = 0.0
    mesh_center = np.asarray(mesh.bounds, dtype=np.float64).mean(axis=0)
    if carve_xpart_for_face and prefer_face:
        for uid in sorted(prefer_face):
            box = _label_aabb(mesh, face_ids, uid)
            if box is not None:
                carve_boxes.append(box)
        preserve_pts = _label_points(mesh, face_ids, prefer_face)
        # Raio relativo à diagonal do mesh completo.
        b = np.asarray(mesh.bounds, dtype=np.float64)
        diag = float(np.linalg.norm(b[1] - b[0])) or 1.0
        carve_radius = max(diag * float(carve_radius_frac), 1e-3)

    out = trimesh.Scene()
    if xpart_scene is not None:
        for name, geom in xpart_scene.geometry.items():
            # part_12 → 12
            try:
                lid = int(str(name).rsplit("_", 1)[-1])
            except ValueError:
                lid = -1
            if lid in prefer_face:
                continue
            g = geom
            if isinstance(geom, trimesh.Trimesh) and (carve_boxes or len(preserve_pts)):
                if carve_boxes:
                    # Slab no eixo fino: casco derretido à volta da escada sem
                    # expandir o AABB grosso (fura a parede).
                    g = carve_meshes_outside_aabbs(
                        g,
                        carve_boxes,
                        margin_frac=0.04,
                        thin_axis_pad_scale=4.0,
                    )
                if len(preserve_pts) and carve_radius > 0:
                    g = carve_thin_components_near_points(g, preserve_pts, radius=carve_radius)
                    g = carve_meshes_near_points(
                        g,
                        preserve_pts,
                        radius=carve_radius,
                        outward_from=mesh_center,
                        outward_only=True,
                    )
            if isinstance(g, trimesh.Trimesh) and len(g.faces) == 0:
                continue
            out.add_geometry(g, geom_name=name, node_name=name)
    missing = [int(u) for u in label_ids_ordered(face_ids) if int(u) not in ok]
    if missing:
        face_scene = split_mesh_by_face_ids(mesh, face_ids, cap_holes=cap_holes, only_labels=missing, min_faces=1)
        for name, geom in face_scene.geometry.items():
            if name not in out.geometry:
                out.add_geometry(geom, geom_name=name, node_name=name)
    return out


def face_part_stats(scene: trimesh.Scene) -> list[dict[str, Any]]:
    """AABB / face counts for face-split parts (door heuristics)."""
    rows: list[dict[str, Any]] = []
    for name, geom in scene.geometry.items():
        if not isinstance(geom, trimesh.Trimesh) or len(geom.faces) == 0:
            continue
        b = geom.bounds
        extents = b[1] - b[0]
        rows.append(
            {
                "name": name,
                "faces": len(geom.faces),
                "extents": extents.tolist(),
                "thin_axis": int(np.argmin(extents)),
                "thin_extent": float(np.min(extents)),
                "aspect_max_min": float(np.max(extents) / max(float(np.min(extents)), 1e-6)),
            }
        )
    rows.sort(key=lambda r: r["faces"])
    return rows
