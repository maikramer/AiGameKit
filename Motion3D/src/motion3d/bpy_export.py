"""Export SprongMotion joints → animated GLB source for Animator3D retarget.

Pipeline contract:
1. Joints enter **Y-up**, metres, facing +Z at t=0 (SprongMotion output).
2. Convert to Blender **Z-up** (``(x,y,z)_y → (x,-z,y)_z``) — same space as
   glTF-imported SkinTokens hero after the Blender importer.
3. Build a 22-bone armature named after the SprongMotion joints
   (``animator3d retarget --profile sprong22`` maps them to SkinTokens names).
4. Rest pose = the model's own reference skeleton (``REFERENCE_OFFSETS`` or the
   clip's ``offsets``), not walk frame 0.
5. Animation = look-at bone aims (child joint) + pelvis location; root static.

Location-only baking on a skinned mesh is wrong — that path is gone.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from .skeleton import (
    AIM_CHILD,
    ANKLE_HINGES,
    ARM_NEUTRAL_INDICES,
    ARM_NEUTRAL_MAX_CORR_DEG,
    DEFAULT_FPS,
    ELBOW_HINGES,
    HANDS_TOGETHER_M,
    HINGE_FOLLOWERS,
    KNEE_HINGES,
    LEAF_BONES,
    LEG_SPLAY_DEG,
    MAX_FOOT_PITCH_DEG,
    MAX_KNEE_DEG,
    MAX_SWING_DEG,
    MIN_FOOT_PITCH_DEG,
    MIN_KNEE_DEG,
    NEUTRAL_AIM,
    SPRONG_JOINTS,
    SPRONG_PARENTS,
    UPPER_BODY_INDICES,
    aim_directions,
    rest_positions_yup,
)

BONE_NAMES: tuple[str, ...] = SPRONG_JOINTS
"""Source armature bone names — the SprongMotion joint names themselves."""

PARENTS: tuple[int, ...] = SPRONG_PARENTS


def arm_neutral_applies(
    joints: np.ndarray,
    *,
    max_corr_deg: float = ARM_NEUTRAL_MAX_CORR_DEG,
) -> bool:
    """True when clip median arm aims are near soft hang (walk/idle).

    Raised-arm clips (chop, throw, reach) have medians far from hang; forcing
    ``NEUTRAL_AIM`` onto them rotates every frame ~90-150° and reads as
    broken bone mapping even when the retarget map is identity.
    """
    j = np.asarray(joints, dtype=np.float64)
    if j.ndim != 3 or j.shape[1] != 22:
        return True
    aims = _aim_directions(yup_to_blender(j))
    limit = math.radians(float(max_corr_deg))
    for i in ARM_NEUTRAL_INDICES:
        target = NEUTRAL_AIM.get(i)
        if target is None:
            continue
        median = np.median(aims[:, i], axis=0)
        norm = float(np.linalg.norm(median))
        if norm < 1e-6:
            continue
        med_u = median / norm
        tgt = np.asarray(target, dtype=np.float64)
        cos = float(np.clip(np.dot(med_u, tgt), -1.0, 1.0))
        if math.acos(cos) > limit:
            return False
    return True


def resolve_neutral_targets(
    neutral_aim: dict[str, tuple[float, float, float]] | None,
) -> dict[int, tuple[float, float, float]]:
    """Bone-index neutral targets. ``neutral_aim`` replaces the defaults entirely.

    Merging over :data:`NEUTRAL_AIM` meant a caller could never *drop* a
    calibration, so raised-arm clips kept the hang neutrals.
    """
    if neutral_aim is None:
        return dict(NEUTRAL_AIM)
    return {BONE_NAMES.index(bname): direction for bname, direction in neutral_aim.items() if bname in BONE_NAMES}


def filter_neutral_aim_for_clip(
    joints: np.ndarray,
    neutral_aim: dict[str, tuple[float, float, float]],
    *,
    arm_neutral: str = "auto",
) -> dict[str, tuple[float, float, float]]:
    """Drop arm hang neutrals when ``arm_neutral`` says so.

    Args:
        joints: Y-up joint positions (SprongMotion, 22 joints).
        neutral_aim: Name→direction map (usually ``NEUTRAL_AIM``).
        arm_neutral: ``auto`` (skip arms if median far from hang), ``on``, ``off``.
    """
    mode = (arm_neutral or "auto").strip().lower()
    if mode not in {"auto", "on", "off"}:
        raise ValueError(f"arm_neutral must be auto|on|off; got {arm_neutral!r}")
    out = dict(neutral_aim)
    apply_arms = True if mode == "on" else False if mode == "off" else arm_neutral_applies(joints)
    if not apply_arms:
        for i in ARM_NEUTRAL_INDICES:
            out.pop(BONE_NAMES[i], None)
    return out


def sanitize_locomotion_joints(
    joints: np.ndarray,
    *,
    min_knee_deg: float = MIN_KNEE_DEG,
    max_knee_deg: float = MAX_KNEE_DEG,
    min_foot_pitch_deg: float = MIN_FOOT_PITCH_DEG,
    max_foot_pitch_deg: float = MAX_FOOT_PITCH_DEG,
    hinge_limits: tuple[tuple[int, int, int, float, float], ...] | None = None,
    max_lean_deg: float | None = None,
    hands_together_m: float | None = None,
    plant_feet: bool = False,
) -> np.ndarray:
    """Physical-plausibility pass on Y-up HML joints, plus opt-in constraints.

    Always on (rig-safety, gesture-agnostic): hinge angle clamps on knees,
    ankles and elbows, plus a world foot-pitch guard so a jump apex pointe
    does not flip the sole. Soft safety net, not IK.

    Opt-in, requested by the caller (never guessed from the clip):

    Args:
        max_lean_deg: Cap how far the torso tilts off vertical, rotating the
            whole upper body (arms included) around the pelvis.
        hands_together_m: Hold both wrists within this distance, at the same
            height, uncrossed on the hip axis — any two-hand prop (axe, staff,
            greatsword) then rides the midpoint.
        plant_feet: Hold the stance rigid under the pelvis for stationary
            actions, so the feet do not shuffle.
    """
    j = np.asarray(joints, dtype=np.float64).copy()
    if j.ndim != 3 or j.shape[1] != 22:
        raise ValueError(f"joints must be (T, 22, 3); got {j.shape}")
    if hinge_limits is not None:
        _clamp_hinges_yup(j, list(hinge_limits))
        _clamp_foot_pitch_yup(
            j,
            min_pitch_deg=float(min_foot_pitch_deg),
            max_pitch_deg=float(max_foot_pitch_deg),
        )
    else:
        knees = [
            (prox, joint, dist, float(min_knee_deg), float(max_knee_deg)) for prox, joint, dist, _lo, _hi in KNEE_HINGES
        ]
        # Knee → world foot pitch → ankle (shin-relative wins) → elbow.
        _clamp_hinges_yup(j, knees)
        _clamp_foot_pitch_yup(
            j,
            min_pitch_deg=float(min_foot_pitch_deg),
            max_pitch_deg=float(max_foot_pitch_deg),
        )
        _clamp_hinges_yup(j, list(ANKLE_HINGES))
        _clamp_hinges_yup(j, list(ELBOW_HINGES))
    if max_lean_deg is not None:
        _clamp_torso_lean_yup(j, max_deg=float(max_lean_deg))
    if plant_feet:
        _plant_feet_yup(j)
    if hands_together_m is not None:
        _join_wrists_yup(j, max_sep_m=float(hands_together_m))
    return j


def _rodrigues(v: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    """Rotate vector ``v`` around unit ``axis`` by ``angle`` radians."""
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    return v * cos_a + np.cross(axis, v) * sin_a + axis * float(np.dot(axis, v)) * (1.0 - cos_a)


def _clamp_hinges_yup(
    joints: np.ndarray,
    limits: list[tuple[int, int, int, float, float]],
) -> None:
    """In-place: keep each hinge interior angle inside ``[min, max]``.

    Moves the distal joint around the hinge axis (Rodrigues). Followers of the
    distal joint (e.g. ball when the ankle moves from a knee correction) are
    translated by the same delta so the foot/hand chain stays rigid.
    """
    for prox, joint, dist, min_deg, max_deg in limits:
        min_rad = math.radians(float(min_deg))
        max_rad = math.radians(float(max_deg))
        if max_rad < min_rad:
            min_rad, max_rad = max_rad, min_rad
        followers = HINGE_FOLLOWERS.get(dist, ())
        for fi in range(joints.shape[0]):
            p = joints[fi, prox]
            h = joints[fi, joint]
            d = joints[fi, dist]
            v1 = p - h
            v2 = d - h
            n1 = float(np.linalg.norm(v1))
            n2 = float(np.linalg.norm(v2))
            if n1 < 1e-8 or n2 < 1e-8:
                continue
            cos = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
            ang = math.acos(cos)
            if min_rad <= ang <= max_rad:
                continue
            target = min(max(ang, min_rad), max_rad)
            axis = np.cross(v1, v2)
            axn = float(np.linalg.norm(axis))
            if axn < 1e-8:
                axis = np.cross(v1, np.array([0.0, 1.0, 0.0], dtype=np.float64))
                axn = float(np.linalg.norm(axis))
            if axn < 1e-8:
                axis = np.cross(v1, np.array([1.0, 0.0, 0.0], dtype=np.float64))
                axn = float(np.linalg.norm(axis))
            if axn < 1e-8:
                continue
            axis = axis / axn
            v2r = _rodrigues(v2 / n2, axis, target - ang) * n2
            new_d = h + v2r
            delta = new_d - d
            joints[fi, dist] = new_d
            for child in followers:
                joints[fi, child] = joints[fi, child] + delta


def _clamp_torso_lean_yup(joints: np.ndarray, *, max_deg: float) -> None:
    """In-place: cap pelvis→neck tilt off vertical, carrying the upper body.

    HY answers "swing down to waist height" by folding the spine, which reads as
    bending over to grab something. Rotating the whole upper chain back around
    the pelvis keeps the gesture but restores the stance; the arms travel with
    the torso so the reach stays attached to the shoulders. The fold usually
    sits in the upper spine, so the neck — not the chest — is the honest axis.
    """
    max_rad = math.radians(max(float(max_deg), 0.0))
    up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    for fi in range(joints.shape[0]):
        pelvis = joints[fi, 0]
        v = joints[fi, 12] - pelvis
        n = float(np.linalg.norm(v))
        if n < 1e-8:
            continue
        u = v / n
        tilt = math.acos(float(np.clip(np.dot(u, up), -1.0, 1.0)))
        if tilt <= max_rad:
            continue
        axis = np.cross(u, up)
        axn = float(np.linalg.norm(axis))
        if axn < 1e-8:
            continue
        axis = axis / axn
        delta = tilt - max_rad
        cos_d = math.cos(delta)
        sin_d = math.sin(delta)
        for joint_i in UPPER_BODY_INDICES:
            rel = joints[fi, joint_i] - pelvis
            joints[fi, joint_i] = pelvis + (
                rel * cos_d + np.cross(axis, rel) * sin_d + axis * float(np.dot(axis, rel)) * (1.0 - cos_d)
            )


def _body_lateral_yup(joints: np.ndarray) -> np.ndarray:
    """Per-frame horizontal hip axis (right hip → left hip), unit, Y-up.

    Constraints anchored on this axis survive ``stabilize_facing_zup`` and root
    motion, which world X/Z do not: both apply a per-frame rigid transform.
    """
    lat = joints[:, 1, :] - joints[:, 2, :]
    lat = lat.copy()
    lat[:, 1] = 0.0
    norm = np.linalg.norm(lat, axis=-1, keepdims=True)
    fallback = np.zeros_like(lat)
    fallback[:, 0] = 1.0
    return np.where(norm > 1e-6, lat / np.maximum(norm, 1e-12), fallback)


def _plant_feet_yup(joints: np.ndarray) -> None:
    """In-place: hold the stance rigid under the pelvis (keeps Y).

    Freezes each foot joint at its frame-0 horizontal offset from the pelvis,
    measured in the hip-yaw frame. Pinning world X/Z instead would fight the
    pelvis sway (~1.2 m of weight shift in a stationary swing) and splay the
    legs; with ``in_place`` the pelvis is zeroed anyway, so this lands as feet
    that truly do not move.
    """
    lat = _body_lateral_yup(joints)
    fwd = np.stack([-lat[:, 2], np.zeros(len(lat)), lat[:, 0]], axis=-1)
    pelvis = joints[:, 0, :]
    for joint_i in (7, 8, 10, 11):
        rel = joints[0, joint_i] - pelvis[0]
        along_lat = float(np.dot(rel, lat[0]))
        along_fwd = float(np.dot(rel, fwd[0]))
        world = pelvis + lat * along_lat + fwd * along_fwd
        joints[:, joint_i, 0] = world[:, 0]
        joints[:, joint_i, 2] = world[:, 2]


def _join_wrists_yup(
    joints: np.ndarray,
    *,
    max_sep_m: float = HANDS_TOGETHER_M,
) -> None:
    """In-place: hold wrists within ``max_sep_m``, same height, uncrossed.

    Keeps the original hand midpoint (so the gesture path survives); look-at
    then re-aims the forearms at the moved wrists. "Uncrossed" is judged on the
    hip axis, so it holds whatever way the body faces.
    """
    max_sep = max(float(max_sep_m), 1e-4)
    half = 0.5 * max_sep
    lat = _body_lateral_yup(joints)
    for fi in range(joints.shape[0]):
        left = joints[fi, 20]
        right = joints[fi, 21]
        mid = 0.5 * (left + right)
        mid_y = float(mid[1])
        delta = np.asarray(right - left, dtype=np.float64)
        delta[1] = 0.0  # grip width is lateral; shaft stacks height via shared Y
        sep_h = float(np.linalg.norm(delta))
        if sep_h < 1e-6:
            axis = -lat[fi]
            half_use = half
        else:
            axis = delta / sep_h
            # Axis must point away from the left side: left never crosses right.
            if float(np.dot(axis, lat[fi])) > 0.0:
                axis = -axis
            half_use = min(half, 0.5 * sep_h) if sep_h <= max_sep else half
        joints[fi, 20] = mid - axis * half_use
        joints[fi, 21] = mid + axis * half_use
        joints[fi, 20, 1] = mid_y
        joints[fi, 21, 1] = mid_y


def _clamp_foot_pitch_yup(
    joints: np.ndarray,
    *,
    min_pitch_deg: float,
    max_pitch_deg: float,
) -> None:
    """In-place: rebuild ball from ankle so foot pitch stays in ``[min, max]``."""
    min_p = math.radians(float(min_pitch_deg))
    max_p = math.radians(float(max_pitch_deg))
    up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    for an, ball in ((7, 10), (8, 11)):
        for fi in range(joints.shape[0]):
            ankle = joints[fi, an]
            v = joints[fi, ball] - ankle
            length = float(np.linalg.norm(v))
            if length < 1e-6:
                continue
            horiz = float(np.linalg.norm(v[[0, 2]]))
            pitch = math.atan2(float(v[1]), max(horiz, 1e-8))
            if min_p <= pitch <= max_p:
                continue
            pitch_c = min(max(pitch, min_p), max_p)
            if horiz < 1e-5:
                # Degenerate vertical foot — aim slightly forward (-Z in Y-up HML).
                yaw = np.array([0.0, 0.0, -1.0], dtype=np.float64)
            else:
                yaw = np.array([v[0], 0.0, v[2]], dtype=np.float64)
                yaw /= float(np.linalg.norm(yaw))
            dir_c = yaw * math.cos(pitch_c) + up * math.sin(pitch_c)
            joints[fi, ball] = ankle + dir_c * length


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


def _build_armature_zup(bpy: Any, rest_zup: np.ndarray, *, name: str = "Sprong22") -> Any:
    """Create the 22-bone source armature; rest bones follow the model skeleton."""
    aim_child = AIM_CHILD

    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = arm_data.edit_bones
    bones: list[Any] = []
    for i, bname in enumerate(BONE_NAMES):
        bone = edit_bones.new(bname)
        head = rest_zup[i]
        stub = head + np.array([0.0, 0.0, 0.05], dtype=np.float64)
        tip = rest_zup[aim_child[i]] if i in aim_child else stub
        if float(np.linalg.norm(tip - head)) < 1e-4:
            tip = head + np.array([0.0, 0.0, 0.05], dtype=np.float64)
        bone.head = (float(head[0]), float(head[1]), float(head[2]))
        bone.tail = (float(tip[0]), float(tip[1]), float(tip[2]))
        parent_i = PARENTS[i]
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
    return aim_directions(joints_zup)


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

    ``neutral_aim``, when given, **replaces** :data:`NEUTRAL_AIM` — callers
    that know the target rig (``apply_rigged``) pass its rest directions so the
    motion is calibrated against the rig the clip actually plays on. Merging
    instead of replacing made it impossible to *drop* a calibration: a raised-arm
    clip kept the hang neutrals and every forearm frame got rotated ~145°.
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

    aim_child = AIM_CHILD

    root0 = joints_zup[0, 0].copy()
    prev: dict[str, Any] = {}

    # Rest orientation + rest bone direction, both in ARMATURE space.
    # ``Bone.y_axis`` is bone-local — using it here aimed every bone 90° off.
    rest_quat = {bn: arm_obj.data.bones[bn].matrix_local.to_quaternion() for bn in BONE_NAMES}
    rest_dir = {
        bn: (arm_obj.data.bones[bn].tail_local - arm_obj.data.bones[bn].head_local).normalized() for bn in BONE_NAMES
    }

    # Animator3D copies bone directions in world space, so the neutral correction
    # has to be baked into the aim itself (not into the source rest pose).
    aim_dirs = _aim_directions(joints_zup)
    targets = resolve_neutral_targets(neutral_aim)
    aim_fix: dict[int, Any] = {}
    for i, target in targets.items():
        median = np.median(aim_dirs[:, i], axis=0)
        norm = float(np.linalg.norm(median))
        if norm > 1e-6:
            aim_fix[i] = Vector(tuple(median / norm)).rotation_difference(Vector(target))
    for i, splay in LEG_SPLAY_DEG.items():
        outward = Quaternion(Vector((0.0, 1.0, 0.0)), math.radians(-splay))
        aim_fix[i] = outward @ aim_fix[i] if i in aim_fix else outward

    for fi in range(n_frames):
        frame = fi + 1
        scene.frame_set(frame)
        for i, bname in enumerate(BONE_NAMES):
            pb = arm_obj.pose.bones.get(bname)
            if pb is None:
                continue
            head = joints_zup[fi, i]
            if i in LEAF_BONES:
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
                max_swing = MAX_SWING_DEG.get(i)
                if max_swing is not None and swing.angle > math.radians(max_swing):
                    swing = Quaternion(swing.axis, math.radians(max_swing))
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
    clip_name: str = "sprong_motion",
    offsets: np.ndarray | None = None,
    in_place: bool = True,
    neutral_aim: dict[str, tuple[float, float, float]] | None = None,
    max_lean_deg: float | None = None,
    hands_together_m: float | None = None,
    plant_feet: bool = False,
) -> Path:
    """Write an animated source GLB (SprongMotion skeleton) for Animator3D retarget.

    Args:
        joints: Joint positions in metres, Y-up, facing +Z at t=0.
        output: Destination ``.glb`` path.
        fps: Scene / bake FPS (default 20).
        clip_name: Action / clip name (retarget ``--source-track``).
        offsets: Parent-local rest skeleton ``(22, 3)`` (NPZ ``offsets``);
            defaults to :data:`motion3d.skeleton.REFERENCE_OFFSETS`.
        in_place: Strip horizontal travel + yaw drift (loopable game clip).
        neutral_aim: Per-bone neutral directions (armature space) overriding
            :data:`NEUTRAL_AIM` — usually the target rig's rest pose.
        max_lean_deg: Cap torso tilt off vertical (degrees).
        hands_together_m: Hold both wrists this close (two-hand prop grip).
        plant_feet: Hold the stance rigid under the pelvis (stationary actions).

    Returns:
        Resolved output path.
    """
    if joints.ndim != 3 or joints.shape[1] != 22 or joints.shape[2] != 3:
        raise ValueError(f"joints must be (T, 22, 3); got {joints.shape}")
    joints = sanitize_locomotion_joints(
        joints,
        max_lean_deg=max_lean_deg,
        hands_together_m=hands_together_m,
        plant_feet=plant_feet,
    )

    out = Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    joints_y = np.asarray(joints, dtype=np.float64)
    # The model's reference skeleton has the pelvis at the origin (feet at
    # -0.89 m). Lift it to where the body actually is: Animator3D scales the
    # transferred ``pelvis`` location by ``target_height / source_height``, and
    # a rest pelvis on the floor makes that ratio explode (~800x).
    rest_y = rest_positions_yup(offsets) + joints_y[0, 0]
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
