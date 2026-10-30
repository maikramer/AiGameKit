"""Multi-seed picking: the mechanical score used by ``--attempts``."""

from __future__ import annotations

import numpy as np
import pytest
from motion3d.quality import (
    ClipQuality,
    best_clip,
    foot_slide,
    ground_penetration,
    jerk,
    score_clip,
    world_joints,
)
from motion3d.skeleton import REFERENCE_OFFSETS, SPRONG_JOINTS
from motion3d.sprong import Clip


def make_clip(
    joints: np.ndarray,
    *,
    root: np.ndarray | None = None,
    contacts: np.ndarray | None = None,
    fps: int = 20,
) -> Clip:
    joints = np.asarray(joints, dtype=np.float32)
    frames = joints.shape[0]
    pelvis = joints[:, 0, :].astype(np.float32)
    return Clip(
        prompt="test",
        joints=joints,
        rotations=np.zeros((frames, len(SPRONG_JOINTS), 4), dtype=np.float32),
        offsets=REFERENCE_OFFSETS.astype(np.float32),
        root=(pelvis.copy() if root is None else np.asarray(root, dtype=np.float32)),
        speed=np.zeros(frames, dtype=np.float32),
        contacts=(np.ones((frames, 4), dtype=np.uint8) if contacts is None else contacts),
        fps=fps,
        seed=0,
        model_id="test-model",
        token_count=0,
        path_error=-1.0,
    )


def static_feet(frames: int = 8) -> np.ndarray:
    rest = REFERENCE_OFFSETS.copy()
    joints = np.zeros((frames, 22, 3), dtype=np.float64)
    world = np.zeros((22, 3))
    for index, parent in enumerate([-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19]):
        world[index] = rest[index] if parent < 0 else world[parent] + rest[index]
    world[:, 1] += 1.0
    joints[:, :, :] = world[None]
    return joints


class TestWorldJoints:
    def test_root_motion_clip_is_unchanged(self) -> None:
        joints = static_feet()
        clip = make_clip(joints)
        assert np.allclose(world_joints(clip), joints, atol=1e-6)

    def test_in_place_clip_gets_the_travel_back(self) -> None:
        joints = static_feet(4)
        root = joints[:, 0, :].copy()
        root[:, 2] += np.arange(4) * 0.5  # 0.5 m per frame forward
        clip = make_clip(joints, root=root)
        restored = world_joints(clip)
        assert np.allclose(restored[:, :, 2] - joints[:, :, 2], np.arange(4)[:, None] * 0.5, atol=1e-6)
        # The pelvis itself is exactly the root again.
        assert np.allclose(restored[:, 0, :], root, atol=1e-6)


class TestFootSlide:
    def test_static_feet_score_zero(self) -> None:
        assert foot_slide(make_clip(static_feet())) == pytest.approx(0.0, abs=1e-6)

    def test_sliding_feet_score_positive(self) -> None:
        joints = static_feet()
        joints[:, 7, 2] += np.arange(len(joints)) * 0.1
        joints[:, 8, 2] += np.arange(len(joints)) * 0.1
        assert foot_slide(make_clip(joints)) > 0.05

    def test_no_contact_means_no_slide(self) -> None:
        joints = static_feet()
        joints[:, 7, 2] += np.arange(len(joints)) * 0.1
        contacts = np.zeros((len(joints), 4), dtype=np.uint8)
        assert foot_slide(make_clip(joints, contacts=contacts)) == pytest.approx(0.0)

    def test_single_frame_is_zero(self) -> None:
        assert foot_slide(make_clip(static_feet(1))) == pytest.approx(0.0)


class TestGroundPenetration:
    def test_flat_ground_is_zero(self) -> None:
        assert ground_penetration(make_clip(static_feet())) == pytest.approx(0.0)

    def test_dipping_foot_is_penalised(self) -> None:
        joints = static_feet()
        joints[2, 7, 1] -= 0.2
        # The floor is the clip's own p05 stance plane, so a single dipped frame
        # reads as a fraction of the dip, never the raw 0.2 m.
        depth = ground_penetration(make_clip(joints))
        assert 0.02 < depth < 0.2

    def test_empty_is_zero(self) -> None:
        assert ground_penetration(make_clip(static_feet(0))) == pytest.approx(0.0)


class TestJerk:
    def test_linear_root_has_no_jerk(self) -> None:
        joints = static_feet(8)
        root = joints[:, 0, :].copy()
        root[:, 2] = np.arange(8) * 0.1
        assert jerk(make_clip(joints, root=root)) == pytest.approx(0.0, abs=1e-6)

    def test_spike_has_jerk(self) -> None:
        joints = static_feet(8)
        root = joints[:, 0, :].copy()
        root[4, 2] += 0.5
        assert jerk(make_clip(joints, root=root)) > 0.01

    def test_short_clip_is_zero(self) -> None:
        assert jerk(make_clip(static_feet(3))) == pytest.approx(0.0)


class TestScore:
    def test_returns_breakdown(self) -> None:
        quality = score_clip(make_clip(static_feet()))
        assert isinstance(quality, ClipQuality)
        assert quality.score >= 0.0
        assert set(quality.as_dict()) == {"foot_slide", "ground_penetration", "jerk", "score"}

    def test_glitchy_clip_scores_worse(self) -> None:
        clean = make_clip(static_feet())
        joints = static_feet()
        joints[2, 7, 1] -= 0.3
        joints[:, 7, 2] += np.arange(len(joints)) * 0.2
        dirty = make_clip(joints)
        assert score_clip(dirty).score > score_clip(clean).score

    def test_as_dict_rounds(self) -> None:
        data = score_clip(make_clip(static_feet())).as_dict()
        assert all(isinstance(value, float) for value in data.values())


class TestBestClip:
    def test_picks_the_lowest_score(self) -> None:
        clean = make_clip(static_feet())
        joints = static_feet()
        joints[2, 7, 1] -= 0.3
        dirty = make_clip(joints)
        chosen, quality = best_clip([dirty, clean])
        assert chosen is clean
        assert quality.score == score_clip(clean).score

    def test_ties_keep_the_first(self) -> None:
        first = make_clip(static_feet())
        second = make_clip(static_feet())
        chosen, _ = best_clip([first, second])
        assert chosen is first

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            best_clip([])
