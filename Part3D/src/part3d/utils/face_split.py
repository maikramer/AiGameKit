"""Split a mesh into per-part geometries using P3-SAM face IDs."""

from __future__ import annotations

from typing import Any

import numpy as np
import trimesh


def split_mesh_by_face_ids(
    mesh: trimesh.Trimesh,
    face_ids: np.ndarray,
    *,
    min_faces: int = 8,
    cap_holes: bool = False,
) -> trimesh.Scene:
    """Extract one submesh per face-id label (original topology — no X-Part regen).

    Args:
        mesh: Clean mesh from P3-SAM (same face count as ``face_ids``).
        face_ids: Per-face part labels (negative = ignore).
        min_faces: Skip tiny fragments below this face count.
        cap_holes: Close boundary loops per part via bpy ``fill_holes`` so
            removing a part (e.g. a door) leaves closed, usable geometry.

    Returns:
        Scene with nodes ``face_part_{id}``.
    """
    if len(mesh.faces) != len(face_ids):
        raise ValueError(f"face_ids length {len(face_ids)} != mesh faces {len(mesh.faces)}")

    scene = trimesh.Scene()
    ids = np.asarray(face_ids)
    for uid in sorted(int(u) for u in np.unique(ids) if u >= 0):
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
        scene.add_geometry(sub, node_name=f"face_part_{uid}")
    return scene


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
