"""Geometria pura: enquadramento, alinhamento, decimação e limpeza de lixo."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import numpy as np
import pytest
import trimesh
from hi3d.align import (
    Frame,
    decimate,
    denormalize_vertices,
    export_glb,
    face_count,
    frame_of,
    load_mesh,
    mesh_vertices,
    normalize_vertices,
    refine_output_size_note,
    restore_frame,
    snap_to_bounds,
    strip_debris,
)


def _cube(size: float = 2.0, center: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=(size, size, size))
    mesh.apply_translation(center)
    return mesh


def _cube_at_origin(offset: tuple[float, float, float], size: float = 1.0) -> trimesh.Trimesh:
    mesh = trimesh.creation.box(extents=(size, size, size))
    mesh.apply_translation(offset)
    return mesh


class TestFrameOf:
    def test_center_is_vertex_mean(self) -> None:
        verts = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 4.0, 0.0]])
        frame = frame_of(verts)
        assert np.allclose(frame.center, [2.0 / 3.0, 4.0 / 3.0, 0.0])

    def test_radius_is_max_norm_from_mean(self) -> None:
        verts = np.array([[-1.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
        frame = frame_of(verts)
        assert frame.radius == pytest.approx(1.0)

    def test_empty_mesh_raises(self) -> None:
        with pytest.raises(ValueError):
            frame_of(np.empty((0, 3)))

    def test_degenerate_point_raises(self) -> None:
        with pytest.raises(ValueError):
            frame_of(np.zeros((4, 3)))

    def test_frame_is_frozen(self) -> None:
        frame = Frame(center=np.zeros(3), radius=1.0)
        with pytest.raises(FrozenInstanceError):
            frame.radius = 2.0  # type: ignore[misc]

    @pytest.mark.parametrize("size", [0.01, 1.0, 250.0])
    def test_radius_scales_with_extent(self, size: float) -> None:
        # Duas pontas: a média fica no meio, logo o raio é metade da extensão.
        verts = np.array([[0.0, 0.0, 0.0], [size, 0.0, 0.0]])
        assert frame_of(verts).radius == pytest.approx(size / 2.0)


class TestNormalization:
    def test_roundtrip_is_identity(self) -> None:
        verts = np.array([[1.0, 2.0, 3.0], [-4.0, 5.0, -6.0], [7.0, -8.0, 9.0]])
        frame = frame_of(verts)
        back = denormalize_vertices(normalize_vertices(verts, frame), frame)
        assert np.allclose(back, verts)

    def test_normalized_radius_is_one(self) -> None:
        verts = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [0.0, 4.0, 0.0]])
        frame = frame_of(verts)
        norm = normalize_vertices(verts, frame)
        assert np.linalg.norm(norm, axis=1).max() == pytest.approx(1.0)

    def test_restore_frame_recovers_world_scale(self) -> None:
        coarse = _cube(size=2.0, center=(10.0, 1.0, -3.0))
        # O upstream devolve a malha já normalizada (raio máximo 1).
        unit = _cube(size=1.0)
        unit_frame = frame_of(mesh_vertices(unit))
        normalized = trimesh.Trimesh(
            vertices=normalize_vertices(mesh_vertices(unit), unit_frame),
            faces=unit.faces.copy(),
            process=False,
        )
        restored = restore_frame(normalized, frame_of(mesh_vertices(coarse)))
        assert np.allclose(restored.bounds, coarse.bounds, atol=1e-6)

    def test_restore_frame_does_not_mutate_input(self) -> None:
        mesh = _cube(size=1.0)
        before = mesh_vertices(mesh).copy()
        restore_frame(mesh, Frame(center=np.zeros(3), radius=5.0))
        assert np.allclose(mesh_vertices(mesh), before)

    def test_mesh_vertices_is_a_copy(self) -> None:
        mesh = _cube()
        verts = mesh_vertices(mesh)
        verts[0] = [99.0, 99.0, 99.0]
        assert not np.allclose(mesh_vertices(mesh)[0], [99.0, 99.0, 99.0])

    @pytest.mark.parametrize("scale", [0.5, 1.0, 7.25])
    def test_restore_frame_scale(self, scale: float) -> None:
        coarse = _cube(size=scale)
        unit = _cube(size=1.0)
        unit_frame = frame_of(mesh_vertices(unit))
        normalized = trimesh.Trimesh(
            vertices=normalize_vertices(mesh_vertices(unit), unit_frame),
            faces=unit.faces.copy(),
            process=False,
        )
        restored = restore_frame(normalized, frame_of(mesh_vertices(coarse)))
        assert float(restored.extents[0]) == pytest.approx(scale, rel=1e-5)


class TestLoadExport:
    def test_roundtrip_glb(self, tmp_path) -> None:
        src = _cube(size=3.0)
        path = export_glb(src, tmp_path / "cube.glb")
        loaded = load_mesh(path)
        assert face_count(loaded) == face_count(src)

    def test_export_creates_parent_dirs(self, tmp_path) -> None:
        out = export_glb(_cube(), tmp_path / "a" / "b" / "c.glb")
        assert out.is_file()

    def test_load_missing_file_raises(self, tmp_path) -> None:
        with pytest.raises((ValueError, FileNotFoundError)):
            load_mesh(tmp_path / "missing.glb")

    def test_load_empty_scene_raises(self, tmp_path) -> None:
        empty = trimesh.Scene()
        path = tmp_path / "empty.glb"
        with pytest.raises((ValueError, TypeError)):
            # O exporter já recusa uma cena vazia; se não recusar, o loader tem de recusar.
            empty.export(str(path), file_type="glb")
            load_mesh(path)

    def test_face_count_of_box(self) -> None:
        assert face_count(_cube()) == 12

    def test_face_count_invalid_object(self) -> None:
        assert face_count(object()) == 0  # type: ignore[arg-type]


class TestDecimate:
    def test_noop_when_target_zero(self) -> None:
        mesh = _cube()
        assert face_count(decimate(mesh, 0)) == face_count(mesh)

    def test_noop_when_already_below_target(self) -> None:
        mesh = _cube()
        assert face_count(decimate(mesh, 1000)) == face_count(mesh)

    def test_reduces_face_count(self) -> None:
        mesh = trimesh.creation.icosphere(subdivisions=4)
        out = decimate(mesh, 200)
        assert 0 < face_count(out) <= 400

    def test_never_returns_empty(self) -> None:
        mesh = trimesh.creation.icosphere(subdivisions=3)
        assert face_count(decimate(mesh, 4)) > 0

    @pytest.mark.parametrize("target", [20, 100, 500])
    def test_targets_are_respected(self, target: int) -> None:
        mesh = trimesh.creation.icosphere(subdivisions=4)
        assert face_count(decimate(mesh, target)) <= target * 2


class TestStripDebris:
    def test_single_component_is_untouched(self) -> None:
        mesh = _cube()
        assert strip_debris(mesh) is mesh

    def test_removes_tiny_islands(self) -> None:
        main = trimesh.creation.icosphere(subdivisions=4)
        parts = [main] + [_cube_at_origin((20.0 + i, 0.0, 0.0), size=0.001) for i in range(4)]
        merged = trimesh.util.concatenate(parts)
        cleaned = strip_debris(merged)
        assert len(cleaned.split(only_watertight=False)) == 1
        assert face_count(cleaned) == face_count(main)

    def test_keeps_secondary_part_above_ratio(self) -> None:
        a = trimesh.creation.icosphere(subdivisions=3)
        b = trimesh.creation.icosphere(subdivisions=3)
        b.apply_translation((10.0, 0.0, 0.0))
        merged = trimesh.util.concatenate([a, b])
        assert len(strip_debris(merged).split(only_watertight=False)) == 2

    def test_empty_mesh_is_returned(self) -> None:
        empty = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=int))
        assert strip_debris(empty) is empty

    def test_custom_min_faces_threshold(self) -> None:
        main = trimesh.creation.icosphere(subdivisions=2)
        small = _cube_at_origin((10.0, 0.0, 0.0), size=0.5)
        merged = trimesh.util.concatenate([main, small])
        assert len(strip_debris(merged, ratio=0.0, min_faces=1).split(only_watertight=False)) == 2
        assert len(strip_debris(merged, ratio=0.0, min_faces=10_000).split(only_watertight=False)) == 1


class TestSizeNote:
    def test_unknown_faces(self) -> None:
        assert refine_output_size_note(0, 10) == "faces: ?"

    def test_no_reduction(self) -> None:
        assert refine_output_size_note(100, 0) == "faces: 100"
        assert refine_output_size_note(100, 200) == "faces: 100"

    def test_reduction_percentage(self) -> None:
        assert "-75%" in refine_output_size_note(1000, 250)


class TestSnapToBounds:
    def test_feet_land_on_reference_floor(self) -> None:
        ref = trimesh.creation.box(extents=(0.5, 1.6, 0.3))
        ref.apply_translation((0.0, 0.8, 0.0))  # pés em y=0
        drifted = ref.copy()
        drifted.apply_scale(0.97)
        drifted.apply_translation((0.02, -0.05, 0.01))
        out = snap_to_bounds(drifted, ref)
        np.testing.assert_allclose(out.bounds, ref.bounds, atol=1e-9)

    def test_does_not_mutate_input(self) -> None:
        ref = trimesh.creation.box(extents=(1.0, 2.0, 1.0))
        mesh = trimesh.creation.box(extents=(0.5, 1.0, 0.5))
        before = mesh.vertices.copy()
        snap_to_bounds(mesh, ref)
        np.testing.assert_array_equal(mesh.vertices, before)
