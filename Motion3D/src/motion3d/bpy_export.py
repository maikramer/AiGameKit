"""Export HumanML3D joints → animated GLB source for Animator3D retarget.

Pipeline contract:
1. Joints enter as HumanML3D **Y-up** (``recover_from_ric``).
2. Convert to Blender **Z-up** (``(x,y,z)_y → (x,-z,y)_z``) — same space as
   glTF-imported SkinTokens hero after Blender importer.
3. Build a 22-bone armature with **SkinTokens / UE5 names** so
   ``animator3d retarget --profile hml22`` (identity map) works.
4. Rest pose = canonical T-pose from ``t2m_raw_offsets`` (not walk frame-0).
5. Animation = look-at bone aims (child joint) + pelvis location; root static.

Location-only baking on a skinned mesh is wrong — that path is gone.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

# SkinTokens / Quaternius naming — identity bone_map in Animator3D hml22 profile.
HML22_BONE_NAMES: tuple[str, ...] = (
    "pelvis",
    "thigh_l",
    "thigh_r",
    "spine_01",
    "calf_l",
    "calf_r",
    "spine_02",
    "foot_l",
    "foot_r",
    "spine_03",
    "ball_l",
    "ball_r",
    "neck_01",
    "clavicle_l",
    "clavicle_r",
    "Head",
    "upperarm_l",
    "upperarm_r",
    "lowerarm_l",
    "lowerarm_r",
    "hand_l",
    "hand_r",
)

# Parent index per joint (-1 = root). Matches t2m_kinematic_chain topology.
HML22_PARENTS: tuple[int, ...] = (
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

# Joint each bone aims at (its tail). Deriving this from ``t2m_kinematic_chain``
# is wrong: the three chains starting at spine_03 overwrite each other, so
# spine_03 ended up aiming at the left clavicle — chest, neck, head and left arm
# all collapsed sideways.
HML22_AIM_CHILD: dict[int, int] = {
    0: 3,  # pelvis   → spine_01
    3: 6,  # spine_01 → spine_02
    6: 9,  # spine_02 → spine_03
    9: 12,  # spine_03 → neck_01
    12: 15,  # neck_01  → Head
    1: 4,  # thigh_l  → calf_l
    4: 7,  # calf_l   → foot_l
    7: 10,  # foot_l   → ball_l
    2: 5,  # thigh_r  → calf_r
    5: 8,  # calf_r   → foot_r
    8: 11,  # foot_r   → ball_r
    13: 16,  # clavicle_l → upperarm_l
    16: 18,  # upperarm_l → lowerarm_l
    18: 20,  # lowerarm_l → hand_l
    14: 17,  # clavicle_r → upperarm_r
    17: 19,  # upperarm_r → lowerarm_r
    19: 21,  # lowerarm_r → hand_r
}

# Leaves have no joint to aim at (HML has no toe-tip / finger / skull-top joint).
# Extending the parent direction onto them curled the toes ("clown shoes"),
# twisted the hands and added a second nod to the head — leave them at rest and
# let the parent carry the motion.
HML22_LEAF_BONES = frozenset(i for i in range(22) if i not in HML22_AIM_CHILD)

# Where each of these bones should point when the motion is at its neutral, in
# armature space (Z-up, character facing -Y). SMPL/HML anatomy is systematically
# offset from a game rig: the neck→head segment leans ~28° forward (hero staring
# at the floor) and the arms hang dead vertical (arms glued to a chunky torso).
# The clip's median direction is rotated onto these targets, so the pose reads as
# a game A-pose while every frame keeps its motion relative to it. Legs, pelvis
# and feet are absent on purpose — facing and foot contacts must stay absolute.
HML22_NEUTRAL_AIM: dict[int, tuple[float, float, float]] = {
    3: (0.0, 0.0, 1.0),  # spine_01 → up
    6: (0.0, 0.0, 1.0),  # spine_02 → up
    9: (0.0, 0.0, 1.0),  # spine_03 → up
    12: (0.0, 0.0, 1.0),  # neck_01  → up
    16: (0.26, 0.0, -0.97),  # upperarm_l → down, 15° out
    17: (-0.26, 0.0, -0.97),  # upperarm_r
    18: (0.26, 0.0, -0.97),  # lowerarm_l
    19: (-0.26, 0.0, -0.97),  # lowerarm_r
    # T2M walks are asymmetric: the sampled clip toes the right foot out 29°
    # while the left one sits at 6°. Both feet get the same forward neutral.
    7: (0.0, -0.95, -0.31),  # foot_l → forward, toes 18° down
    8: (0.0, -0.95, -0.31),  # foot_r
}

# Only these neutrals come from the *target* rig's rest. Arms stay on the soft
# A-pose above: SkinTokens rest is a full T-pose (~horizontal), and calibrating
# hanging T2M arms onto that made the hero walk with arms wide open again.
HML22_TARGET_REST_BONES = frozenset({7, 8})  # foot_l, foot_r

# Extra outward rotation (degrees towards ±X, about the armature forward axis)
# applied to the leg aim. The T2M sample walks a narrow catwalk line: the ankles
# cross by up to 8 cm on 17 of 116 frames, which a chibi rig with wide boots
# renders as the feet passing through each other. Widening the stance ~6° per leg
# clears the crossing without touching the gait timing or the ground contacts.
HML22_LEG_SPLAY_DEG: dict[int, float] = {
    1: 6.0,  # thigh_l
    4: 6.0,  # calf_l
    2: -6.0,  # thigh_r
    5: -6.0,  # calf_r
}

DEFAULT_FPS = 20


def yup_to_blender(v: np.ndarray) -> np.ndarray:
    """HumanML3D Y-up → Blender Z-up: ``(x, y, z) → (x, -z, y)``."""
    v = np.asarray(v, dtype=np.float64)
    return np.stack([v[..., 0], -v[..., 2], v[..., 1]], axis=-1)


def _bpy() -> Any:
    import bpy

    return bpy


def _clear_scene(bpy: Any) -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.actions, bpy.data.armatures, bpy.data.meshes):
        for item in list(block):
            block.remove(item)


def _canonical_rest_joints_yup(ref_joints: np.ndarray) -> np.ndarray:
    """T-pose joint positions in Y-up from ``t2m_raw_offsets`` scaled by bone lengths."""
    from motion3d.vendor.t2mgpt.utils.paramUtil import t2m_raw_offsets

    offsets = np.asarray(t2m_raw_offsets, dtype=np.float64).copy()
    # Raw offsets put the head *in front of* the neck and hang the arms straight
    # down. A retarget that transfers bone-local rotations then inherits a ~90°
    # compensation on neck/head and on every arm bone (collapsed head + arms).
    # Rebuild those limbs as a real T-pose so source rest ≈ target rest.
    offsets[15] = (0.0, 1.0, 0.0)  # neck → head: up
    for i in (16, 18, 20):
        offsets[i] = (1.0, 0.0, 0.0)  # left arm: +X
    for i in (17, 19, 21):
        offsets[i] = (-1.0, 0.0, 0.0)  # right arm: -X
    parents = HML22_PARENTS
    lengths = np.ones(22, dtype=np.float64)
    for i in range(1, 22):
        p = parents[i]
        lengths[i] = float(np.linalg.norm(ref_joints[i] - ref_joints[p])) or 1e-3
    rest = np.zeros((22, 3), dtype=np.float64)
    for i in range(1, 22):
        p = parents[i]
        d = offsets[i]
        n = float(np.linalg.norm(d)) or 1.0
        rest[i] = rest[p] + (d / n) * lengths[i]
    rest[:, 1] += float(ref_joints[0, 1])
    return rest


def stabilize_facing_zup(joints_zup: np.ndarray) -> np.ndarray:
    """Cancel yaw drift so an in-place clip keeps a constant facing.

    T2M walks curve (the sample drifts ~0.5 m sideways over 2.5 m forward). With
    the travel stripped for a loopable clip, that residual yaw makes the hero
    rotate on the spot. Rotate every frame about the pelvis Z axis so the
    hips/shoulders axis matches frame 0.
    """
    j = np.asarray(joints_zup, dtype=np.float64).copy()
    across = (j[:, 1] - j[:, 2]) + (j[:, 16] - j[:, 17])
    yaw = np.arctan2(across[:, 1], across[:, 0])
    delta = yaw - yaw[0]
    cos, sin = np.cos(-delta), np.sin(-delta)
    pivot = j[:, 0:1, :2].copy()
    xy = j[..., :2] - pivot
    x = xy[..., 0] * cos[:, None] - xy[..., 1] * sin[:, None]
    y = xy[..., 0] * sin[:, None] + xy[..., 1] * cos[:, None]
    j[..., 0] = x + pivot[..., 0]
    j[..., 1] = y + pivot[..., 1]
    return j


def _build_armature_zup(bpy: Any, rest_zup: np.ndarray, *, name: str = "HML22") -> Any:
    """Create SkinTokens-named armature; rest bones follow Z-up T-pose joints."""
    aim_child = HML22_AIM_CHILD

    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = arm_data.edit_bones
    bones: list[Any] = []
    for i, bname in enumerate(HML22_BONE_NAMES):
        bone = edit_bones.new(bname)
        head = rest_zup[i]
        stub = head + np.array([0.0, 0.0, 0.05], dtype=np.float64)
        tip = rest_zup[aim_child[i]] if i in aim_child else stub
        if float(np.linalg.norm(tip - head)) < 1e-4:
            tip = head + np.array([0.0, 0.0, 0.05], dtype=np.float64)
        bone.head = (float(head[0]), float(head[1]), float(head[2]))
        bone.tail = (float(tip[0]), float(tip[1]), float(tip[2]))
        parent_i = HML22_PARENTS[i]
        if parent_i >= 0:
            bone.parent = bones[parent_i]
            bone.use_connect = False
        bones.append(bone)

    # Static root at feet (Animator3D contract — never retarget root rotation).
    root = edit_bones.new("root")
    root.head = (0.0, 0.0, 0.0)
    root.tail = (0.0, 0.0, 0.05)
    pelvis_eb = edit_bones["pelvis"]
    pelvis_eb.parent = root
    pelvis_eb.use_connect = False

    bpy.ops.object.mode_set(mode="OBJECT")
    return arm_obj


def _aim_directions(joints_zup: np.ndarray) -> np.ndarray:
    """Unit direction from each bone's joint to the joint it aims at, per frame."""
    dirs = np.zeros((joints_zup.shape[0], 22, 3), dtype=np.float64)
    for i, child in HML22_AIM_CHILD.items():
        d = joints_zup[:, child] - joints_zup[:, i]
        n = np.linalg.norm(d, axis=-1, keepdims=True)
        n[n < 1e-8] = 1.0
        dirs[:, i] = d / n
    return dirs


def _lookat_keyframe(
    bpy: Any,
    arm_obj: Any,
    joints_zup: np.ndarray,
    *,
    fps: int,
    clip_name: str,
    in_place: bool = True,
    neutral_aim: dict[str, tuple[float, float, float]] | None = None,
) -> None:
    """Keyframe pose by aiming each bone at its child joint (armature Z-up).

    Orientation is **swing-only**: minimal arc from the bone's rest direction to
    the target direction, composed with the rest orientation. ``to_track_quat``
    picks its own roll from a world reference axis, so limbs came out twisted
    around their own axis once Animator3D copied that roll onto the hero.

    ``in_place`` keeps the horizontal pelvis travel out of the clip (loopable
    animation); vertical bob stays. Game code drives locomotion, not the clip.

    ``neutral_aim`` overrides :data:`HML22_NEUTRAL_AIM` per bone name — callers
    that know the target rig (``apply_rigged``) pass its rest directions so the
    motion is calibrated against the rig the clip actually plays on.
    """
    from mathutils import Quaternion, Vector

    scene = bpy.context.scene
    scene.render.fps = int(fps)
    scene.frame_start = 1
    n_frames = int(joints_zup.shape[0])
    scene.frame_end = n_frames

    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="POSE")

    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    for act in list(bpy.data.actions):
        bpy.data.actions.remove(act)
    action = bpy.data.actions.new(name=clip_name)
    arm_obj.animation_data.action = action

    for pb in arm_obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.matrix_basis.identity()

    aim_child = HML22_AIM_CHILD

    root0 = joints_zup[0, 0].copy()
    prev: dict[str, Any] = {}

    # Rest orientation + rest bone direction, both in ARMATURE space.
    # ``Bone.y_axis`` is bone-local — using it here aimed every bone 90° off.
    rest_quat = {bn: arm_obj.data.bones[bn].matrix_local.to_quaternion() for bn in HML22_BONE_NAMES}
    rest_dir = {
        bn: (arm_obj.data.bones[bn].tail_local - arm_obj.data.bones[bn].head_local).normalized()
        for bn in HML22_BONE_NAMES
    }

    # Animator3D copies bone directions in world space, so the neutral correction
    # has to be baked into the aim itself (not into the source rest pose).
    aim_dirs = _aim_directions(joints_zup)
    targets = dict(HML22_NEUTRAL_AIM)
    for bname, direction in (neutral_aim or {}).items():
        if bname in HML22_BONE_NAMES:
            targets[HML22_BONE_NAMES.index(bname)] = direction
    aim_fix: dict[int, Any] = {}
    for i, target in targets.items():
        median = np.median(aim_dirs[:, i], axis=0)
        norm = float(np.linalg.norm(median))
        if norm > 1e-6:
            aim_fix[i] = Vector(tuple(median / norm)).rotation_difference(Vector(target))
    for i, splay in HML22_LEG_SPLAY_DEG.items():
        outward = Quaternion(Vector((0.0, 1.0, 0.0)), math.radians(-splay))
        aim_fix[i] = outward @ aim_fix[i] if i in aim_fix else outward

    for fi in range(n_frames):
        frame = fi + 1
        scene.frame_set(frame)
        for i, bname in enumerate(HML22_BONE_NAMES):
            pb = arm_obj.pose.bones.get(bname)
            if pb is None:
                continue
            head = joints_zup[fi, i]
            if i in HML22_LEAF_BONES:
                q = Quaternion((1.0, 0.0, 0.0, 0.0))
            else:
                tip = joints_zup[fi, aim_child[i]]
                y = Vector((float(tip[0] - head[0]), float(tip[1] - head[1]), float(tip[2] - head[2])))
                if y.length < 1e-8:
                    y = rest_dir[bname].copy()
                else:
                    y.normalize()
                if i in aim_fix:
                    y = aim_fix[i] @ y
                # Swing-only: no arbitrary roll → no twisted limbs downstream.
                swing = rest_dir[bname].rotation_difference(y)
                mat = (swing @ rest_quat[bname]).to_matrix().to_4x4()
                mat.translation = Vector((float(head[0]), float(head[1]), float(head[2])))
                pb.matrix = mat
                bpy.context.view_layer.update()

                q = pb.rotation_quaternion.copy()
                q.normalize()
                if bname in prev:
                    q.make_compatible(prev[bname])
            prev[bname] = q.copy()

            pb.matrix_basis.identity()
            pb.rotation_quaternion = q
            if bname == "pelvis":
                dx = 0.0 if in_place else float(head[0] - root0[0])
                dy = 0.0 if in_place else float(head[1] - root0[1])
                delta = Vector((dx, dy, float(head[2] - root0[2])))
                rest_m3 = arm_obj.data.bones["pelvis"].matrix_local.to_3x3()
                loc = rest_m3.inverted() @ delta
                pb.location = (float(loc.x), float(loc.y), float(loc.z))
                pb.keyframe_insert(data_path="location", frame=frame)
            else:
                pb.location = (0.0, 0.0, 0.0)
            pb.keyframe_insert(data_path="rotation_quaternion", frame=frame)
            bpy.context.view_layer.update()

    bpy.ops.object.mode_set(mode="OBJECT")


def export_joints_glb(
    joints: np.ndarray,
    output: str | Path,
    *,
    fps: int = DEFAULT_FPS,
    clip_name: str = "t2m_motion",
    in_place: bool = True,
    neutral_aim: dict[str, tuple[float, float, float]] | None = None,
) -> Path:
    """Write an animated HML22 source GLB for Animator3D retarget.

    Args:
        joints: Joint positions in meters, Y-up (HumanML3D / recover_from_ric).
        output: Destination ``.glb`` path.
        fps: Scene / bake FPS (default 20).
        clip_name: Action / clip name (retarget ``--source-track``).
        in_place: Strip horizontal travel + yaw drift (loopable game clip).
        neutral_aim: Per-bone neutral directions (armature space) overriding
            :data:`HML22_NEUTRAL_AIM` — usually the target rig's rest pose.

    Returns:
        Resolved output path.
    """
    if joints.ndim != 3 or joints.shape[1] != 22 or joints.shape[2] != 3:
        raise ValueError(f"joints must be (T, 22, 3); got {joints.shape}")

    out = Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    joints_y = np.asarray(joints, dtype=np.float64)
    rest_y = _canonical_rest_joints_yup(joints_y[0])
    rest_z = yup_to_blender(rest_y)
    joints_z = yup_to_blender(joints_y)
    if in_place:
        joints_z = stabilize_facing_zup(joints_z)

    bpy = _bpy()
    _clear_scene(bpy)
    bpy.context.scene.render.fps = int(fps)
    arm = _build_armature_zup(bpy, rest_z)
    _lookat_keyframe(
        bpy,
        arm,
        joints_z,
        fps=fps,
        clip_name=clip_name,
        in_place=in_place,
        neutral_aim=neutral_aim,
    )

    from aigamekit_shared.bpy_mesh import save_glb

    save_glb(
        [arm],
        out,
        export_animations=True,
        export_skins=True,
        export_apply=False,
        export_normals=False,
        export_tangents=False,
        skip_verify=True,
    )
    return out
