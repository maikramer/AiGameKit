"""Tests for gamedev_shared.mesh_utils — normal-preserving weld."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy")

from gamedev_shared.mesh_utils import _weld_distance, normal_preserving_weld, weld_glb  # noqa: E402


def _clean_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=True)
    for mesh in bpy.data.meshes:
        bpy.data.meshes.remove(mesh)
    for armature in bpy.data.armatures:
        bpy.data.armatures.remove(armature)


def _make_cube(name: str = "TestCube") -> object:
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    return obj


def _make_cube_with_doubles(name: str = "DupCube") -> object:
    obj = _make_cube(name)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.duplicate_move(
        MESH_OT_duplicate=None,
        TRANSFORM_OT_translate={"value": (0.0, 0.0, 0.0)},
    )
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def _export_glb(obj: object, path: Path) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=False,
    )


class TestWeldDistance:
    def test_small_mesh_returns_largest_threshold(self) -> None:
        assert _weld_distance(100) == 0.01

    def test_medium_mesh(self) -> None:
        assert _weld_distance(60_000) == 0.008

    def test_large_mesh(self) -> None:
        assert _weld_distance(120_000) == 0.005

    def test_very_large_mesh(self) -> None:
        assert _weld_distance(200_000) == 0.003

    def test_boundary_150k(self) -> None:
        assert _weld_distance(150_001) == 0.003
        assert _weld_distance(150_000) == 0.005

    def test_boundary_100k(self) -> None:
        assert _weld_distance(100_001) == 0.005
        assert _weld_distance(100_000) == 0.008

    def test_boundary_50k(self) -> None:
        assert _weld_distance(50_001) == 0.008
        assert _weld_distance(50_000) == 0.01


class TestNormalPreservingWeldRemovesDuplicates:
    def setup_method(self) -> None:
        _clean_scene()

    def teardown_method(self) -> None:
        _clean_scene()

    def test_removes_duplicate_vertices(self) -> None:
        obj = _make_cube_with_doubles()
        assert len(obj.data.vertices) > 8

        removed = normal_preserving_weld(obj, threshold=0.01)
        assert removed > 0
        assert len(obj.data.vertices) == 8

    def test_no_removal_on_clean_mesh(self) -> None:
        obj = _make_cube()
        verts_before = len(obj.data.vertices)
        removed = normal_preserving_weld(obj, threshold=0.01)
        assert removed == 0
        assert len(obj.data.vertices) == verts_before


class TestNormalPreservingWeldPreservesNormals:
    def setup_method(self) -> None:
        _clean_scene()

    def teardown_method(self) -> None:
        _clean_scene()

    def test_normals_not_all_zeros_after_weld(self) -> None:
        obj = _make_cube_with_doubles()
        normal_preserving_weld(obj, threshold=0.01)

        obj.data.calc_normals_split()
        has_nonzero = False
        for loop in obj.data.loops:
            n = loop.normal
            length = math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z)
            if length > 0.5:
                has_nonzero = True
                break
        assert has_nonzero, "All normals are zero after weld"

    def test_normals_are_unit_length(self) -> None:
        obj = _make_cube_with_doubles()
        normal_preserving_weld(obj, threshold=0.01)

        obj.data.calc_normals_split()
        for loop in obj.data.loops:
            n = loop.normal
            length = math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z)
            if length > 0.0:
                assert abs(length - 1.0) < 0.1


class TestWeldWithoutSharpFlagBreaksNormals:
    """remove_doubles without use_sharp_edge_from_normals averages normals."""

    def setup_method(self) -> None:
        _clean_scene()

    def teardown_method(self) -> None:
        _clean_scene()

    def test_old_pattern_averages_normals(self) -> None:
        obj = _make_cube_with_doubles()

        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        assert len(obj.data.vertices) > 8

        bpy.ops.mesh.remove_doubles(threshold=0.01, use_sharp_edge_from_normals=False)
        bpy.ops.object.mode_set(mode="OBJECT")
        assert len(obj.data.vertices) == 8

        obj.data.calc_normals_split()
        has_averaged_normals = False
        for loop in obj.data.loops:
            n = loop.normal
            length = math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z)
            if length < 0.01:
                continue
            # axis-aligned: one component ≈ 1.0, others ≈ 0.0
            components = sorted([abs(n.x), abs(n.y), abs(n.z)], reverse=True)
            is_axis_aligned = components[0] > 0.95 and components[1] < 0.1
            if not is_axis_aligned:
                has_averaged_normals = True
                break

        assert has_averaged_normals


class TestWeldGlbSkipsArmature:
    def setup_method(self) -> None:
        _clean_scene()

    def teardown_method(self) -> None:
        _clean_scene()

    def test_skips_armature_glb(self) -> None:
        obj = _make_cube("ArmatureCube")

        bpy.ops.object.armature_add(location=(0, 0, 0))
        armature_obj = bpy.context.active_object
        armature_obj.name = "TestArmature"
        assert armature_obj.type == "ARMATURE"

        with tempfile.TemporaryDirectory() as tmpdir:
            glb_path = Path(tmpdir) / "with_armature.glb"
            _export_glb(obj, glb_path)
            assert glb_path.is_file()

            size_before = glb_path.stat().st_size
            weld_glb(glb_path)
            size_after = glb_path.stat().st_size
            assert size_after == size_before

    def test_welds_mesh_only_glb(self) -> None:
        obj = _make_cube_with_doubles("WeldTestCube")
        assert len(obj.data.vertices) > 8

        with tempfile.TemporaryDirectory() as tmpdir:
            glb_path = Path(tmpdir) / "no_armature.glb"
            _export_glb(obj, glb_path)
            assert glb_path.is_file()

            weld_glb(glb_path)
            assert glb_path.is_file()
            assert glb_path.stat().st_size > 0

    def test_weld_glb_handles_missing_file(self) -> None:
        weld_glb("/nonexistent/path/test.glb")
