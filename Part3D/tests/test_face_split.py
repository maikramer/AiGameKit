"""Tests for face_id mesh split."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh
from part3d.utils.face_split import face_part_stats, split_mesh_by_face_ids


def _open_edge_count(mesh: trimesh.Trimesh) -> int:
    edges = np.sort(mesh.faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
    _uniq, counts = np.unique(edges, axis=0, return_counts=True)
    return int((counts == 1).sum())


def test_split_mesh_by_face_ids_two_parts():
    # Two quads sharing no verts ideally — box faces labeled 0/1
    mesh = trimesh.creation.box()
    face_ids = np.zeros(len(mesh.faces), dtype=np.int32)
    face_ids[len(mesh.faces) // 2 :] = 1
    scene = split_mesh_by_face_ids(mesh, face_ids, min_faces=1)
    assert len(scene.geometry) == 2
    stats = face_part_stats(scene)
    assert sum(s["faces"] for s in stats) == len(mesh.faces)


def test_split_skips_tiny():
    mesh = trimesh.creation.box()
    face_ids = np.arange(len(mesh.faces), dtype=np.int32)  # each face own id
    scene = split_mesh_by_face_ids(mesh, face_ids, min_faces=8)
    assert len(scene.geometry) == 0


def test_split_cap_holes_closes_boundaries():
    pytest.importorskip("bpy")
    mesh = trimesh.creation.box().subdivide()
    # Split by centroid height: each half is an open shell with a square boundary loop.
    face_ids = (mesh.triangles_center[:, 2] > 0).astype(np.int32)
    open_scene = split_mesh_by_face_ids(mesh, face_ids, min_faces=1, cap_holes=False)
    capped_scene = split_mesh_by_face_ids(mesh, face_ids, min_faces=1, cap_holes=True)
    assert len(capped_scene.geometry) == len(open_scene.geometry) == 2
    for name, capped in capped_scene.geometry.items():
        open_part = open_scene.geometry[name]
        assert _open_edge_count(open_part) > 0
        assert _open_edge_count(capped) < _open_edge_count(open_part)
