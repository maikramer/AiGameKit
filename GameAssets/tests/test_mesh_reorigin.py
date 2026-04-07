"""Testes para reposicionamento de origem (pés) em GLB."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from gameassets.mesh_reorigin import (
    filter_excluded_paths,
    reorigin_glb_file,
    reorigin_scene_feet_yup,
)


def test_reorigin_scene_feet_yup_centers_base(tmp_path: Path) -> None:
    box = trimesh.creation.box([1.0, 2.0, 1.0])
    scene = trimesh.Scene(geometry={"box": box})
    b0 = scene.bounds
    assert b0 is not None
    assert np.isclose(b0[0][1], -1.0) and np.isclose(b0[1][1], 1.0)

    reorigin_scene_feet_yup(scene)
    b1 = scene.bounds
    assert b1 is not None
    assert np.isclose(b1[0][1], 0.0), b1
    assert np.isclose(b1[1][1], 2.0), b1
    cx = 0.5 * (b1[0][0] + b1[1][0])
    cz = 0.5 * (b1[0][2] + b1[1][2])
    assert abs(cx) < 1e-5 and abs(cz) < 1e-5


def test_reorigin_glb_file_roundtrip(tmp_path: Path) -> None:
    box = trimesh.creation.box([0.4, 1.2, 0.4])
    scene = trimesh.Scene(geometry={"box": box})
    path = tmp_path / "t.glb"
    scene.export(str(path), file_type="glb")

    reorigin_glb_file(path)

    loaded = trimesh.load(str(path), force="scene")
    assert isinstance(loaded, trimesh.Scene)
    b = loaded.bounds
    assert b is not None
    assert np.isclose(b[0][1], 0.0)
    assert np.isclose(b[1][1], 1.2)


def test_filter_excluded_paths() -> None:
    base = Path("/x/public/models")
    paths = [base / "hero.glb", base / "tree.glb", base / "player_rig.glb"]
    out = filter_excluded_paths(paths, ("hero.glb", "*player*"))
    assert out == [base / "tree.glb"]
