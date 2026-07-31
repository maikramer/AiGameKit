"""Testes para geração de mesh de colisão."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("bpy")

import bpy
from mathutils import Vector

from aigamekit_shared.bpy_mesh import clear_scene, face_count, load_glb, save_glb
from text3d.utils.collision import generate_collision_mesh, resolve_collision_mode


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


def _save_torus_glb(path: Path) -> Path:
    clear_scene()
    bpy.ops.mesh.primitive_torus_add(major_radius=2.0, minor_radius=0.35, major_segments=48, minor_segments=16)
    save_glb([bpy.context.active_object], path)
    return path


def _load_face_count(path: Path) -> int:
    objs = load_glb(path)
    return sum(face_count(o) for o in objs)


def _origin_ray_hits(path: Path) -> bool:
    """True se raio pelo eixo +Z (eixo do furo do torus bpy) atravessa faces."""
    objs = load_glb(path)
    assert objs
    obj = objs[0]
    bpy.context.view_layer.objects.active = obj
    hit, *_ = obj.ray_cast(Vector((0.0, 0.0, -4.0)), Vector((0.0, 0.0, 1.0)))
    return bool(hit)


def test_resolve_collision_mode() -> None:
    assert resolve_collision_mode() == "hull"
    assert resolve_collision_mode(convex_hull=True) == "hull"
    assert resolve_collision_mode(convex_hull=False) == "mesh"
    assert resolve_collision_mode(mode="envelope") == "envelope"
    assert resolve_collision_mode(mode="ENVELOPE", convex_hull=False) == "envelope"
    with pytest.raises(ValueError):
        resolve_collision_mode(mode="vhax")


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


def test_collision_envelope_keeps_torus_hole(tmp_path: Path) -> None:
    inp = _save_torus_glb(tmp_path / "torus.glb")
    env_out = tmp_path / "env.glb"
    hull_out = tmp_path / "hull.glb"
    generate_collision_mesh(inp, env_out, max_faces=120, mode="envelope", voxel_size=0.12, inflate=0.02)
    generate_collision_mesh(inp, hull_out, max_faces=120, mode="hull")
    assert env_out.is_file()
    n = _load_face_count(env_out)
    assert 4 <= n <= 280
    # Hull seals the hole; envelope must leave the axis free.
    assert _origin_ray_hits(hull_out)
    assert not _origin_ray_hits(env_out)


def test_collision_mesh_mode_precise_keeps_torus_hole(tmp_path: Path) -> None:
    inp = _save_torus_glb(tmp_path / "torus.glb")
    out = tmp_path / "mesh.glb"
    generate_collision_mesh(inp, out, max_faces=160, mode="mesh", inflate=0.05)
    assert out.is_file()
    assert 4 <= _load_face_count(out) <= 220
    assert not _origin_ray_hits(out)


def test_collision_creates_parent_dir(tmp_path: Path) -> None:
    inp = _save_box_glb(tmp_path / "box.glb", extents=(1.0, 1.0, 1.0))
    out = tmp_path / "subdir" / "deep" / "collision.glb"
    result = generate_collision_mesh(inp, out, max_faces=50)
    assert result.is_file()
