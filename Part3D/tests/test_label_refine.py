"""Tests for crease-aware label refinement (part3d.utils.label_refine)."""

from __future__ import annotations

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")

from part3d.utils.label_refine import (  # noqa: E402
    aabbs_from_face_ids,
    edge_costs,
    icm_boundary_snap,
    refine_face_labels,
)


def _plane_mesh(nx: int = 6, ny: int = 6) -> trimesh.Trimesh:
    """Flat triangulated grid on z=0 with nx*ny quads (2 tris each)."""
    verts = []
    for j in range(ny + 1):
        for i in range(nx + 1):
            verts.append((float(i), float(j), 0.0))
    faces = []
    for j in range(ny):
        for i in range(nx):
            v00 = j * (nx + 1) + i
            v10 = v00 + 1
            v01 = v00 + (nx + 1)
            v11 = v01 + 1
            faces.append((v00, v10, v11))
            faces.append((v00, v11, v01))
    mesh = trimesh.Trimesh(vertices=np.array(verts), faces=np.array(faces), process=False)
    mesh.fix_normals()
    return mesh


def _tent_mesh(cols: int = 4) -> trimesh.Trimesh:
    """Two rectangular strips meeting at a 90° ridge along x.

    Side A (faces 0..2*cols-1): horizontal plane (y in [-1, 0], z=0).
    Side B (faces 2*cols..4*cols-1): vertical plane (y=0, z in [0, 1]).
    Ridge = shared edge row at y=0, z=0.
    """
    n = cols + 1
    verts = []
    for i in range(n):  # rowA: 0..cols
        verts.append((float(i), -1.0, 0.0))
    for i in range(n):  # rowR (ridge): n..2n-1
        verts.append((float(i), 0.0, 0.0))
    for i in range(n):  # rowB (top): 2n..3n-1
        verts.append((float(i), 0.0, 1.0))
    faces = []
    for q in range(cols):  # side A
        a0, a1 = q, q + 1
        r0, r1 = n + q, n + q + 1
        faces.append((a0, a1, r1))
        faces.append((a0, r1, r0))
    for q in range(cols):  # side B
        r0, r1 = n + q, n + q + 1
        b0, b1 = 2 * n + q, 2 * n + q + 1
        faces.append((r0, r1, b1))
        faces.append((r0, b1, b0))
    mesh = trimesh.Trimesh(vertices=np.array(verts), faces=np.array(faces), process=False)
    mesh.fix_normals()
    return mesh


class TestEdgeCosts:
    def test_ridge_edges_cheaper_than_flat(self):
        mesh = _tent_mesh()
        cost = edge_costs(mesh, smooth_angle_deg=25.0)
        angles = np.asarray(mesh.face_adjacency_angles)
        sharp = angles > np.radians(60.0)
        assert sharp.any() and (~sharp).any()
        assert cost[sharp].max() < cost[~sharp].min()


class TestRefineFaceLabels:
    def test_speckle_island_absorbed(self):
        mesh = _plane_mesh(6, 6)
        labels = np.zeros(len(mesh.faces), dtype=np.int64)
        labels[30] = 1  # one-face island in a flat sea of label 0
        out = refine_face_labels(mesh, labels)
        assert (out == 0).all()

    def test_icm_snaps_boundary_to_ridge(self):
        mesh = _tent_mesh(cols=4)
        n_side = len(mesh.faces) // 2
        labels = np.zeros(len(mesh.faces), dtype=np.int64)
        labels[n_side:] = 1
        # Mislabel one side-B triangle that touches the ridge: its same-label
        # region connects to side A across the ridge, so only ICM can fix it.
        mislabeled = n_side + 2
        labels[mislabeled] = 0
        out = refine_face_labels(mesh, labels, island_min_faces=2)
        expected = np.zeros(len(mesh.faces), dtype=np.int64)
        expected[n_side:] = 1
        assert (out == expected).all()

    def test_correct_labels_untouched(self):
        mesh = _tent_mesh(cols=4)
        n_side = len(mesh.faces) // 2
        labels = np.zeros(len(mesh.faces), dtype=np.int64)
        labels[n_side:] = 1
        out = refine_face_labels(mesh, labels, island_min_faces=2)
        assert (out == labels).all()

    def test_length_mismatch_returns_input(self):
        mesh = _plane_mesh(2, 2)
        labels = np.zeros(3, dtype=np.int64)
        out = refine_face_labels(mesh, labels)
        assert (out == labels).all()


class TestIcmBoundarySnap:
    def test_never_flips_to_negative_label(self):
        mesh = _plane_mesh(4, 4)
        labels = np.zeros(len(mesh.faces), dtype=np.int64)
        labels[: len(labels) // 2] = -1
        cost = edge_costs(mesh)
        out = icm_boundary_snap(labels, np.asarray(mesh.face_adjacency, dtype=np.int64), cost)
        # Faces may flip out of -1, never into it beyond the originals.
        assert (out[labels >= 0] >= 0).all()


class TestAabbsFromFaceIds:
    def test_shapes_and_bounds(self):
        mesh = _tent_mesh(cols=4)
        n_side = len(mesh.faces) // 2
        labels = np.zeros(len(mesh.faces), dtype=np.int64)
        labels[n_side:] = 1
        aabb = aabbs_from_face_ids(mesh, labels)
        assert aabb.shape == (2, 2, 3)
        # Side A: y in [-1, 0], z == 0
        np.testing.assert_allclose(aabb[0][0][1], -1.0)
        np.testing.assert_allclose(aabb[0][1][2], 0.0)
        # Side B: z in [0, 1]
        np.testing.assert_allclose(aabb[1][1][2], 1.0)

    def test_negative_labels_skipped(self):
        mesh = _plane_mesh(2, 2)
        labels = -np.ones(len(mesh.faces), dtype=np.int64)
        assert aabbs_from_face_ids(mesh, labels).size == 0
