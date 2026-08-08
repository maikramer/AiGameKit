"""Suite de cobertura elaborada (≥100 casos) — aigamekit_shared, sem GPU."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from aigamekit_shared.cli_helpers import (
    BACKEND_FOOTPRINT_KEYS,
    env_bool,
    format_vramd_debug_line,
    legacy_server_allowed,
    needed_mib_for_backend,
    raise_if_vramd_queue_full,
    with_vramd_load_opts,
    with_vramd_peak_opts,
)
from aigamekit_shared.env import (
    ENV_TO_TOOL,
    PYTORCH_CUDA_ALLOC_CONF,
    TOOL_BINS,
    ensure_pytorch_cuda_alloc_conf,
    prefer_monorepo_tools,
    subprocess_gpu_env,
)
from aigamekit_shared.hardware import hw_auto_enabled
from aigamekit_shared.lowvram import (
    GIB,
    QUANT_WEIGHT_FACTOR,
    ModelFootprint,
    OffloadPlan,
    get_footprint,
    plan_offload,
)
from aigamekit_shared.mesh_repair import (
    _fibonacci_sphere_dirs,
    drop_nonfinite_faces,
    dynamic_weld_distance,
    get_repair_profile,
    infer_up_axis,
)
from aigamekit_shared.path_utils import ensure_directory, generate_output_path, safe_filename
from aigamekit_shared.quality import VALID_QUALITIES, QualityEngine
from aigamekit_shared.seed_utils import generate_seed, resolve_effective_seed, seed_everything

QUALITIES = list(VALID_QUALITIES)
TOOLS = [
    "text2d",
    "text2icon",
    "text3d",
    "paint3d",
    "texture2d",
    "skymap2d",
    "text2sound",
    "simplify",
    "rigging3d",
    "terrain3d",
    "rocks3d",
    "part3d",
]


@pytest.mark.parametrize("quality", QUALITIES)
@pytest.mark.parametrize("tool", TOOLS)
def test_quality_engine_resolve_returns_dict(quality: str, tool: str) -> None:
    engine = QualityEngine()
    r = engine.resolve(tool, quality=quality)
    assert isinstance(r.params, dict)
    assert r.source in ("quality_profile", "category", "explicit")


@pytest.mark.parametrize("quality", QUALITIES)
def test_quality_engine_list_qualities_contains(quality: str) -> None:
    assert quality in QualityEngine().list_qualities()


@pytest.mark.parametrize(
    "category",
    [
        "weapon",
        "humanoid",
        "rock",
        "building",
        "chest",
        "prop",
        "vehicle",
        "creature",
        "terrain",
        "vegetation",
        "environment",
        "item",
        "door",
        "effects",
        "ui",
    ],
)
def test_quality_engine_category_info(category: str) -> None:
    info = QualityEngine().category_info(category)
    assert isinstance(info, dict) and info


@pytest.mark.parametrize("category", ["weapon", "humanoid", "tree"])
def test_quality_engine_text3d_with_category(category: str) -> None:
    r = QualityEngine().resolve("text3d", quality="medium", category=category)
    assert "preset" in r.params or r.params


@pytest.mark.parametrize("kind", ["sfx_impact", "footstep", "music_loop", "ambient_wind"])
def test_quality_engine_audio_kind_info(kind: str) -> None:
    engine = QualityEngine()
    if kind in engine.list_audio_kinds():
        assert isinstance(engine.audio_kind_info(kind), dict)


@pytest.mark.parametrize(
    "tool,category",
    [
        ("text3d", "weapon"),
        ("paint3d", "humanoid"),
        ("text2sound", "weapon"),
        ("simplify", "tree"),
    ],
)
def test_quality_engine_category_source(tool: str, category: str) -> None:
    r = QualityEngine().resolve(tool, quality="high", category=category)
    assert r.category == category


@pytest.mark.parametrize("override_val", [1, 512, 1024, 2048])
def test_quality_engine_override_explicit(override_val: int) -> None:
    r = QualityEngine().resolve("text2d", quality="medium", overrides={"width": override_val})
    assert r.params["width"] == override_val
    assert r.source == "explicit"


@pytest.mark.parametrize("bad_quality", ["ultra", "unknown_tier"])
def test_quality_engine_bad_quality(bad_quality: str) -> None:
    with pytest.raises(KeyError):
        QualityEngine().resolve("text2d", quality=bad_quality)


@pytest.mark.parametrize("bad_cat", ["not_a_real_category"])
def test_quality_engine_bad_category(bad_cat: str) -> None:
    with pytest.raises(KeyError):
        QualityEngine().category_info(bad_cat)


@pytest.mark.parametrize(
    "env_val,cli_wants,expected",
    [
        ("1", False, True),
        ("true", False, True),
        ("yes", False, True),
        ("on", False, True),
        ("0", True, False),
        ("false", True, False),
        ("no", True, False),
        ("off", True, False),
        ("", True, True),
        ("", False, False),
        ("  TRUE  ", False, True),
        ("garbage", True, True),
    ],
)
def test_env_bool(env_val: str, cli_wants: bool, expected: bool) -> None:
    with patch.dict(os.environ, {"TEST_ENV_BOOL": env_val}, clear=False):
        assert env_bool("TEST_ENV_BOOL", cli_wants) is expected


@pytest.mark.parametrize("env_val,expected", [("1", True), ("0", False), ("", False)])
def test_legacy_server_allowed(env_val: str, expected: bool) -> None:
    with patch.dict(os.environ, {"VRAMD_ALLOW_LEGACY_SERVER": env_val}, clear=False):
        assert legacy_server_allowed() is expected


@pytest.mark.parametrize(
    "backend",
    [
        "text2d",
        "text2icon",
        "skymap2d",
        "text3d",
        "paint3d",
        "part3d",
        "text2sound",
        "texture2d",
        "terrain3d",
        "unknown_backend",
    ],
)
def test_needed_mib_positive(backend: str) -> None:
    assert needed_mib_for_backend(backend) >= 512


@pytest.mark.parametrize("backend", ["text3d", "paint3d", "text2d", "part3d"])
def test_needed_mib_memory_efficient(backend: str) -> None:
    a = needed_mib_for_backend(backend, memory_efficient=False)
    b = needed_mib_for_backend(backend, memory_efficient=True)
    assert isinstance(a, int) and isinstance(b, int)


@pytest.mark.parametrize("backend,quant", [("text3d", "sdnq-int4"), ("paint3d", "sdnq-uint8")])
def test_needed_mib_quant_mode(backend: str, quant: str) -> None:
    assert needed_mib_for_backend(backend, quant_mode=quant) >= 512


@pytest.mark.parametrize(
    "gpu_ids,expected",
    [
        ([0], [0]),
        ([0, 1], [0, 1]),
        ("0,1", [0, 1]),
        (" 2 , 3 ", [2, 3]),
        ("", None),
        ([], None),
    ],
)
def test_with_vramd_load_opts(gpu_ids: Any, expected: list[int] | None) -> None:
    out = with_vramd_load_opts({"a": 1}, gpu_ids=gpu_ids)
    if expected is None:
        assert "gpu_ids" not in out
    else:
        assert out["gpu_ids"] == expected


@pytest.mark.parametrize("k,v", [("seed", 1), ("steps", 4), ("output", "/x")])
def test_with_vramd_load_extra(k: str, v: Any) -> None:
    assert with_vramd_load_opts({}, **{k: v})[k] == v


@pytest.mark.parametrize(
    "backend,mem,exp",
    [
        ("text3d", True, "sdnq-int4"),
        ("paint3d", True, "sdnq-uint8"),
        ("text2d", True, "sdnq-uint8"),
        ("skymap2d", True, "none"),
        ("text3d", False, None),
    ],
)
def test_with_ums_peak_mem_eff(backend: str, mem: bool, exp: str | None) -> None:
    out = with_vramd_peak_opts({}, backend=backend, memory_efficient=mem)
    if exp:
        assert out.get("sdnq_preset") == exp


@pytest.mark.parametrize("preset", ["sdnq-int4", "sdnq-uint8", "none"])
def test_with_ums_peak_explicit_preset(preset: str) -> None:
    out = with_vramd_peak_opts({}, backend="text3d", sdnq_preset=preset)
    assert out["sdnq_preset"] == preset


@pytest.mark.parametrize("fk", ["flux-klein-4b", "hunyuan3d-omni"])
def test_with_ums_peak_footprint(fk: str) -> None:
    assert with_vramd_peak_opts({}, backend="text3d", footprint_key=fk)["footprint_key"] == fk


@pytest.mark.parametrize("fragment", ["backend=", "pri="])
def test_format_ums_debug(fragment: str) -> None:
    line = format_vramd_debug_line({"ums_debug": {"backend": "text3d", "priority": "batch"}})
    assert fragment in line


def test_raise_if_vramd_queue_full() -> None:
    import click

    with pytest.raises(click.ClickException):
        raise_if_vramd_queue_full({"status": "queue_full", "queue_depth": 1, "max_depth": 1})


def test_raise_if_ums_ok() -> None:
    raise_if_vramd_queue_full({"status": "ok"})


@pytest.mark.parametrize(
    "text,max_len",
    [
        ("Hello!", 40),
        ("café & bar", 40),
        ("   x   y  ", 40),
        ("a" * 80, 20),
        ("UPPER", 40),
    ],
)
def test_safe_filename(text: str, max_len: int) -> None:
    out = safe_filename(text, max_len=max_len)
    assert len(out) <= max_len
    assert "/" not in out


def test_ensure_directory(tmp_path: Path) -> None:
    d = tmp_path / "n" / "d"
    ensure_directory(d)
    assert d.is_dir()


def test_generate_output_path(tmp_path: Path) -> None:
    p = generate_output_path("foo", tmp_path, fmt="png")
    assert p.suffix == ".png" and p.parent == tmp_path


@pytest.mark.parametrize("seed", [None, 0, 999])
def test_resolve_seed(seed: int | None) -> None:
    assert 0 <= resolve_effective_seed(seed) < 2**32


def test_generate_seed() -> None:
    assert 0 <= generate_seed() < 2**32


def test_seed_everything() -> None:
    seed_everything(7)
    assert os.environ["PL_GLOBAL_SEED"] == "7"


@pytest.mark.parametrize("ev,v,exp", [("H", "1", True), ("H", "0", False), ("H", "", True)])
def test_hw_auto(ev: str, v: str, exp: bool) -> None:
    with patch.dict(os.environ, {ev: v}, clear=False):
        assert hw_auto_enabled(ev) is exp


@pytest.mark.parametrize(
    "key",
    [
        "flux-klein-4b",
        "flux-klein-9b",
        "flux-dev-uint4",
        "hunyuan3d-2.1-dit",
        "hunyuan3d-omni",
        "hunyuan3d-part",
        "hunyuan-paint",
        "stable-audio-open",
        "sana-sprint-600m",
    ],
)
def test_get_footprint(key: str) -> None:
    assert get_footprint(key).fp16_weights_gib > 0


def test_footprint_fallback() -> None:
    assert get_footprint("__missing__").fp16_weights_gib == 8.0


@pytest.mark.parametrize(
    "quant", ["none", "fp8", "fp8-layerwise", "sdnq-fp8", "int8", "sdnq-uint8", "sdnq-int8", "int4", "sdnq-int4"]
)
def test_footprint_weights(quant: str) -> None:
    fp = ModelFootprint(10.0, 2.0, 4.0)
    assert fp.weights_gib(quant) == pytest.approx(10.0 * QUANT_WEIGHT_FACTOR[quant])


@pytest.mark.parametrize("specs,dev", [([], "cpu"), ([(0, 24 * GIB)], "cuda")])
def test_plan_offload_device(specs: list, dev: str) -> None:
    plan = plan_offload(specs, get_footprint("sana-sprint-600m"))
    assert plan.device == dev


@pytest.mark.parametrize("gib", [4, 6, 8, 12, 24])
def test_plan_offload_vram_gib(gib: int) -> None:
    plan = plan_offload([(0, gib * GIB)], get_footprint("flux-klein-9b"), allow_multi_gpu=False)
    assert isinstance(plan, OffloadPlan)


def test_plan_offload_force_group() -> None:
    plan = plan_offload([(0, 8 * GIB)], get_footprint("hunyuan3d-omni"), force_group_offload=True)
    assert plan.device == "cuda"


@pytest.mark.parametrize("n", [1, 8, 32])
def test_fibonacci_dirs(n: int) -> None:
    d = _fibonacci_sphere_dirs(n)
    np.testing.assert_allclose(np.linalg.norm(d, axis=1), 1.0, rtol=1e-4)


@pytest.mark.parametrize("drop", [0, 1])
def test_drop_nonfinite(drop: int) -> None:
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    if drop:
        v[1, 0] = np.nan
    f = np.array([[0, 1, 2]], int)
    _, _, n = drop_nonfinite_faces(v, f)
    assert n == drop


@pytest.mark.parametrize("axis", [0, 1, 2])
def test_infer_up_axis(axis: int) -> None:
    c = np.zeros((3, 3))
    c[0, axis] = 10.0
    c[1, axis] = -10.0
    assert infer_up_axis(c) == axis


@pytest.mark.parametrize("vc,exp", [(200_000, 0.003), (120_000, 0.005), (80_000, 0.008), (5_000, 0.01)])
def test_dynamic_weld(vc: int, exp: float) -> None:
    assert dynamic_weld_distance(vc) == exp


@pytest.mark.parametrize("name", ["pre_decimate_uv", "post_decimate", "part_decode", "topology_clean", "post_voxel"])
def test_repair_profile(name: str) -> None:
    assert get_repair_profile(name).name == name


def test_repair_unknown() -> None:
    with pytest.raises(ValueError):
        get_repair_profile("nope")


@pytest.mark.parametrize("v,e", [("1", True), ("0", False)])
def test_prefer_monorepo(v: str, e: bool) -> None:
    with patch.dict(os.environ, {"AIGAMEKIT_PREFER_MONOREPO": v}, clear=False):
        assert prefer_monorepo_tools() is e


def test_tool_bins_roundtrip() -> None:
    for t, e in TOOL_BINS.items():
        assert ENV_TO_TOOL[e] == t


@pytest.mark.parametrize("b", list(BACKEND_FOOTPRINT_KEYS))
def test_backend_keys(b: str) -> None:
    assert BACKEND_FOOTPRINT_KEYS[b]


def test_pytorch_alloc_respect() -> None:
    with patch.dict(os.environ, {PYTORCH_CUDA_ALLOC_CONF: "x"}, clear=False):
        ensure_pytorch_cuda_alloc_conf()
        assert os.environ[PYTORCH_CUDA_ALLOC_CONF] == "x"


def test_subprocess_gpu_ids() -> None:
    assert subprocess_gpu_env(gpu_ids=[0])["CUDA_VISIBLE_DEVICES"] == "0"
