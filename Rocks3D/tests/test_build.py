"""Tests for the shared build pipeline (scale, GLB validity)."""

from __future__ import annotations

from pathlib import Path

import trimesh
from rocks3d.build import build_rock_glb


def _load(path: Path) -> trimesh.Trimesh:
    scene = trimesh.load(str(path))
    return next(iter(scene.geometry.values()))


def test_build_produces_valid_glb(tmp_path: Path) -> None:
    out = tmp_path / "pebble.glb"
    summary = build_rock_glb("pebble", out, seed=1, quality="medium")
    assert out.exists()
    assert summary["vertices"] > 0
    mesh = _load(out)
    assert mesh.visual.uv is not None
    assert mesh.visual.material.baseColorTexture is not None
    # Origin sits on the ground plane.
    assert abs(mesh.vertices[:, 1].min()) < 1e-4


def test_scale_is_applied(tmp_path: Path) -> None:
    base = tmp_path / "base.glb"
    scaled = tmp_path / "scaled.glb"
    build_rock_glb("boulder", base, seed=3, quality="fast", scale=1.0)
    build_rock_glb("boulder", scaled, seed=3, quality="fast", scale=3.0)

    e_base = _load(base).extents
    e_scaled = _load(scaled).extents
    ratio = e_scaled / e_base
    # Every axis grows ~3x (allow tolerance for the y=0 base translation).
    assert all(2.7 < r < 3.3 for r in ratio), ratio


def test_no_erosion_runs(tmp_path: Path) -> None:
    out = tmp_path / "ne.glb"
    summary = build_rock_glb("boulder", out, seed=4, quality="fast", erosion=False)
    assert out.exists()
    assert summary["faces"] > 0
