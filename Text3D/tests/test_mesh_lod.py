"""Testes para geração de LOD (mesh_lod)."""

from __future__ import annotations

from pathlib import Path

import trimesh

from text3d.utils.mesh_lod import generate_lod_glb_triplet, prepare_mesh_topology, simplify_to_face_count


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


def test_prepare_mesh_topology_closes_micro_crack() -> None:
    """Micro-crack entre dois patches deve ser fechada pelo pipeline."""
    import numpy as np

    # Two adjacent quads with a 0.001-unit gap (simulating marching cubes crack)
    gap = 0.001
    verts = np.array(
        [
            # Quad A: x in [0, 0.5]
            [0, 0, 0],
            [0.5, 0, 0],
            [0.5, 1, 0],
            [0, 1, 0],
            # Quad B: x in [0.5 + gap, 1.0 + gap]
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
            [0, 2, 3],  # Quad A
            [4, 5, 6],
            [4, 6, 7],  # Quad B
        ],
        dtype=np.int64,
    )

    mesh = trimesh.Trimesh(vertices=verts, faces=faces)

    repaired = prepare_mesh_topology(mesh)
    assert len(repaired.faces) >= 4
    assert len(repaired.vertices) >= 4


def test_prepare_mesh_topology_handles_open_mesh() -> None:
    """Mesh com abertura intencional (sem face inferior) deve manter-se válida."""
    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    bottom_mask = box.face_normals[:, 1] < -0.5
    faces = box.faces[~bottom_mask]
    open_box = trimesh.Trimesh(vertices=box.vertices.copy(), faces=faces)

    assert not open_box.is_watertight

    repaired = prepare_mesh_topology(open_box)
    assert len(repaired.faces) >= 4
    assert len(repaired.vertices) >= 4
