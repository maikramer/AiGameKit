"""Testes para geração de LOD (mesh_lod)."""

from __future__ import annotations

from pathlib import Path

import trimesh

from text3d.utils.mesh_lod import generate_lod_glb_triplet, simplify_to_face_count
from text3d.utils.mesh_repair import prepare_mesh_topology


def test_prepare_mesh_topology_keeps_solid_box() -> None:
    box = trimesh.creation.box(extents=[1.0, 2.0, 3.0])
    prep = prepare_mesh_topology(box)
    assert len(prep.faces) >= 4
    assert len(prep.vertices) >= 4


def test_simplify_reduces_faces(tmp_path: Path) -> None:
    mesh = trimesh.creation.icosphere(subdivisions=3)
    n0 = len(mesh.faces)
    low = simplify_to_face_count(mesh, max(20, n0 // 10))
    assert len(low.faces) < n0
    assert len(low.faces) >= 4


def test_generate_lod_triplet_writes_three_glbs(tmp_path: Path) -> None:
    box = trimesh.creation.box(extents=[1.0, 2.0, 3.0])
    inp = tmp_path / "box.glb"
    scene = trimesh.Scene(geometry={"m": box})
    inp.write_bytes(scene.export(file_type="glb"))

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
        loaded = trimesh.load(str(p), force="mesh")
        if isinstance(loaded, trimesh.Scene):
            loaded = trimesh.util.concatenate(list(loaded.geometry.values()))
        assert len(loaded.faces) >= 4


def test_generate_lod_triplet_meshfix_opt_in(tmp_path: Path) -> None:
    box = trimesh.creation.box(extents=[1.0, 2.0, 3.0])
    inp = tmp_path / "box.glb"
    scene = trimesh.Scene(geometry={"m": box})
    inp.write_bytes(scene.export(file_type="glb"))

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
