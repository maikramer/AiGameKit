"""Cobertura elaborada Text3D — bbox_tune, ums_payload, defaults, mesh metrics, CLI --help."""

from __future__ import annotations

import math
import os
import subprocess
import sys
from unittest.mock import patch

import pytest
import trimesh

from text3d import defaults
from text3d.bbox_tune import (
    _OCTREE_CEILING,
    _OCTREE_FLOOR,
    BBoxTuneResult,
    _snap_octree,
    characteristic_meters,
    latent_detail_ceiling,
    max_octree_for_vram,
    morph_close_meters,
    morph_close_voxels_for,
    target_voxel_for,
    tune_hunyuan_for_bbox,
    voxel_meters,
)
from text3d.ums_payload import build_generate_request
from text3d.utils import mesh_metrics

# --- target_voxel_for (24 casos) ---


@pytest.mark.parametrize(
    "category,preset,quality,lo,hi",
    [
        (None, None, None, 0.012, 0.06),
        ("building", None, "medium", 0.012, 0.06),
        ("weapon", None, "high", 0.012, 0.06),
        ("tree", None, "fast", 0.012, 0.06),
        (None, "chapel", "highest", 0.012, 0.06),
        (None, "humanoid", "low", 0.012, 0.06),
        ("humanoid", None, "medium", 0.012, 0.06),
        ("terrain", None, "medium", 0.012, 0.06),
        ("rock", None, "high", 0.012, 0.06),
        ("prop", None, None, 0.012, 0.06),
        ("vehicle", None, "fast", 0.012, 0.06),
        ("creature", None, "highest", 0.012, 0.06),
        ("environment", None, "low", 0.012, 0.06),
        ("furniture", None, "medium", 0.012, 0.06),
        ("door", None, "high", 0.012, 0.06),
        ("chest", None, None, 0.012, 0.06),
        (None, "sword", "medium", 0.012, 0.06),
        (None, "crate", "fast", 0.012, 0.06),
        (None, "barrel", "low", 0.012, 0.06),
        ("unknown_cat", None, "medium", 0.012, 0.06),
        (None, "unknown_preset", "medium", 0.012, 0.06),
        ("building", "building", "highest", 0.012, 0.06),
        ("tree", "tree", "fast", 0.012, 0.06),
        (None, None, "bogus", 0.012, 0.06),
    ],
)
def test_target_voxel_for_in_range(
    category: str | None,
    preset: str | None,
    quality: str | None,
    lo: float,
    hi: float,
) -> None:
    v = target_voxel_for(category, preset, quality)
    assert lo <= v <= hi


# --- characteristic_meters (16 casos) ---


@pytest.mark.parametrize(
    "size_m,category,preset,expected_m,source",
    [
        ([1.0, 2.0, 0.5], None, None, 2.0, "size_m"),
        ([0.0, 0.0, 0.0], None, None, None, "none"),
        (None, "building", None, 6.0, "category"),
        (None, None, "chapel", 7.0, "bbox_preset"),
        (None, "tree", None, 5.0, "category"),
        (None, None, "humanoid", 1.7, "bbox_preset"),
        (None, None, None, None, "none"),
        ([10.0, 8.0, 6.0], "prop", "crate", 10.0, "size_m"),
        (None, None, "sword", 0.9, "bbox_preset"),
        (None, "door", None, 2.2, "category"),
        (None, None, "cube", 1.0, "bbox_preset"),
        (None, "vehicle", None, 4.0, "category"),
        (None, "rock", None, 1.2, "category"),
        (None, None, "quadruped", 1.4, "bbox_preset"),
        (None, None, "barrel", 0.9, "bbox_preset"),
        (None, "furniture", None, 1.5, "category"),
    ],
)
def test_characteristic_meters(
    size_m: list[float] | None,
    category: str | None,
    preset: str | None,
    expected_m: float | None,
    source: str,
) -> None:
    char_m, src = characteristic_meters(size_m, category=category, bbox_preset=preset)
    assert char_m == expected_m
    assert src == source


@pytest.mark.parametrize(
    "char_m,octree,expected",
    [
        (1.7, 256, 1.7 / 256),
        (10.0, 320, 10.0 / 320),
        (0.0, 256, 0.0),
        (5.0, 0, 0.0),
        (3.0, 160, 3.0 / 160),
    ],
)
def test_voxel_meters(char_m: float, octree: int, expected: float) -> None:
    assert abs(voxel_meters(char_m, octree) - expected) < 1e-9


# --- _snap_octree (20 valores) ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0, 160),
        (159, 160),
        (160, 160),
        (176, 160),
        (192, 192),
        (256, 256),
        (320, 320),
        (384, 384),
        (448, 448),
        (512, 512),
        (600, 512),
        (999, 512),
        (161, 160),
        (177, 192),
        (200, 192),
        (208, 224),
        (240, 224),
        (241, 256),
        (400, 416),
        (480, 480),
    ],
)
def test_snap_octree_ladder(raw: int, expected: int) -> None:
    assert _snap_octree(raw) == expected


# --- max_octree_for_vram (14 casos) ---


@pytest.mark.parametrize(
    "vram,offload,expected",
    [
        (None, False, 384),
        (None, True, 448),
        (6.0, True, 448),
        (6.0, False, 320),
        (12.0, False, 512),
        (10.0, False, 448),
        (7.5, False, 384),
        (5.0, True, 384),
        (4.0, True, 320),
        (16.0, False, 512),
        (11.0, True, 512),
        (8.0, True, 448),
        (5.5, True, 384),
        (5.5, False, 256),
    ],
)
def test_max_octree_for_vram(vram: float | None, offload: bool, expected: int) -> None:
    assert max_octree_for_vram(vram, group_offload=offload) == expected


# --- morph close (12 casos) ---


@pytest.mark.parametrize(
    "category,explicit,expected",
    [
        (None, None, 0.125),
        ("terrain", None, 0.375),
        ("rock", None, 0.375),
        ("humanoid", 0.5, 0.5),
        ("prop", 0.0, 0.0),
    ],
)
def test_morph_close_voxels_for(category: str | None, explicit: float | None, expected: float) -> None:
    assert morph_close_voxels_for(category, explicit=explicit) == expected


def test_morph_close_voxels_negative_raises() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        morph_close_voxels_for(None, explicit=-0.1)


@pytest.mark.parametrize("char_m", [0.0, -1.0])
def test_morph_close_meters_invalid_char(char_m: float) -> None:
    assert morph_close_meters(char_m, 256) is None


def test_morph_close_meters_positive() -> None:
    m = morph_close_meters(2.0, 256, category="humanoid")
    assert m is not None
    assert 0.001 <= m <= 0.08


# --- tune_hunyuan_for_bbox (10 casos) ---


@pytest.mark.parametrize(
    "size_m",
    [
        [0.5, 1.7, 0.4],
        [8.0, 10.0, 6.0],
        None,
    ],
)
def test_tune_hunyuan_result_shape(size_m: list[float] | None) -> None:
    r = tune_hunyuan_for_bbox(
        base_steps=30,
        base_octree=256,
        base_chunks=8000,
        size_m=size_m,
        category="humanoid" if size_m is None else "building",
        total_vram_gib=6.0,
        group_offload=True,
    )
    assert isinstance(r, BBoxTuneResult)
    assert _OCTREE_FLOOR <= r.octree <= _OCTREE_CEILING
    assert r.steps >= 1
    assert r.chunks >= 1


def test_latent_detail_ceiling_default() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("TEXT3D_LATENT_OCTREE_CEILING", None)
        assert latent_detail_ceiling() >= 128


def test_latent_detail_ceiling_env() -> None:
    with patch.dict(os.environ, {"TEXT3D_LATENT_OCTREE_CEILING": "512"}):
        assert latent_detail_ceiling() == 512


# --- ums_payload (14 casos) ---


@pytest.mark.parametrize(
    "kwargs,must_have",
    [
        ({}, {"from_image", "output", "guidance", "bbox_tune"}),
        ({"steps": 40, "octree_resolution": 320}, {"steps", "octree_resolution"}),
        ({"sdnq_preset": "sdnq-int4", "offload": True}, {"sdnq_preset"}),
        ({"memory_efficient": True}, {"memory_efficient"}),
        ({"quality": "high", "category": "humanoid"}, {"quality", "category"}),
        ({"size_m": [1.0, 1.7, 0.5]}, {"size_m"}),
        ({"bbox_tune": False}, {"bbox_tune"}),
        ({"torch_compile": True, "channels_last": True}, {"torch_compile", "channels_last"}),
        ({"seed": 123, "seed_fingerprint": 456}, {"seed", "seed_fingerprint"}),
        ({"gpu_ids": [0]}, {"gpu_ids"}),
        ({"extra": {"wave": 1}}, {"wave"}),
        ({"topology_fix": False}, {"topology_fix"}),
        ({"mc_level": 0.5}, {"mc_level"}),
        ({"pose_preset": "t-pose"}, {"pose_preset"}),
    ],
)
def test_build_generate_request_text3d(kwargs: dict, must_have: set[str]) -> None:
    base = {"from_image": "/tmp/in.png", "output": "/tmp/out.glb"}
    payload = build_generate_request(**base, **kwargs)
    for key in must_have:
        assert key in payload


# --- defaults (12 casos) ---


@pytest.mark.parametrize(
    "origin",
    ["feet", "center", "none"],
)
def test_export_origin_override_roundtrip(origin: str) -> None:
    defaults.set_export_origin_override(origin)
    assert defaults.get_export_origin() == origin
    defaults.set_export_origin_override(None)


def test_export_origin_invalid_raises() -> None:
    with pytest.raises(ValueError, match="inválido"):
        defaults.set_export_origin_override("invalid")


@pytest.mark.parametrize("deg", [0.0, 90.0, 180.0])
def test_export_rotation_override(deg: float) -> None:
    rad = deg * math.pi / 180.0
    defaults.set_export_rotation_x_rad_override(rad)
    assert abs(defaults.get_export_rotation_x_rad() - rad) < 1e-9
    defaults.set_export_rotation_x_rad_override(None)


def test_preset_hunyuan_keys() -> None:
    assert set(defaults.PRESET_HUNYUAN) == {"fast", "balanced", "hq"}
    assert defaults.PRESET_HUNYUAN["balanced"]["octree"] == defaults.MEMORY_EFFICIENT_OCTREE


# --- mesh_metrics (8 casos) ---


def _box_mesh() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=[1.0, 1.0, 1.0])


@pytest.mark.parametrize("empty", [True, False])
def test_boundary_edge_count(empty: bool) -> None:
    if empty:
        assert mesh_metrics.boundary_edge_count(None) == 0
    else:
        assert mesh_metrics.boundary_edge_count(_box_mesh()) == 0


def test_split_components_box() -> None:
    parts = mesh_metrics.split_components(_box_mesh())
    assert len(parts) == 1


def test_classify_component_labels_single() -> None:
    out = mesh_metrics.classify_component_labels(_box_mesh())
    assert out is not None
    _labels, main, internal, _external, _vols = out
    assert int(main) == 0
    assert len(internal) == 0


def test_mesh_quality_metrics_keys() -> None:
    q = mesh_metrics.mesh_quality_metrics(_box_mesh())
    assert "faces" in q
    assert "vertices" in q


# --- CLI --help (14 comandos) ---

_CLI_COMMANDS = [
    "generate",
    "generate-batch",
    "topology-fix",
    "bake-master",
    "lod",
    "remesh",
    "remesh-textured",
    "collision",
    "split-at-height",
    "align-plus-z",
    "convert",
    "doctor",
    "info",
    "models",
]


@pytest.mark.parametrize("subcommand", _CLI_COMMANDS)
def test_cli_subcommand_help(subcommand: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "text3d", subcommand, "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    assert proc.returncode == 0, proc.stderr[:500]
    assert "Usage" in proc.stdout or "Options" in proc.stdout or subcommand in proc.stdout


def test_octree_ladder_constants() -> None:
    from text3d.bbox_tune import _OCTREE_LADDER

    assert _OCTREE_LADDER[0] == _OCTREE_FLOOR
    assert _OCTREE_LADDER[-1] == _OCTREE_CEILING
