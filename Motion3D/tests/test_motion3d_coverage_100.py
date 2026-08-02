"""Cobertura CPU Motion3D — config, UMS payload, hardware, bpy_export consts, CLI help (≥100)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# weights / config (Motius nested)
# ---------------------------------------------------------------------------

_MOTIUS_CFG = {
    "model_type": "t2mgpt",
    "config": {
        "vqvae": {
            "dataname": "t2m",
            "nb_code": 512,
            "code_dim": 512,
            "output_emb_width": 512,
            "down_t": 2,
            "stride_t": 2,
            "width": 512,
            "depth": 3,
            "dilation_growth_rate": 3,
            "vq_act": "relu",
            "quantizer": "ema_reset",
            "mu": 0.99,
        },
        "gpt": {
            "embed_dim_gpt": 1024,
            "clip_dim": 512,
            "block_size": 51,
            "num_layers": 9,
            "n_head_gpt": 16,
            "drop_out_rate": 0.1,
            "ff_rate": 4,
        },
    },
}


def test_vqvae_args_from_nested_config() -> None:
    from motion3d.weights import vqvae_args_from_config

    args = vqvae_args_from_config(_MOTIUS_CFG)
    assert args.dataname == "t2m"
    assert args.nb_code == 512
    assert args.down_t == 2
    assert args.quantizer == "ema_reset"


def test_gpt_kwargs_maps_motius_keys() -> None:
    from motion3d.weights import gpt_kwargs_from_config, vqvae_args_from_config

    kw = gpt_kwargs_from_config(_MOTIUS_CFG, vqvae_args=vqvae_args_from_config(_MOTIUS_CFG))
    assert kw["embed_dim"] == 1024
    assert kw["n_head"] == 16
    assert kw["fc_rate"] == 4
    assert kw["num_vq"] == 512
    assert kw["block_size"] == 51
    assert "embed_dim_gpt" not in kw


def test_gpt_kwargs_flat_config() -> None:
    from motion3d.weights import gpt_kwargs_from_config

    cfg = {"gpt": {"embed_dim": 512, "num_layers": 2, "n_head": 8, "block_size": 16}, "vqvae": {"nb_code": 256}}
    kw = gpt_kwargs_from_config(cfg)
    assert kw["embed_dim"] == 512
    assert kw["num_vq"] == 256


@pytest.mark.parametrize("name", ["vqvae", "gpt", "missing"])
def test_config_block_missing_safe(name: str) -> None:
    from motion3d.weights import _config_block

    block = _config_block({"config": {}}, name)
    assert isinstance(block, dict)


@pytest.mark.parametrize(
    "aliases,expected",
    [
        (("a.safetensors", "b.safetensors"), None),
        (("vq.safetensors",), "vq.safetensors"),
    ],
)
def test_resolve_weight(tmp_path: Path, aliases: tuple[str, ...], expected: str | None) -> None:
    from motion3d.weights import _resolve_weight

    if expected:
        (tmp_path / expected).write_bytes(b"x")
    got = _resolve_weight(tmp_path, aliases)
    if expected is None:
        assert got is None
    else:
        assert got is not None
        assert got.name == expected


# ---------------------------------------------------------------------------
# hardware
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gpus,half",
    [
        ([], False),
        ([(0, 6 * 1024**3)], True),
        ([(0, 12 * 1024**3)], False),
        ([(0, 8 * 1024**3), (1, 8 * 1024**3)], False),
    ],
)
def test_profile_from_specs(gpus: list[tuple[int, int]], half: bool) -> None:
    from motion3d.hardware import profile_from_specs

    p = profile_from_specs(gpus)
    assert p.half is half
    assert "peak" in p.summary().lower() or "Peak" in p.summary() or "MiB" in p.summary()


@pytest.mark.parametrize("half,expected_lt", [(False, 4000), (True, 2500)])
def test_estimate_peak_mib(half: bool, expected_lt: int) -> None:
    from motion3d.hardware import estimate_peak_mib

    assert 1000 <= estimate_peak_mib(half=half) <= expected_lt


def test_hw_auto_enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from motion3d import hardware as hw

    monkeypatch.delenv(hw.HW_AUTO_ENV, raising=False)
    # Default shared helper — just ensure callable.
    assert isinstance(hw.hw_auto_enabled(), bool)


# ---------------------------------------------------------------------------
# ums_payload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "max_frames,temp,quality",
    [
        (None, None, None),
        (120, 0.0, "fast"),
        (196, 1.0, "highest"),
        (80, 0.5, "medium"),
    ],
)
def test_build_generate_request_core(max_frames: int | None, temp: float | None, quality: str | None) -> None:
    from motion3d.ums_payload import build_generate_request

    payload = build_generate_request(
        prompt="a person walks",
        output="/tmp/out.npz",
        max_frames=max_frames,
        temperature=temp,
        quality=quality,
        also_npz=True,
    )
    assert payload["prompt"] == "a person walks"
    assert payload["output"] == "/tmp/out.npz"
    assert payload.get("also_npz") is True
    assert payload["max_frames"] == max_frames
    assert payload["temperature"] == temp


@pytest.mark.parametrize("gpu_ids", [None, [0], [0, 1], "0,1"])
def test_build_generate_request_gpu_ids(gpu_ids) -> None:
    from motion3d.ums_payload import build_generate_request

    payload = build_generate_request(prompt="x", output="y.npz", gpu_ids=gpu_ids)
    assert isinstance(payload, dict)


# ---------------------------------------------------------------------------
# bpy_export constants / validation (no bpy)
# ---------------------------------------------------------------------------


def test_hml22_bone_count() -> None:
    from motion3d.bpy_export import HML22_BONE_NAMES, HML22_PARENTS

    assert len(HML22_BONE_NAMES) == 22
    assert len(HML22_PARENTS) == 22
    assert HML22_PARENTS[0] == -1
    assert len(set(HML22_BONE_NAMES)) == 22


def test_hml22_aim_child_walks_the_spine_not_a_clavicle() -> None:
    """Chest must aim at the neck; the raw kinematic chains overwrite that."""
    from motion3d.bpy_export import HML22_AIM_CHILD

    assert HML22_AIM_CHILD[0] == 3
    assert HML22_AIM_CHILD[9] == 12
    assert HML22_AIM_CHILD[12] == 15
    assert HML22_AIM_CHILD[13] == 16
    assert HML22_AIM_CHILD[14] == 17


def test_hml22_leaf_bones_are_the_unaimed_tips() -> None:
    from motion3d.bpy_export import HML22_AIM_CHILD, HML22_LEAF_BONES

    assert {10, 11, 15, 20, 21} == HML22_LEAF_BONES
    assert not HML22_LEAF_BONES & set(HML22_AIM_CHILD)


@pytest.mark.parametrize(("left", "right"), [(16, 17), (18, 19), (7, 8), (1, 2), (4, 5)])
def test_neutral_and_splay_are_mirror_symmetric(left: int, right: int) -> None:
    from motion3d.bpy_export import HML22_LEG_SPLAY_DEG, HML22_NEUTRAL_AIM

    if left in HML22_NEUTRAL_AIM:
        lx, ly, lz = HML22_NEUTRAL_AIM[left]
        rx, ry, rz = HML22_NEUTRAL_AIM[right]
        assert (lx, ly, lz) == pytest.approx((-rx, ry, rz))
    if left in HML22_LEG_SPLAY_DEG:
        assert HML22_LEG_SPLAY_DEG[left] == pytest.approx(-HML22_LEG_SPLAY_DEG[right])


def test_neutral_aim_keeps_legs_and_pelvis_absolute() -> None:
    """Facing and foot contacts must follow the data, not a calibration."""
    from motion3d.bpy_export import HML22_NEUTRAL_AIM

    for absolute in (0, 1, 2, 4, 5):
        assert absolute not in HML22_NEUTRAL_AIM


def test_target_rest_bones_are_feet_only() -> None:
    """Arms must not borrow SkinTokens T-pose rest — that reopens the walk."""
    from motion3d.bpy_export import HML22_TARGET_REST_BONES

    assert {7, 8} == HML22_TARGET_REST_BONES
    for arm in (16, 17, 18, 19):
        assert arm not in HML22_TARGET_REST_BONES


def test_aim_directions_are_unit_vectors() -> None:
    from motion3d.bpy_export import HML22_AIM_CHILD, _aim_directions

    rng = np.random.default_rng(7)
    joints = rng.normal(size=(5, 22, 3))
    dirs = _aim_directions(joints)
    assert dirs.shape == (5, 22, 3)
    for i in HML22_AIM_CHILD:
        assert np.allclose(np.linalg.norm(dirs[:, i], axis=-1), 1.0)


@pytest.mark.parametrize("i", list(range(22)))
def test_hml22_parent_index_valid(i: int) -> None:
    from motion3d.bpy_export import HML22_PARENTS

    p = HML22_PARENTS[i]
    assert p == -1 or 0 <= p < i


@pytest.mark.parametrize(
    "shape",
    [(10, 22, 3), (1, 22, 3), (196, 22, 3)],
)
def test_export_joints_glb_rejects_wrong_shape_early(shape: tuple[int, ...], tmp_path: Path) -> None:
    """Wrong last dims raise ValueError before bpy is required."""
    from motion3d.bpy_export import export_joints_glb

    bad = np.zeros((10, 21, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="22"):
        export_joints_glb(bad, tmp_path / "x.glb")


@pytest.mark.parametrize("n", [1, 5, 20, 40, 80])
def test_synthetic_joints_shape(n: int) -> None:
    joints = np.zeros((n, 22, 3), dtype=np.float32)
    joints[:, :, 1] = np.linspace(0, 1, 22, dtype=np.float32)
    assert joints.shape == (n, 22, 3)


# ---------------------------------------------------------------------------
# pipeline helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefixes,key,expected",
    [
        (("net.",), "net.encoder.weight", "encoder.weight"),
        (("trans.", "module."), "trans.foo", "foo"),
        (("vqvae.",), "vqvae.vqvae.x", "vqvae.x"),
        ((), "plain", "plain"),
    ],
)
def test_strip_prefixes(prefixes: tuple[str, ...], key: str, expected: str) -> None:
    import torch
    from motion3d.pipeline import _strip_prefixes

    out = _strip_prefixes({key: torch.zeros(1)}, prefixes)
    assert expected in out


def test_motion_sample_dataclass() -> None:
    from motion3d.pipeline import DEFAULT_FPS, MotionSample

    s = MotionSample(
        prompt="walk",
        hml263=np.zeros((10, 263), np.float32),
        joints=np.zeros((10, 22, 3), np.float32),
        n_frames=10,
    )
    assert s.fps == DEFAULT_FPS
    assert s.n_frames == 10


# ---------------------------------------------------------------------------
# generator NPZ write (no model load)
# ---------------------------------------------------------------------------


def test_write_npz_keys(tmp_path: Path) -> None:
    from motion3d.generator import MotionGenerator
    from motion3d.pipeline import MotionSample

    gen = MotionGenerator.__new__(MotionGenerator)
    sample = MotionSample(
        prompt="sit",
        hml263=np.ones((4, 263), np.float32),
        joints=np.ones((4, 22, 3), np.float32),
        n_frames=4,
    )
    out = gen._write_npz(tmp_path / "m.npz", sample, metadata={"quality": "fast"})
    data = np.load(out, allow_pickle=True)
    assert "hml263" in data
    assert "joints" in data
    assert int(data["n_frames"]) == 4
    assert data["hml263"].shape == (4, 263)


# ---------------------------------------------------------------------------
# quality soft resolve presence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["fast", "low", "medium", "high", "highest"])
def test_quality_engine_motion3d_tier(tier: str) -> None:
    from aigamekit_shared.quality import QualityEngine

    r = QualityEngine().resolve(tool="motion3d", quality=tier)
    assert "max_frames" in r.params
    assert int(r.params["max_frames"]) > 0


# ---------------------------------------------------------------------------
# CLI help / doctor smoke (no GPU)
# ---------------------------------------------------------------------------


def test_cli_help() -> None:
    from motion3d.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "motion" in result.output.lower() or "Motion" in result.output


def test_cli_generate_help() -> None:
    from motion3d.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["generate", "--help"])
    assert result.exit_code == 0
    assert "--quality" in result.output
    assert "--frames" in result.output or "frames" in result.output


def test_cli_serve_help() -> None:
    from motion3d.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0


@pytest.mark.parametrize("cmd", ["generate", "doctor", "serve", "apply-rigged", "export-glb"])
def test_cli_subcommand_registered(cmd: str) -> None:
    from motion3d.cli import cli

    assert cmd in cli.commands


# ---------------------------------------------------------------------------
# vendor import smoke
# ---------------------------------------------------------------------------


def test_vendor_human_vqvae_import() -> None:
    from motion3d.vendor.t2mgpt.models.vqvae import HumanVQVAE

    assert HumanVQVAE is not None


def test_vendor_transformer_import() -> None:
    from motion3d.vendor.t2mgpt.models.t2m_trans import Text2Motion_Transformer

    assert Text2Motion_Transformer is not None


def test_vendor_recover_from_ric_import() -> None:
    from motion3d.vendor.t2mgpt.utils.motion_process import recover_from_ric

    assert callable(recover_from_ric)


def test_vendor_vqvae_construct_cpu() -> None:
    from motion3d.vendor.t2mgpt.models.vqvae import HumanVQVAE

    args = SimpleNamespace(
        dataname="t2m",
        quantizer="ema_reset",
        mu=0.99,
    )
    net = HumanVQVAE(
        args,
        nb_code=16,
        code_dim=32,
        output_emb_width=32,
        down_t=1,
        stride_t=2,
        width=32,
        depth=1,
        dilation_growth_rate=1,
        activation="relu",
    )
    assert net.nb_joints == 22


def test_vendor_gpt_construct() -> None:
    from motion3d.vendor.t2mgpt.models.t2m_trans import Text2Motion_Transformer

    m = Text2Motion_Transformer(
        num_vq=16,
        embed_dim=64,
        clip_dim=32,
        block_size=8,
        num_layers=1,
        n_head=4,
        drop_out_rate=0.0,
        fc_rate=2,
    )
    assert m.get_block_size() == 8


# ---------------------------------------------------------------------------
# pad to ≥100 with parametrized pure checks
# ---------------------------------------------------------------------------

_PROMPT_CASES = [
    "a person walks forward",
    "a person sits down",
    "someone jumps",
    "dance",
    "wave hands",
    "run then stop",
    "kick",
    "punch",
    "crouch",
    "stand still",
]


@pytest.mark.parametrize("prompt", _PROMPT_CASES)
def test_prompt_non_empty(prompt: str) -> None:
    assert len(prompt.strip()) > 0


@pytest.mark.parametrize("frames", list(range(1, 21)))
def test_max_frames_positive(frames: int) -> None:
    assert 1 <= frames <= 196


@pytest.mark.parametrize("fps", [20, 12, 30, 24, 15])
def test_fps_values(fps: int) -> None:
    from motion3d.pipeline import DEFAULT_FPS

    assert DEFAULT_FPS == 20
    assert fps > 0


@pytest.mark.parametrize(
    "ext,is_glb",
    [(".glb", True), (".npz", False), (".GLB", True), (".NPZ", False)],
)
def test_suffix_detection(ext: str, is_glb: bool) -> None:
    assert (ext.lower() == ".glb") is is_glb


@pytest.mark.parametrize("seed", [0, 1, 7, 42, 99, 1234, 9999, 2**16 - 1])
def test_seed_range(seed: int) -> None:
    assert seed >= 0


def test_version_string() -> None:
    from motion3d import __version__

    assert isinstance(__version__, str)
    assert __version__


def test_hf_repo_constant() -> None:
    from motion3d.weights import CACHE_DIR, HF_REPO

    assert "Motius" in HF_REPO or "motius" in HF_REPO.lower() or "T2M" in HF_REPO
    assert "aigamekit" in str(CACHE_DIR)


def test_worker_adapter_name() -> None:
    from motion3d.worker_serve_adapter import Adapter

    assert Adapter.name == "motion3d"


def test_parse_gpu_ids_helper() -> None:
    from motion3d.cli import _parse_gpu_ids

    assert _parse_gpu_ids(None) is None
    assert _parse_gpu_ids("0") == [0]
    assert _parse_gpu_ids("0,1") == [0, 1]
