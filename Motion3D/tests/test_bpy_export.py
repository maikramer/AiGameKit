"""Y-up → source-GLB export: constraints, aim bake and the real bpy export."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest
from motion3d import bpy_export as be
from motion3d.skeleton import REFERENCE_OFFSETS, SPRONG_JOINTS, aim_directions

from .conftest import make_walk_joints


def read_glb_json(path: Path) -> dict:
    """Minimal GLB container parser (no bpy, no gltf-transform)."""
    data = path.read_bytes()
    magic, version, _length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67 and version == 2
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    assert chunk_type == 0x4E4F534A  # 'JSON'
    return json.loads(data[20 : 20 + chunk_length].decode("utf-8"))


class TestYupToBlender:
    def test_mapping(self) -> None:
        out = be.yup_to_blender(np.array([1.0, 2.0, 3.0]))
        assert np.allclose(out, [1.0, -3.0, 2.0])

    def test_keeps_height_on_z(self) -> None:
        out = be.yup_to_blender(np.array([[0.0, 1.5, 0.0], [0.0, 0.0, 1.0]]))
        assert np.allclose(out[:, 2], [1.5, 0.0])

    def test_handles_batches(self) -> None:
        joints = make_walk_joints(4)
        assert be.yup_to_blender(joints).shape == joints.shape


class TestAimDirections:
    def test_units(self, walk_joints: np.ndarray) -> None:
        dirs = be._aim_directions(walk_joints)
        assert np.allclose(dirs, aim_directions(walk_joints))


class TestNeutralAim:
    def test_defaults_when_none(self) -> None:
        targets = be.resolve_neutral_targets(None)
        assert targets[16] == be.NEUTRAL_AIM[16]

    def test_names_replace_the_defaults(self) -> None:
        targets = be.resolve_neutral_targets({"l_shoulder": (1.0, 0.0, 0.0)})
        assert targets == {SPRONG_JOINTS.index("l_shoulder"): (1.0, 0.0, 0.0)}

    def test_unknown_names_are_ignored(self) -> None:
        assert be.resolve_neutral_targets({"nope": (0.0, 0.0, 1.0)}) == {}

    def test_filter_off_drops_arms(self) -> None:
        base = {name: direction for name, direction in ((SPRONG_JOINTS[i], d) for i, d in be.NEUTRAL_AIM.items())}
        out = be.filter_neutral_aim_for_clip(make_walk_joints(), base, arm_neutral="off")
        assert "l_shoulder" not in out and "spine1" in out

    def test_filter_on_keeps_arms(self) -> None:
        base = {SPRONG_JOINTS[i]: d for i, d in be.NEUTRAL_AIM.items()}
        out = be.filter_neutral_aim_for_clip(make_walk_joints(), base, arm_neutral="on")
        assert "l_shoulder" in out

    def test_invalid_mode(self) -> None:
        with pytest.raises(ValueError, match="auto"):
            be.filter_neutral_aim_for_clip(make_walk_joints(), {}, arm_neutral="maybe")

    def test_walk_is_near_hang(self, walk_joints: np.ndarray) -> None:
        assert be.arm_neutral_applies(walk_joints) is True

    def test_raised_arms_skip_the_calibration(self) -> None:
        joints = make_walk_joints()
        # Lift both wrists and elbows above the shoulders (chop/reach pose).
        for side in ("l", "r"):
            elbow = SPRONG_JOINTS.index(f"{side}_elbow")
            wrist = SPRONG_JOINTS.index(f"{side}_wrist")
            shoulder = SPRONG_JOINTS.index(f"{side}_shoulder")
            joints[:, elbow, 1] = joints[:, shoulder, 1] + 0.3
            joints[:, wrist, 1] = joints[:, shoulder, 1] + 0.55
        assert be.arm_neutral_applies(joints) is False


class TestStabilizeFacing:
    def yaw_series(self, joints: np.ndarray) -> np.ndarray:
        across = (joints[:, 1] - joints[:, 2]) + (joints[:, 16] - joints[:, 17])
        return np.arctan2(across[:, 1], across[:, 0])

    def test_cancels_accumulated_yaw(self) -> None:
        joints = be.yup_to_blender(make_walk_joints(12))
        rotated = joints.copy()
        for frame in range(len(rotated)):
            angle = 0.08 * frame  # the walk curves while it travels
            cos, sin = np.cos(angle), np.sin(angle)
            pivot = rotated[frame, 0, :2]
            xy = rotated[frame, :, :2] - pivot
            rotated[frame, :, 0] = xy[:, 0] * cos - xy[:, 1] * sin + pivot[0]
            rotated[frame, :, 1] = xy[:, 0] * sin + xy[:, 1] * cos + pivot[1]
        assert np.ptp(self.yaw_series(rotated)) > 0.3
        stabilized = be.stabilize_facing_zup(rotated)
        assert np.ptp(self.yaw_series(stabilized)) < 1e-6

    def test_keeps_frame_zero(self) -> None:
        joints = be.yup_to_blender(make_walk_joints(6))
        assert np.allclose(be.stabilize_facing_zup(joints)[0], joints[0])


class TestSanitize:
    def test_rejects_wrong_shape(self) -> None:
        with pytest.raises(ValueError, match="22"):
            be.sanitize_locomotion_joints(np.zeros((4, 21, 3)))

    def test_opens_acute_knees(self) -> None:
        joints = np.zeros((1, 22, 3))
        joints[0, 1] = (0.0, 1.0, 0.0)
        joints[0, 4] = (0.0, 0.6, 0.0)
        joints[0, 7] = (0.0, 0.85, 0.15)
        joints[0, 10] = (0.0, 0.85, 0.25)
        out = be.sanitize_locomotion_joints(joints, min_knee_deg=be.MIN_KNEE_DEG)
        assert self.hinge(out[0], 1, 4, 7) >= be.MIN_KNEE_DEG - 0.5

    def test_clamps_pointe_foot(self) -> None:
        joints = np.zeros((1, 22, 3))
        joints[0, 4] = (0.0, 0.5, 0.0)
        joints[0, 7] = (0.0, 0.1, 0.0)
        joints[0, 10] = (0.0, -0.02, 0.01)
        out = be.sanitize_locomotion_joints(joints)
        v = out[0, 10] - out[0, 7]
        pitch = float(np.degrees(np.arctan2(v[1], max(float(np.linalg.norm(v[[0, 2]])), 1e-8))))
        assert be.MIN_FOOT_PITCH_DEG - 0.5 <= pitch <= be.MAX_FOOT_PITCH_DEG + 0.5
        assert 70.0 - 0.5 <= self.hinge(out[0], 4, 7, 10) <= 115.0 + 0.5

    def test_clamps_collapsed_elbow(self) -> None:
        joints = np.zeros((1, 22, 3))
        joints[0, 16] = (0.2, 1.2, 0.0)
        joints[0, 18] = (0.35, 1.0, 0.0)
        joints[0, 20] = (0.22, 1.15, 0.0)
        out = be.sanitize_locomotion_joints(joints)
        assert 35.0 - 0.5 <= self.hinge(out[0], 16, 18, 20) <= 170.0 + 0.5

    def test_max_lean_is_off_by_default(self) -> None:
        joints = make_walk_joints()
        folded = joints.copy()
        for index in be.UPPER_BODY_INDICES:
            folded[:, index, 2] += 0.6  # torso folded forward
        out = be.sanitize_locomotion_joints(folded)
        # The torso chain is not re-aimed unless the caller asks for it (the
        # limb hinge clamps may still adjust the arms/legs).
        assert np.allclose(self.torso_tilt(out), self.torso_tilt(folded))

    def test_max_lean_caps_the_torso(self) -> None:
        joints = make_walk_joints()
        folded = joints.copy()
        for index in be.UPPER_BODY_INDICES:
            folded[:, index, 2] += 0.6
        out = be.sanitize_locomotion_joints(folded, max_lean_deg=20.0)
        v = out[:, 12] - out[:, 0]
        tilt = np.degrees(np.arccos(np.clip(v[:, 1] / np.linalg.norm(v, axis=-1), -1.0, 1.0)))
        assert tilt.max() <= 20.0 + 0.5

    def test_hands_together_holds_the_wrists(self) -> None:
        joints = make_walk_joints()
        joints[:, 20, 0] -= 0.4
        joints[:, 21, 0] += 0.4
        out = be.sanitize_locomotion_joints(joints, hands_together_m=0.1)
        separation = np.linalg.norm(out[:, 20] - out[:, 21], axis=-1)
        assert separation.max() <= 0.1 + 1e-6

    def test_plant_feet_freezes_the_stance(self) -> None:
        joints = make_walk_joints(12)
        joints[:, [7, 8, 10, 11], 2] += np.arange(12)[:, None] * 0.05
        out = be.sanitize_locomotion_joints(joints, plant_feet=True)
        # Horizontal position relative to the pelvis is held from frame 0.
        assert np.allclose(out[:, 7, 2] - out[:, 0, 2], out[0, 7, 2] - out[0, 0, 2], atol=1e-6)

    @staticmethod
    def hinge(frame: np.ndarray, proximal: int, joint: int, distal: int) -> float:
        v1 = frame[proximal] - frame[joint]
        v2 = frame[distal] - frame[joint]
        cos = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
        return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))

    @staticmethod
    def torso_tilt(joints: np.ndarray) -> np.ndarray:
        """Pelvis→neck tilt off vertical, per frame (degrees)."""
        v = joints[:, 12] - joints[:, 0]
        return np.degrees(np.arccos(np.clip(v[:, 1] / np.linalg.norm(v, axis=-1), -1.0, 1.0)))


@pytest.fixture(scope="module")
def bpy_module() -> object:
    return pytest.importorskip("bpy", reason="bpy ausente (export GLB)")


class TestExportGlb:
    def test_rejects_wrong_shape_early(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="22"):
            be.export_joints_glb(np.zeros((10, 21, 3)), tmp_path / "x.glb")

    def test_writes_armature_and_action(self, tmp_path: Path, bpy_module: object) -> None:
        target = tmp_path / "walk_source.glb"
        be.export_joints_glb(
            make_walk_joints(8),
            target,
            fps=20,
            offsets=REFERENCE_OFFSETS,
        )
        document = read_glb_json(target)
        node_names = {node.get("name") for node in document.get("nodes", [])}
        assert {"pelvis", "spine1", "l_wrist", "r_foot", "root"} <= node_names
        animations = document.get("animations", [])
        assert len(animations) == 1
        assert animations[0].get("name") == "sprong_motion"
        assert len(animations[0]["channels"]) > 22  # rotation per bone + pelvis location

    def test_bakes_the_requested_length(self, tmp_path: Path, bpy_module: object) -> None:
        target = tmp_path / "short.glb"
        be.export_joints_glb(make_walk_joints(4), target, fps=20)
        document = read_glb_json(target)
        animation = document["animations"][0]
        counts = {document["accessors"][sampler["input"]]["count"] for sampler in animation["samplers"]}
        # The exporter collapses constant channels to 2 keys; the animated ones
        # must keep one key per baked frame.
        assert max(counts) == 4
        assert counts <= {2, 4}
