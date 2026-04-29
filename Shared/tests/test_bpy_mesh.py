"""Tests for gamedev_shared.bpy_mesh — shared bpy mesh utilities."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy")

from gamedev_shared.bpy_mesh import (  # noqa: E402
    clear_scene,
    face_count,
    get_bounds,
    get_mesh_objects,
    load_any,
    load_glb,
    save_glb,
    vertex_count,
)


def _make_cube(name: str = "TestCube") -> object:
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    return obj


class TestPublicFunctionsExist:
    def test_all_functions_importable(self) -> None:
        assert callable(load_glb)
        assert callable(save_glb)
        assert callable(get_mesh_objects)
        assert callable(get_bounds)
        assert callable(face_count)
        assert callable(vertex_count)
        assert callable(clear_scene)
        assert callable(load_any)


class TestClearScene:
    def setup_method(self) -> None:
        clear_scene()

    def teardown_method(self) -> None:
        clear_scene()

    def test_clears_all_objects(self) -> None:
        _make_cube("A")
        _make_cube("B")
        assert len(bpy.context.scene.objects) == 2
        clear_scene()
        assert len(bpy.context.scene.objects) == 0

    def test_clears_mesh_data_blocks(self) -> None:
        _make_cube("Cube")
        assert len(bpy.data.meshes) > 0
        clear_scene()
        assert len(bpy.data.meshes) == 0


class TestGetMeshObjects:
    def setup_method(self) -> None:
        clear_scene()

    def teardown_method(self) -> None:
        clear_scene()

    def test_returns_only_meshes(self) -> None:
        _make_cube("Mesh1")
        bpy.ops.object.light_add(type="POINT", location=(0, 0, 0))
        meshes = get_mesh_objects()
        assert len(meshes) == 1
        assert meshes[0].type == "MESH"

    def test_empty_scene(self) -> None:
        assert get_mesh_objects() == []


class TestFaceAndVertexCount:
    def setup_method(self) -> None:
        clear_scene()

    def teardown_method(self) -> None:
        clear_scene()

    def test_cube_counts(self) -> None:
        obj = _make_cube()
        assert face_count(obj) == 6
        assert vertex_count(obj) == 8


class TestGetBounds:
    def setup_method(self) -> None:
        clear_scene()

    def teardown_method(self) -> None:
        clear_scene()

    def test_unit_cube_at_origin(self) -> None:
        obj = _make_cube()
        mn, mx = get_bounds(obj)
        for i in range(3):
            assert mn[i] == pytest.approx(-1.0, abs=0.01)
            assert mx[i] == pytest.approx(1.0, abs=0.01)

    def test_translated_cube(self) -> None:
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(5, 0, 0))
        obj = bpy.context.active_object
        mn, mx = get_bounds(obj)
        assert mn[0] == pytest.approx(4.5, abs=0.01)
        assert mx[0] == pytest.approx(5.5, abs=0.01)


class TestRoundtrip:
    def setup_method(self) -> None:
        clear_scene()

    def teardown_method(self) -> None:
        clear_scene()

    def test_load_save_roundtrip(self) -> None:
        obj = _make_cube("ExportCube")
        with tempfile.TemporaryDirectory() as tmpdir:
            glb_path = Path(tmpdir) / "test.glb"
            save_glb(obj, glb_path)
            assert glb_path.is_file()
            assert glb_path.stat().st_size > 0

            loaded = load_glb(glb_path)
            assert len(loaded) >= 1
            assert face_count(loaded[0]) == 6

    def test_save_entire_scene(self) -> None:
        _make_cube("A")
        _make_cube("B")
        with tempfile.TemporaryDirectory() as tmpdir:
            glb_path = Path(tmpdir) / "scene.glb"
            save_glb(None, glb_path)
            assert glb_path.is_file()

            loaded = load_glb(glb_path)
            assert len(loaded) >= 2
