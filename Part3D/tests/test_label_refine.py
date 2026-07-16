from __future__ import annotations

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")

from part3d.utils.label_refine import (  # noqa: E402
    aabbs_from_face_ids,
    edge_costs,
    icm_boundary_snap,
    refine_face_labels,
    relabel_connected_components,
)

def _plane_grid(size: int = 20) -> trimesh.Trimesh:
    vertices = np.array([[x, y, 0.0] for y in range(size + 1) for x in range(size + 1)])
    faces: list[list[int]] = []
    for y in range(size):
        for x in range(size):
            a = y * (size + 1) + x
            b = a + 1
            c = a + size + 1
            d = c + 1
            faces.extend(([a, b, d], [a, d, c]))
    return trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces), process=False)


def test_anchored_refine_preserves_door_core() -> None:
    mesh = _plane_grid()
    centers = mesh.triangles_center
    labels = np.zeros(len(mesh.faces), dtype=np.int64)
    door = (centers[:, 0] > 7) & (centers[:, 0] < 13) & (centers[:, 1] > 2) & (centers[:, 1] < 11)
    core = (centers[:, 0] > 8) & (centers[:, 0] < 12) & (centers[:, 1] > 3) & (centers[:, 1] < 10)
    labels[door] = 1

    refined = refine_face_labels(mesh, labels, data_weight=0.35, boundary_hops=2)

    assert np.all(refined[core] == 1)
    assert np.count_nonzero(refined == 1) >= int(np.count_nonzero(door) * 0.9)
    assert np.count_nonzero(refined != labels) < int(len(labels) * 0.05)


def test_disconnected_regions_get_distinct_labels_and_debris_is_ignored() -> None:
    labels = np.array([0, 0, 1, 1, 0], dtype=np.int64)
    adjacency = np.array([[0, 1], [1, 2], [2, 3]], dtype=np.int64)
    areas = np.ones(labels.shape[0], dtype=np.float64)

    refined = relabel_connected_components(
        labels,
        adjacency,
        areas,
        min_faces=2,
        min_area_frac=0.0,
    )

    assert refined[0] == refined[1] == 0
    assert refined[2] == refined[3] == 1
    assert refined[4] == -1


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


def test_expand_aabbs_pads_each_axis() -> None:
    from part3d.utils.label_refine import expand_aabbs

    aabb = np.array([[[0.0, 0.0, 0.0], [2.0, 4.0, 6.0]]], dtype=np.float64)
    out = expand_aabbs(aabb, margin_frac=0.1)
    # half extents = (1,2,3); pad = 0.1 * half
    np.testing.assert_allclose(out[0, 0], [-0.1, -0.2, -0.3])
    np.testing.assert_allclose(out[0, 1], [2.1, 4.2, 6.3])
    assert expand_aabbs(aabb, margin_frac=0.0) is aabb or np.allclose(
        expand_aabbs(aabb, margin_frac=0.0), aabb
    )
