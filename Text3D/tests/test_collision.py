"""Testes para geração de mesh de colisão."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("bpy")

import bpy

from gamedev_shared.bpy_mesh import clear_scene, face_count, load_glb, save_glb
from text3d.utils.collision import generate_collision_mesh


def _save_box_glb(path: Path, extents: tuple[float, ...] = (1.0, 2.0, 3.0)) -> Path:
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object
    obj.scale = (extents[0] / 2, extents[1] / 2, extents[2] / 2)
    bpy.ops.object.transform_apply(scale=True)
    save_glb([obj], path)
    return path


def _save_sphere_glb(path: Path, subdivisions: int = 2) -> Path:
    clear_scene()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions)
    save_glb([bpy.context.active_object], path)
    return path


def _load_face_count(path: Path) -> int:
    objs = load_glb(path)
    return sum(face_count(o) for o in objs)


def test_collision_from_box(tmp_path: Path) -> None:
    inp = _save_box_glb(tmp_path / "box.glb")
    out = tmp_path / "collision.glb"
    result = generate_collision_mesh(inp, out, max_faces=50, convex_hull=True)
    assert result.is_file()
    n = _load_face_count(result)
    assert n >= 4
    assert n <= 100


def test_collision_no_convex_hull(tmp_path: Path) -> None:
    inp = _save_sphere_glb(tmp_path / "sphere.glb")
    out = tmp_path / "collision.glb"
    result = generate_collision_mesh(inp, out, max_faces=50, convex_hull=False)
    assert result.is_file()
    assert _load_face_count(result) >= 4


def test_collision_creates_parent_dir(tmp_path: Path) -> None:
    inp = _save_box_glb(tmp_path / "box.glb", extents=(1.0, 1.0, 1.0))
    out = tmp_path / "subdir" / "deep" / "collision.glb"
    result = generate_collision_mesh(inp, out, max_faces=50)
    assert result.is_file()
