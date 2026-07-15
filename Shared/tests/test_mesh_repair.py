"""Tests for gamedev_shared.mesh_repair (unified bpy mesh-repair primitives)."""

from __future__ import annotations

import numpy as np
import pytest


class TestDropNonfiniteFaces:
    """Pure-numpy — runs without bpy."""

    def test_all_finite_passthrough(self) -> None:
        from gamedev_shared.mesh_repair import drop_nonfinite_faces

        verts = np.zeros((4, 3))
        faces = np.array([[0, 1, 2], [1, 2, 3]])
        _v, f, n = drop_nonfinite_faces(verts, faces)
        assert n == 0
        assert f.shape == (2, 3)

    def test_nan_vertex_drops_incident_faces(self) -> None:
        from gamedev_shared.mesh_repair import drop_nonfinite_faces

        verts = np.zeros((4, 3))
        verts[3, 1] = np.nan
        faces = np.array([[0, 1, 2], [1, 2, 3]])
        _v, f, n = drop_nonfinite_faces(verts, faces)
        assert n == 1
        np.testing.assert_array_equal(f, [[0, 1, 2]])

    def test_inf_vertex_drops_incident_faces(self) -> None:
        from gamedev_shared.mesh_repair import drop_nonfinite_faces

        verts = np.zeros((3, 3))
        verts[0, 0] = np.inf
        faces = np.array([[0, 1, 2]])
        _v, f, n = drop_nonfinite_faces(verts, faces)
        assert n == 1
        assert f.size == 0


@pytest.fixture(scope="module")
def _bpy():
    return pytest.importorskip("bpy")


class TestFixMesh:
    def test_removes_tiny_floater(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import fix_mesh

        main = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide().subdivide()
        floater = trimesh.creation.box(extents=[0.01, 0.01, 0.01])
        floater.apply_translation([5.0, 5.0, 5.0])
        combined = trimesh.util.concatenate([main, floater])

        out = fix_mesh(combined)
        assert 0 < len(out.faces) < len(combined.faces)
        assert len(out.faces) >= len(main.faces) // 2

    def test_preserves_simple_box(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import fix_mesh

        out = fix_mesh(trimesh.creation.box(extents=[1.0, 1.0, 1.0]))
        assert len(out.faces) >= 8
        assert len(out.vertices) >= 8

    def test_empty_passthrough(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import fix_mesh

        empty = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64), process=False)
        assert len(fix_mesh(empty).vertices) == 0

    def test_nan_fan_removed(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import fix_mesh

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()
        verts = np.vstack([box.vertices, [[np.nan, np.nan, np.nan]]])
        nan_idx = len(verts) - 1
        fan = np.array([[0, 1, nan_idx], [1, 2, nan_idx], [2, 3, nan_idx]])
        faces = np.vstack([box.faces, fan])
        broken = trimesh.Trimesh(vertices=verts, faces=faces, process=False)

        out = fix_mesh(broken)
        assert np.isfinite(np.asarray(out.vertices)).all()
        assert len(out.faces) >= len(box.faces) // 2


class TestCapBoundaryHoles:
    @staticmethod
    def _open_edge_count(mesh) -> int:
        edges = np.sort(mesh.faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2), axis=1)
        _uniq, counts = np.unique(edges, axis=0, return_counts=True)
        return int((counts == 1).sum())

    def test_caps_small_planar_loop(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import cap_boundary_holes

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()
        # Remove top half → open shell with a planar square boundary loop.
        keep = box.triangles_center[:, 2] < 0
        shell = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[keep], process=False)
        shell.remove_unreferenced_vertices()

        capped = cap_boundary_holes(shell)
        assert self._open_edge_count(capped) < self._open_edge_count(shell)

    def test_giant_loop_skipped(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.mesh_repair import cap_boundary_holes

        box = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()
        keep = box.triangles_center[:, 2] < 0
        shell = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[keep], process=False)
        shell.remove_unreferenced_vertices()

        # max_loop_edges menor que o loop → nada tapado.
        capped = cap_boundary_holes(shell, max_loop_edges=3)
        assert self._open_edge_count(capped) == self._open_edge_count(shell)


class TestPrimitives:
    def test_remove_doubles_counts(self, _bpy) -> None:
        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import remove_doubles

        verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0.00001]])
        faces = np.array([[0, 1, 2], [3, 1, 2]])
        clear_scene()
        obj = create_mesh_from_arrays(verts.astype(np.float64), faces.astype(np.int64), name="weld_test")
        removed = remove_doubles(obj, threshold=1e-3)
        assert removed == 1
        clear_scene()

    def test_remove_loose_debris_keeps_largest(self, _bpy) -> None:
        import trimesh

        from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays
        from gamedev_shared.mesh_repair import remove_loose_debris

        # main grande o suficiente para tiny (12 faces) cair abaixo de
        # max(min_faces, face_ratio * total): 204 faces → threshold 20.
        main = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide().subdivide()
        tiny = trimesh.creation.box(extents=[0.01, 0.01, 0.01])
        tiny.apply_translation([4.0, 0.0, 0.0])
        combined = trimesh.util.concatenate([main, tiny])
        clear_scene()
        obj = create_mesh_from_arrays(
            np.asarray(combined.vertices), np.asarray(combined.faces, dtype=np.int64), name="debris_test"
        )
        removed = remove_loose_debris(obj, face_ratio=0.1, min_faces=8)
        assert removed == len(tiny.faces)
        clear_scene()
