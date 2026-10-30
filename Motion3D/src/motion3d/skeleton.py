"""SprongMotion-14M skeleton (22 joints, no fingers) and retarget constants.

The runtime exposes the skeleton itself (``joint_names``, ``joint_parents``,
``rest_offsets``); this module pins the v1.0 layout so NPZs are self-describing
and the bpy export works without a loaded model.

Topology (index: joint → parent)::

    0 pelvis ── 1 l_hip ── 4 l_knee ── 7 l_ankle ── 10 l_foot
             ├─ 2 r_hip ── 5 r_knee ── 8 r_ankle ── 11 r_foot
             ├─ 3 spine1 ── 6 spine2 ── 9 spine3 ── 12 neck ── 15 head
             │                                     ├─ 13 l_collar ── 16 l_shoulder ── 18 l_elbow ── 20 l_wrist
             │                                     └─ 14 r_collar ── 17 r_shoulder ── 19 r_elbow ── 21 r_wrist

It is the same 22-joint body as HumanML3D, so the index-based clamps (hinges,
splay, upper body) carry over unchanged. Rest pose is measured from the model:
arms hang straight down, feet flat, facing +Z (Y-up, metres).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

JOINT_COUNT = 22

SPRONG_JOINTS: tuple[str, ...] = (
    "pelvis",
    "l_hip",
    "r_hip",
    "spine1",
    "l_knee",
    "r_knee",
    "spine2",
    "l_ankle",
    "r_ankle",
    "spine3",
    "l_foot",
    "r_foot",
    "neck",
    "l_collar",
    "r_collar",
    "head",
    "l_shoulder",
    "r_shoulder",
    "l_elbow",
    "r_elbow",
    "l_wrist",
    "r_wrist",
)

SPRONG_PARENTS: tuple[int, ...] = (
    -1,
    0,
    0,
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    9,
    9,
    12,
    13,
    14,
    16,
    17,
    18,
    19,
)

# Joint each bone aims at (its tail). Derived from the parent table, not from a
# chain list: the three limbs leaving spine3 would otherwise overwrite each other.
AIM_CHILD: dict[int, int] = {
    0: 3,  # pelvis   → spine1
    3: 6,  # spine1   → spine2
    6: 9,  # spine2   → spine3
    9: 12,  # spine3   → neck
    12: 15,  # neck     → head
    1: 4,  # l_hip    → l_knee
    4: 7,  # l_knee   → l_ankle
    7: 10,  # l_ankle  → l_foot
    2: 5,  # r_hip    → r_knee
    5: 8,  # r_knee   → r_ankle
    8: 11,  # r_ankle  → r_foot
    13: 16,  # l_collar → l_shoulder
    16: 18,  # l_shoulder → l_elbow
    18: 20,  # l_elbow  → l_wrist
    14: 17,  # r_collar → r_shoulder
    17: 19,  # r_shoulder → r_elbow
    19: 21,  # r_elbow  → r_wrist
}

# Leaves (head, feet, hands) stay at rest and let the parent carry the motion:
# aiming them curls the toes ("clown shoes") and twists the hands.
LEAF_BONES: frozenset[int] = frozenset(i for i in range(JOINT_COUNT) if i not in AIM_CHILD)

# Reference rest offsets (parent-local, Y-up, metres) from SprongMotion-14M v1.0.
# The NPZ carries the clip's own ``offsets``; this table is the fallback/contract
# check (tests assert it matches what the runtime reports).
REFERENCE_OFFSETS: np.ndarray = np.array(
    [
        [0.0000, 0.0000, 0.0000],
        [0.0754, -0.0662, 0.0195],
        [-0.0846, -0.0662, 0.0195],
        [0.0026, 0.1317, 0.0000],
        [0.0000, -0.3928, 0.0000],
        [0.0000, -0.3893, 0.0000],
        [0.0000, 0.1431, 0.0000],
        [0.0000, -0.4291, 0.0000],
        [0.0000, -0.4230, 0.0000],
        [0.0000, 0.0572, 0.0000],
        [0.0000, 0.0000, 0.1373],
        [0.0000, 0.0000, 0.1430],
        [0.0000, 0.2188, 0.0000],
        [0.0122, 0.1330, 0.0309],
        [-0.0060, 0.1388, 0.0336],
        [0.0000, 0.0000, 0.1025],
        [0.0000, -0.1301, 0.0000],
        [0.0000, -0.1208, 0.0000],
        [0.0000, -0.2580, 0.0000],
        [0.0000, -0.2607, 0.0000],
        [0.0000, -0.2656, 0.0000],
        [0.0000, -0.2701, 0.0000],
    ],
    dtype=np.float64,
)

# Neutral direction per bone in armature space (Z-up, character facing -Y).
# SprongMotion's rest hangs the arms dead vertical; a game A-pose carries them
# ~15° out so they clear a chunky torso. Legs/pelvis/feet stay absolute (facing
# and foot contacts must not be re-aimed).
NEUTRAL_AIM: dict[int, tuple[float, float, float]] = {
    3: (0.0, 0.0, 1.0),  # spine1 → up
    6: (0.0, 0.0, 1.0),  # spine2 → up
    9: (0.0, 0.0, 1.0),  # spine3 → up
    12: (0.0, 0.0, 1.0),  # neck → up
    16: (0.26, 0.0, -0.97),  # l_shoulder → down, 15° out
    17: (-0.26, 0.0, -0.97),  # r_shoulder
    18: (0.26, 0.0, -0.97),  # l_elbow
    19: (-0.26, 0.0, -0.97),  # r_elbow
}

ARM_NEUTRAL_INDICES: frozenset[int] = frozenset({16, 17, 18, 19})
ARM_NEUTRAL_MAX_CORR_DEG = 50.0

LEG_SPLAY_DEG: dict[int, float] = {1: 6.0, 4: 6.0, 2: -6.0, 5: -6.0}

MAX_SWING_DEG: dict[int, float] = {1: 55.0, 2: 55.0, 4: 65.0, 5: 65.0}

MIN_KNEE_DEG = 120.0
MAX_KNEE_DEG = 172.0
MIN_FOOT_PITCH_DEG = -35.0
MAX_FOOT_PITCH_DEG = 8.0

KNEE_HINGES: tuple[tuple[int, int, int, float, float], ...] = (
    (1, 4, 7, MIN_KNEE_DEG, MAX_KNEE_DEG),
    (2, 5, 8, MIN_KNEE_DEG, MAX_KNEE_DEG),
)
ANKLE_HINGES: tuple[tuple[int, int, int, float, float], ...] = (
    (4, 7, 10, 70.0, 115.0),
    (5, 8, 11, 70.0, 115.0),
)
ELBOW_HINGES: tuple[tuple[int, int, int, float, float], ...] = (
    (16, 18, 20, 35.0, 170.0),
    (17, 19, 21, 35.0, 170.0),
)
HINGE_LIMITS: tuple[tuple[int, int, int, float, float], ...] = (
    *KNEE_HINGES,
    *ANKLE_HINGES,
    *ELBOW_HINGES,
)

HINGE_FOLLOWERS: dict[int, tuple[int, ...]] = {7: (10,), 8: (11,), 20: (), 21: ()}

HANDS_TOGETHER_M = 0.10

# Everything above the pelvis — moved as one body by the torso lean clamp.
UPPER_BODY_INDICES: tuple[int, ...] = (3, 6, 9, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21)

DEFAULT_FPS = 20
MIN_SECONDS = 0.5
MAX_SECONDS = 10.0

# Action name inside the intermediate source GLB (same for every clip: the
# retarget reads ``--source-track`` by name, and each pack bakes one source).
SOURCE_TRACK = "sprong_motion"


class SkeletonMismatchError(ValueError):
    """The model/NPZ skeleton is not the 22-joint SprongMotion body."""


def check_skeleton(names: list[str] | tuple[str, ...], parents: list[int] | tuple[int, ...]) -> None:
    """Validate a runtime/NPZ skeleton against the pinned layout.

    Raises:
        SkeletonMismatchError: Different joint count, names or parents — the
            retarget profile (``sprong22``) and every index-based clamp would
            silently target the wrong bones.
    """
    if len(names) != JOINT_COUNT or len(parents) != JOINT_COUNT:
        raise SkeletonMismatchError(
            f"esqueleto com {len(names)} joints (esperado {JOINT_COUNT}); modelo incompatível com motion3d"
        )
    if tuple(names) != SPRONG_JOINTS or tuple(parents) != SPRONG_PARENTS:
        raise SkeletonMismatchError(
            "esqueleto diferente do SprongMotion-22 esperado "
            f"(names[0:3]={list(names)[0:3]}, parents={list(parents)[0:5]}…); "
            "atualiza Motion3D + perfil sprong22 antes de usar este modelo"
        )


def rest_positions_yup(offsets: Any = None) -> np.ndarray:
    """World rest joint positions (Y-up, metres) from parent-local offsets."""
    offs = np.asarray(REFERENCE_OFFSETS if offsets is None else offsets, dtype=np.float64)
    if offs.shape != (JOINT_COUNT, 3):
        raise ValueError(f"offsets must be ({JOINT_COUNT}, 3); got {offs.shape}")
    out = np.zeros_like(offs)
    for i, parent in enumerate(SPRONG_PARENTS):
        out[i] = offs[i] if parent < 0 else out[parent] + offs[i]
    return out


def rest_directions_yup(offsets: Any = None) -> dict[int, np.ndarray]:
    """Unit rest direction of every bone that aims at a child (Y-up)."""
    rest = rest_positions_yup(offsets)
    directions: dict[int, np.ndarray] = {}
    for bone, child in AIM_CHILD.items():
        delta = rest[child] - rest[bone]
        norm = float(np.linalg.norm(delta))
        directions[bone] = delta / norm if norm > 1e-9 else np.array([0.0, 1.0, 0.0])
    return directions


def aim_directions(joints_yup: Any, *, offsets: Any = None) -> np.ndarray:
    """Per-frame unit directions ``(T, J, 3)`` from each bone to the joint it aims at.

    Degenerate frames (child exactly on top of the bone) fall back to the rest
    direction instead of a zero vector: callers use these as a look-at target,
    and a zero vector there silently freezes the bone.
    """
    joints = np.asarray(joints_yup, dtype=np.float64)
    if joints.ndim != 3 or joints.shape[1] != JOINT_COUNT or joints.shape[2] != 3:
        raise ValueError(f"joints must be (T, {JOINT_COUNT}, 3); got {joints.shape}")
    fallback = rest_directions_yup(offsets)
    dirs = np.zeros_like(joints)
    for bone, child in AIM_CHILD.items():
        delta = joints[:, child] - joints[:, bone]
        norm = np.linalg.norm(delta, axis=-1, keepdims=True)
        degenerate = norm[..., 0] < 1e-8
        dirs[:, bone] = delta / np.where(norm < 1e-8, 1.0, norm)
        if degenerate.any():
            dirs[degenerate, bone] = fallback[bone]
    return dirs


@dataclass(frozen=True)
class Skeleton:
    """Everything the export/retarget code needs to know about a source body."""

    name: str
    joints: tuple[str, ...]
    parents: tuple[int, ...]
    aim_child: dict[int, int] = field(default_factory=lambda: dict(AIM_CHILD))
    leaf_bones: frozenset[int] = field(default_factory=lambda: LEAF_BONES)
    neutral_aim: dict[int, tuple[float, float, float]] = field(default_factory=lambda: dict(NEUTRAL_AIM))
    arm_neutral_indices: frozenset[int] = field(default_factory=lambda: ARM_NEUTRAL_INDICES)
    leg_splay_deg: dict[int, float] = field(default_factory=lambda: dict(LEG_SPLAY_DEG))
    max_swing_deg: dict[int, float] = field(default_factory=lambda: dict(MAX_SWING_DEG))
    hinge_limits: tuple[tuple[int, int, int, float, float], ...] = HINGE_LIMITS
    hinge_followers: dict[int, tuple[int, ...]] = field(default_factory=lambda: dict(HINGE_FOLLOWERS))
    upper_body_indices: tuple[int, ...] = UPPER_BODY_INDICES

    @property
    def index(self) -> dict[str, int]:
        """``joint name → index``."""
        return {name: i for i, name in enumerate(self.joints)}

    def bone_name(self, index: int) -> str:
        return self.joints[index]


SPRONG = Skeleton(
    name="sprong22",
    joints=SPRONG_JOINTS,
    parents=SPRONG_PARENTS,
)

__all__ = [
    "AIM_CHILD",
    "ANKLE_HINGES",
    "ARM_NEUTRAL_INDICES",
    "ARM_NEUTRAL_MAX_CORR_DEG",
    "DEFAULT_FPS",
    "ELBOW_HINGES",
    "HANDS_TOGETHER_M",
    "HINGE_FOLLOWERS",
    "HINGE_LIMITS",
    "JOINT_COUNT",
    "KNEE_HINGES",
    "LEAF_BONES",
    "LEG_SPLAY_DEG",
    "MAX_FOOT_PITCH_DEG",
    "MAX_SECONDS",
    "MAX_SWING_DEG",
    "MIN_FOOT_PITCH_DEG",
    "NEUTRAL_AIM",
    "REFERENCE_OFFSETS",
    "SPRONG",
    "SPRONG_JOINTS",
    "SPRONG_PARENTS",
    "UPPER_BODY_INDICES",
    "Skeleton",
    "SkeletonMismatchError",
    "aim_directions",
    "check_skeleton",
    "rest_directions_yup",
    "rest_positions_yup",
]
