"""Tests for geometry-first segmentation."""

from __future__ import annotations

import numpy as np
import trimesh
from part3d.utils.geometry_segment import (
    GeometrySegmentParams,
    segment_mesh_geometry,
    snap_semantic_labels_to_geometry,
    weld_mesh_topology,
)


def _curved_strip(segments: int = 6) -> trimesh.Trimesh:
    """Quarter-cylinder strip with smooth local edges and 90° total curvature."""
    angles = np.linspace(0.0, np.pi / 2.0, segments + 1)
    vertices = []
    for x in (0.0, 1.0):
        vertices.extend((x, float(np.cos(angle)), float(np.sin(angle))) for angle in angles)
    faces = []
    row = segments + 1
    for i in range(segments):
        a, b = i, i + 1
        c, d = row + i, row + i + 1
        faces.extend(((a, c, d), (a, d, b)))
    return trimesh.Trimesh(vertices=np.asarray(vertices), faces=np.asarray(faces), process=False)


def _tent_mesh(cols: int = 4) -> trimesh.Trimesh:
    """Two strips meeting at a 90° crease."""
    n = cols + 1
    vertices = [(float(i), -1.0, 0.0) for i in range(n)]
    vertices += [(float(i), 0.0, 0.0) for i in range(n)]
    vertices += [(float(i), 0.0, 1.0) for i in range(n)]
    side_a = []
    side_b = []
    for i in range(cols):
        a0, a1 = i, i + 1
        r0, r1 = n + i, n + i + 1
        b0, b1 = 2 * n + i, 2 * n + i + 1
        side_a.extend(((a0, a1, r1), (a0, r1, r0)))
        side_b.extend(((r0, r1, b1), (r0, b1, b0)))
    faces = side_a + side_b
    mesh = trimesh.Trimesh(vertices=np.asarray(vertices), faces=np.asarray(faces), process=False)
    mesh.fix_normals()
    return mesh


def _plane_grid(size: int = 8) -> trimesh.Trimesh:
    vertices = [(float(x), float(y), 0.0) for y in range(size + 1) for x in range(size + 1)]
    faces = []
    for y in range(size):
        for x in range(size):
            a = y * (size + 1) + x
            b = a + 1
            c = a + size + 1
            d = c + 1
            faces.extend(((a, b, d), (a, d, c)))
    return trimesh.Trimesh(vertices=np.asarray(vertices), faces=np.asarray(faces), process=False)


class TestWeld:
    def test_weld_creates_adjacency(self) -> None:
        verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float64)
        faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
        soup = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        assert len(soup.face_adjacency) == 0
        welded = weld_mesh_topology(soup, digits=5)
        assert len(welded.face_adjacency) >= 1


class TestSegmentGeometry:
    def test_runs_on_box(self) -> None:
        mesh = trimesh.creation.box(extents=[1.0, 2.0, 1.5])
        welded, labels = segment_mesh_geometry(
            mesh,
            GeometrySegmentParams(min_part_area_frac=0.0, min_part_faces=1),
        )
        assert labels.shape[0] == len(welded.faces)
        assert (labels >= 0).all()
        assert len(np.unique(labels)) == 6

    def test_result_is_rotation_invariant(self) -> None:
        mesh = trimesh.creation.box(extents=[1.0, 2.0, 1.5])
        params = GeometrySegmentParams(min_part_area_frac=0.0, min_part_faces=1)
        _welded, labels = segment_mesh_geometry(mesh, params)
        rotated = mesh.copy()
        rotated.apply_transform(trimesh.transformations.rotation_matrix(0.73, [0.3, 0.7, 0.2]))
        _welded_rotated, rotated_labels = segment_mesh_geometry(rotated, params)
        assert np.array_equal(labels[:, None] == labels[None, :], rotated_labels[:, None] == rotated_labels[None, :])

    def test_region_normal_stops_bevel_chaining(self) -> None:
        mesh = _curved_strip()
        _welded, labels = segment_mesh_geometry(
            mesh,
            GeometrySegmentParams(
                crease_angle_deg=32.0,
                region_normal_angle_deg=35.0,
                min_part_area_frac=0.0,
                min_part_faces=1,
            ),
        )
        assert len(np.unique(labels)) >= 2

    def test_disconnected_objects_never_share_a_label(self) -> None:
        left = trimesh.creation.box()
        right = trimesh.creation.box()
        right.apply_translation([3.0, 0.0, 0.0])
        mesh = trimesh.util.concatenate((left, right))
        welded, labels = segment_mesh_geometry(
            mesh,
            GeometrySegmentParams(min_part_area_frac=0.0, min_part_faces=1),
        )
        centers = welded.triangles_center
        left_labels = set(labels[centers[:, 0] < 1.0].tolist())
        right_labels = set(labels[centers[:, 0] > 1.0].tolist())
        assert left_labels.isdisjoint(right_labels)

    def test_microscopic_detached_debris_is_removed(self) -> None:
        box = trimesh.creation.box()
        debris = trimesh.Trimesh(
            vertices=np.array([[4.0, 0.0, 0.0], [4.00001, 0.0, 0.0], [4.0, 0.00001, 0.0]]),
            faces=np.array([[0, 1, 2]]),
            process=False,
        )
        welded, labels = segment_mesh_geometry(trimesh.util.concatenate((box, debris)))
        assert len(welded.faces) == len(box.faces)
        assert labels.shape[0] == len(box.faces)


class TestSnapSemanticLabels:
    def test_snaps_leaked_face_back_across_crease(self) -> None:
        mesh = _tent_mesh()
        labels = np.zeros(len(mesh.faces), dtype=np.int64)
        labels[len(mesh.faces) // 2 :] = 1
        leaked_face = len(mesh.faces) // 2 + 2
        labels[leaked_face] = 0

        snapped, regions = snap_semantic_labels_to_geometry(
            mesh,
            labels,
            GeometrySegmentParams(min_part_area_frac=0.0, min_part_faces=1),
            dominance_threshold=0.75,
            semantic_core_min_area_frac=0.0,
        )

        assert len(np.unique(regions)) == 2
        assert snapped[leaked_face] == 1

    def test_preserves_small_semantic_core_on_smooth_surface(self) -> None:
        mesh = _plane_grid()
        labels = np.zeros(len(mesh.faces), dtype=np.int64)
        labels[:12] = 1

        snapped, regions = snap_semantic_labels_to_geometry(
            mesh,
            labels,
            GeometrySegmentParams(min_part_area_frac=0.0, min_part_faces=1),
            dominance_threshold=0.75,
            semantic_core_min_area_frac=0.0,
        )

        assert len(np.unique(regions)) == 1
        assert np.array_equal(snapped, labels)
