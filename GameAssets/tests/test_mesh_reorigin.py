"""Testes para reposicionamento de origem (pés) em GLB."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("bpy")

import bpy
import numpy as np

from gameassets.mesh_reorigin import (
    filter_excluded_paths,
    reorigin_glb_file,
)
from gamedev_shared.bpy_mesh import clear_scene, get_bounds, load_glb, save_glb


def _save_box_glb(path: Path, extents: tuple[float, ...] = (1.0, 2.0, 1.0)) -> Path:
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object
    obj.scale = (extents[0] / 2, extents[1] / 2, extents[2] / 2)
    bpy.ops.object.transform_apply(scale=True)
    save_glb([obj], path)
    return path


def _load_bounds(path: Path):
    objs = load_glb(path)
    if not objs:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    return get_bounds(objs[0])


def test_reorigin_glb_file_feet_yup(tmp_path: Path) -> None:
    path = _save_box_glb(tmp_path / "t.glb", extents=(1.0, 2.0, 1.0))
    b0 = _load_bounds(path)
    assert np.isclose(b0[0][1], -1.0) and np.isclose(b0[1][1], 1.0)

    reorigin_glb_file(path)

    b1 = _load_bounds(path)
    assert np.isclose(b1[0][1], 0.0), b1
    assert np.isclose(b1[1][1], 2.0), b1
    cx = 0.5 * (b1[0][0] + b1[1][0])
    cz = 0.5 * (b1[0][2] + b1[1][2])
    assert abs(cx) < 1e-3 and abs(cz) < 1e-3


def test_reorigin_glb_file_roundtrip(tmp_path: Path) -> None:
    path = _save_box_glb(tmp_path / "t.glb", extents=(0.4, 1.2, 0.4))
    reorigin_glb_file(path)

    b = _load_bounds(path)
    assert np.isclose(b[0][1], 0.0, atol=1e-3)
    assert np.isclose(b[1][1], 1.2, atol=0.01)


def test_filter_excluded_paths() -> None:
    base = Path("/x/public/models")
    paths = [base / "hero.glb", base / "tree.glb", base / "player_rig.glb"]
    out = filter_excluded_paths(paths, ("hero.glb", "*player*"))
    assert out == [base / "tree.glb"]
