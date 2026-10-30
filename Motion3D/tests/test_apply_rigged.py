"""NPZ → rigged GLB plumbing (no bpy): specs, clip loading and pack validation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from motion3d.apply_rigged import (
    DEFAULT_PROFILE,
    DEFAULT_SOURCE_TRACK,
    MotionClip,
    _coerce_clip,
    _load_npz_joints,
    apply_motions_to_rigged,
    load_clip_joints,
    parse_motion_spec,
)
from motion3d.npz_io import LegacyNpzError
from motion3d.skeleton import REFERENCE_OFFSETS

from .conftest import make_motion_npz, make_walk_joints


class TestParseMotionSpec:
    def test_simple(self) -> None:
        clip = parse_motion_spec("walk=/tmp/a.npz")
        assert clip.name == "walk"
        assert clip.npz_path == Path("/tmp/a.npz")

    def test_dashed_name(self) -> None:
        assert parse_motion_spec("run-fast=./r.npz").name == "run-fast"

    def test_underscored_name(self) -> None:
        assert parse_motion_spec("attack_01=a.npz").name == "attack_01"

    @pytest.mark.parametrize("spec", ["", "walk", "=a.npz", "1walk=a.npzp", "walk=", "na me=a.npz"])
    def test_invalid(self, spec: str) -> None:
        with pytest.raises(ValueError, match=r"name=path\.npz|missing path"):
            parse_motion_spec(spec)

    def test_defaults_are_the_sprong_profile(self) -> None:
        assert DEFAULT_PROFILE == "sprong22"
        assert DEFAULT_SOURCE_TRACK == "sprong_motion"


class TestLoadClipJoints:
    def test_from_arrays(self, walk_joints: np.ndarray) -> None:
        joints, fps, offsets = load_clip_joints(MotionClip(name="walk", joints=walk_joints, fps=20))
        assert joints.shape == walk_joints.shape
        assert fps == 20
        assert offsets is None

    def test_from_npz_reads_offsets(self, tmp_path: Path) -> None:
        path = make_motion_npz(tmp_path / "w.npz", frames=6)
        joints, fps, offsets = load_clip_joints(MotionClip(name="walk", npz_path=path))
        assert joints.shape == (6, 22, 3)
        assert fps == 20
        assert offsets is not None and offsets.shape == (22, 3)
        assert np.allclose(offsets, REFERENCE_OFFSETS)

    def test_clip_offsets_override_the_npz(self, tmp_path: Path) -> None:
        path = make_motion_npz(tmp_path / "w.npz", frames=4)
        custom = REFERENCE_OFFSETS * 2.0
        _, _, offsets = load_clip_joints(MotionClip(name="walk", npz_path=path, offsets=custom))
        assert np.allclose(offsets, custom)

    def test_clip_fps_overrides_the_npz(self, tmp_path: Path) -> None:
        path = make_motion_npz(tmp_path / "w.npz", frames=4)
        _, fps, _ = load_clip_joints(MotionClip(name="walk", npz_path=path, fps=30))
        assert fps == 30

    def test_both_sources_rejected(self, tmp_path: Path, walk_joints: np.ndarray) -> None:
        clip = MotionClip(name="walk", joints=walk_joints, npz_path=tmp_path / "x.npz")
        with pytest.raises(ValueError, match="not both"):
            load_clip_joints(clip)

    def test_neither_source_rejected(self) -> None:
        with pytest.raises(ValueError, match="need joints or npz_path"):
            load_clip_joints(MotionClip(name="walk"))

    def test_missing_npz(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_clip_joints(MotionClip(name="walk", npz_path=tmp_path / "gone.npz"))

    def test_nan_joints_rejected(self) -> None:
        joints = make_walk_joints(4)
        joints[0, 0, 0] = np.nan
        with pytest.raises(ValueError, match="NaN"):
            load_clip_joints(MotionClip(name="walk", joints=joints))


class TestNpzLoader:
    def test_returns_offsets(self, tmp_path: Path) -> None:
        path = make_motion_npz(tmp_path / "w.npz", frames=4)
        joints, fps, offsets = _load_npz_joints(path)
        assert joints.shape == (4, 22, 3) and fps == 20 and offsets is not None

    def test_legacy_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "old.npz"
        np.savez_compressed(path, joints=np.zeros((4, 22, 3), np.float32), fps=np.int32(30), model=np.array("lite"))
        with pytest.raises(LegacyNpzError):
            _load_npz_joints(path)


class TestCoerceClip:
    def test_passthrough(self, walk_joints: np.ndarray) -> None:
        clip = MotionClip(name="walk", joints=walk_joints)
        assert _coerce_clip(clip) is clip

    def test_tuple_with_array(self, walk_joints: np.ndarray) -> None:
        clip = _coerce_clip(("walk", walk_joints))
        assert clip.name == "walk" and clip.joints is not None

    def test_tuple_with_path(self, tmp_path: Path) -> None:
        clip = _coerce_clip(("walk", str(tmp_path / "a.npz")))
        assert clip.npz_path == tmp_path / "a.npz"

    def test_invalid_tuple(self) -> None:
        with pytest.raises(TypeError):
            _coerce_clip(("walk",))  # type: ignore[arg-type]


class TestPackValidation:
    def test_empty_pack(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            apply_motions_to_rigged([], tmp_path / "rig.glb", tmp_path / "out.glb")

    def test_duplicate_names(self, tmp_path: Path, walk_joints: np.ndarray) -> None:
        clips = [MotionClip(name="walk", joints=walk_joints), MotionClip(name="walk", joints=walk_joints)]
        with pytest.raises(ValueError, match="duplicate"):
            apply_motions_to_rigged(clips, tmp_path / "rig.glb", tmp_path / "out.glb")

    def test_missing_target_glb(self, tmp_path: Path, walk_joints: np.ndarray) -> None:
        with pytest.raises(FileNotFoundError):
            apply_motions_to_rigged(
                [MotionClip(name="walk", joints=walk_joints)],
                tmp_path / "nope.glb",
                tmp_path / "out.glb",
            )


class TestRetargetProfile:
    def test_sprong22_maps_the_whole_body(self) -> None:
        rt = pytest.importorskip("animator3d.retarget")
        profile = rt.load_profile("sprong22")
        expected = {
            "pelvis": "pelvis",
            "spine1": "spine_01",
            "spine2": "spine_02",
            "spine3": "spine_03",
            "neck": "neck_01",
            "head": "Head",
            "l_collar": "clavicle_l",
            "l_shoulder": "upperarm_l",
            "l_elbow": "lowerarm_l",
            "l_wrist": "hand_l",
            "l_hip": "thigh_l",
            "l_knee": "calf_l",
            "l_ankle": "foot_l",
            "l_foot": "ball_l",
        }
        for source, target in expected.items():
            assert profile.bone_map[source][0] == target
        assert "root" not in profile.bone_map

    def test_sprong22_clip_map_uses_the_source_track(self) -> None:
        rt = pytest.importorskip("animator3d.retarget")
        profile = rt.load_profile("sprong22")
        assert profile.clip_map["walk"] == DEFAULT_SOURCE_TRACK
