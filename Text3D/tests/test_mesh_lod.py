"""Testes para geração de LOD (mesh_lod)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("bpy")

import bpy
import numpy as np

from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays, face_count, save_glb
from text3d.utils.mesh_lod import generate_lod_glb_triplet, prepare_mesh_topology, simplify_to_face_count


def _save_box_glb(path: Path, extents: tuple[float, ...] = (1.0, 2.0, 3.0)) -> Path:
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object
    obj.scale = (extents[0] / 2, extents[1] / 2, extents[2] / 2)
    bpy.ops.object.transform_apply(scale=True)
    save_glb([obj], path)
    return path


def _save_sphere_glb(path: Path, subdivisions: int = 3) -> Path:
    clear_scene()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions)
    save_glb([bpy.context.active_object], path)
    return path


def _save_arrays_glb(path: Path, verts: np.ndarray, faces: np.ndarray) -> Path:
    clear_scene()
    obj = create_mesh_from_arrays(verts, faces)
    save_glb([obj], path)
    return path


def _load_face_count(path: Path) -> int:
    from gamedev_shared.bpy_mesh import load_glb

    objs = load_glb(path)
    return sum(face_count(o) for o in objs)


def test_prepare_mesh_topology_keeps_solid_box(tmp_path: Path) -> None:
    inp = _save_box_glb(tmp_path / "box.glb")
    out = tmp_path / "prepared.glb"
    prepare_mesh_topology(inp, out)
    assert out.is_file()
    assert _load_face_count(out) >= 4


def test_simplify_reduces_faces(tmp_path: Path) -> None:
    inp = _save_sphere_glb(tmp_path / "sphere.glb", subdivisions=3)
    n0 = _load_face_count(inp)
    out = tmp_path / "simplified.glb"
    simplify_to_face_count(inp, max(20, n0 // 10), out)
    n1 = _load_face_count(out)
    assert n1 < n0
    assert n1 >= 4


def test_generate_lod_triplet_writes_three_glbs(tmp_path: Path) -> None:
    inp = _save_box_glb(tmp_path / "box.glb")
    out = generate_lod_glb_triplet(
        inp,
        tmp_path / "lod",
        "prop",
        lod1_ratio=0.5,
        lod2_ratio=0.25,
        min_faces_lod1=8,
        min_faces_lod2=4,
    )
    assert len(out) == 3
    for i, p in enumerate(out):
        assert p.name == f"prop_lod{i}.glb"
        assert p.is_file()
        assert _load_face_count(p) >= 4


def test_generate_lod_triplet_meshfix_opt_in(tmp_path: Path) -> None:
    inp = _save_box_glb(tmp_path / "box.glb")
    out = generate_lod_glb_triplet(
        inp,
        tmp_path / "lod2",
        "prop",
        lod1_ratio=0.5,
        lod2_ratio=0.25,
        min_faces_lod1=8,
        min_faces_lod2=4,
        meshfix=True,
    )
    assert len(out) == 3
    assert all(p.is_file() for p in out)


def test_prepare_mesh_topology_closes_micro_crack(tmp_path: Path) -> None:
    gap = 0.001
    verts = np.array(
        [
            [0, 0, 0],
            [0.5, 0, 0],
            [0.5, 1, 0],
            [0, 1, 0],
            [0.5 + gap, 0, 0],
            [1.0 + gap, 0, 0],
            [1.0 + gap, 1, 0],
            [0.5 + gap, 1, 0],
        ],
        dtype=np.float64,
    )
    faces = np.array(
        [
            [0, 1, 2],
            [0, 2, 3],
            [4, 5, 6],
            [4, 6, 7],
        ],
        dtype=np.int64,
    )

    inp = _save_arrays_glb(tmp_path / "crack.glb", verts, faces)
    out = tmp_path / "repaired.glb"
    prepare_mesh_topology(inp, out)
    assert _load_face_count(out) >= 4


def test_prepare_mesh_topology_handles_open_mesh(tmp_path: Path) -> None:
    inp = _save_box_glb(tmp_path / "box.glb", extents=(1.0, 1.0, 1.0))
    out = tmp_path / "open.glb"
    prepare_mesh_topology(inp, out)
    assert _load_face_count(out) >= 4
