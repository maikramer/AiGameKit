"""Fixtures partilhadas — tudo CPU-first; modelo e bpy são gates opcionais."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from motion3d.skeleton import REFERENCE_OFFSETS, SPRONG_JOINTS, rest_positions_yup


def make_walk_joints(frames: int = 8, stride: float = 0.12, height: float = 0.93) -> np.ndarray:
    """Upright synthetic walk: rigid leg/arm swings, small vertical bob.

    Good enough for the look-at bake and the hinge/lean clamps (it is a real
    22-joint body in a plausible pose, not random noise).
    """
    rest = rest_positions_yup() + np.array([0.0, height, 0.0])
    out = np.repeat(rest[None, :, :], frames, axis=0).astype(np.float64)
    legs = {
        "l": (1, 4, 7, 10),
        "r": (2, 5, 8, 11),
    }
    arms = {
        "l": (13, 16, 18, 20),
        "r": (14, 17, 19, 21),
    }
    for t in range(frames):
        phase = 2.0 * np.pi * t / frames
        swing = stride * float(np.sin(phase))
        for chain, delta in ((legs["l"], swing), (legs["r"], -swing)):
            for joint in chain[1:]:
                out[t, joint, 2] += delta
        # Slight knee/elbow bend: a dead-straight limb (180 deg interior) trips
        # the hinge clamps, which would move joints the tests expect untouched.
        for side in ("l", "r"):
            for joint in (7, 10):
                out[t, joint, 2] += 0.08
            out[t, 20 if side == "l" else 21, 2] += 0.06
        arm_delta = -0.6 * swing
        for chain, delta in ((arms["l"], arm_delta), (arms["r"], -arm_delta)):
            for joint in chain[2:]:
                out[t, joint, 2] += delta
        bob = 0.01 * float(np.cos(2.0 * phase))
        out[t, :, 1] += bob
    return out


def make_motion_npz(
    path: Path,
    *,
    frames: int = 8,
    fps: int = 20,
    prompt: str = "a person walks forward",
    offsets: np.ndarray | None = None,
    include_offsets: bool = True,
    include_skeleton: bool = True,
) -> Path:
    """Write a minimal valid motion NPZ (skeleton offsets + all optional keys)."""
    joints = make_walk_joints(frames)
    payload = {
        "joints": joints.astype(np.float32),
        "fps": np.int32(fps),
        "prompt": np.array(prompt),
        "seed": np.int32(3),
        "seconds": np.float32(frames / fps),
    }
    if include_offsets:
        payload["offsets"] = np.asarray(REFERENCE_OFFSETS if offsets is None else offsets, dtype=np.float32)
    if include_skeleton:
        payload["skeleton"] = np.array("sprong22")
        payload["joint_names"] = np.array(SPRONG_JOINTS, dtype="<U16")
        payload["joint_parents"] = np.array([-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19])
    np.savez_compressed(path, **payload)
    return path


@pytest.fixture
def walk_joints() -> np.ndarray:
    return make_walk_joints()


@pytest.fixture(scope="session")
def sprong_model() -> Path:
    """GGUF SprongMotion (env ``SPRONG_MODEL`` ou cache HF) — skip quando ausente."""
    from motion3d.weights import cached_model_path

    path = cached_model_path()
    if path is None:
        pytest.skip("GGUF SprongMotion ausente (define SPRONG_MODEL ou corre `motion3d download-model`)")
    return path
