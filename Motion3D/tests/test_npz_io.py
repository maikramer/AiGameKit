"""NPZ contract: write/read, validation and legacy rejection."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from motion3d.npz_io import (
    FORMAT_VERSION,
    LegacyNpzError,
    MotionData,
    read_clip,
    write_clip,
)
from motion3d.skeleton import REFERENCE_OFFSETS, SPRONG_JOINTS, SPRONG_PARENTS, SkeletonMismatchError

from .conftest import make_motion_npz, make_walk_joints


class TestWriteRead:
    def test_roundtrip_keeps_arrays(self, tmp_path: Path) -> None:
        joints = make_walk_joints(10)
        path = write_clip(
            tmp_path / "clip.npz",
            joints=joints,
            offsets=REFERENCE_OFFSETS,
            fps=20,
            prompt="a person walks forward",
            model="SprongMotion-14M@deadbeef",
            seed=7,
            seconds=0.5,
            root=np.zeros((10, 3)),
            contacts=np.ones((10, 4), dtype=np.uint8),
            codes=np.zeros((5, 6), dtype=np.int16),
        )
        motion = read_clip(path)
        assert motion.joints.shape == (10, 22, 3)
        assert np.allclose(motion.joints, joints, atol=1e-5)
        assert motion.fps == 20
        assert motion.prompt == "a person walks forward"
        assert motion.model == "SprongMotion-14M@deadbeef"
        assert motion.seed == 7
        assert motion.seconds == pytest.approx(0.5)
        assert motion.joint_names == SPRONG_JOINTS
        assert motion.joint_parents == SPRONG_PARENTS
        assert motion.contacts is not None and motion.contacts.shape == (10, 4)
        assert motion.codes is not None and motion.codes.shape == (5, 6)

    def test_adds_npz_suffix(self, tmp_path: Path) -> None:
        path = write_clip(
            tmp_path / "noext",
            joints=make_walk_joints(4),
            offsets=REFERENCE_OFFSETS,
            fps=20,
            prompt="x",
            model="m",
            seed=0,
            seconds=0.2,
        )
        assert path.suffix == ".npz" and path.is_file()

    def test_quality_keys_roundtrip(self, tmp_path: Path) -> None:
        path = write_clip(
            tmp_path / "q.npz",
            joints=make_walk_joints(4),
            offsets=REFERENCE_OFFSETS,
            fps=20,
            prompt="x",
            model="m",
            seed=1,
            seconds=0.2,
            quality={"score": 0.25, "foot_slide": 0.01},
        )
        motion = read_clip(path)
        assert motion.quality["score"] == pytest.approx(0.25)
        assert motion.quality["foot_slide"] == pytest.approx(0.01)

    def test_extra_scalar_metadata_is_stored(self, tmp_path: Path) -> None:
        path = write_clip(
            tmp_path / "meta.npz",
            joints=make_walk_joints(4),
            offsets=REFERENCE_OFFSETS,
            fps=20,
            prompt="x",
            model="m",
            seed=0,
            seconds=0.2,
            extra={"quality_tier": "medium", "nested": {"ignored": 1}},
        )
        with np.load(path, allow_pickle=False) as data:
            assert str(data["quality_tier"].item()) == "medium"
            assert "nested" not in data

    def test_format_version_is_written(self, tmp_path: Path) -> None:
        path = write_clip(
            tmp_path / "v.npz",
            joints=make_walk_joints(4),
            offsets=REFERENCE_OFFSETS,
            fps=20,
            prompt="x",
            model="m",
            seed=0,
            seconds=0.2,
        )
        with np.load(path, allow_pickle=False) as data:
            assert int(data["format_version"]) == FORMAT_VERSION

    def test_resolved_path_is_absolute(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        path = write_clip(
            "rel.npz",
            joints=make_walk_joints(4),
            offsets=REFERENCE_OFFSETS,
            fps=20,
            prompt="x",
            model="m",
            seed=0,
            seconds=0.2,
        )
        assert path.is_absolute()


class TestReadValidation:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_clip(tmp_path / "nope.npz")

    def test_missing_joints(self, tmp_path: Path) -> None:
        np.savez_compressed(tmp_path / "x.npz", fps=np.int32(20))
        with pytest.raises(ValueError, match="joints"):
            read_clip(tmp_path / "x.npz")

    def test_legacy_hml22_is_rejected_with_instructions(self, tmp_path: Path) -> None:
        np.savez_compressed(
            tmp_path / "old.npz",
            joints=np.zeros((5, 22, 3), dtype=np.float32),
            fps=np.int32(30),
            model=np.array("lite"),
        )
        with pytest.raises(LegacyNpzError, match="motion3d generate"):
            read_clip(tmp_path / "old.npz")

    def test_wrong_joint_count(self, tmp_path: Path) -> None:
        np.savez_compressed(
            tmp_path / "w.npz",
            joints=np.zeros((5, 21, 3), dtype=np.float32),
            offsets=REFERENCE_OFFSETS.astype(np.float32),
            fps=np.int32(20),
        )
        with pytest.raises(ValueError, match="joints"):
            read_clip(tmp_path / "w.npz")

    def test_nan_joints(self, tmp_path: Path) -> None:
        joints = make_walk_joints(4)
        joints[1, 2, 0] = np.nan
        path = make_motion_npz(tmp_path / "nan.npz", frames=4)
        with np.load(path, allow_pickle=False) as data:
            payload = {key: data[key] for key in data}
        payload["joints"] = joints.astype(np.float32)
        np.savez_compressed(path, **payload)
        with pytest.raises(ValueError, match="NaN"):
            read_clip(path)

    def test_bad_fps(self, tmp_path: Path) -> None:
        path = make_motion_npz(tmp_path / "fps.npz", frames=4)
        with np.load(path, allow_pickle=False) as data:
            payload = {key: data[key] for key in data}
        payload["fps"] = np.int32(0)
        np.savez_compressed(path, **payload)
        with pytest.raises(ValueError, match="fps"):
            read_clip(path)

    def test_single_frame_is_rejected(self, tmp_path: Path) -> None:
        path = make_motion_npz(tmp_path / "one.npz", frames=1)
        with pytest.raises(ValueError, match="frames"):
            read_clip(path)

    def test_bad_offsets_shape(self, tmp_path: Path) -> None:
        path = make_motion_npz(tmp_path / "off.npz", frames=4, offsets=np.zeros((3, 3)))
        with pytest.raises(ValueError, match="offsets"):
            read_clip(path)

    def test_different_skeleton_names_rejected(self, tmp_path: Path) -> None:
        path = make_motion_npz(tmp_path / "skel.npz", frames=4)
        with np.load(path, allow_pickle=False) as data:
            payload = {key: data[key] for key in data}
        names = np.array(SPRONG_JOINTS, dtype="<U16")
        names[1] = "left_hip"
        payload["joint_names"] = names
        np.savez_compressed(path, **payload)
        with pytest.raises(SkeletonMismatchError):
            read_clip(path)

    def test_offsets_fallback_when_absent(self, tmp_path: Path) -> None:
        path = make_motion_npz(tmp_path / "noff.npz", frames=4, include_offsets=False)
        motion = read_clip(path)
        assert np.allclose(motion.offsets, REFERENCE_OFFSETS)

    def test_skeleton_key_alone_is_enough(self, tmp_path: Path) -> None:
        path = make_motion_npz(tmp_path / "skeleton.npz", frames=4, include_offsets=False)
        motion = read_clip(path)
        assert motion.joint_names == SPRONG_JOINTS

    def test_never_uses_pickle(self, tmp_path: Path) -> None:
        path = tmp_path / "obj.npz"
        np.savez_compressed(path, joints=np.array([{"a": 1}], dtype=object), offsets=REFERENCE_OFFSETS)
        with pytest.raises(ValueError):
            read_clip(path)


class TestMotionData:
    def test_frame_count_and_check(self, walk_joints: np.ndarray) -> None:
        motion = MotionData(joints=walk_joints, offsets=REFERENCE_OFFSETS.copy(), fps=20)
        motion.check()
        assert motion.frame_count == len(walk_joints)

    def test_check_reports_the_path(self, tmp_path: Path) -> None:
        bad = MotionData(joints=np.zeros((4, 21, 3)), offsets=REFERENCE_OFFSETS.copy(), fps=20, path=tmp_path / "x.npz")
        with pytest.raises(ValueError, match=r"x\.npz"):
            bad.check()

    def test_defaults_are_the_sprong_skeleton(self, walk_joints: np.ndarray) -> None:
        motion = MotionData(joints=walk_joints, offsets=REFERENCE_OFFSETS.copy())
        assert motion.fps == 20
        assert motion.joint_names == SPRONG_JOINTS
        assert motion.joint_parents == SPRONG_PARENTS
