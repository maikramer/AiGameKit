"""Cheap, deterministic quality score for generated clips (multi-seed picking).

SprongMotion's own model card warns that results vary a lot between seeds. The
runtime is fast enough (~0.2 s per clip on a laptop CPU) that generating a few
seeds and keeping the cleanest one is the pragmatic default.

The score combines three mechanically observable defects — it is **not** a
perceptual judgement:

* ``foot_slide`` — planted feet must stay put in world space (foot_lock
  enforcement leaks on hard transitions);
* ``ground_penetration`` — how far the lowest foot goes below the clip's own
  stance plane (floor guesses are relative: no absolute ground in the data);
* ``jerk`` — third difference of the root trajectory (single-frame glitches,
  the "pirouette" failure mode).

Lower is better. Weights are dimensionless-ish ratios chosen so a typical clean
walk scores < 1 and a glitchy clip > 2; only the ranking between seeds matters.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .sprong import Clip

# Joint indices (see :mod:`motion3d.skeleton`).
L_ANKLE, R_ANKLE, L_FOOT, R_FOOT = 7, 8, 10, 11

_SLIDE_WEIGHT = 40.0
_PENETRATION_WEIGHT = 80.0
_JERK_WEIGHT = 4.0


@dataclass(frozen=True)
class ClipQuality:
    """Breakdown of :func:`score_clip` (all metres / metres per frame²)."""

    foot_slide: float
    ground_penetration: float
    jerk: float
    score: float

    def as_dict(self) -> dict[str, float]:
        return {
            "foot_slide": round(self.foot_slide, 5),
            "ground_penetration": round(self.ground_penetration, 5),
            "jerk": round(self.jerk, 5),
            "score": round(self.score, 4),
        }


def world_joints(clip: Clip) -> np.ndarray:
    """Joint positions with the root travel restored (works with ``in_place`` clips).

    With ``in_place=True`` the runtime strips horizontal travel from ``joints``
    but keeps it in ``root``; adding the difference back gives one trajectory
    that can be compared frame to frame regardless of that option.
    """
    joints = np.asarray(clip.joints, dtype=np.float64)
    root = np.asarray(clip.root, dtype=np.float64)
    return joints + (root - joints[:, 0, :])[:, None, :]


def foot_slide(clip: Clip) -> float:
    """Mean horizontal motion of a planted foot, across feet and contact frames."""
    joints = world_joints(clip)
    contacts = np.asarray(clip.contacts)
    if len(joints) < 2:
        return 0.0
    delta = np.linalg.norm(np.diff(joints[:, [L_ANKLE, L_FOOT, R_ANKLE, R_FOOT], :], axis=0)[..., [0, 2]], axis=-1)
    planted = contacts[:-1].astype(bool) & contacts[1:].astype(bool)
    if not planted.any():
        return 0.0
    return float(delta[planted].mean())


def ground_penetration(clip: Clip) -> float:
    """Depth the lowest foot sinks below the clip's own stance plane (p05)."""
    joints = world_joints(clip)
    lowest = joints[:, [L_ANKLE, L_FOOT, R_ANKLE, R_FOOT], 1].min(axis=1)
    if lowest.size == 0:
        return 0.0
    floor = float(np.percentile(lowest, 5))
    return float(max(0.0, floor - float(lowest.min())))


def jerk(clip: Clip) -> float:
    """Mean magnitude of the third difference of the pelvis trajectory."""
    root = np.asarray(clip.root, dtype=np.float64)
    if root.shape[0] < 4:
        return 0.0
    third = np.diff(root, n=3, axis=0)
    return float(np.linalg.norm(third, axis=-1).mean())


def score_clip(clip: Clip) -> ClipQuality:
    """Score one clip (lower is better); see module docstring for the weights."""
    slide = foot_slide(clip)
    penetration = ground_penetration(clip)
    shake = jerk(clip)
    score = _SLIDE_WEIGHT * slide + _PENETRATION_WEIGHT * penetration + _JERK_WEIGHT * shake
    return ClipQuality(
        foot_slide=slide,
        ground_penetration=penetration,
        jerk=shake,
        score=float(score),
    )


def best_clip(clips: list[Clip]) -> tuple[Clip, ClipQuality]:
    """Pick the lowest-scoring clip (first wins on ties — deterministic)."""
    if not clips:
        raise ValueError("clips cannot be empty")
    scored = [(score_clip(clip), index, clip) for index, clip in enumerate(clips)]
    quality, _, clip = min(scored, key=lambda item: (item[0].score, item[1]))
    return clip, quality
