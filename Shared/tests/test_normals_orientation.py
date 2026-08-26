"""Guarda de orientação: winding para fora depois de normals_make_consistent.

O heurístico "recalculate outside" do Blender decide o exterior por ray casting
e, em malhas marching-cubes com muitas ilhas e cascas interiores, chega a
inverter a malha inteira. Isso colapsa o bake do Paint3D (o ``back_project``
zera o ``cos_map`` das faces viradas ao contrário).
"""

from __future__ import annotations

import pytest

bpy = pytest.importorskip("bpy")

from aigamekit_shared.bpy_mesh import clear_scene, flip_normals, signed_volume  # noqa: E402
from aigamekit_shared.mesh_repair import normals_consistent  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_scene():
    clear_scene()
    yield
    clear_scene()


def _cube(size: float = 2.0):
    bpy.ops.mesh.primitive_cube_add(size=size, location=(0, 0, 0))
    return bpy.context.active_object


def _plane():
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=(0, 0, 0))
    return bpy.context.active_object


class TestSignedVolume:
    def test_outward_cube_is_positive(self) -> None:
        obj = _cube()
        assert signed_volume(obj.data) == pytest.approx(8.0, rel=1e-3)

    def test_flipping_negates_it(self) -> None:
        obj = _cube()
        flip_normals(obj)
        assert signed_volume(obj.data) == pytest.approx(-8.0, rel=1e-3)

    def test_flat_mesh_is_zero(self) -> None:
        assert signed_volume(_plane().data) == pytest.approx(0.0, abs=1e-6)

    def test_empty_mesh_is_zero(self) -> None:
        mesh = bpy.data.meshes.new("empty")
        assert signed_volume(mesh) == 0.0


class TestNormalsConsistentEnforcesOutward:
    def test_inverted_cube_is_restored(self) -> None:
        obj = _cube()
        flip_normals(obj)
        assert signed_volume(obj.data) < 0
        normals_consistent(obj)
        assert signed_volume(obj.data) > 0

    def test_already_outward_is_left_alone(self) -> None:
        obj = _cube()
        before = signed_volume(obj.data)
        normals_consistent(obj)
        assert signed_volume(obj.data) == pytest.approx(before, rel=1e-3)

    def test_enforce_outward_can_be_disabled(self) -> None:
        """Sem a guarda, o resultado é o que o Blender decidir — só não deve rebentar."""
        obj = _cube()
        flip_normals(obj)
        normals_consistent(obj, enforce_outward=False)
        assert len(obj.data.polygons) == 6

    def test_inside_mode_skips_the_guard(self) -> None:
        obj = _cube()
        normals_consistent(obj, inside=True)
        assert signed_volume(obj.data) < 0

    def test_flat_mesh_is_not_flipped_by_noise(self) -> None:
        """Volume ~0 não tem sinal fiável — a guarda tem de ficar quieta."""
        obj = _plane()
        normals_consistent(obj)
        assert len(obj.data.polygons) == 1
