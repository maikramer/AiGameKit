"""Round 2 — checkpoints e _classify_row_state_master."""

from __future__ import annotations

from pathlib import Path


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_classify_master_need_image(tmp_path: Path) -> None:
    from gameassets.paths import _ROW_NEED_IMAGE, _classify_row_state_master

    img = tmp_path / "img.png"
    mesh = tmp_path / "mesh.glb"
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_NEED_IMAGE


def test_classify_master_need_topology_fix(tmp_path: Path) -> None:
    from gameassets.paths import _ROW_NEED_TOPOLOGY_FIX, _classify_row_state_master, _shape_path

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_NEED_TOPOLOGY_FIX


def test_classify_master_need_paint(tmp_path: Path) -> None:
    from gameassets.paths import (
        _ROW_NEED_PAINT,
        _classify_row_state_master,
        _clean_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    _touch(_clean_path(mesh))
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_NEED_PAINT


def test_classify_master_need_bake_master(tmp_path: Path) -> None:
    from gameassets.paths import (
        _ROW_NEED_BAKE_MASTER,
        _classify_row_state_master,
        _clean_path,
        _painted_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    _touch(_clean_path(mesh))
    _touch(_painted_path(mesh))
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_NEED_BAKE_MASTER


def test_classify_master_need_lod_gen(tmp_path: Path) -> None:
    from gameassets.paths import (
        _ROW_NEED_LOD_GEN,
        _classify_row_state_master,
        _clean_path,
        _lod_path,
        _painted_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    _touch(_clean_path(mesh))
    _touch(_painted_path(mesh))
    _touch(_lod_path(mesh, 0))
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_NEED_LOD_GEN


def test_classify_master_done(tmp_path: Path) -> None:
    from gameassets.paths import (
        _ROW_DONE,
        _classify_row_state_master,
        _clean_path,
        _lod_path,
        _painted_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    _touch(_clean_path(mesh))
    _touch(_painted_path(mesh))
    _touch(_lod_path(mesh, 0))
    _touch(_lod_path(mesh, 1))
    _touch(_lod_path(mesh, 2))
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_DONE


def test_classify_master_need_rig_hi(tmp_path: Path) -> None:
    from gameassets.paths import (
        _ROW_NEED_RIG_HI,
        _classify_row_state_master,
        _clean_path,
        _lod_path,
        _painted_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    for p in (
        _shape_path(mesh),
        _clean_path(mesh),
        _painted_path(mesh),
        _lod_path(mesh, 0),
        _lod_path(mesh, 1),
        _lod_path(mesh, 2),
    ):
        _touch(p)
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=True, wants_animate=False
    )
    assert state == _ROW_NEED_RIG_HI


def test_resume_master_pipeline_importable() -> None:
    from gameassets import pipeline_master

    assert hasattr(pipeline_master, "resume_master_pipeline")
    assert hasattr(pipeline_master, "run_master_pipeline")
