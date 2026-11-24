"""Procedural animation engine for multi-legged arthropods and scorpions.

Handles creatures with 6+ walking legs (insects, spiders, scorpions, crabs)
and optional forward pincers (pedipalps) and arched overhead stinger tails:

- **Walk & Run**: alternating tripod (6-leg) / alternating tetrapod with
  metachronal wave (8-leg) using true multi-segment arthropod vertical-plane
  IK (coxa world-Z yaw + femur/knee-to-foot 2-bone IK with upward ``+Z`` pole
  + distal tarsus curl). Feet track straight ground-contact lines parallel to
  ``forward`` with zero foot skate, zero leg-to-leg overlap, and exact
  roll-independent quaternion rotations.
- **Attack**: signature overhead scorpion tail-stinger whip strike (coil/rear
  back -> sequential whip acceleration base-to-tip -> uncoil forward past the
  head and pincers -> downward stinger jab -> damped follow-through), paired
  with pincer spread/clamp, body lunge, and IK-grounded legs.
- **Idle / Hit / Roar / Death / Jump / Turn**: living predatory breathing with
  IK-planted feet, armored recoil flinch, scorpion threat display, curled-leg
  arachnid death collapse with drooping tail, pounce jump, and turn-in-place.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


def _bpy():
    import bpy

    return bpy


def _smoothstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def _smootherstep(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * x * (x * (x * 6.0 - 15.0) + 10.0)


# ---------------------------------------------------------------------------
# Classification & detection helpers
# ---------------------------------------------------------------------------


def has_stinger_tail(arm_obj: Any, chains: dict[str, Any], forward: Any | None = None) -> bool:
    """Return True when the rig has a prominent dorsal tail arching over the back."""
    from mathutils import Vector

    tail = chains.get("tail", [])
    if not tail or len(tail) < 4:
        return False
    bones = arm_obj.data.bones
    pts = [Vector(bones[bn].head_local) for bn in tail if bn in bones]
    if len(pts) < 4:
        return False
    tip_bone = bones.get(tail[-1])
    if tip_bone is not None:
        pts.append(Vector(tip_bone.tail_local))

    all_zs = [b.head_local.z for b in bones] + [b.tail_local.z for b in bones]
    height = max(max(all_zs) - min(all_zs), 1e-4)
    rise = max(p.z for p in pts) - pts[0].z
    if rise < 0.22 * height:
        return False

    fwd = Vector((forward.x, forward.y, 0.0)) if forward is not None else Vector((0.0, -1.0, 0.0))
    if fwd.length < 1e-6:
        fwd = Vector((0.0, -1.0, 0.0))
    fwd.normalize()

    # Check forward hook: tip curls forward past the rearmost point of the arch.
    projs = [p.dot(fwd) for p in pts]
    arc_len = sum((pts[i + 1] - pts[i]).length for i in range(len(pts) - 1))
    rearmost = min(projs)
    tip_proj = projs[-1]
    return (tip_proj - rearmost) > 0.15 * max(arc_len, 1e-4)


def is_arthropod(arm_obj: Any, chains: dict[str, Any], forward: Any | None = None) -> bool:
    """Return True for multi-legged arthropods (6+ legs) or scorpions (pincers/stinger)."""
    legs_r = chains.get("legs_r", [])
    legs_l = chains.get("legs_l", [])
    n_legs = len(legs_r) + len(legs_l)
    if len(legs_r) >= 3 or len(legs_l) >= 3 or n_legs >= 6:
        return True
    if (chains.get("pincers_r") or chains.get("pincers_l")) and n_legs >= 4:
        return True
    return has_stinger_tail(arm_obj, chains, forward=forward) and n_legs >= 4


# ---------------------------------------------------------------------------
# Roll-independent quaternion math & keyframing
# ---------------------------------------------------------------------------


def _world_rot_to_basis(rest_q: Any, world_q: Any) -> Any:
    """Convert an armature-space rotation ``world_q`` into bone-local basis quaternion."""
    return rest_q.inverted() @ world_q @ rest_q


def _axis_angle_quat(axis: Any, angle: float) -> Any:
    from mathutils import Quaternion

    if abs(angle) < 1e-9 or axis.length < 1e-8:
        return Quaternion()
    return Quaternion(axis.normalized(), angle)


def _key_quat(arm_obj: Any, bone_name: str, q_basis: Any, frame: int, prev: dict[str, Any]) -> None:
    pb = arm_obj.pose.bones.get(bone_name)
    if pb is None:
        return
    q = q_basis.normalized()
    pq = prev.get(bone_name)
    if pq is not None and q.dot(pq) < 0.0:
        q.negate()
    prev[bone_name] = q.copy()
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = q
    pb.keyframe_insert(data_path="rotation_quaternion", frame=frame)


def _key_loc_world(arm_obj: Any, bone_name: str, delta_world: Any, frame: int) -> None:
    """Key pose-bone local translation corresponding to an armature-space offset."""
    pb = arm_obj.pose.bones.get(bone_name)
    b = arm_obj.data.bones.get(bone_name)
    if pb is None or b is None:
        return
    r_inv = b.matrix_local.to_quaternion().inverted()
    loc_local = r_inv @ delta_world
    pb.location = (loc_local.x, loc_local.y, loc_local.z)
    pb.keyframe_insert(data_path="location", frame=frame)


# ---------------------------------------------------------------------------
# Arthropod leg specification & analytical vertical-plane IK
# ---------------------------------------------------------------------------


@dataclass
class _LegSpec:
    names: list[str]
    side: str  # "r" (+X) or "l" (-X)
    side_sign: float  # +1.0 for +X, -1.0 for -X
    pair_idx: int  # 0 = frontmost, increasing toward rear
    coxa: str | None
    femur: str
    shin: str
    distal: list[str]
    foot_rest: Any  # Vector in armature space
    l1: float
    l2: float
    o1_dir: Any  # femur -> knee direction in femur rest frame
    o2_dir: Any  # knee -> foot direction in shin rest frame
    r_coxa: Any | None
    r_femur: Any
    r_shin: Any
    coxa_offset: Any  # coxa.head - root.head in root rest frame
    femur_offset: Any  # femur.head - coxa.head in coxa rest frame (or root frame if no coxa)
    rest_rad_xy: Any  # (foot_rest - coxa_head)_xy
    max_stride: float


def _foot_contact_point(bone: Any) -> Any:
    """Ground contact point of the terminal leg bone (clamped at z=0 when tail dips below)."""
    from mathutils import Vector

    h = Vector(bone.head_local)
    t = Vector(bone.tail_local)
    if t.z < 0.0 < h.z:
        alpha = h.z / max(h.z - t.z, 1e-6)
        return h.lerp(t, alpha)
    return t


def _build_leg_specs(
    arm_obj: Any,
    chains: dict[str, Any],
    root_name: str,
    forward: Any,
) -> list[_LegSpec]:
    from mathutils import Quaternion, Vector

    bones = arm_obj.data.bones
    b_root = bones.get(root_name)
    r_root = b_root.matrix_local.to_quaternion() if b_root is not None else Quaternion()
    root_head = Vector(b_root.head_local) if b_root is not None else Vector((0.0, 0.0, 0.0))

    raw_by_side: dict[str, list[tuple[int, list[str], Vector]]] = {"r": [], "l": []}
    for side in ("r", "l"):
        for idx, leg in enumerate(chains.get(f"legs_{side}", [])):
            if len(leg) < 2 or any(bn not in bones for bn in leg):
                continue
            foot_pt = _foot_contact_point(bones[leg[-1]])
            raw_by_side[side].append((idx, list(leg), foot_pt))

    specs: list[_LegSpec] = []
    for side in ("r", "l"):
        entries = raw_by_side[side]
        for pos_i, (pair_idx, leg, foot_pt) in enumerate(entries):
            # Nearest neighbor foot spacing on the same side prevents leg-to-leg clipping.
            min_nbr = 1e9
            for pos_j, (_, _, other_pt) in enumerate(entries):
                if pos_i == pos_j:
                    continue
                d_xy = Vector((foot_pt.x - other_pt.x, foot_pt.y - other_pt.y, 0.0)).length
                if d_xy < min_nbr:
                    min_nbr = d_xy
            if min_nbr > 1e8:
                min_nbr = 0.28

            # Identify the upward-arching knee peak in 4/5-bone arthropod legs.
            if len(leg) >= 4:
                # Knee is the highest interior joint (normally index 2: coxa=0, femur=1, patella=2).
                knee_idx = max(range(1, len(leg) - 1), key=lambda k: bones[leg[k]].head_local.z)
                if knee_idx >= 2:
                    coxa_n: str | None = leg[0]
                    femur_n = leg[knee_idx - 1]
                    shin_n = leg[knee_idx]
                    distal_ns = leg[knee_idx + 1 :]
                else:
                    coxa_n = None
                    femur_n = leg[0]
                    shin_n = leg[1]
                    distal_ns = leg[2:]
            elif len(leg) == 3:
                coxa_n = leg[0]
                femur_n = leg[1]
                shin_n = leg[2]
                distal_ns = []
            else:
                coxa_n = None
                femur_n = leg[0]
                shin_n = leg[1]
                distal_ns = []

            b_coxa = bones[coxa_n] if coxa_n else None
            b_femur = bones[femur_n]
            b_shin = bones[shin_n]

            r_coxa = b_coxa.matrix_local.to_quaternion() if b_coxa else None
            r_femur = b_femur.matrix_local.to_quaternion()
            r_shin = b_shin.matrix_local.to_quaternion()

            h_femur = Vector(b_femur.head_local)
            h_shin = Vector(b_shin.head_local)
            o1_local = r_femur.inverted() @ (h_shin - h_femur)
            o2_local = r_shin.inverted() @ (foot_pt - h_shin)
            l1 = float(o1_local.length)
            l2 = float(o2_local.length)
            if l1 < 1e-4 or l2 < 1e-4:
                continue

            if b_coxa is not None and r_coxa is not None:
                h_coxa = Vector(b_coxa.head_local)
                coxa_offset = r_root.inverted() @ (h_coxa - root_head)
                femur_offset = r_coxa.inverted() @ (h_femur - h_coxa)
                rad_origin = h_coxa
            else:
                coxa_offset = Vector((0.0, 0.0, 0.0))
                femur_offset = r_root.inverted() @ (h_femur - root_head)
                rad_origin = h_femur

            rest_rad = Vector((foot_pt.x - rad_origin.x, foot_pt.y - rad_origin.y, 0.0))
            side_sign = 1.0 if foot_pt.x >= 0.0 else -1.0
            # Cap stride so neighboring feet on the same side never collide.
            max_stride = min(0.68 * min_nbr, 0.48 * (l1 + l2))

            specs.append(
                _LegSpec(
                    names=leg,
                    side=side,
                    side_sign=side_sign,
                    pair_idx=pair_idx,
                    coxa=coxa_n,
                    femur=femur_n,
                    shin=shin_n,
                    distal=distal_ns,
                    foot_rest=foot_pt,
                    l1=l1,
                    l2=l2,
                    o1_dir=o1_local.normalized(),
                    o2_dir=o2_local.normalized(),
                    r_coxa=r_coxa,
                    r_femur=r_femur,
                    r_shin=r_shin,
                    coxa_offset=coxa_offset,
                    femur_offset=femur_offset,
                    rest_rad_xy=rest_rad,
                    max_stride=max_stride,
                )
            )
    return specs


def _solve_leg_ik_frame(
    arm_obj: Any,
    spec: _LegSpec,
    *,
    p_target: Any,
    w_root: Any,
    r_root: Any,
    p_root: Any,
    frame: int,
    prev: dict[str, Any],
    coxa_elev_rad: float = 0.0,
    distal_curl_rad: float = 0.0,
) -> None:
    """Solve one frame of multi-segment arthropod vertical-plane IK and key bones."""
    from mathutils import Quaternion, Vector

    from . import ik as _ik

    parent_delta = w_root @ r_root.inverted()

    # 1) Coxa horizontal yaw + subtle elevation assist.
    if spec.coxa is not None and spec.r_coxa is not None:
        p_coxa = p_root + (parent_delta @ (r_root @ spec.coxa_offset))
        v_tgt_xy = Vector((p_target.x - p_coxa.x, p_target.y - p_coxa.y, 0.0))
        v_rest_xy = spec.rest_rad_xy
        if v_rest_xy.length > 1e-4 and v_tgt_xy.length > 1e-4:
            cross_z = v_rest_xy.x * v_tgt_xy.y - v_rest_xy.y * v_tgt_xy.x
            dot_xy = v_rest_xy.x * v_tgt_xy.x + v_rest_xy.y * v_tgt_xy.y
            dyaw = max(-0.58, min(0.58, math.atan2(cross_z, dot_xy)))
        else:
            dyaw = 0.0

        u_out = v_tgt_xy.normalized() if v_tgt_xy.length > 1e-4 else Vector((spec.side_sign, 0.0, 0.0))
        tan_axis = u_out.cross(Vector((0.0, 0.0, 1.0)))
        q_coxa_world = Quaternion(Vector((0.0, 0.0, 1.0)), dyaw)
        if abs(coxa_elev_rad) > 1e-6 and tan_axis.length > 1e-4:
            # Rotate upward around horizontal tangent axis perpendicular to leg plane.
            q_coxa_world = Quaternion(tan_axis.normalized(), -coxa_elev_rad) @ q_coxa_world

        w_coxa_rest = parent_delta @ spec.r_coxa
        w_coxa = q_coxa_world @ w_coxa_rest
        basis_coxa = spec.r_coxa.inverted() @ r_root @ w_root.inverted() @ w_coxa
        _key_quat(arm_obj, spec.coxa, basis_coxa, frame, prev)

        p_femur = p_coxa + ((w_coxa @ spec.r_coxa.inverted()) @ (spec.r_coxa @ spec.femur_offset))
        w_femur_parent = w_coxa
        r_femur_parent = spec.r_coxa
    else:
        p_femur = p_root + (parent_delta @ (r_root @ spec.femur_offset))
        w_femur_parent = w_root
        r_femur_parent = r_root

    # 2) Two-bone vertical-plane IK on Femur -> Knee -> Foot target (pole = UP + outward).
    d_vec = p_target - p_femur
    d_len = d_vec.length
    # Respect joint hinge_min (>45 deg) by construction so every clip is IK-limits-clean.
    d_min = max(
        abs(spec.l1 - spec.l2) * 1.08,
        _ik.distance_for_interior_angle(spec.l1, spec.l2, 46.0),
    )
    d_max = (spec.l1 + spec.l2) * 0.985
    if d_len > d_max and d_len > 1e-6:
        p_eff = p_femur + d_vec * (d_max / d_len)
    elif d_len < d_min and d_len > 1e-6:
        p_eff = p_femur + d_vec * (d_min / d_len)
    else:
        p_eff = p_target

    out_xy = Vector((p_eff.x - p_femur.x, p_eff.y - p_femur.y, 0.0))
    u_out = out_xy.normalized() if out_xy.length > 1e-4 else Vector((spec.side_sign, 0.0, 0.0))
    pole = (Vector((0.0, 0.0, 1.0)) + u_out * 0.18).normalized()

    def _np(v: Vector) -> np.ndarray:
        return np.array([v.x, v.y, v.z], dtype=float)

    def _mq(arr: np.ndarray) -> Quaternion:
        return Quaternion((float(arr[0]), float(arr[1]), float(arr[2]), float(arr[3])))

    knee_arr = _ik.two_bone_knee_position(_np(p_femur), _np(p_eff), spec.l1, spec.l2, _np(pole))
    p_knee = Vector((float(knee_arr[0]), float(knee_arr[1]), float(knee_arr[2])))

    w_femur_rest = w_femur_parent @ r_femur_parent.inverted() @ spec.r_femur
    dir1_rest = w_femur_rest @ spec.o1_dir
    dir1_want = (p_knee - p_femur).normalized()
    q1 = _mq(_ik.rotation_between(_np(dir1_rest), _np(dir1_want)))
    w_femur = q1 @ w_femur_rest

    w_shin_rest = w_femur @ spec.r_femur.inverted() @ spec.r_shin
    dir2_rest = w_shin_rest @ spec.o2_dir
    dir2_want = (p_eff - p_knee).normalized()
    q2 = _mq(_ik.rotation_between(_np(dir2_rest), _np(dir2_want)))
    w_shin = q2 @ w_shin_rest

    basis_femur = spec.r_femur.inverted() @ r_femur_parent @ w_femur_parent.inverted() @ w_femur
    basis_shin = spec.r_shin.inverted() @ spec.r_femur @ w_femur.inverted() @ w_shin

    _key_quat(arm_obj, spec.femur, basis_femur, frame, prev)
    _key_quat(arm_obj, spec.shin, basis_shin, frame, prev)

    # 3) Distal tibia / tarsus curl in the leg's own vertical plane.
    if spec.distal:
        tan_axis = u_out.cross(Vector((0.0, 0.0, 1.0)))
        tan_u = tan_axis.normalized() if tan_axis.length > 1e-4 else Vector((0.0, 1.0, 0.0))
        bones = arm_obj.data.bones
        for di, dbn in enumerate(spec.distal):
            db = bones.get(dbn)
            if db is None:
                continue
            r_d = db.matrix_local.to_quaternion()
            scale = 1.0 if di == 0 else 0.55
            if abs(distal_curl_rad) > 1e-6:
                q_curl = Quaternion(tan_u, distal_curl_rad * scale)
                basis_d = _world_rot_to_basis(r_d, q_curl)
            else:
                basis_d = Quaternion()
            _key_quat(arm_obj, dbn, basis_d, frame, prev)


# ---------------------------------------------------------------------------
# Tetrapod / Tripod phase tables
# ---------------------------------------------------------------------------


def _arthropod_phases(n_per_side: int, *, wave_lag: float = 0.055) -> tuple[list[float], list[float]]:
    """Return (phases_r, phases_l) for N legs/side ordered front -> back.

    - 3 legs/side (insect tripod): R = [0.0, 0.5+d, 0.0+2d], L = (R + 0.5) % 1.
    - 4 legs/side (arachnid tetrapod with metachronal wave):
      R = [0.0, 0.5+d, 2d, 0.5+3d], L = (R + 0.5) % 1.
      Adjacent legs on the same side stay ~0.45-0.55 apart (never collide),
      opposite sides are always anti-phase (0.5), and >=4 legs stay planted.
    """
    phases_r: list[float] = []
    for i in range(max(n_per_side, 1)):
        base = 0.0 if (i % 2 == 0) else 0.5
        phases_r.append((base + i * wave_lag) % 1.0)
    phases_l = [(p + 0.5) % 1.0 for p in phases_r]
    return phases_r, phases_l


# ---------------------------------------------------------------------------
# Pincers & Tail helpers (exact world-axis rotations, roll-independent)
# ---------------------------------------------------------------------------


def _key_pincers_frame(
    arm_obj: Any,
    chains: dict[str, Any],
    *,
    frame: int,
    prev: dict[str, Any],
    forward: Any,
    right_axis: Any,
    spread_r: float = 0.0,
    spread_l: float = 0.0,
    raise_r: float = 0.0,
    raise_l: float = 0.0,
    pinch_r: float = 0.0,
    pinch_l: float = 0.0,
) -> None:
    """Animate both pedipalps/pincers with exact world-axis rotations.

    - ``spread > 0`` swings the pincer OUTWARD away from midline; ``< 0`` clamps INWARD.
    - ``raise > 0`` lifts the pincer UPWARD; ``< 0`` lowers it.
    - ``pinch > 0`` curls the distal claw inward (clamp); ``< 0`` opens it.
    """
    from mathutils import Vector

    bones = arm_obj.data.bones
    up = Vector((0.0, 0.0, 1.0))
    # Determine yaw sign for outward spread on +X vs -X when facing `forward`.
    # Facing -Y: right_axis = up x fwd = +X; rotating +X toward +Y (outward/back) is +Z yaw,
    # Wait: for a forward-pointing (-Y) pincer on +X:
    #   +Z rotation turns -Y toward +X (OUTWARD!).
    # For a forward-pointing (-Y) pincer on -X:
    #   -Z rotation turns -Y toward -X (OUTWARD!).
    # General formula: sign of (fwd x out).z -> for fwd=(0,-1,0), out=(+1,0,0): (0,-1,0)x(1,0,0)=(0,0,+1) -> +1!
    for key, side_sign, spread, lift_ang, pinch in (
        ("pincers_r", 1.0, spread_r, raise_r, pinch_r),
        ("pincers_l", -1.0, spread_l, raise_l, pinch_l),
    ):
        pchain = chains.get(key, [])
        if not pchain:
            continue
        out_vec = right_axis * side_sign
        yaw_sign = 1.0 if forward.cross(out_vec).z >= 0.0 else -1.0
        # Raise upward: for forward = -Y, right_axis = +X:
        # Rotating -Y by -theta around +X gives (0, -cos, +sin) -> UP!
        pitch_up_sign = -1.0

        n = len(pchain)
        for ci, bn in enumerate(pchain):
            b = bones.get(bn)
            if b is None:
                continue
            r_q = b.matrix_local.to_quaternion()
            # Distribute spread & lift mainly across shoulder (0) and arm (1), subtle follow on claw (2).
            if ci == 0:
                ang_yaw = yaw_sign * spread * 0.55
                ang_pitch = pitch_up_sign * lift_ang * 0.60
            elif ci == 1:
                ang_yaw = yaw_sign * spread * 0.35
                ang_pitch = pitch_up_sign * lift_ang * 0.40
            elif ci == 2:
                ang_yaw = yaw_sign * (spread * 0.15 - pinch * 0.35)
                ang_pitch = pitch_up_sign * lift_ang * 0.12
            else:
                # Distal finger/jaw: closes inward on pinch > 0, opens on pinch < 0.
                ang_yaw = -yaw_sign * pinch * 0.85
                ang_pitch = 0.0
            if n == 2 and ci == 1:
                ang_yaw = yaw_sign * (spread * 0.45 - pinch * 0.5)
            q_w = _axis_angle_quat(up, ang_yaw) @ _axis_angle_quat(right_axis, ang_pitch)
            _key_quat(arm_obj, bn, _world_rot_to_basis(r_q, q_w), frame, prev)


def _key_tail_frame(
    arm_obj: Any,
    tail: list[str],
    *,
    frame: int,
    prev: dict[str, Any],
    right_axis: Any,
    pitches: list[float],
    yaws: list[float],
) -> None:
    """Key all tail segments with exact world-axis pitch (sagittal) and lateral sway."""
    from mathutils import Vector

    bones = arm_obj.data.bones
    up = Vector((0.0, 0.0, 1.0))
    for ci, bn in enumerate(tail):
        b = bones.get(bn)
        if b is None:
            continue
        r_q = b.matrix_local.to_quaternion()
        p = pitches[ci] if ci < len(pitches) else 0.0
        y = yaws[ci] if ci < len(yaws) else 0.0
        q_w = _axis_angle_quat(up, y) @ _axis_angle_quat(right_axis, p)
        _key_quat(arm_obj, bn, _world_rot_to_basis(r_q, q_w), frame, prev)


def _tail_rest_sagittal_angles(arm_obj: Any, tail: list[str], fwd: Any) -> list[float]:
    """Measure each tail segment's rest elevation angle ``atan2(dot(v, fwd), dot(v, up))``.

    0 rad = straight up (+Z), +pi/2 = straight forward, +3pi/4 = forward-down,
    -pi/2 = straight backward. Increasing rotation around ``right_axis = up x fwd``
    by +delta increases this angle by +delta.
    """
    from mathutils import Vector

    bones = arm_obj.data.bones
    up = Vector((0.0, 0.0, 1.0))
    angles: list[float] = []
    for bn in tail:
        b = bones.get(bn)
        if b is None:
            angles.append(0.0)
            continue
        v = Vector(b.tail_local) - Vector(b.head_local)
        angles.append(math.atan2(v.dot(fwd), v.dot(up)))
    return angles


def _eval_knots_deg(u: float, knots: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation of degree knots at normalized position ``u in [0, 1]``."""
    for i in range(len(knots) - 1):
        u0, a0 = knots[i]
        u1, a1 = knots[i + 1]
        if u <= u1:
            w = (u - u0) / max(u1 - u0, 1e-6)
            return math.radians(a0 + (a1 - a0) * w)
    return math.radians(knots[-1][1])


def _sagittal_targets_to_local_deltas(
    rest_angles: list[float],
    target_angles: list[float],
    body_pitch: float = 0.0,
) -> list[float]:
    """Convert desired world sagittal angles per tail segment into hierarchical local deltas."""
    deltas: list[float] = []
    cum_prev = body_pitch
    for r_ang, t_ang in zip(rest_angles, target_angles, strict=True):
        # world_angle = r_ang + cum_prev + delta => delta = t_ang - r_ang - cum_prev
        d = t_ang - r_ang - cum_prev
        deltas.append(d)
        cum_prev += d
    return deltas


# ---------------------------------------------------------------------------
# Locomotion (Walk & Run)
# ---------------------------------------------------------------------------


def _arthropod_locomotion(
    armature_name: str,
    chains: dict[str, Any],
    *,
    frame_start: int,
    frame_end: int,
    cycles: float,
    action_name: str,
    is_run: bool,
) -> dict[str, Any]:
    from mathutils import Vector

    from . import bpy_ops

    bpy = _bpy()
    bpy_ops.normalize_armature_before_animation(armature_name)
    bpy_ops.stash_if_needed_for_action(armature_name, action_name)
    bpy_ops.ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    bpy_ops._ensure_pose_mode(armature_name)

    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = bpy_ops._detect_forward(arm_obj, chains)
    fwd = Vector((forward.x, forward.y, 0.0))
    if fwd.length < 1e-6:
        fwd = Vector((0.0, -1.0, 0.0))
    fwd.normalize()
    up = Vector((0.0, 0.0, 1.0))
    right_axis = up.cross(fwd).normalized()  # +X when fwd = -Y

    root_name = chains.get("body", [arm_obj.data.bones[0].name])[0]
    b_root = arm_obj.data.bones[root_name]
    r_root = b_root.matrix_local.to_quaternion()
    root_head = Vector(b_root.head_local)

    # Integer cycle count guarantees exact seamless looping (frame_start == frame_end).
    eff_cycles = float(max(1, round(cycles)))
    if is_run and eff_cycles < 3.0 and total >= 30:
        eff_cycles = 3.0  # crisp predatory scuttle cadence

    specs = _build_leg_specs(arm_obj, chains, root_name, fwd)
    n_r = sum(1 for s in specs if s.side == "r")
    n_l = sum(1 for s in specs if s.side == "l")
    phases_r, phases_l = _arthropod_phases(max(n_r, n_l, 1), wave_lag=0.055 if not is_run else 0.045)

    duty = 0.54 if is_run else 0.64
    stride_scale = 0.96 if is_run else 0.78
    lift_base = 0.068 if is_run else 0.048
    flare_base = 0.018 if is_run else 0.012

    bob_amp = 0.014 if is_run else 0.008
    sway_lat_amp = 0.012 if is_run else 0.007
    body_yaw_amp = 0.042 if is_run else 0.025
    body_roll_amp = 0.030 if is_run else 0.018
    body_pitch_bias = -0.045 if is_run else -0.012  # low forward predatory crouch in run
    body_pitch_osc = 0.022 if is_run else 0.010

    tail = chains.get("tail", [])
    n_tail = len(tail)
    stinger = has_stinger_tail(arm_obj, chains, forward=fwd)

    prev: dict[str, Any] = {}
    two_pi = 2.0 * math.pi

    for fi in range(total):
        t = fi / max(total - 1, 1)
        frame = frame_start + fi
        bpy.context.scene.frame_set(frame)
        ang = t * eff_cycles * two_pi

        # 1) Body (Root) — armored low micro-bob, subtle tetrapod lateral sway & yaw.
        bob_z = -bob_amp * math.cos(2.0 * ang)
        sway_x = sway_lat_amp * math.sin(ang)
        delta_root = right_axis * sway_x + up * (bob_z - (0.012 if is_run else 0.0))

        body_yaw = body_yaw_amp * math.sin(ang)
        body_roll = body_roll_amp * math.cos(ang)
        body_pitch = body_pitch_bias + body_pitch_osc * math.sin(2.0 * ang)

        q_root_world = (
            _axis_angle_quat(up, body_yaw) @ _axis_angle_quat(fwd, body_roll) @ _axis_angle_quat(right_axis, body_pitch)
        )
        basis_root = _world_rot_to_basis(r_root, q_root_world)
        _key_quat(arm_obj, root_name, basis_root, frame, prev)
        _key_loc_world(arm_obj, root_name, delta_root, frame)

        w_root = q_root_world @ r_root
        p_root = root_head + delta_root

        # 2) All walking legs — alternating tetrapod/tripod IK on straight ground tracks.
        for spec in specs:
            ph_list = phases_r if spec.side == "r" else phases_l
            ph = ph_list[min(spec.pair_idx, len(ph_list) - 1)]
            phi = (t * eff_cycles + ph) % 1.0

            # Front legs reach slightly further forward, rear legs drive cleanly without overlapping.
            pair_stride = spec.max_stride * stride_scale
            pair_lift = min(lift_base, 0.45 * pair_stride + 0.02)

            if phi < duty:
                # Stance: linear backward progression at constant ground velocity (zero skate).
                w = phi / duty
                s = 0.5 * pair_stride * (1.0 - 2.0 * w)
                h = 0.0
                flare = 0.0
                swing_w = 0.0
            else:
                # Swing: crisp arthropod lift + smoothstep advance + micro outward clearance.
                w = (phi - duty) / max(1.0 - duty, 1e-6)
                s = pair_stride * (_smoothstep(w) - 0.5)
                h = pair_lift * (math.sin(math.pi * w) ** 1.45)
                flare = flare_base * math.sin(math.pi * w)
                swing_w = math.sin(math.pi * w)

            out_dir = right_axis * spec.side_sign
            p_target = spec.foot_rest + fwd * s + up * h + out_dir * flare
            _solve_leg_ik_frame(
                arm_obj,
                spec,
                p_target=p_target,
                w_root=w_root,
                r_root=r_root,
                p_root=p_root,
                frame=frame,
                prev=prev,
                coxa_elev_rad=0.14 * swing_w,
                distal_curl_rad=0.22 * swing_w,
            )

        # 3) Pincers — predatory low guard, alternating balance sway + subtle breathing pinch.
        p_sway = (0.12 if is_run else 0.08) * math.sin(ang - 0.5)
        p_spread_bias = 0.14 if is_run else 0.06
        p_raise_bias = 0.14 if is_run else 0.06
        p_bob = (0.065 if is_run else 0.04) * math.cos(2.0 * ang - 0.7)
        _key_pincers_frame(
            arm_obj,
            chains,
            frame=frame,
            prev=prev,
            forward=fwd,
            right_axis=right_axis,
            spread_r=p_spread_bias + p_sway,
            spread_l=p_spread_bias - p_sway,
            raise_r=p_raise_bias + p_bob,
            raise_l=p_raise_bias - p_bob * 0.5,
            pinch_r=0.09 * math.sin(ang),
            pinch_l=-0.09 * math.sin(ang),
        )

        # 4) Arched tail — poised alive S-wave + inertial vertical bounce (overlapping action).
        if n_tail > 0:
            pitches: list[float] = []
            yaws: list[float] = []
            for ci in range(n_tail):
                u = ci / max(n_tail - 1, 1)
                lag = ci * 0.42
                if stinger:
                    # Slight aggressive forward poise in run + expressive inertial vertical bounce.
                    poise = (0.06 if is_run else 0.015) * (1.0 - 0.8 * u)
                    bob_p = (0.085 if is_run else 0.052) * (0.45 + 0.55 * u) * math.sin(2.0 * ang - lag)
                    # Traveling lateral wave (gentle at base, expressive near crown/stinger).
                    sway_y = (0.095 if is_run else 0.068) * (0.35 + 0.65 * u) * math.sin(ang - lag)
                    pitches.append(poise + bob_p)
                    yaws.append(sway_y)
                else:
                    pitches.append(0.03 * math.sin(2.0 * ang - lag))
                    yaws.append(0.08 * (0.4 + 0.6 * u) * math.sin(ang - lag))
            _key_tail_frame(arm_obj, tail, frame=frame, prev=prev, right_axis=right_axis, pitches=pitches, yaws=yaws)

    bpy_ops.finalize_current_action_to_nla(armature_name)
    return chains


# ---------------------------------------------------------------------------
# Scorpion Overhead Tail-Stinger Whip Strike (Attack)
# ---------------------------------------------------------------------------


def _tail_coil_env(t: float) -> float:
    """Windup/coil envelope peaking at t in [0.28, 0.36], releasing cleanly into strike."""
    if t < 0.28:
        return _smootherstep(t / 0.28)
    if t < 0.36:
        return 1.0
    if t < 0.45:
        return 1.0 - _smootherstep((t - 0.36) / 0.09)
    return 0.0


def _tail_strike_env(t: float) -> float:
    """Explosive overhead whip strike peaking at t=0.50..0.58 with damped follow-through."""
    if t < 0.35:
        return 0.0
    if t < 0.49:
        # Fast whip acceleration (power ease-in-out).
        w = (t - 0.35) / 0.14
        return _smootherstep(w)
    if t < 0.58:
        # Impact hold with sharp venom-injection overshoot pulse at t=0.52.
        w = (t - 0.49) / 0.09
        return 1.0 + 0.07 * math.sin(w * math.pi)
    if t < 0.90:
        # Smooth elastic recovery with subtle damped recoil ripple.
        w = (t - 0.58) / 0.32
        base = 1.0 - _smootherstep(w)
        ripple = -0.08 * math.sin(w * math.pi) * math.exp(-w * 2.5)
        return max(-0.12, base + ripple)
    # Final settle cleanly to 0 at t=1.0.
    w = (t - 0.90) / 0.10
    rem = -0.08 * math.sin(math.pi) * math.exp(-2.5)
    return rem * (1.0 - _smoothstep(w))


def _arthropod_attack(
    armature_name: str,
    chains: dict[str, Any],
    *,
    frame_start: int,
    frame_end: int,
    action_name: str,
) -> dict[str, Any]:
    """Signature scorpion overhead tail-stinger whip strike + pincer clamp + grounded lunge."""
    from mathutils import Vector

    from . import bpy_ops

    bpy = _bpy()
    bpy_ops.normalize_armature_before_animation(armature_name)
    bpy_ops.stash_if_needed_for_action(armature_name, action_name)
    bpy_ops.ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    bpy_ops._ensure_pose_mode(armature_name)

    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = bpy_ops._detect_forward(arm_obj, chains)
    fwd = Vector((forward.x, forward.y, 0.0))
    if fwd.length < 1e-6:
        fwd = Vector((0.0, -1.0, 0.0))
    fwd.normalize()
    up = Vector((0.0, 0.0, 1.0))
    right_axis = up.cross(fwd).normalized()

    root_name = chains.get("body", [arm_obj.data.bones[0].name])[0]
    b_root = arm_obj.data.bones[root_name]
    r_root = b_root.matrix_local.to_quaternion()
    root_head = Vector(b_root.head_local)

    specs = _build_leg_specs(arm_obj, chains, root_name, fwd)
    tail = chains.get("tail", [])
    n_tail = len(tail)
    stinger = has_stinger_tail(arm_obj, chains, forward=fwd)
    tail_rest_angles = _tail_rest_sagittal_angles(arm_obj, tail, fwd) if n_tail > 0 else []

    prev: dict[str, Any] = {}

    # Exact target sagittal silhouette angles (deg: 0 = up, +90 = straight fwd, +135 = fwd-down, -45 = back-up).
    # Coil (windup): tail rears tall & back with stinger cocked tightly overhead.
    coil_knots = [
        (0.00, -64.0),
        (0.17, -38.0),
        (0.33, -4.0),
        (0.50, 36.0),
        (0.67, 76.0),
        (0.83, 118.0),
        (1.00, 148.0),
    ]
    # Overhead whip strike: base arches HIGH (+22°/+60° clearing carapace cleanly),
    # upper segments UNCOIL STRAIGHT FORWARD (+84°..+102°) so the turquoise stinger
    # lances out PAST the head & pincer palms and plunges downward (+126° + jab) into prey!
    strike_knots = [
        (0.00, 22.0),
        (0.17, 60.0),
        (0.33, 84.0),
        (0.50, 92.0),
        (0.67, 98.0),
        (0.83, 106.0),
        (1.00, 126.0),
    ]
    tail_coil_angles = [_eval_knots_deg(ci / max(n_tail - 1, 1), coil_knots) for ci in range(n_tail)]
    tail_strike_angles = [_eval_knots_deg(ci / max(n_tail - 1, 1), strike_knots) for ci in range(n_tail)]

    for fi in range(total):
        t = fi / max(total - 1, 1)
        frame = frame_start + fi
        bpy.context.scene.frame_set(frame)

        c_env = _tail_coil_env(t)
        s_env = _tail_strike_env(t)

        # 1) Body (Root): rear back & rise slightly during windup coil -> explosive lunge forward on strike!
        lunge_fwd = -0.055 * c_env + 0.130 * max(s_env, 0.0) + 0.04 * min(s_env, 0.0)
        body_lift = 0.024 * c_env - 0.014 * max(s_env, 0.0)
        # Pitch: positive around right_axis (+X) pitches nose (-Y) DOWN, negative pitches nose UP!
        body_pitch = math.radians(-8.0) * c_env + math.radians(7.0) * max(s_env, 0.0)
        body_yaw = math.radians(2.5) * math.sin(t * math.pi * 2.0) * (c_env + max(s_env, 0.0)) * 0.5

        delta_root = fwd * lunge_fwd + up * body_lift
        q_root_world = _axis_angle_quat(up, body_yaw) @ _axis_angle_quat(right_axis, body_pitch)
        _key_quat(arm_obj, root_name, _world_rot_to_basis(r_root, q_root_world), frame, prev)
        _key_loc_world(arm_obj, root_name, delta_root, frame)

        w_root = q_root_world @ r_root
        p_root = root_head + delta_root

        # 2) All 8 walking legs stay firmly PLANTED on ground via IK while body shifts/lunges!
        for spec in specs:
            out_dir = right_axis * spec.side_sign
            brace_out = 0.018 * (c_env + 0.7 * max(s_env, 0.0)) if spec.pair_idx == 0 else 0.0
            brace_fwd = 0.025 * max(s_env, 0.0) if spec.pair_idx == 0 else 0.0
            step_h = (
                0.022 * math.sin(math.pi * _smoothstep(min(t / 0.30, 1.0)))
                if (spec.pair_idx == 0 and t < 0.30)
                else 0.0
            )
            p_target = spec.foot_rest + out_dir * brace_out + fwd * brace_fwd + up * step_h
            _solve_leg_ik_frame(
                arm_obj,
                spec,
                p_target=p_target,
                w_root=w_root,
                r_root=r_root,
                p_root=p_root,
                frame=frame,
                prev=prev,
                coxa_elev_rad=0.10 * (step_h / 0.022 if step_h > 0 else 0.0),
                distal_curl_rad=0.12 * (step_h / 0.022 if step_h > 0 else 0.0),
            )

        # 3) Pincers: spread WIDE & RAISE UP menacingly in windup -> SNAP INWARD framing stinger (no overlap)!
        p_spread = 0.32 * c_env - 0.08 * max(s_env, 0.0)
        p_raise = 0.30 * c_env + 0.04 * max(s_env, 0.0)
        p_pinch = -0.22 * c_env + 0.36 * max(s_env, 0.0)
        _key_pincers_frame(
            arm_obj,
            chains,
            frame=frame,
            prev=prev,
            forward=fwd,
            right_axis=right_axis,
            spread_r=p_spread,
            spread_l=p_spread,
            raise_r=p_raise,
            raise_l=p_raise,
            pinch_r=p_pinch,
            pinch_l=p_pinch,
        )

        # 4) Tail: exact target-angle overhead whip with sequential base-to-tip stagger!
        if n_tail > 0:
            yaws: list[float] = []
            tremor_env = c_env if (0.20 < t < 0.37) else 0.0
            if stinger:
                target_angles: list[float] = []
                for ci in range(n_tail):
                    u = ci / max(n_tail - 1, 1)
                    # Whip stagger: base leads tip on strike launch, recovers first on pull-back.
                    lag = ci * 0.010 if t < 0.60 else (n_tail - 1 - ci) * 0.008
                    t_seg = max(0.0, min(1.0, t - lag))
                    c_seg = _tail_coil_env(t_seg)
                    s_seg = _tail_strike_env(t_seg)
                    r_ang = tail_rest_angles[ci]
                    jab = 0.0
                    if u > 0.78 and 0.47 < t < 0.61:
                        jab = math.radians(16.0) * math.sin(((t - 0.47) / 0.14) * math.pi)
                    ang_des = (
                        r_ang + (tail_coil_angles[ci] - r_ang) * c_seg + (tail_strike_angles[ci] - r_ang) * s_seg + jab
                    )
                    target_angles.append(ang_des)
                    yaws.append(math.radians(2.8) * u * tremor_env * math.sin(t * math.pi * 24.0 + ci))
                pitches = _sagittal_targets_to_local_deltas(tail_rest_angles, target_angles, body_pitch=body_pitch)
            else:
                pitches = []
                for ci in range(n_tail):
                    u = ci / max(n_tail - 1, 1)
                    pitches.append(math.radians(-14.0) * c_env + math.radians(22.0) * s_env)
                    yaws.append(math.radians(10.0) * u * s_env)
            _key_tail_frame(arm_obj, tail, frame=frame, prev=prev, right_axis=right_axis, pitches=pitches, yaws=yaws)

    bpy_ops.finalize_current_action_to_nla(armature_name)
    return chains


# ---------------------------------------------------------------------------
# Idle, Roar (Threat Display), Hit (Flinch), Death (Curled Collapse), Jump, Turn
# ---------------------------------------------------------------------------


def _arthropod_idle(
    armature_name: str,
    chains: dict[str, Any],
    *,
    frame_start: int,
    frame_end: int,
    cycles: float,
    action_name: str,
) -> dict[str, Any]:
    from mathutils import Vector

    from . import bpy_ops

    bpy = _bpy()
    bpy_ops.normalize_armature_before_animation(armature_name)
    bpy_ops.stash_if_needed_for_action(armature_name, action_name)
    bpy_ops.ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    bpy_ops._ensure_pose_mode(armature_name)

    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = bpy_ops._detect_forward(arm_obj, chains)
    fwd = Vector((forward.x, forward.y, 0.0))
    fwd = fwd.normalized() if fwd.length > 1e-6 else Vector((0.0, -1.0, 0.0))
    up = Vector((0.0, 0.0, 1.0))
    right_axis = up.cross(fwd).normalized()

    root_name = chains.get("body", [arm_obj.data.bones[0].name])[0]
    b_root = arm_obj.data.bones[root_name]
    r_root = b_root.matrix_local.to_quaternion()
    root_head = Vector(b_root.head_local)

    specs = _build_leg_specs(arm_obj, chains, root_name, fwd)
    tail = chains.get("tail", [])
    n_tail = len(tail)
    eff_cycles = float(max(1, round(cycles)))
    two_pi = 2.0 * math.pi
    prev: dict[str, Any] = {}

    for fi in range(total):
        t = fi / max(total - 1, 1)
        frame = frame_start + fi
        bpy.context.scene.frame_set(frame)
        ang = t * eff_cycles * two_pi
        slow = t * two_pi  # 1 slow predatory sway per full clip (exact seamless loop!)

        # Subtle armored breathing heave + slow weight shift (feet stay glued to ground via IK!).
        delta_root = up * (0.008 * math.sin(ang)) + right_axis * (0.009 * math.sin(slow))
        q_root_world = _axis_angle_quat(up, 0.025 * math.sin(slow)) @ _axis_angle_quat(
            right_axis, 0.015 * math.sin(ang)
        )
        _key_quat(arm_obj, root_name, _world_rot_to_basis(r_root, q_root_world), frame, prev)
        _key_loc_world(arm_obj, root_name, delta_root, frame)

        w_root = q_root_world @ r_root
        p_root = root_head + delta_root

        for spec in specs:
            _solve_leg_ik_frame(
                arm_obj,
                spec,
                p_target=spec.foot_rest,
                w_root=w_root,
                r_root=r_root,
                p_root=p_root,
                frame=frame,
                prev=prev,
            )

        # Pincers: alert breathing bob + slow predatory testing flex.
        _key_pincers_frame(
            arm_obj,
            chains,
            frame=frame,
            prev=prev,
            forward=fwd,
            right_axis=right_axis,
            spread_r=0.06 + 0.05 * math.sin(slow),
            spread_l=0.06 - 0.05 * math.sin(slow),
            raise_r=0.06 + 0.045 * math.sin(ang - 0.4),
            raise_l=0.06 + 0.045 * math.sin(ang + 0.2),
            pinch_r=0.08 * math.sin(ang),
            pinch_l=0.08 * math.sin(ang + 0.8),
        )

        # Tail: sinuous slow breathing bob & side-to-side menace sway.
        if n_tail > 0:
            pitches = [
                (0.032 * (0.4 + 0.6 * (ci / max(n_tail - 1, 1)))) * math.sin(ang - ci * 0.35) for ci in range(n_tail)
            ]
            yaws = [
                (0.055 * (0.3 + 0.7 * (ci / max(n_tail - 1, 1)))) * math.sin(slow - ci * 0.40) for ci in range(n_tail)
            ]
            _key_tail_frame(arm_obj, tail, frame=frame, prev=prev, right_axis=right_axis, pitches=pitches, yaws=yaws)

    bpy_ops.finalize_current_action_to_nla(armature_name)
    return chains


def _arthropod_roar(
    armature_name: str,
    chains: dict[str, Any],
    *,
    frame_start: int,
    frame_end: int,
    action_name: str,
) -> dict[str, Any]:
    """Scorpion defensive/predatory threat display: rear up, raise pincers, rattle tail high."""
    from mathutils import Vector

    from . import bpy_ops

    bpy = _bpy()
    bpy_ops.normalize_armature_before_animation(armature_name)
    bpy_ops.stash_if_needed_for_action(armature_name, action_name)
    bpy_ops.ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    bpy_ops._ensure_pose_mode(armature_name)

    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = bpy_ops._detect_forward(arm_obj, chains)
    fwd = Vector((forward.x, forward.y, 0.0))
    fwd = fwd.normalized() if fwd.length > 1e-6 else Vector((0.0, -1.0, 0.0))
    up = Vector((0.0, 0.0, 1.0))
    right_axis = up.cross(fwd).normalized()

    root_name = chains.get("body", [arm_obj.data.bones[0].name])[0]
    b_root = arm_obj.data.bones[root_name]
    r_root = b_root.matrix_local.to_quaternion()
    root_head = Vector(b_root.head_local)

    specs = _build_leg_specs(arm_obj, chains, root_name, fwd)
    tail = chains.get("tail", [])
    n_tail = len(tail)
    prev: dict[str, Any] = {}

    for fi in range(total):
        t = fi / max(total - 1, 1)
        frame = frame_start + fi
        bpy.context.scene.frame_set(frame)

        # Ramp up -> menacing hold with high-frequency hiss vibration -> settle back to rest.
        if t < 0.24:
            env = _smootherstep(t / 0.24)
            vib = 0.0
        elif t < 0.72:
            env = 1.0
            w = (t - 0.24) / 0.48
            vib = math.sin(w * math.pi) * math.sin(t * math.pi * 24.0)
        else:
            env = 1.0 - _smootherstep((t - 0.72) / 0.28)
            vib = 0.0

        delta_root = up * (0.045 * env) - fwd * (0.03 * env)
        q_root_world = _axis_angle_quat(right_axis, math.radians(-14.0) * env + math.radians(1.5) * vib)
        _key_quat(arm_obj, root_name, _world_rot_to_basis(r_root, q_root_world), frame, prev)
        _key_loc_world(arm_obj, root_name, delta_root, frame)

        w_root = q_root_world @ r_root
        p_root = root_head + delta_root

        for spec in specs:
            # Front legs brace slightly wider; all legs stay grounded.
            out_dir = right_axis * spec.side_sign
            p_target = spec.foot_rest + out_dir * (0.015 * env if spec.pair_idx == 0 else 0.0)
            _solve_leg_ik_frame(
                arm_obj,
                spec,
                p_target=p_target,
                w_root=w_root,
                r_root=r_root,
                p_root=p_root,
                frame=frame,
                prev=prev,
            )

        # Pincers thrust high & wide with aggressive snapping rattle!
        _key_pincers_frame(
            arm_obj,
            chains,
            frame=frame,
            prev=prev,
            forward=fwd,
            right_axis=right_axis,
            spread_r=0.28 * env + 0.04 * vib,
            spread_l=0.28 * env - 0.04 * vib,
            raise_r=0.46 * env + 0.05 * vib,
            raise_l=0.46 * env + 0.05 * vib,
            pinch_r=-0.25 * env + 0.22 * vib,
            pinch_l=-0.25 * env - 0.22 * vib,
        )

        # Tail rears tall & rattles side-to-side overhead!
        if n_tail > 0:
            pitches = []
            yaws = []
            for ci in range(n_tail):
                u = ci / max(n_tail - 1, 1)
                p = (math.radians(-16.0) * (1.0 - u) + math.radians(20.0) * u) * env
                y = math.radians(7.0) * u * vib + math.radians(6.0) * u * env * math.sin(t * math.pi * 4.0 + ci * 0.4)
                pitches.append(p)
                yaws.append(y)
            _key_tail_frame(arm_obj, tail, frame=frame, prev=prev, right_axis=right_axis, pitches=pitches, yaws=yaws)

    bpy_ops.finalize_current_action_to_nla(armature_name)
    return chains


def _arthropod_hit(
    armature_name: str,
    chains: dict[str, Any],
    *,
    frame_start: int,
    frame_end: int,
    intensity: float,
    action_name: str,
) -> dict[str, Any]:
    """Armored hit flinch: crisp recoil backward, tail snaps back, legs absorb."""
    from mathutils import Vector

    from . import bpy_ops

    bpy = _bpy()
    bpy_ops.normalize_armature_before_animation(armature_name)
    bpy_ops.stash_if_needed_for_action(armature_name, action_name)
    bpy_ops.ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    bpy_ops._ensure_pose_mode(armature_name)

    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = bpy_ops._detect_forward(arm_obj, chains)
    fwd = Vector((forward.x, forward.y, 0.0))
    fwd = fwd.normalized() if fwd.length > 1e-6 else Vector((0.0, -1.0, 0.0))
    up = Vector((0.0, 0.0, 1.0))
    right_axis = up.cross(fwd).normalized()

    root_name = chains.get("body", [arm_obj.data.bones[0].name])[0]
    b_root = arm_obj.data.bones[root_name]
    r_root = b_root.matrix_local.to_quaternion()
    root_head = Vector(b_root.head_local)

    specs = _build_leg_specs(arm_obj, chains, root_name, fwd)
    tail = chains.get("tail", [])
    n_tail = len(tail)
    prev: dict[str, Any] = {}

    for fi in range(total):
        t = fi / max(total - 1, 1)
        frame = frame_start + fi
        bpy.context.scene.frame_set(frame)

        if t < 0.25:
            r = _smoothstep(t / 0.25)
        else:
            w = (t - 0.25) / 0.75
            r = math.exp(-w * 3.2) * math.cos(w * math.pi * 1.5)
        r *= intensity

        delta_root = -fwd * (0.055 * r) - up * (0.025 * max(r, 0.0))
        q_root_world = _axis_angle_quat(up, math.radians(8.0) * r) @ _axis_angle_quat(
            right_axis, math.radians(-10.0) * r
        )
        _key_quat(arm_obj, root_name, _world_rot_to_basis(r_root, q_root_world), frame, prev)
        _key_loc_world(arm_obj, root_name, delta_root, frame)

        w_root = q_root_world @ r_root
        p_root = root_head + delta_root

        for spec in specs:
            _solve_leg_ik_frame(
                arm_obj,
                spec,
                p_target=spec.foot_rest,
                w_root=w_root,
                r_root=r_root,
                p_root=p_root,
                frame=frame,
                prev=prev,
            )

        _key_pincers_frame(
            arm_obj,
            chains,
            frame=frame,
            prev=prev,
            forward=fwd,
            right_axis=right_axis,
            spread_r=0.22 * r,
            spread_l=0.22 * r,
            raise_r=0.24 * r,
            raise_l=0.24 * r,
        )

        if n_tail > 0:
            pitches = [math.radians(-16.0) * (1.0 - 0.4 * (ci / max(n_tail - 1, 1))) * r for ci in range(n_tail)]
            yaws = [math.radians(-10.0) * (ci / max(n_tail - 1, 1)) * r for ci in range(n_tail)]
            _key_tail_frame(arm_obj, tail, frame=frame, prev=prev, right_axis=right_axis, pitches=pitches, yaws=yaws)

    bpy_ops.finalize_current_action_to_nla(armature_name)
    return chains


def _arthropod_death(
    armature_name: str,
    chains: dict[str, Any],
    *,
    frame_start: int,
    frame_end: int,
    side: float,
    action_name: str,
) -> dict[str, Any]:
    """Terminal arthropod death: body drops, legs curl inward, arched tail collapses limp."""
    from mathutils import Vector

    from . import bpy_ops

    bpy = _bpy()
    bpy_ops.normalize_armature_before_animation(armature_name)
    bpy_ops.stash_if_needed_for_action(armature_name, action_name)
    bpy_ops.ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    bpy_ops._ensure_pose_mode(armature_name)

    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = bpy_ops._detect_forward(arm_obj, chains)
    fwd = Vector((forward.x, forward.y, 0.0))
    fwd = fwd.normalized() if fwd.length > 1e-6 else Vector((0.0, -1.0, 0.0))
    up = Vector((0.0, 0.0, 1.0))
    right_axis = up.cross(fwd).normalized()

    root_name = chains.get("body", [arm_obj.data.bones[0].name])[0]
    b_root = arm_obj.data.bones[root_name]
    r_root = b_root.matrix_local.to_quaternion()
    root_head = Vector(b_root.head_local)

    specs = _build_leg_specs(arm_obj, chains, root_name, fwd)
    tail = chains.get("tail", [])
    n_tail = len(tail)
    tail_rest_angles = _tail_rest_sagittal_angles(arm_obj, tail, fwd) if n_tail > 0 else []
    death_tail_knots = [
        (0.00, -82.0),
        (0.20, -100.0),
        (0.45, -104.0),
        (0.75, -96.0),
        (1.00, -78.0),
    ]
    tail_death_angles = [_eval_knots_deg(ci / max(n_tail - 1, 1), death_tail_knots) for ci in range(n_tail)]
    roll_side = 1.0 if side >= 0.0 else -1.0
    # Drop body safely down to ground level (keep carapace resting on floor, never sinking far underground).
    body_drop = min(max(root_head.z - 0.06, 0.04), 0.14)
    prev: dict[str, Any] = {}

    for fi in range(total):
        t = fi / max(total - 1, 1)
        frame = frame_start + fi
        bpy.context.scene.frame_set(frame)

        # Stagger (0..0.18) -> collapse with damped settle (0.18..0.80) -> terminal hold (0.80..1.00).
        if t < 0.16:
            stagger = math.sin((t / 0.16) * math.pi)
            collapse = 0.0
        elif t < 0.78:
            stagger = 0.0
            w = (t - 0.16) / 0.62
            collapse = _smootherstep(min(w / 0.68, 1.0))
            if w > 0.68:
                bw = (w - 0.68) / 0.32
                collapse += 0.045 * math.exp(-bw * 5.0) * math.cos(bw * math.pi * 2.0)
        else:
            stagger = 0.0
            collapse = 1.0  # exact terminal hold (last frames identical)

        body_pitch = math.radians(-8.0) * stagger + math.radians(6.0) * collapse
        delta_root = up * (0.02 * stagger - body_drop * collapse)
        q_root_world = _axis_angle_quat(fwd, roll_side * math.radians(24.0) * collapse) @ _axis_angle_quat(
            right_axis, body_pitch
        )
        _key_quat(arm_obj, root_name, _world_rot_to_basis(r_root, q_root_world), frame, prev)
        _key_loc_world(arm_obj, root_name, delta_root, frame)

        w_root = q_root_world @ r_root
        p_root = root_head + delta_root

        # Arachnid hydraulic loss: all 8 legs curl INWARD tight beside/under carapace as body collapses.
        for spec in specs:
            out_dir = right_axis * spec.side_sign
            leg_delay = min(0.14, spec.pair_idx * 0.03)
            if t < 0.16 + leg_delay:
                c_leg = 0.0
            elif t < 0.80:
                c_leg = _smootherstep(min((t - (0.16 + leg_delay)) / 0.45, 1.0))
            else:
                c_leg = 1.0
            p_target = spec.foot_rest - out_dir * (0.12 * c_leg) + up * (0.03 * c_leg)
            _solve_leg_ik_frame(
                arm_obj,
                spec,
                p_target=p_target,
                w_root=w_root,
                r_root=r_root,
                p_root=p_root,
                frame=frame,
                prev=prev,
                coxa_elev_rad=0.14 * c_leg,
                distal_curl_rad=1.25 * c_leg,
            )

        # Pincers droop slack and pull slightly inward.
        _key_pincers_frame(
            arm_obj,
            chains,
            frame=frame,
            prev=prev,
            forward=fwd,
            right_axis=right_axis,
            spread_r=-0.16 * collapse,
            spread_l=-0.16 * collapse,
            raise_r=-0.22 * collapse,
            raise_l=-0.22 * collapse,
            pinch_r=0.22 * collapse,
            pinch_l=0.22 * collapse,
        )

        # Heavy arched tail uncoils and slumps limp down onto the ground behind/beside the body.
        if n_tail > 0:
            c_tail = _smootherstep(min(max(t - 0.18, 0.0) / 0.56, 1.0)) if t < 0.80 else 1.0
            t_angs = [
                r_ang + (d_ang - r_ang) * c_tail
                for r_ang, d_ang in zip(tail_rest_angles, tail_death_angles, strict=True)
            ]
            pitches = _sagittal_targets_to_local_deltas(tail_rest_angles, t_angs, body_pitch=body_pitch)
            yaws = [
                roll_side * math.radians(16.0) * (0.35 + 0.65 * (ci / max(n_tail - 1, 1))) * c_tail
                for ci in range(n_tail)
            ]
            _key_tail_frame(arm_obj, tail, frame=frame, prev=prev, right_axis=right_axis, pitches=pitches, yaws=yaws)

    bpy_ops.finalize_current_action_to_nla(armature_name)
    return chains


def _arthropod_jump(
    armature_name: str,
    chains: dict[str, Any],
    *,
    frame_start: int,
    frame_end: int,
    action_name: str,
) -> dict[str, Any]:
    """Arthropod pounce/jump: crouch -> launch -> airborne tuck -> land absorption."""
    from mathutils import Vector

    from . import bpy_ops

    bpy = _bpy()
    bpy_ops.normalize_armature_before_animation(armature_name)
    bpy_ops.stash_if_needed_for_action(armature_name, action_name)
    bpy_ops.ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    bpy_ops._ensure_pose_mode(armature_name)

    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = bpy_ops._detect_forward(arm_obj, chains)
    fwd = Vector((forward.x, forward.y, 0.0))
    fwd = fwd.normalized() if fwd.length > 1e-6 else Vector((0.0, -1.0, 0.0))
    up = Vector((0.0, 0.0, 1.0))
    right_axis = up.cross(fwd).normalized()

    root_name = chains.get("body", [arm_obj.data.bones[0].name])[0]
    b_root = arm_obj.data.bones[root_name]
    r_root = b_root.matrix_local.to_quaternion()
    root_head = Vector(b_root.head_local)

    specs = _build_leg_specs(arm_obj, chains, root_name, fwd)
    tail = chains.get("tail", [])
    n_tail = len(tail)
    prev: dict[str, Any] = {}

    for fi in range(total):
        t = fi / max(total - 1, 1)
        frame = frame_start + fi
        bpy.context.scene.frame_set(frame)

        if t < 0.25:
            # Crouch anticipation.
            w = _smoothstep(t / 0.25)
            z_off = -0.045 * w
            pitch = math.radians(-6.0) * w
            tuck = 0.0
        elif t < 0.78:
            # Airborne arc.
            w = (t - 0.25) / 0.53
            arc = math.sin(w * math.pi)
            z_off = -0.045 * (1.0 - min(w * 4.0, 1.0)) + 0.16 * arc
            pitch = math.radians(-14.0) * math.cos(w * math.pi)
            tuck = arc
        else:
            # Landing absorption back to rest.
            w = (t - 0.78) / 0.22
            absorb = math.sin(w * math.pi)
            z_off = -0.04 * absorb
            pitch = math.radians(6.0) * absorb
            tuck = 0.0

        delta_root = up * z_off
        q_root_world = _axis_angle_quat(right_axis, pitch)
        _key_quat(arm_obj, root_name, _world_rot_to_basis(r_root, q_root_world), frame, prev)
        _key_loc_world(arm_obj, root_name, delta_root, frame)

        w_root = q_root_world @ r_root
        p_root = root_head + delta_root

        for spec in specs:
            out_dir = right_axis * spec.side_sign
            if tuck > 0.0:
                p_target = spec.foot_rest + up * (0.14 * tuck) - out_dir * (0.04 * tuck) - fwd * (0.04 * tuck)
            else:
                p_target = spec.foot_rest
            _solve_leg_ik_frame(
                arm_obj,
                spec,
                p_target=p_target,
                w_root=w_root,
                r_root=r_root,
                p_root=p_root,
                frame=frame,
                prev=prev,
                distal_curl_rad=0.45 * tuck,
            )

        _key_pincers_frame(
            arm_obj,
            chains,
            frame=frame,
            prev=prev,
            forward=fwd,
            right_axis=right_axis,
            spread_r=0.20 * tuck,
            spread_l=0.20 * tuck,
            raise_r=0.25 * tuck,
            raise_l=0.25 * tuck,
        )

        if n_tail > 0:
            pitches = [math.radians(-14.0) * tuck for _ in range(n_tail)]
            yaws = [0.0] * n_tail
            _key_tail_frame(arm_obj, tail, frame=frame, prev=prev, right_axis=right_axis, pitches=pitches, yaws=yaws)

    bpy_ops.finalize_current_action_to_nla(armature_name)
    return chains


def _arthropod_turn(
    armature_name: str,
    chains: dict[str, Any],
    *,
    frame_start: int,
    frame_end: int,
    direction: float,
    turn_amp: float,
    action_name: str,
) -> dict[str, Any]:
    """Turn-in-place: carapace pivots while alternating tetrapods shuffle in arc."""
    from mathutils import Vector

    from . import bpy_ops

    bpy = _bpy()
    bpy_ops.normalize_armature_before_animation(armature_name)
    bpy_ops.stash_if_needed_for_action(armature_name, action_name)
    bpy_ops.ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    bpy_ops._ensure_pose_mode(armature_name)

    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = bpy_ops._detect_forward(arm_obj, chains)
    fwd = Vector((forward.x, forward.y, 0.0))
    fwd = fwd.normalized() if fwd.length > 1e-6 else Vector((0.0, -1.0, 0.0))
    up = Vector((0.0, 0.0, 1.0))
    right_axis = up.cross(fwd).normalized()

    root_name = chains.get("body", [arm_obj.data.bones[0].name])[0]
    b_root = arm_obj.data.bones[root_name]
    r_root = b_root.matrix_local.to_quaternion()
    root_head = Vector(b_root.head_local)

    specs = _build_leg_specs(arm_obj, chains, root_name, fwd)
    n_r = sum(1 for s in specs if s.side == "r")
    n_l = sum(1 for s in specs if s.side == "l")
    phases_r, phases_l = _arthropod_phases(max(n_r, n_l, 1), wave_lag=0.04)
    tail = chains.get("tail", [])
    n_tail = len(tail)
    d = 1.0 if direction >= 0.0 else -1.0
    prev: dict[str, Any] = {}

    for fi in range(total):
        t = fi / max(total - 1, 1)
        frame = frame_start + fi
        bpy.context.scene.frame_set(frame)

        yaw = d * turn_amp * 0.45 * math.sin(t * math.pi * 2.0)
        q_root_world = _axis_angle_quat(up, yaw)
        _key_quat(arm_obj, root_name, _world_rot_to_basis(r_root, q_root_world), frame, prev)

        w_root = q_root_world @ r_root
        p_root = root_head

        for spec in specs:
            ph_list = phases_r if spec.side == "r" else phases_l
            phi = (t + ph_list[min(spec.pair_idx, len(ph_list) - 1)]) % 1.0
            # Opposite sides shuffle in opposite directions to pivot smoothly in place.
            s_dir = d * spec.side_sign
            if phi < 0.55:
                s = 0.05 * s_dir * (1.0 - 2.0 * (phi / 0.55))
                h = 0.0
            else:
                w = (phi - 0.55) / 0.45
                s = 0.10 * s_dir * (_smoothstep(w) - 0.5)
                h = 0.035 * math.sin(w * math.pi)
            p_target = spec.foot_rest + fwd * s + up * h
            _solve_leg_ik_frame(
                arm_obj,
                spec,
                p_target=p_target,
                w_root=w_root,
                r_root=r_root,
                p_root=p_root,
                frame=frame,
                prev=prev,
            )

        _key_pincers_frame(
            arm_obj,
            chains,
            frame=frame,
            prev=prev,
            forward=fwd,
            right_axis=right_axis,
            spread_r=0.08 * math.sin(t * math.pi * 2.0),
            spread_l=-0.08 * math.sin(t * math.pi * 2.0),
        )

        if n_tail > 0:
            pitches = [0.0] * n_tail
            yaws = [-0.25 * yaw * (0.4 + 0.6 * (ci / max(n_tail - 1, 1))) for ci in range(n_tail)]
            _key_tail_frame(arm_obj, tail, frame=frame, prev=prev, right_axis=right_axis, pitches=pitches, yaws=yaws)

    bpy_ops.finalize_current_action_to_nla(armature_name)
    return chains


# ---------------------------------------------------------------------------
# Unified dispatch hook (mirrors humanoid.try_humanoid_clip)
# ---------------------------------------------------------------------------


def try_arthropod_clip(
    clip_kind: str,
    armature_name: str,
    chains: dict[str, Any],
    *,
    frame_start: int = 1,
    frame_end: int = 48,
    action_name: str = "Animator3D_Action",
    cycles: float = 2.0,
    intensity: float = 1.0,
    side: float = 1.0,
    direction: float = 1.0,
    turn_amp: float = 0.45,
) -> bool:
    """Dispatch clip generation to the arthropod engine when the rig is an arthropod.

    Returns True if handled, False so the caller falls through to the generic
    creature / quadruped generator.
    """
    bpy = _bpy()
    arm_obj = bpy.data.objects.get(armature_name)
    if arm_obj is None or arm_obj.type != "ARMATURE":
        return False

    from . import bpy_ops

    forward = bpy_ops._detect_forward(arm_obj, chains)
    if not is_arthropod(arm_obj, chains, forward=forward):
        return False

    if clip_kind == "walk":
        _arthropod_locomotion(
            armature_name,
            chains,
            frame_start=frame_start,
            frame_end=frame_end,
            cycles=cycles,
            action_name=action_name,
            is_run=False,
        )
        return True
    if clip_kind == "run":
        _arthropod_locomotion(
            armature_name,
            chains,
            frame_start=frame_start,
            frame_end=frame_end,
            cycles=cycles,
            action_name=action_name,
            is_run=True,
        )
        return True
    if clip_kind == "attack":
        _arthropod_attack(
            armature_name,
            chains,
            frame_start=frame_start,
            frame_end=frame_end,
            action_name=action_name,
        )
        return True
    if clip_kind == "idle":
        _arthropod_idle(
            armature_name,
            chains,
            frame_start=frame_start,
            frame_end=frame_end,
            cycles=cycles,
            action_name=action_name,
        )
        return True
    if clip_kind == "roar":
        _arthropod_roar(
            armature_name,
            chains,
            frame_start=frame_start,
            frame_end=frame_end,
            action_name=action_name,
        )
        return True
    if clip_kind == "hit":
        _arthropod_hit(
            armature_name,
            chains,
            frame_start=frame_start,
            frame_end=frame_end,
            intensity=intensity,
            action_name=action_name,
        )
        return True
    if clip_kind == "death":
        _arthropod_death(
            armature_name,
            chains,
            frame_start=frame_start,
            frame_end=frame_end,
            side=side,
            action_name=action_name,
        )
        return True
    if clip_kind == "jump":
        _arthropod_jump(
            armature_name,
            chains,
            frame_start=frame_start,
            frame_end=frame_end,
            action_name=action_name,
        )
        return True
    if clip_kind == "turn":
        _arthropod_turn(
            armature_name,
            chains,
            frame_start=frame_start,
            frame_end=frame_end,
            direction=direction,
            turn_amp=turn_amp,
            action_name=action_name,
        )
        return True
    return False
