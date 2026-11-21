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
    in_place: bool = True,
) -> bool:
    """True when clip median arm aims are near soft hang (walk/idle).

    Raised-arm clips (chop, throw, reach) have medians far from hang; forcing
    ``NEUTRAL_AIM`` onto them rotates every frame ~90-150° and reads as
    broken bone mapping even when the retarget map is identity.

    The medians must be measured on the **same frame the bake uses**: the
    exporter stabilizes the facing yaw when ``in_place`` (game clips), so a
    clip that turns while gesturing can look near-hang in raw world space and
    far from it after stabilization (or the reverse). Deciding on the raw
    joints made ``apply-rigged`` neutralize arms the bake would have kept —
    the "arms inside the trunk" regression. Always pass the clip's real
    ``in_place`` value.
    """
    j = np.asarray(joints, dtype=np.float64)
    if j.ndim != 3 or j.shape[1] != 22:
        return True
    aims_zup = yup_to_blender(j)
    if in_place:
        aims_zup = stabilize_facing_zup(aims_zup)
    aims = _aim_directions(aims_zup)
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
    in_place: bool = True,
) -> dict[str, tuple[float, float, float]]:
    """Drop arm hang neutrals when ``arm_neutral`` says so.

    Args:
        joints: Y-up joint positions (SprongMotion, 22 joints).
        neutral_aim: Name→direction map (usually ``NEUTRAL_AIM``).
        arm_neutral: ``auto`` (skip arms if median far from hang), ``on``, ``off``.
        in_place: Must match the bake's ``in_place`` — the auto decision is
            measured on the stabilized (or not) joints, exactly like
            :func:`_lookat_keyframe` will sample them.
    """
    mode = (arm_neutral or "auto").strip().lower()
    if mode not in {"auto", "on", "off"}:
        raise ValueError(f"arm_neutral must be auto|on|off; got {arm_neutral!r}")
    out = dict(neutral_aim)
    apply_arms = True if mode == "on" else False if mode == "off" else arm_neutral_applies(joints, in_place=in_place)
    if not apply_arms:
        for i in ARM_NEUTRAL_INDICES:
            out.pop(BONE_NAMES[i], None)
    return out


def _bilateral_thigh_flexion(joints: np.ndarray, up_axis: int) -> np.ndarray:
    """Blend into seated/squat limits when both thighs approach horizontal."""
    thighs = joints[:, [4, 5]] - joints[:, [1, 2]]
    lengths = np.linalg.norm(thighs, axis=-1)
    down = np.divide(-thighs[..., up_axis], lengths, out=np.ones_like(lengths), where=lengths > 1e-8)
    # Use the more vertical leg: a single lifted foot is an ordinary stride.
    return np.clip((0.65 - down.max(axis=1)) / 0.45, 0.0, 1.0)


def sanitize_locomotion_joints(
    joints: np.ndarray,
    *,
    min_knee_deg: float | None = None,
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
        # A standing gait needs a conservative guard, but the same 120°
        # minimum straightens a seated rower's knees and pushes the feet out.
        knee_floor = (
            MIN_KNEE_DEG - 85.0 * _bilateral_thigh_flexion(j, 1) if min_knee_deg is None else float(min_knee_deg)
        )
        knees = [(prox, joint, dist, knee_floor, max_knee_deg) for prox, joint, dist, _, _ in KNEE_HINGES]
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


def _rodrigues_batch(v: np.ndarray, axis: np.ndarray, angle: np.ndarray) -> np.ndarray:
    """Vectorized Rodrigues rotation of ``v`` ``(N, 3)`` around unit ``axis`` ``(N, 3)`` by ``angle`` ``(N,)``."""
    cos_a = np.cos(angle)[:, None]
    sin_a = np.sin(angle)[:, None]
    dot = np.sum(axis * v, axis=-1, keepdims=True)
    return v * cos_a + np.cross(axis, v) * sin_a + axis * dot * (1.0 - cos_a)


def _clamp_hinges_yup(
    joints: np.ndarray,
    limits: list[tuple[int, int, int, float | np.ndarray, float]],
) -> None:
    """In-place: keep each hinge interior angle inside ``[min, max]``.

    Moves the distal joint around the hinge axis (Rodrigues). Followers of the
    distal joint (e.g. ball when the ankle moves from a knee correction) are
    translated by the same delta so the foot/hand chain stays rigid.
    """
    n_frames = joints.shape[0]
    for prox, joint, dist, min_deg, max_deg in limits:
        lower_raw = np.broadcast_to(np.deg2rad(min_deg), (n_frames,))
        upper_raw = math.radians(float(max_deg))
        min_rad = np.minimum(lower_raw, upper_raw)
        max_rad = np.maximum(lower_raw, upper_raw)
        followers = HINGE_FOLLOWERS.get(dist, ())

        p = joints[:, prox]
        h = joints[:, joint]
        d = joints[:, dist]
        v1 = p - h
        v2 = d - h
        n1 = np.linalg.norm(v1, axis=-1)
        n2 = np.linalg.norm(v2, axis=-1)
        valid = (n1 >= 1e-8) & (n2 >= 1e-8)
        if not valid.any():
            continue

        denom = np.where(valid, n1 * n2, 1.0)
        cos = np.clip(np.sum(v1 * v2, axis=-1) / denom, -1.0, 1.0)
        ang = np.arccos(cos)
        mask = valid & ((ang < min_rad) | (ang > max_rad))
        if not mask.any():
            continue

        idx = np.flatnonzero(mask)
        v1_m = v1[idx]
        v2_m = v2[idx]
        n2_m = n2[idx]
        ang_m = ang[idx]
        target_m = np.clip(ang_m, min_rad[idx], max_rad[idx])

        axis = np.cross(v1_m, v2_m)
        axn = np.linalg.norm(axis, axis=-1)
        deg1 = axn < 1e-8
        if deg1.any():
            alt1 = np.cross(v1_m[deg1], np.array([0.0, 1.0, 0.0], dtype=np.float64))
            axis[deg1] = alt1
            axn[deg1] = np.linalg.norm(alt1, axis=-1)
        deg2 = axn < 1e-8
        if deg2.any():
            alt2 = np.cross(v1_m[deg2], np.array([1.0, 0.0, 0.0], dtype=np.float64))
            axis[deg2] = alt2
            axn[deg2] = np.linalg.norm(alt2, axis=-1)

        ok = axn >= 1e-8
        if not ok.any():
            continue
        idx = idx[ok]
        axis_u = axis[ok] / axn[ok, None]
        v2_u = v2_m[ok] / n2_m[ok, None]
        v2r = _rodrigues_batch(v2_u, axis_u, target_m[ok] - ang_m[ok]) * n2_m[ok, None]
        new_d = h[idx] + v2r
        delta = new_d - d[idx]
        joints[idx, dist] = new_d
        for child in followers:
            joints[idx, child] += delta


def _clamp_torso_lean_yup(joints: np.ndarray, *, max_deg: float) -> None:
    """In-place: cap pelvis→neck tilt off vertical, carrying the upper body.

    HY answers "swing down to waist height" by folding the spine, which reads as
    bending over to grab something. Rotating the whole upper chain back around
    the pelvis keeps the gesture but restores the stance; the arms travel with
    the torso so the reach stays attached to the shoulders. The fold usually
    sits in the upper spine, so the neck — not the chest — is the honest axis.
    """
    max_rad = math.radians(max(float(max_deg), 0.0))
    pelvis = joints[:, 0]
    v = joints[:, 12] - pelvis
    n = np.linalg.norm(v, axis=-1)
    valid = n >= 1e-8
    if not valid.any():
        return
    u = np.divide(v, n[:, None], out=np.zeros_like(v), where=valid[:, None])
    tilt = np.arccos(np.clip(u[:, 1], -1.0, 1.0))
    mask = valid & (tilt > max_rad)
    if not mask.any():
        return

    idx = np.flatnonzero(mask)
    up = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    axis = np.cross(u[idx], up)
    axn = np.linalg.norm(axis, axis=-1)
    ok = axn >= 1e-8
    if not ok.any():
        return
    idx = idx[ok]
    axis_u = (axis[ok] / axn[ok, None])[:, None, :]  # (K, 1, 3)
    delta = (tilt[idx] - max_rad)[:, None, None]  # (K, 1, 1)
    cos_d = np.cos(delta)
    sin_d = np.sin(delta)
    upper = np.asarray(UPPER_BODY_INDICES, dtype=np.intp)
    piv = pelvis[idx, None, :]  # (K, 1, 3)
    rel = joints[idx[:, None], upper] - piv  # (K, U, 3)
    dot = np.sum(axis_u * rel, axis=-1, keepdims=True)
    joints[idx[:, None], upper] = piv + (rel * cos_d + np.cross(axis_u, rel) * sin_d + axis_u * dot * (1.0 - cos_d))


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
    left = joints[:, 20]
    right = joints[:, 21]
    mid = 0.5 * (left + right)
    mid_y = mid[:, 1].copy()

    delta = (right - left).copy()
    delta[:, 1] = 0.0  # grip width is lateral; shaft stacks height via shared Y
    sep_h = np.linalg.norm(delta, axis=-1)
    degen = sep_h < 1e-6
    safe_sep = np.where(degen, 1.0, sep_h)
    axis = np.where(degen[:, None], -lat, delta / safe_sep[:, None])
    # Axis must point away from the left side: left never crosses right.
    flipped = (~degen) & (np.sum(axis * lat, axis=-1) > 0.0)
    axis[flipped] = -axis[flipped]
    half_use = np.where(degen | (sep_h > max_sep), half, np.minimum(half, 0.5 * sep_h))

    offset = axis * half_use[:, None]
    joints[:, 20] = mid - offset
    joints[:, 21] = mid + offset
    joints[:, 20, 1] = mid_y
    joints[:, 21, 1] = mid_y


def _clamp_foot_pitch_yup(
    joints: np.ndarray,
    *,
    min_pitch_deg: float,
    max_pitch_deg: float,
) -> None:
    """In-place: rebuild ball from ankle so foot pitch stays in ``[min, max]``."""
    min_p = math.radians(float(min_pitch_deg))
    max_p = math.radians(float(max_pitch_deg))
    for an, ball in ((7, 10), (8, 11)):
        ankle = joints[:, an]
        v = joints[:, ball] - ankle
        length = np.linalg.norm(v, axis=-1)
        valid = length >= 1e-6
        if not valid.any():
            continue
        horiz = np.hypot(v[:, 0], v[:, 2])
        pitch = np.arctan2(v[:, 1], np.maximum(horiz, 1e-8))
        mask = valid & ((pitch < min_p) | (pitch > max_p))
        if not mask.any():
            continue
        idx = np.flatnonzero(mask)
        pitch_c = np.clip(pitch[idx], min_p, max_p)
        v_m = v[idx]
        h_m = horiz[idx]
        len_m = length[idx]
        degen = h_m < 1e-5
        safe_h = np.where(degen, 1.0, h_m)
        yaw_x = np.where(degen, 0.0, v_m[:, 0] / safe_h)
        yaw_z = np.where(degen, -1.0, v_m[:, 2] / safe_h)
        cos_p = np.cos(pitch_c) * len_m
        sin_p = np.sin(pitch_c) * len_m
        joints[idx, ball, 0] = ankle[idx, 0] + yaw_x * cos_p
        joints[idx, ball, 1] = ankle[idx, 1] + sin_p
        joints[idx, ball, 2] = ankle[idx, 2] + yaw_z * cos_p


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


def _action_fcurves(action: Any, arm_obj: Any) -> Any:
    """Return the writable FCurve collection for ``action`` (Blender 5.x channelbag or 4.x)."""
    if hasattr(action, "layers"):
        slot = action.slots.new(id_type="OBJECT", name=arm_obj.name)
        arm_obj.animation_data.action_slot = slot
        layer = action.layers.new(name="Layer")
        strip = layer.strips.new(type="KEYFRAME")
        return strip.channelbag(slot, ensure=True).fcurves
    return action.fcurves


def _lookat_keyframe(
    bpy: Any,
    arm_obj: Any,
    joints_zup: np.ndarray,
    *,
    fps: int,
    clip_name: str,
    in_place: bool = True,
    neutral_aim: dict[str, tuple[float, float, float]] | None = None,
    clear_other_actions: bool = True,
) -> Any:
    """Keyframe pose by aiming each bone at its child joint (armature Z-up).

    Orientation is **swing-only**: minimal arc from the bone's rest direction to
    the target direction, composed with the rest orientation. ``to_track_quat``
    picks its own roll from a world reference axis, so limbs came out twisted
    around their own axis once Animator3D copied that roll onto the hero.

    Pose-basis quaternions are solved analytically in topological bone order
    (``PARENTS[i] < i``) and written straight into FCurves via ``foreach_set``,
    avoiding per-bone ``view_layer.update()`` and ``frame_set()`` depsgraph
    re-evaluations (~50x faster with <1e-5 parity).

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

    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    if clear_other_actions:
        for act in list(bpy.data.actions):
            bpy.data.actions.remove(act)
    action = bpy.data.actions.new(name=clip_name)
    arm_obj.animation_data.action = action
    fcurves = _action_fcurves(action, arm_obj)

    for pb in arm_obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.matrix_basis.identity()

    aim_child = AIM_CHILD
    n_bones = len(BONE_NAMES)
    root0 = joints_zup[0, 0]

    # Rest orientation + rest bone direction, both in ARMATURE space.
    # ``Bone.y_axis`` is bone-local — using it here aimed every bone 90° off.
    bones_data = arm_obj.data.bones
    rest_quat = [bones_data[bn].matrix_local.to_quaternion() for bn in BONE_NAMES]
    rest_quat_inv = [q.inverted() for q in rest_quat]
    rest_rel_inv = [
        (rest_quat_inv[i] @ rest_quat[PARENTS[i]]) if PARENTS[i] >= 0 else rest_quat_inv[i] for i in range(n_bones)
    ]
    rest_rel = [(rest_quat_inv[PARENTS[i]] @ rest_quat[i]) if PARENTS[i] >= 0 else rest_quat[i] for i in range(n_bones)]
    rest_dir = [(bones_data[bn].tail_local - bones_data[bn].head_local).normalized() for bn in BONE_NAMES]
    pelvis_rest_m3_inv = bones_data["pelvis"].matrix_local.to_3x3().inverted()
    pelvis_head_z = float(bones_data["pelvis"].head_local.z)

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

    flexion = _bilateral_thigh_flexion(joints_zup, 2)
    quats = np.empty((n_bones, n_frames, 4), dtype=np.float64)
    pelvis_locs = np.empty((n_frames, 3), dtype=np.float64)
    prev: list[Any] = [None] * n_bones
    identity_q = Quaternion((1.0, 0.0, 0.0, 0.0))

    for fi in range(n_frames):
        pose_q: list[Any] = [None] * n_bones
        flex_bonus = 65.0 * float(flexion[fi])
        for i in range(n_bones):
            parent_i = PARENTS[i]
            if i in LEAF_BONES:
                q = identity_q
                pose_q[i] = (pose_q[parent_i] @ rest_rel[i]) if parent_i >= 0 else rest_quat[i]
            else:
                head = joints_zup[fi, i]
                tip = joints_zup[fi, aim_child[i]]
                y = Vector((float(tip[0] - head[0]), float(tip[1] - head[1]), float(tip[2] - head[2])))
                if y.length < 1e-8:
                    y = rest_dir[i].copy()
                else:
                    y.normalize()
                if i in aim_fix:
                    y = aim_fix[i] @ y
                # Swing-only: no arbitrary roll → no twisted limbs downstream.
                swing = rest_dir[i].rotation_difference(y)
                max_swing = MAX_SWING_DEG.get(i)
                if max_swing is not None:
                    limit = math.radians(max_swing + flex_bonus)
                    if swing.angle > limit:
                        swing = Quaternion(swing.axis, limit)
                world_q = swing @ rest_quat[i]
                pose_q[i] = world_q
                if parent_i >= 0:
                    q = rest_rel_inv[i] @ pose_q[parent_i].inverted() @ world_q
                else:
                    q = rest_quat_inv[i] @ world_q
                q.normalize()
                if prev[i] is not None:
                    q.make_compatible(prev[i])
            prev[i] = q.copy()
            quats[i, fi, 0] = q.w
            quats[i, fi, 1] = q.x
            quats[i, fi, 2] = q.y
            quats[i, fi, 3] = q.z
            if i == 0:
                head0 = joints_zup[fi, 0]
                dx = 0.0 if in_place else float(head0[0] - root0[0])
                dy = 0.0 if in_place else float(head0[1] - root0[1])
                delta = Vector((dx, dy, float(head0[2] - pelvis_head_z)))
                loc = pelvis_rest_m3_inv @ delta
                pelvis_locs[fi, 0] = loc.x
                pelvis_locs[fi, 1] = loc.y
                pelvis_locs[fi, 2] = loc.z

    frames = np.arange(1, n_frames + 1, dtype=np.float64)
    co_buf = np.empty(n_frames * 2, dtype=np.float64)
    co_buf[0::2] = frames

    pelvis_loc_dp = 'pose.bones["pelvis"].location'
    for idx in range(3):
        fc = fcurves.new(data_path=pelvis_loc_dp, index=idx)
        fc.keyframe_points.add(n_frames)
        co_buf[1::2] = pelvis_locs[:, idx]
        fc.keyframe_points.foreach_set("co", co_buf)
        fc.update()

    for i, bname in enumerate(BONE_NAMES):
        dp = f'pose.bones["{bname}"].rotation_quaternion'
        for idx in range(4):
            fc = fcurves.new(data_path=dp, index=idx)
            fc.keyframe_points.add(n_frames)
            co_buf[1::2] = quats[i, :, idx]
            fc.keyframe_points.foreach_set("co", co_buf)
            fc.update()

    return action


def build_source_armature_in_scene(
    bpy: Any,
    joints: np.ndarray,
    *,
    fps: int = DEFAULT_FPS,
    clip_name: str = "sprong_motion",
    armature_name: str = "Sprong22",
    offsets: np.ndarray | None = None,
    in_place: bool = True,
    neutral_aim: dict[str, tuple[float, float, float]] | None = None,
    max_lean_deg: float | None = None,
    hands_together_m: float | None = None,
    plant_feet: bool = False,
    clear_other_actions: bool = False,
) -> tuple[Any, Any]:
    """Build and keyframe a Sprong22 source armature directly in the current Blender scene.

    Returns ``(armature_object, action)``. Used by both :func:`export_joints_glb`
    and in-memory retargeting in :mod:`motion3d.apply_rigged` (skips temporary
    GLB export/re-import round-trips when ``keep_sources_dir`` is not requested).
    """
    if joints.ndim != 3 or joints.shape[1] != 22 or joints.shape[2] != 3:
        raise ValueError(f"joints must be (T, 22, 3); got {joints.shape}")
    if neutral_aim is None:
        neutral_aim = filter_neutral_aim_for_clip(
            joints, {BONE_NAMES[i]: direction for i, direction in NEUTRAL_AIM.items()}, in_place=in_place
        )
    joints = sanitize_locomotion_joints(
        joints,
        max_lean_deg=max_lean_deg,
        hands_together_m=hands_together_m,
        plant_feet=plant_feet,
    )

    joints_y = np.asarray(joints, dtype=np.float64)
    rest_y = rest_positions_yup(offsets)
    # Bind at standing height. Key the absolute pelvis height above the
    # floor so a clip starting in a seat reproduces its initial hip drop.
    rest_y = rest_y + np.array([joints_y[0, 0, 0], -rest_y[[7, 8, 10, 11], 1].min(), joints_y[0, 0, 2]])
    rest_z = yup_to_blender(rest_y)
    joints_z = yup_to_blender(joints_y)
    if in_place:
        joints_z = stabilize_facing_zup(joints_z)

    bpy.context.scene.render.fps = int(fps)
    arm = _build_armature_zup(bpy, rest_z, name=armature_name)
    action = _lookat_keyframe(
        bpy,
        arm,
        joints_z,
        fps=fps,
        clip_name=clip_name,
        in_place=in_place,
        neutral_aim=neutral_aim,
        clear_other_actions=clear_other_actions,
    )
    return arm, action


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

    out = Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    bpy = _bpy()
    _clear_scene(bpy)
    arm, _action = build_source_armature_in_scene(
        bpy,
        joints,
        fps=fps,
        clip_name=clip_name,
        armature_name="Sprong22",
        offsets=offsets,
        in_place=in_place,
        neutral_aim=neutral_aim,
        max_lean_deg=max_lean_deg,
        hands_together_m=hands_together_m,
        plant_feet=plant_feet,
        clear_other_actions=True,
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
