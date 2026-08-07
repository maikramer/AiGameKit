"""Cobertura CPU Motion3D — HY-Motion weights/hw/UMS/bpy_export/CLI (≥100)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from click.testing import CliRunner

GIB = 1024**3


# ---------------------------------------------------------------------------
# weights / config
# ---------------------------------------------------------------------------


def test_hf_repo_and_cache() -> None:
    from motion3d.weights import CACHE_DIR, HF_REPO, VARIANT_DIR

    assert "HY-Motion" in HF_REPO or "tencent" in HF_REPO.lower()
    assert "aigamekit" in str(CACHE_DIR)
    assert "Lite" in VARIANT_DIR["lite"]
    assert VARIANT_DIR["full"] == "HY-Motion-1.0"


def test_is_lfs_pointer(tmp_path: Path) -> None:
    from motion3d.weights import _is_lfs_pointer

    real = tmp_path / "real.ckpt"
    real.write_bytes(b"\x00" * 2048)
    assert _is_lfs_pointer(real) is False
    ptr = tmp_path / "ptr.ckpt"
    ptr.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n")
    assert _is_lfs_pointer(ptr) is True


def test_rewrite_config_mean_std(tmp_path: Path) -> None:
    from motion3d.weights import _rewrite_config

    src = tmp_path / "config.yml"
    src.write_text(
        "train_pipeline_args:\n  test_cfg:\n    mean_std_dir: ./stats/\n",
        encoding="utf-8",
    )
    dest = tmp_path / "out.yml"
    stats = tmp_path / "stats"
    stats.mkdir()
    _rewrite_config(src, dest, mean_std_dir=stats)
    text = dest.read_text(encoding="utf-8")
    assert str(stats.resolve()) in text


def test_footprint_key_for() -> None:
    from motion3d.hardware import footprint_key_for

    assert footprint_key_for("lite") == "hy-motion-lite"
    assert footprint_key_for("full") == "hy-motion-full"


# ---------------------------------------------------------------------------
# hardware (Text2D-style plan_offload)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vram_gib,expect_model",
    [
        (6, "full"),
        (8, "full"),
        (12, "full"),
        (24, "full"),
    ],
)
def test_profile_prefers_full_when_staged_fits(vram_gib: int, expect_model: str) -> None:
    from motion3d.hardware import profile_from_specs

    p = profile_from_specs([(0, int(vram_gib * GIB))])
    assert p.model == expect_model
    assert p.staged_load is True
    assert p.validation_steps >= 20


def test_profile_6gb_stages_text_on_cpu() -> None:
    from motion3d.hardware import profile_from_specs

    p = profile_from_specs([(0, 6 * GIB)])
    assert p.offload_text_encoder is True
    assert p.memory_efficient is True
    assert p.duration_cap_s is not None
    assert p.duration_cap_s <= 4.0


def test_profile_cpu() -> None:
    from motion3d.hardware import profile_from_specs

    p = profile_from_specs([])
    assert p.device == "cpu"
    assert p.model == "lite"
    assert "cpu" in p.summary().lower() or p.offload_mode == "cpu"


def test_estimate_peak_mib_bounded() -> None:
    from motion3d.hardware import estimate_peak_mib

    peak = estimate_peak_mib(model="full", sdnq_preset="sdnq-int4", memory_efficient=True)
    assert 1024 <= peak <= 12_000


def test_hw_auto_enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from motion3d import hardware as hw

    monkeypatch.delenv(hw.HW_AUTO_ENV, raising=False)
    assert isinstance(hw.hw_auto_enabled(), bool)


# ---------------------------------------------------------------------------
# ums_payload
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "duration,cfg,quality,model",
    [
        (None, None, None, None),
        (4.0, 5.0, "fast", "lite"),
        (6.5, 5.5, "highest", "full"),
        (3.0, 4.0, "medium", "lite"),
    ],
)
def test_build_generate_request_core(
    duration: float | None,
    cfg: float | None,
    quality: str | None,
    model: str | None,
) -> None:
    from motion3d.ums_payload import build_generate_request

    payload = build_generate_request(
        prompt="a person walks",
        output="/tmp/out.npz",
        duration=duration,
        cfg_scale=cfg,
        quality=quality,
        model=model,
        also_npz=True,
        memory_efficient=True,
        sdnq_preset="sdnq-int4",
    )
    assert payload["prompt"] == "a person walks"
    assert payload["output"] == "/tmp/out.npz"
    assert payload.get("also_npz") is True
    assert payload.get("sdnq_preset") == "sdnq-int4"
    assert payload.get("footprint_key") in ("hy-motion-lite", "hy-motion-full")
    assert "peak_mib_hint" in payload


@pytest.mark.parametrize("gpu_ids", [None, [0], [0, 1], "0,1"])
def test_build_generate_request_gpu_ids(gpu_ids) -> None:
    from motion3d.ums_payload import build_generate_request

    payload = build_generate_request(prompt="x", output="y.npz", gpu_ids=gpu_ids)
    assert isinstance(payload, dict)


def test_ums_payload_allow_group_offload() -> None:
    from motion3d.ums_payload import build_generate_request

    payload = build_generate_request(
        prompt="x",
        output="y.npz",
        model="full",
        memory_efficient=True,
        allow_group_offload=True,
        sdnq_preset="sdnq-int4",
    )
    assert payload.get("allow_group_offload") is True
    assert payload.get("memory_efficient") is True


# ---------------------------------------------------------------------------
# bpy_export constants / validation (no bpy)
# ---------------------------------------------------------------------------


def test_default_fps_is_30() -> None:
    from motion3d.pipeline import DEFAULT_FPS

    assert DEFAULT_FPS == 30


def test_hml22_bone_count() -> None:
    from motion3d.bpy_export import HML22_BONE_NAMES, HML22_PARENTS

    assert len(HML22_BONE_NAMES) == 22
    assert len(HML22_PARENTS) == 22
    assert HML22_PARENTS[0] == -1
    assert len(set(HML22_BONE_NAMES)) == 22


def test_hml22_aim_child_walks_the_spine_not_a_clavicle() -> None:
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
    from motion3d.bpy_export import HML22_NEUTRAL_AIM

    for absolute in (0, 1, 2, 4, 5):
        assert absolute not in HML22_NEUTRAL_AIM


def test_target_rest_bones_empty_feet_use_hinge_clamp() -> None:
    """Feet left absolute — hinge clamp + look-at at ball, no rest calibration."""
    from motion3d.bpy_export import HML22_MAX_SWING_DEG, HML22_NEUTRAL_AIM, HML22_TARGET_REST_BONES

    assert frozenset() == HML22_TARGET_REST_BONES
    assert 7 not in HML22_NEUTRAL_AIM and 8 not in HML22_NEUTRAL_AIM
    assert 7 not in HML22_MAX_SWING_DEG and 8 not in HML22_MAX_SWING_DEG


def test_sanitize_locomotion_joints_opens_acute_knees() -> None:
    from motion3d.bpy_export import HML22_MIN_KNEE_DEG, sanitize_locomotion_joints

    j = np.zeros((1, 22, 3), dtype=np.float64)
    j[0, 1] = (0.0, 1.0, 0.0)
    j[0, 4] = (0.0, 0.6, 0.0)
    j[0, 7] = (0.0, 0.85, 0.15)
    j[0, 10] = (0.0, 0.85, 0.25)  # ball_l — needed so foot-pitch clamp is defined
    out = sanitize_locomotion_joints(j, min_knee_deg=HML22_MIN_KNEE_DEG)
    v1 = out[0, 1] - out[0, 4]
    v2 = out[0, 7] - out[0, 4]
    cos = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
    ang = float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))
    assert ang >= HML22_MIN_KNEE_DEG - 0.5


def test_sanitize_locomotion_joints_clamps_pointe_foot() -> None:
    from motion3d.bpy_export import (
        HML22_MAX_FOOT_PITCH_DEG,
        HML22_MIN_FOOT_PITCH_DEG,
        sanitize_locomotion_joints,
    )

    j = np.zeros((1, 22, 3), dtype=np.float64)
    # Standing shin + ballet pointe (~-85° toes down).
    j[0, 4] = (0.0, 0.5, 0.0)
    j[0, 7] = (0.0, 0.1, 0.0)
    j[0, 10] = (0.0, 0.1 - 0.12, 0.01)  # almost straight down
    out = sanitize_locomotion_joints(j)
    v = out[0, 10] - out[0, 7]
    pitch = float(np.degrees(np.arctan2(v[1], max(float(np.linalg.norm(v[[0, 2]])), 1e-8))))
    assert pitch >= HML22_MIN_FOOT_PITCH_DEG - 0.5
    assert pitch <= HML22_MAX_FOOT_PITCH_DEG + 0.5
    # Interior ankle angle (knee←ankle→ball): pointe~180°, dorsiflex~0°, flat~90°.
    v1 = out[0, 4] - out[0, 7]
    v2 = out[0, 10] - out[0, 7]
    ang = float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)), -1.0, 1.0))))
    assert 70.0 - 0.5 <= ang <= 115.0 + 0.5


def test_sanitize_clamps_extreme_dorsiflex_and_elbow() -> None:
    from motion3d.bpy_export import sanitize_locomotion_joints

    j = np.zeros((1, 22, 3), dtype=np.float64)
    # Ankle folded toes-to-shin (~20° interior).
    j[0, 4] = (0.0, 0.5, 0.0)
    j[0, 7] = (0.0, 0.1, 0.0)
    j[0, 10] = (0.0, 0.25, 0.02)
    # Elbow collapsed.
    j[0, 16] = (0.2, 1.2, 0.0)
    j[0, 18] = (0.35, 1.0, 0.0)
    j[0, 20] = (0.22, 1.15, 0.0)
    out = sanitize_locomotion_joints(j)

    def hinge(a: int, b: int, c: int) -> float:
        v1 = out[0, a] - out[0, b]
        v2 = out[0, c] - out[0, b]
        return float(
            np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)), -1.0, 1.0)))
        )

    assert 70.0 - 0.5 <= hinge(4, 7, 10) <= 115.0 + 0.5
    assert 35.0 - 0.5 <= hinge(16, 18, 20) <= 170.0 + 0.5


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


@pytest.mark.parametrize("n", [1, 5, 20, 40, 80, 120])
def test_synthetic_joints_shape(n: int) -> None:
    joints = np.zeros((n, 22, 3), dtype=np.float32)
    joints[:, :, 1] = np.linspace(0, 1, 22, dtype=np.float32)
    assert joints.shape == (n, 22, 3)


def test_export_joints_glb_rejects_wrong_shape_early(tmp_path: Path) -> None:
    from motion3d.bpy_export import export_joints_glb

    bad = np.zeros((10, 21, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="22"):
        export_joints_glb(bad, tmp_path / "x.glb")


# ---------------------------------------------------------------------------
# pipeline / generator (mocked)
# ---------------------------------------------------------------------------


def test_motion_sample_dataclass() -> None:
    from motion3d.pipeline import DEFAULT_FPS, MotionSample

    s = MotionSample(
        prompt="walk",
        joints=np.zeros((10, 22, 3), np.float32),
        n_frames=10,
    )
    assert s.fps == DEFAULT_FPS
    assert s.n_frames == 10


def test_write_npz_keys(tmp_path: Path) -> None:
    from motion3d.generator import MotionGenerator
    from motion3d.pipeline import MotionSample

    gen = MotionGenerator.__new__(MotionGenerator)
    gen._model = "lite"
    sample = MotionSample(
        prompt="sit",
        joints=np.ones((4, 22, 3), np.float32),
        n_frames=4,
        rot6d=np.zeros((4, 22, 6), np.float32),
        transl=np.zeros((4, 3), np.float32),
    )
    out = gen._write_npz(tmp_path / "m.npz", sample, metadata={"quality": "fast"})
    data = np.load(out, allow_pickle=True)
    assert "joints" in data
    assert "rot6d" in data
    assert int(data["n_frames"]) == 4
    assert int(data["fps"]) == 30
    assert "hml263" not in data


def test_arm_neutral_applies_hang_vs_raise() -> None:
    from motion3d.bpy_export import arm_neutral_applies, filter_neutral_aim_for_clip

    hang = np.zeros((8, 22, 3), dtype=np.float64)
    # upperarm → lowerarm roughly down (-Y in Blender Z-up after convert: aim from
    # shoulder to elbow). Build Y-up hang: elbows below shoulders.
    hang[:, 16] = (0.2, 1.2, 0.0)  # upperarm_l
    hang[:, 18] = (0.25, 0.9, 0.0)  # lowerarm_l
    hang[:, 20] = (0.25, 0.6, 0.0)  # hand_l
    hang[:, 17] = (-0.2, 1.2, 0.0)
    hang[:, 19] = (-0.25, 0.9, 0.0)
    hang[:, 21] = (-0.25, 0.6, 0.0)
    hang[:, 13] = (0.1, 1.35, 0.0)
    hang[:, 14] = (-0.1, 1.35, 0.0)
    assert arm_neutral_applies(hang) is True

    raise_ = hang.copy()
    raise_[:, 16] = (0.2, 1.4, 0.0)
    raise_[:, 18] = (0.3, 1.9, 0.0)  # elbow high
    raise_[:, 20] = (0.35, 2.2, 0.0)
    raise_[:, 17] = (-0.2, 1.4, 0.0)
    raise_[:, 19] = (-0.3, 1.9, 0.0)
    raise_[:, 21] = (-0.35, 2.2, 0.0)
    assert arm_neutral_applies(raise_) is False
    filtered = filter_neutral_aim_for_clip(
        raise_,
        {"upperarm_l": (0.0, 0.0, -1.0), "spine_01": (0.0, 0.0, 1.0)},
        arm_neutral="auto",
    )
    assert "upperarm_l" not in filtered
    assert "spine_01" in filtered


def test_opt_in_constraints_are_off_by_default() -> None:
    from motion3d.bpy_export import sanitize_locomotion_joints

    j = np.zeros((8, 22, 3), dtype=np.float64)
    for i in range(8):
        j[i, 20] = (0.4, 1.0, 0.0)
        j[i, 21] = (-0.4, 1.0, 0.2)
        j[i, 7] = (0.1 + 0.05 * i, 0.05, 0.0)
    out = sanitize_locomotion_joints(j)
    assert np.allclose(out[:, 20], j[:, 20])
    assert np.allclose(out[:, 21], j[:, 21])
    assert not np.allclose(out[:, 7, 0], out[0, 7, 0])


def test_hands_together_and_plant_feet_constraints() -> None:
    from motion3d.bpy_export import HML22_HANDS_TOGETHER_M, sanitize_locomotion_joints

    j = np.zeros((8, 22, 3), dtype=np.float64)
    j[:, 7] = (0.1, 0.05, 0.0)
    j[:, 8] = (-0.1, 0.05, 0.0)
    for i in range(8):
        y = 1.0 + 0.15 * i
        # Intentionally crossed wrists + skating feet.
        j[i, 20] = (-0.12, y, 0.2)
        j[i, 21] = (0.12, y + 0.08, 0.15)
        j[i, 7, 0] = 0.1 + 0.02 * i
        j[i, 8, 0] = -0.1 - 0.02 * i
    j[:, 1] = (0.1, 0.9, 0.0)
    j[:, 2] = (-0.1, 0.9, 0.0)
    out = sanitize_locomotion_joints(j, hands_together_m=HML22_HANDS_TOGETHER_M, plant_feet=True)
    sep = np.linalg.norm(out[:, 20] - out[:, 21], axis=-1)
    assert float(sep.max()) <= HML22_HANDS_TOGETHER_M + 1e-6
    assert np.allclose(out[:, 20, 1], out[:, 21, 1])
    assert np.all(out[:, 20, 0] >= out[:, 21, 0] - 1e-6)  # no cross
    assert np.allclose(out[:, 7, [0, 2]], out[0, 7, [0, 2]])  # feet planted
    assert np.allclose(out[:, 8, [0, 2]], out[0, 8, [0, 2]])


def test_max_lean_clamps_torso_and_carries_arms() -> None:
    from motion3d.bpy_export import sanitize_locomotion_joints

    j = np.zeros((4, 22, 3), dtype=np.float64)
    j[:, 1] = (0.1, 0.9, 0.0)
    j[:, 2] = (-0.1, 0.9, 0.0)
    j[:, 0] = (0.0, 0.95, 0.0)
    # Torso folded ~60° forward, hands hanging off it.
    j[:, 9] = (0.0, 0.95 + 0.15, 0.26)
    j[:, 12] = (0.0, 0.95 + 0.25, 0.43)
    j[:, 20] = (0.15, 0.95, 0.6)
    j[:, 21] = (-0.15, 0.95, 0.6)
    out = sanitize_locomotion_joints(j, max_lean_deg=25.0)

    def tilt(frame: np.ndarray) -> float:
        v = frame[12] - frame[0]
        return float(np.degrees(np.arccos(v[1] / np.linalg.norm(v))))

    assert tilt(j[0]) > 50.0
    assert tilt(out[0]) == pytest.approx(25.0, abs=1e-6)
    # Arms ride the torso: neck-relative offsets are unchanged, hands rise.
    assert np.allclose(out[0, 20] - out[0, 12], out[0, 21] - out[0, 12] + np.array([0.3, 0.0, 0.0]))
    assert out[0, 20, 1] > j[0, 20, 1]
    assert np.allclose(out[:, 1], j[:, 1])  # lower body untouched


def test_plant_feet_tracks_pelvis_sway_not_world() -> None:
    """Stance is rigid under the pelvis, so a weight shift keeps the legs sane."""
    from motion3d.bpy_export import sanitize_locomotion_joints

    j = np.zeros((6, 22, 3), dtype=np.float64)
    for i in range(6):
        sway = 0.06 * i
        j[i, 0] = (sway, 0.95, 0.0)
        j[i, 1] = (sway + 0.1, 0.9, 0.0)
        j[i, 2] = (sway - 0.1, 0.9, 0.0)
        j[i, 7] = (0.1 + 0.03 * i, 0.05, 0.02 * i)  # shuffling stance
        j[i, 8] = (-0.1, 0.05, 0.0)
    out = sanitize_locomotion_joints(j, plant_feet=True)
    for foot in (7, 8):
        rel = out[:, foot, [0, 2]] - out[:, 0, [0, 2]]
        assert np.allclose(rel, rel[0], atol=1e-9)  # rigid under the pelvis
    assert not np.allclose(out[:, 7, 0], out[0, 7, 0])  # follows the sway


def test_parse_motion_spec_and_load_clip(tmp_path: Path) -> None:
    from motion3d.apply_rigged import MotionClip, load_clip_joints, parse_motion_spec

    with pytest.raises(ValueError, match="name=path"):
        parse_motion_spec("walk.npz")
    clip = parse_motion_spec("walk=/tmp/walk.npz")
    assert clip.name == "walk"
    assert clip.npz_path == Path("/tmp/walk.npz")

    joints = np.zeros((4, 22, 3), dtype=np.float32)
    npz = tmp_path / "run.npz"
    np.savez(npz, joints=joints, fps=30)
    loaded, fps = load_clip_joints(MotionClip(name="run", npz_path=npz))
    assert loaded.shape == (4, 22, 3)
    assert fps == 30
    loaded2, fps2 = load_clip_joints(MotionClip(name="j", joints=joints, fps=20))
    assert fps2 == 20
    assert loaded2.shape == (4, 22, 3)


def test_apply_motions_rejects_empty_and_duplicates() -> None:
    from motion3d.apply_rigged import MotionClip, apply_motions_to_rigged

    j = np.zeros((2, 22, 3), dtype=np.float64)
    with pytest.raises(ValueError, match="non-empty"):
        apply_motions_to_rigged([], "missing.glb", "out.glb")
    with pytest.raises(ValueError, match="duplicate"):
        apply_motions_to_rigged(
            [MotionClip(name="walk", joints=j), MotionClip(name="walk", joints=j)],
            "missing.glb",
            "out.glb",
        )


def test_apply_npzs_to_rigged_parses_specs(tmp_path: Path) -> None:
    from motion3d.apply_rigged import apply_npzs_to_rigged

    j = np.zeros((3, 22, 3), dtype=np.float32)
    a = tmp_path / "a.npz"
    b = tmp_path / "b.npz"
    np.savez(a, joints=j, fps=30)
    np.savez(b, joints=j, fps=30)
    with patch("motion3d.apply_rigged.apply_motions_to_rigged") as mock_pack:
        mock_pack.return_value = {"output": tmp_path / "o.glb", "clips": [], "active": "walk"}
        apply_npzs_to_rigged(
            [f"walk={a}", f"run={b}"],
            tmp_path / "rig.glb",
            tmp_path / "o.glb",
            in_place=True,
        )
        assert mock_pack.called
        clips = mock_pack.call_args.args[0]
        assert [c.name for c in clips] == ["walk", "run"]
        assert clips[0].npz_path == a
        assert clips[1].npz_path == b


def test_cli_pack_rigged_help() -> None:
    from motion3d.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["pack-rigged", "--help"])
    assert result.exit_code == 0
    assert "--motion" in result.output
    assert "name=path" in result.output


def test_resolve_neutral_targets_replaces_defaults() -> None:
    from motion3d.bpy_export import (
        HML22_ARM_NEUTRAL_INDICES,
        HML22_BONE_NAMES,
        HML22_NEUTRAL_AIM,
        resolve_neutral_targets,
    )

    assert resolve_neutral_targets(None) == HML22_NEUTRAL_AIM
    without_arms = {HML22_BONE_NAMES[i]: d for i, d in HML22_NEUTRAL_AIM.items() if i not in HML22_ARM_NEUTRAL_INDICES}
    resolved = resolve_neutral_targets(without_arms)
    # Merging over the defaults would silently keep the hang arms (scissor arms).
    assert not (set(resolved) & HML22_ARM_NEUTRAL_INDICES)
    assert 3 in resolved  # spine_01 kept


def test_reanchor_joints_to_transl() -> None:
    from motion3d.pipeline import _reanchor_joints_to_transl

    joints = np.zeros((4, 22, 3), np.float32)
    joints[:, 1, 1] = 0.5  # fixed relative offset from pelvis
    transl = np.zeros((4, 3), np.float32)
    transl[:, 1] = [0.0, 0.2, 0.5, 0.1]
    out = _reanchor_joints_to_transl(joints, transl)
    np.testing.assert_allclose(out[:, 0, :], transl)
    np.testing.assert_allclose(out[:, 1, 1], transl[:, 1] + 0.5)


def test_pipeline_infer_mock() -> None:
    from motion3d.pipeline import HYMotionPipeline

    pipe = HYMotionPipeline(device="cpu", model="lite")
    transl = np.zeros((1, 60, 3), np.float32)
    transl[0, :, 1] = np.linspace(0.0, 0.4, 60, dtype=np.float32)
    fake_out = {
        "keypoints3d": np.zeros((1, 60, 22, 3), np.float32),
        "rot6d": np.zeros((1, 60, 22, 6), np.float32),
        "transl": transl,
    }
    runtime = MagicMock()
    pipeline = MagicMock()
    pipeline.parameters.return_value = iter([MagicMock(device="cpu")])
    pipeline.encode_text.return_value = {
        "text_vec_raw": MagicMock(),
        "text_ctxt_raw": MagicMock(),
        "text_ctxt_raw_length": MagicMock(),
    }
    pipeline.generate.return_value = fake_out
    # encode_text path uses torch tensors — bypass with patch
    with patch.object(pipe, "load"), patch("motion3d.pipeline.torch.is_tensor", return_value=False):
        pipe._runtime = runtime
        pipe._loaded = True
        runtime.pipelines = [pipeline]
        samples = pipe.infer("a person walks", duration=2.0, seed=1)
    assert len(samples) == 1
    assert samples[0].joints.shape == (60, 22, 3)
    assert samples[0].fps == 30
    # pelvis tracks transl Y after re-anchor (keypoints3d alone had zero lift)
    assert float(samples[0].joints[-1, 0, 1] - samples[0].joints[0, 0, 1]) == pytest.approx(0.4, abs=1e-5)


# ---------------------------------------------------------------------------
# quality soft resolve
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["fast", "low", "medium", "high", "highest"])
def test_quality_engine_motion3d_tier(tier: str) -> None:
    from aigamekit_shared.quality import QualityEngine

    r = QualityEngine().resolve(tool="motion3d", quality=tier)
    assert "duration" in r.params
    assert float(r.params["duration"]) > 0
    assert "cfg_scale" in r.params
    assert "model" in r.params
    if tier == "highest":
        assert r.params["model"] == "full"


# ---------------------------------------------------------------------------
# CLI help / doctor smoke
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
    assert "--duration" in result.output or "duration" in result.output
    assert "--model" in result.output


def test_cli_serve_help() -> None:
    from motion3d.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["serve", "--help"])
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "cmd",
    ["generate", "doctor", "serve", "apply-rigged", "pack-rigged", "export-glb"],
)
def test_cli_subcommand_registered(cmd: str) -> None:
    from motion3d.cli import cli

    assert cmd in cli.commands


# ---------------------------------------------------------------------------
# vendor import smoke (hymotion)
# ---------------------------------------------------------------------------


def test_vendor_bootstrap_path() -> None:
    from motion3d.vendor_bootstrap import ensure_hymotion_on_path, hymotion_stats_dir

    root = ensure_hymotion_on_path()
    assert root.is_dir()
    stats = hymotion_stats_dir()
    assert (stats / "Mean.npy").is_file()
    assert (stats / "Std.npy").is_file()


def test_vendor_hymotion_import() -> None:
    from motion3d.vendor_bootstrap import ensure_hymotion_on_path

    ensure_hymotion_on_path()
    from hymotion.pipeline.motion_diffusion import MotionFlowMatching
    from hymotion.utils.t2m_runtime import T2MRuntime

    assert MotionFlowMatching is not None
    assert T2MRuntime is not None


def test_wooden_mesh_default_path() -> None:
    from motion3d.vendor_bootstrap import ensure_hymotion_on_path

    ensure_hymotion_on_path()
    from hymotion.pipeline.body_model import _default_wooden_mesh_path

    path = Path(_default_wooden_mesh_path())
    assert path.is_dir()
    assert (path / "faces.bin").is_file()


def test_t2mgpt_vendor_removed() -> None:
    vendor = Path(__file__).resolve().parents[1] / "src" / "motion3d" / "vendor"
    assert not (vendor / "t2mgpt").exists()


# ---------------------------------------------------------------------------
# pad to ≥100
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


@pytest.mark.parametrize("dur", [1.0, 2.0, 3.0, 4.0, 5.0, 6.5])
def test_duration_positive(dur: float) -> None:
    assert dur > 0


@pytest.mark.parametrize("fps", [30, 20, 24, 15, 12])
def test_fps_values(fps: int) -> None:
    from motion3d.pipeline import DEFAULT_FPS

    assert DEFAULT_FPS == 30
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


def test_worker_adapter_name() -> None:
    from motion3d.worker_serve_adapter import Adapter

    assert Adapter.name == "motion3d"


def test_parse_gpu_ids_helper() -> None:
    from motion3d.cli import _parse_gpu_ids

    assert _parse_gpu_ids(None) is None
    assert _parse_gpu_ids("0") == [0]
    assert _parse_gpu_ids("0,1") == [0, 1]


def test_shared_footprints_registered() -> None:
    from aigamekit_shared.lowvram import get_footprint

    lite = get_footprint("hy-motion-lite")
    full = get_footprint("hy-motion-full")
    assert lite.fp16_weights_gib < full.fp16_weights_gib
    assert full.fp16_weights_gib < 8.0  # staged DiT, not stacked Qwen


def test_backend_footprint_key_motion3d() -> None:
    from aigamekit_shared.cli_helpers import BACKEND_FOOTPRINT_KEYS

    assert BACKEND_FOOTPRINT_KEYS.get("motion3d") == "hy-motion-lite"
