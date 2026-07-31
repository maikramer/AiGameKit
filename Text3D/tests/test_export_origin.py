"""Testes para convenção de origem na exportação (feet / center / none).

Convenção de eixos (Blender Z-up; glTF Y-up via ``export_yup=True``):
- glTF Y (altura)  = Blender Z
- glTF Z (profund) = -Blender Y
- glTF X           = Blender X

Logo o "feet" (base no solo) alinha o **Z Blender** (= Y glTF) a 0, e centra
X e Y Blender (= X e Z glTF).
"""

from __future__ import annotations

import pytest

pytest.importorskip("bpy")

import bpy
import numpy as np

from aigamekit_shared.bpy_mesh import clear_scene, get_bounds
from text3d.utils.export import _apply_origin_trimesh


def _shifted_unit_cube():
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object
    # Deslocar em Z (altura Blender) para que o "feet" tenha efeito visível.
    obj.location.z += 1.0
    return obj


class TestApplyOriginTrimesh:
    def test_feet_origin_sets_base_at_z_zero_blender(self) -> None:
        # glTF Y=0 ↔ Blender Z=0: a base do mesh fica no solo.
        obj = _shifted_unit_cube()
        _apply_origin_trimesh(obj, "feet")
        b = get_bounds(obj)
        assert np.isclose(b[0][2], 0.0, atol=1e-3)

    def test_feet_origin_centers_xy_blender(self) -> None:
        # Centrar em X e Y Blender (= X e Z glTF).
        obj = _shifted_unit_cube()
        _apply_origin_trimesh(obj, "feet")
        b = get_bounds(obj)
        cx = (b[0][0] + b[1][0]) * 0.5
        cy = (b[0][1] + b[1][1]) * 0.5
        assert np.isclose(cx, 0.0, atol=1e-3)
        assert np.isclose(cy, 0.0, atol=1e-3)

    def test_center_origin_centers_all_axes(self) -> None:
        obj = _shifted_unit_cube()
        _apply_origin_trimesh(obj, "center")
        b = get_bounds(obj)
        center = ((b[0][0] + b[1][0]) * 0.5, (b[0][1] + b[1][1]) * 0.5, (b[0][2] + b[1][2]) * 0.5)
        np.testing.assert_allclose(center, [0.0, 0.0, 0.0], atol=1e-3)

    def test_none_origin_preserves_position(self) -> None:
        obj = _shifted_unit_cube()
        bounds_before = get_bounds(obj)
        out = _apply_origin_trimesh(obj, "none")
        assert out is obj
        bounds_after = get_bounds(obj)
        np.testing.assert_allclose(bounds_after[0], bounds_before[0], atol=1e-3)
        np.testing.assert_allclose(bounds_after[1], bounds_before[1], atol=1e-3)
