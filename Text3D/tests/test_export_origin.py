"""Testes para convenção de origem na exportação (feet / center / none)."""

from __future__ import annotations

import pytest

pytest.importorskip("bpy")

import bpy
import numpy as np

from gamedev_shared.bpy_mesh import clear_scene, get_bounds
from text3d.utils.export import _apply_origin_trimesh


def _shifted_unit_cube():
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object
    obj.location.y += 1.0
    return obj


class TestApplyOriginTrimesh:
    def test_feet_origin_sets_base_at_y_zero(self) -> None:
        obj = _shifted_unit_cube()
        _apply_origin_trimesh(obj, "feet")
        b = get_bounds(obj)
        assert np.isclose(b[0][1], 0.0, atol=1e-3)

    def test_feet_origin_centers_xz(self) -> None:
        obj = _shifted_unit_cube()
        _apply_origin_trimesh(obj, "feet")
        b = get_bounds(obj)
        cx = (b[0][0] + b[1][0]) * 0.5
        cz = (b[0][2] + b[1][2]) * 0.5
        assert np.isclose(cx, 0.0, atol=1e-3)
        assert np.isclose(cz, 0.0, atol=1e-3)

    def test_center_origin_centers_all_axes(self) -> None:
        obj = _shifted_unit_cube()
        _apply_origin_trimesh(obj, "center")
        b = get_bounds(obj)
        center = (b[0] + b[1]) * 0.5
        np.testing.assert_allclose(center, [0.0, 0.0, 0.0], atol=1e-3)

    def test_none_origin_preserves_position(self) -> None:
        obj = _shifted_unit_cube()
        bounds_before = get_bounds(obj)
        out = _apply_origin_trimesh(obj, "none")
        assert out is obj
        bounds_after = get_bounds(obj)
        np.testing.assert_allclose(bounds_after[0], bounds_before[0], atol=1e-3)
        np.testing.assert_allclose(bounds_after[1], bounds_before[1], atol=1e-3)
