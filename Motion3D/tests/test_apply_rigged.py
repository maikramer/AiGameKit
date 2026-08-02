"""CPU tests for HML→SkinTokens bake helpers (no GPU, bpy only when available)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from click.testing import CliRunner


class TestYupToBlender:
    def test_axis_swap(self) -> None:
        from motion3d.bpy_export import yup_to_blender

        v = np.array([[1.0, 2.0, 3.0]])
        out = yup_to_blender(v)
        assert out.shape == (1, 3)
        np.testing.assert_allclose(out[0], [1.0, -3.0, 2.0])


class TestStabilizeFacing:
    def test_zeroes_yaw_drift(self) -> None:
        from motion3d.bpy_export import stabilize_facing_zup

        # Pelvis at origin; hips L/R drift in yaw over frames.
        j = np.zeros((4, 22, 3), dtype=np.float64)
        j[:, 1, 0] = 0.1  # thigh_l
        j[:, 2, 0] = -0.1  # thigh_r
        j[:, 16, 0] = 0.2  # upperarm_l
        j[:, 17, 0] = -0.2
        # Frame 2: rotate hips ~45° about Z
        ang = np.pi / 4
        c, s = np.cos(ang), np.sin(ang)
        for i in (1, 2, 16, 17):
            x, y = j[2, i, 0], j[2, i, 1]
            j[2, i, 0] = c * x - s * y
            j[2, i, 1] = s * x + c * y
        out = stabilize_facing_zup(j)
        across0 = (out[0, 1] - out[0, 2]) + (out[0, 16] - out[0, 17])
        across2 = (out[2, 1] - out[2, 2]) + (out[2, 16] - out[2, 17])
        yaw0 = np.arctan2(across0[1], across0[0])
        yaw2 = np.arctan2(across2[1], across2[0])
        assert abs(yaw2 - yaw0) < 1e-5


class TestCanonicalRest:
    def test_head_is_above_neck_and_arms_out(self) -> None:
        from motion3d.bpy_export import _canonical_rest_joints_yup

        ref = np.zeros((22, 3), dtype=np.float64)
        # Minimal non-zero bone lengths from parents
        for i in range(1, 22):
            ref[i] = ref[i - 1] + np.array([0.05, 0.05, 0.05])
        rest = _canonical_rest_joints_yup(ref)
        # Head (15) above neck (12) in Y-up
        assert rest[15, 1] > rest[12, 1]
        # Left arm chain extends +X from clavicle; right -X
        assert rest[16, 0] > rest[13, 0]
        assert rest[17, 0] < rest[14, 0]


class TestMergeNeutralAim:
    def test_defaults_include_soft_arms(self) -> None:
        from motion3d.apply_rigged import merge_neutral_aim

        n = merge_neutral_aim()
        assert "upperarm_l" in n
        lx, _, lz = n["upperarm_l"]
        assert lx > 0.2 and lx < 0.4
        assert lz < -0.9

    def test_foot_override_does_not_touch_arms(self) -> None:
        from motion3d.apply_rigged import merge_neutral_aim

        n = merge_neutral_aim({"foot_l": (0.0, -0.7, -0.7)})
        assert n["foot_l"] == (0.0, -0.7, -0.7)
        assert n["upperarm_l"][0] == pytest.approx(0.26, abs=0.02)


class TestApplyMotionValidation:
    def test_rejects_wrong_joint_shape(self, tmp_path: Path) -> None:
        from motion3d.apply_rigged import apply_motion_to_rigged

        bad = np.zeros((5, 21, 3))
        with pytest.raises(ValueError, match="22"):
            apply_motion_to_rigged(bad, tmp_path / "missing.glb", tmp_path / "out.glb")

    def test_rejects_missing_rigged(self, tmp_path: Path) -> None:
        from motion3d.apply_rigged import apply_motion_to_rigged

        joints = np.zeros((5, 22, 3))
        with pytest.raises(FileNotFoundError):
            apply_motion_to_rigged(joints, tmp_path / "nope.glb", tmp_path / "out.glb")

    def test_npz_missing_joints(self, tmp_path: Path) -> None:
        from motion3d.apply_rigged import apply_npz_to_rigged

        npz = tmp_path / "bad.npz"
        np.savez(npz, hml263=np.zeros((4, 263)))
        with pytest.raises(ValueError, match="joints"):
            apply_npz_to_rigged(npz, tmp_path / "r.glb", tmp_path / "o.glb")


class TestCliApplyRigged:
    def test_help_mentions_in_place(self) -> None:
        from motion3d.cli import cli

        result = CliRunner().invoke(cli, ["apply-rigged", "--help"])
        assert result.exit_code == 0
        assert "--in-place" in result.output or "in-place" in result.output
        assert "hml22" in result.output

    def test_export_glb_help(self) -> None:
        from motion3d.cli import cli

        result = CliRunner().invoke(cli, ["export-glb", "--help"])
        assert result.exit_code == 0
        assert "in-place" in result.output or "root-motion" in result.output


@pytest.mark.parametrize("cmd", ["generate", "doctor", "serve", "apply-rigged", "export-glb"])
def test_cli_has_bake_commands(cmd: str) -> None:
    from motion3d.cli import cli

    assert cmd in cli.commands


def test_hml22_profile_loads() -> None:
    pytest.importorskip("animator3d")
    from animator3d import retarget as rt

    profile = rt.load_profile("hml22")
    assert "pelvis" in profile.bone_map
    assert "foot_l" in profile.bone_map
    assert "root" not in profile.bone_map


def test_target_rest_aims_empty_without_armature(tmp_path: Path) -> None:
    from motion3d.apply_rigged import target_rest_aims

    fake_bpy = MagicMock()
    fake_bpy.data.objects = []
    with (
        patch("motion3d.apply_rigged._bpy", return_value=fake_bpy),
        patch("aigamekit_shared.bpy_mesh.import_gltf"),
    ):
        aims = target_rest_aims(tmp_path / "x.glb", {"foot_l": ["foot_l"]}, ["foot_l"])
    assert aims == {}
