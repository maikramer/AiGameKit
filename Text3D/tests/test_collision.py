"""Testes para geração de mesh de colisão."""

from __future__ import annotations

from pathlib import Path

import trimesh

from text3d.utils.collision import generate_collision_mesh


def test_collision_from_box(tmp_path: Path) -> None:
    box = trimesh.creation.box(extents=[1.0, 2.0, 3.0])
    inp = tmp_path / "box.glb"
    scene = trimesh.Scene(geometry={"m": box})
    inp.write_bytes(scene.export(file_type="glb"))

    out = tmp_path / "collision.glb"
    result = generate_collision_mesh(inp, out, max_faces=50, convex_hull=True)
    assert result.is_file()
    loaded = trimesh.load(str(result), force="mesh")
    if isinstance(loaded, trimesh.Scene):
        loaded = trimesh.util.concatenate(list(loaded.geometry.values()))
    assert len(loaded.faces) >= 4
    assert len(loaded.faces) <= 100  # some tolerance around max_faces


def test_collision_no_convex_hull(tmp_path: Path) -> None:
    mesh = trimesh.creation.icosphere(subdivisions=2)
    inp = tmp_path / "sphere.glb"
    scene = trimesh.Scene(geometry={"m": mesh})
    inp.write_bytes(scene.export(file_type="glb"))

    out = tmp_path / "collision.glb"
    result = generate_collision_mesh(inp, out, max_faces=50, convex_hull=False)
    assert result.is_file()
    loaded = trimesh.load(str(result), force="mesh")
    if isinstance(loaded, trimesh.Scene):
        loaded = trimesh.util.concatenate(list(loaded.geometry.values()))
    assert len(loaded.faces) >= 4


def test_collision_creates_parent_dir(tmp_path: Path) -> None:
    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    inp = tmp_path / "box.glb"
    scene = trimesh.Scene(geometry={"m": box})
    inp.write_bytes(scene.export(file_type="glb"))

    out = tmp_path / "subdir" / "deep" / "collision.glb"
    result = generate_collision_mesh(inp, out, max_faces=50)
    assert result.is_file()
