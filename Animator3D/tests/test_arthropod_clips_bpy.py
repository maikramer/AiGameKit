"""Arthropod / scorpion procedural clips (facing, classification, 8-leg gait, tail-stinger strike).

Validates the full scorpion animation pipeline on a synthetic 57-bone arachnid rig
(8 five-segment walking legs + 2 four-segment pincers + 7-segment arched overhead
stinger tail + 2 one-bone carapace stubs) with real bpy:

- ``canonicalize_creature_facing`` handles curled overhead tails (``-X`` -> ``+90°``,
  ``+X`` -> ``-90°``, backwards ``+Y`` -> ``180°``, idempotent on canonical ``-Y``).
- ``_classify_bone_chains`` classifies all 8 legs (4/side sorted front -> back),
  both pincers, the 7-bone tail, ignores 1-bone stubs, and never puts a leg in ``spine``.
- Walk & Run key all 8 legs, both pincers, all 7 tail segments, and body; loop
  seamlessly; keep feet grounded without penetration; move primarily forward
  (``dY > dX``); and maintain alternating tetrapod anti-phase.
- Attack coils the tail back in windup, whips the stinger far forward past the
  head while clearing the carapace, flares/clamps pincers symmetrically, lunges
  the body, and keeps all 8 legs planted via IK.
- All 8 clips pass ``ik.enforce_joint_limits`` with zero forced corrections.
"""

from __future__ import annotations

import math
from importlib.util import find_spec

import pytest

pytestmark = pytest.mark.skipif(find_spec("bpy") is None, reason="requer bpy (venv do Animator3D)")


def _build_scorpion(facing: str = "-y") -> str:
    """Create a realistic 57-bone scorpion armature facing ``-y`` (canonical), ``+y``, ``-x``, or ``+x``."""
    import bpy
    from mathutils import Matrix, Vector

    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm_data = bpy.data.armatures.new("ScorpionArm")
    obj = bpy.data.objects.new("Scorpion", arm_data)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj

    angle_deg = {"-y": 0.0, "+y": 180.0, "-x": -90.0, "+x": 90.0}[facing]
    rot = Matrix.Rotation(math.radians(angle_deg), 4, "Z")

    bpy.ops.object.mode_set(mode="EDIT")
    ebs = arm_data.edit_bones

    def bone(name: str, h, t, parent: str | None) -> None:
        eb = ebs.new(name)
        eb.head = rot @ Vector(h)
        eb.tail = rot @ Vector(t)
        if parent:
            eb.parent = ebs[parent]

    # Canonical orientation: forward = -Y, back = +Y, right/left = +/-X, up = +Z.
    bone("Root", (0.0, 0.27, 0.14), (0.0, 0.20, 0.14), None)

    # Deliberately scramble Left/Right names like Rigging3D's even/odd creature renamer
    # to verify classification relies on canonical geometry, not bone names.
    leg_y_positions = [
        (0.05, -0.11),  # pair 1 (frontmost)
        (0.13, 0.15),  # pair 2 (mid-front)
        (0.21, 0.37),  # pair 3 (mid-back)
        (0.28, 0.49),  # pair 4 (rearmost)
    ]
    for side_idx, sx in enumerate((1.0, -1.0)):
        for pi, (hip_y, foot_y) in enumerate(leg_y_positions):
            # Intentionally mixed names!
            prefix = f"Leg{'Right' if (pi + side_idx) % 2 == 0 else 'Left'}{pi + 1}_{side_idx}"
            mid_y = 0.5 * (hip_y + foot_y)
            pts = [
                (sx * 0.10, hip_y, 0.11),
                (sx * 0.15, hip_y + 0.02, 0.09),
                (sx * 0.23, mid_y, 0.19),
                (sx * 0.30, foot_y - 0.02, 0.12),
                (sx * 0.31, foot_y - 0.01, 0.05),
                (sx * 0.32, foot_y, -0.03),
            ]
            parent = "Root"
            for si in range(5):
                bn = prefix if si == 0 else f"{prefix}_{si}"
                bone(bn, pts[si], pts[si + 1], parent)
                parent = bn

    # Two 4-segment forward pincers (elevated horizontally, reaching to Y = -0.49).
    for prefix, sx in (("LegRight5", 1.0), ("LegLeft5", -1.0)):
        pts = [
            (sx * 0.10, -0.03, 0.11),
            (sx * 0.18, -0.11, 0.11),
            (sx * 0.22, -0.20, 0.15),
            (sx * 0.20, -0.35, 0.16),
            (sx * 0.18, -0.49, 0.17),
        ]
        parent = "Root"
        for si in range(4):
            bn = prefix if si == 0 else f"{prefix}_{si}"
            bone(bn, pts[si], pts[si + 1], parent)
            parent = bn

    # Two 1-bone carapace helper stubs (must be ignored by leg/spine classifiers).
    bone("LegLeft6", (0.06, 0.30, 0.16), (0.04, 0.32, 0.22), "Root")
    bone("LegRight6", (-0.06, 0.29, 0.17), (-0.08, 0.23, 0.18), "Root")

    # Seven-segment arched overhead stinger tail (named Spine..Spine7 like Rigging3D).
    tail_pts = [
        (0.0, 0.42, 0.17),
        (0.0, 0.51, 0.26),
        (0.0, 0.54, 0.38),
        (0.0, 0.49, 0.51),
        (0.0, 0.38, 0.56),
        (0.0, 0.23, 0.58),
        (0.0, 0.12, 0.52),
        (0.0, 0.01, 0.46),
    ]
    parent = "Root"
    for ti in range(7):
        bn = "Spine" if ti == 0 else f"Spine{ti + 1}"
        bone(bn, tail_pts[ti], tail_pts[ti + 1], parent)
        parent = bn

    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return obj.name


def _get_action(arm_name: str, clip_name: str):
    import bpy

    ad = bpy.data.objects[arm_name].animation_data
    return next(s.action for t in ad.nla_tracks for s in t.strips if s.action and s.action.name == clip_name)


def _activate_clip(arm_name: str, clip_name: str) -> None:
    import bpy

    arm = bpy.data.objects[arm_name]
    arm.animation_data.action = None
    for track in arm.animation_data.nla_tracks:
        track.mute = track.strips[0].action.name != clip_name


@pytest.mark.parametrize("start_facing,expected_deg", [("-x", 90.0), ("+x", -90.0), ("+y", 180.0), ("-y", 0.0)])
def test_canonicalize_curled_tail_scorpion_facing(start_facing: str, expected_deg: float) -> None:
    """Curled overhead tail must canonicalize pincers to -Y (glTF +Z), never backwards."""
    import bpy
    from animator3d import bpy_ops

    arm_name = _build_scorpion(facing=start_facing)
    res = bpy_ops.canonicalize_creature_facing(arm_name)
    assert res["rotated_deg"] == pytest.approx(expected_deg)

    arm = bpy.data.objects[arm_name]
    # After canonicalization, pincers sit at -Y (front) and tail base sits at +Y (rear).
    assert arm.data.bones["LegRight5_3"].tail_local.y < -0.35
    assert arm.data.bones["Spine"].head_local.y > 0.30
    # Idempotency check.
    res2 = bpy_ops.canonicalize_creature_facing(arm_name)
    assert res2["rotated_deg"] == pytest.approx(0.0)


def test_classify_scorpion_chains_recovers_all_limbs() -> None:
    import bpy
    from animator3d import arthropod, bpy_ops

    arm_name = _build_scorpion(facing="-y")
    arm = bpy.data.objects[arm_name]
    chains = bpy_ops._classify_bone_chains(arm_name)

    assert chains["body"] == ["Root"]
    assert len(chains["legs_r"]) == 4
    assert len(chains["legs_l"]) == 4
    assert "pincers_r" in chains and len(chains["pincers_r"]) == 4
    assert "pincers_l" in chains and len(chains["pincers_l"]) == 4
    assert chains["tail"] == ["Spine", "Spine2", "Spine3", "Spine4", "Spine5", "Spine6", "Spine7"]
    assert "spine" not in chains  # No leg may hijack spine!

    # Verify legs are sorted strictly front (-Y) -> back (+Y) on each side and on the right X side.
    for side, sign in (("r", 1.0), ("l", -1.0)):
        ys = [arm.data.bones[leg[0]].head_local.y for leg in chains[f"legs_{side}"]]
        assert ys == sorted(ys), f"legs_{side} must be ordered front (-Y) to back (+Y): {ys}"
        for leg in chains[f"legs_{side}"]:
            assert arm.data.bones[leg[-1]].head_local.x * sign > 0.1

    fwd = bpy_ops._detect_forward(arm, chains)
    assert fwd.y < -0.99
    assert arthropod.is_arthropod(arm, chains, forward=fwd)
    assert arthropod.has_stinger_tail(arm, chains, forward=fwd)


@pytest.mark.parametrize(
    "gen,clip,frame_end",
    [
        ("walk_cycle_keyframes", "Animator3D_Walk", 48),
        ("run_cycle_keyframes", "Animator3D_Run", 36),
    ],
)
def test_scorpion_locomotion_all_8_legs_grounded_and_looping(gen: str, clip: str, frame_end: int) -> None:
    import bpy
    import numpy as np
    from animator3d import bpy_ops

    arm_name = _build_scorpion(facing="-y")
    arm = bpy.data.objects[arm_name]
    chains = getattr(bpy_ops, gen)(arm_name, frame_end=frame_end, action_name=clip)
    _activate_clip(arm_name, clip)

    peak_frames: dict[str, list[int]] = {"r": [], "l": []}
    for side in ("r", "l"):
        for leg in chains[f"legs_{side}"]:
            tip = leg[-1]
            b = arm.data.bones[tip]
            alpha = b.head_local.z / (b.head_local.z - b.tail_local.z)
            pts = []
            for f in range(1, frame_end + 1):
                bpy.context.scene.frame_set(f)
                pb = arm.pose.bones[tip]
                p = pb.head.lerp(pb.tail, alpha)
                pts.append((p.x, p.y, p.z))
            arr = np.array(pts)
            dx = float(arr[:, 0].max() - arr[:, 0].min())
            dy = float(arr[:, 1].max() - arr[:, 1].min())
            dz = float(arr[:, 2].max() - arr[:, 2].min())
            # Seamless loop, grounded stance, meaningful lift, predominantly forward stride.
            assert float(np.linalg.norm(arr[0] - arr[-1])) < 1e-3
            assert float(arr[:, 2].min()) > -0.015
            assert dz > 0.03
            assert dy > dx * 1.4, f"{clip} leg {leg[0]} walked sideways (dY={dy:.3f}, dX={dx:.3f})"
            peak_frames[side].append(int(np.argmax(arr[:, 2])))

    # Alternating tetrapod: adjacent legs on same side must NOT peak together.
    cyc_len = frame_end / (3.0 if clip == "Animator3D_Run" else 2.0)
    for side in ("r", "l"):
        for i in range(3):
            diff = abs(peak_frames[side][i] - peak_frames[side][i + 1]) % cyc_len
            sep = min(diff, cyc_len - diff) / cyc_len
            assert sep > 0.25, f"adjacent legs on {side} peaked too close (sep={sep:.2f})"


def test_scorpion_attack_whips_stinger_overhead_past_head() -> None:
    import bpy
    from animator3d import bpy_ops

    arm_name = _build_scorpion(facing="-y")
    arm = bpy.data.objects[arm_name]
    chains = bpy_ops.attack_keyframes(arm_name, frame_end=48, action_name="Animator3D_Attack")
    _activate_clip(arm_name, "Animator3D_Attack")

    # Rest (F1) vs Windup coil (F16) vs Overhead strike peak (F25) vs Return (F48).
    def sample(f: int):
        bpy.context.scene.frame_set(f)
        tip = arm.pose.bones["Spine7"].tail.copy()
        mid = arm.pose.bones["Spine3"].tail.copy()
        pr = arm.pose.bones[chains["pincers_r"][-1]].tail.copy()
        pl = arm.pose.bones[chains["pincers_l"][-1]].tail.copy()
        return tip, mid, pr, pl

    tip_1, _, pr_1, pl_1 = sample(1)
    tip_coil, _, pr_coil, pl_coil = sample(16)
    tip_hit, mid_hit, pr_hit, pl_hit = sample(25)
    tip_end, _, _, _ = sample(48)

    # Windup pulls stinger back (+Y) and flares pincers wide and up symmetrically.
    assert tip_coil.y > tip_1.y + 0.15
    assert pr_coil.x > pr_1.x + 0.10 and pl_coil.x < pl_1.x - 0.10
    assert pr_coil.z > pr_1.z + 0.15 and pl_coil.z > pl_1.z + 0.15

    # Strike drives stinger far forward past the head (-Y < -0.35) while mid-tail clears carapace (Z > 0.28).
    assert tip_hit.y < -0.35, f"stinger did not reach forward enough: {tip_hit}"
    assert tip_hit.z > 0.05, f"stinger plunged underground: {tip_hit}"
    assert mid_hit.z > 0.28, f"mid-tail clipped carapace roof: {mid_hit}"
    # Pincers clamp inward without crossing midline!
    assert pr_hit.x > 0.02 and pl_hit.x < -0.02
    # Clean return to rest pose.
    assert (tip_end - tip_1).length < 1e-3


def test_scorpion_all_clips_are_ik_clean_and_death_holds() -> None:
    """Every generated scorpion clip must pass ik.enforce_joint_limits with 0 fixes."""
    import bpy
    from animator3d import bpy_ops, ik

    arm_name = _build_scorpion(facing="-y")
    clips = [
        (bpy_ops.breathe_idle_keyframes, 72, "Animator3D_BreatheIdle"),
        (bpy_ops.walk_cycle_keyframes, 48, "Animator3D_Walk"),
        (bpy_ops.run_cycle_keyframes, 36, "Animator3D_Run"),
        (bpy_ops.jump_keyframes, 36, "Animator3D_Jump"),
        (bpy_ops.attack_keyframes, 48, "Animator3D_Attack"),
        (bpy_ops.victory_roar_keyframes, 60, "Animator3D_Roar"),
        (bpy_ops.hit_keyframes, 16, "Animator3D_Hit"),
        (bpy_ops.death_keyframes, 48, "Animator3D_Death"),
    ]
    for fn, fe, name in clips:
        with bpy_ops.isolated_clip_pose(arm_name):
            fn(arm_name, frame_end=fe, action_name=name)

    for _, _, name in clips:
        stats = ik.enforce_joint_limits(arm_name, _get_action(arm_name, name))
        assert stats is not None and stats["chains"] == 8
        total_fixes = stats["hinge_fixes"] + stats["flips"] + stats["ball_clamps"]
        assert total_fixes == 0, f"{name} violated IK limits: {stats}"

    # Verify Death drops body, collapses tail downward, and holds final pose stably.
    _activate_clip(arm_name, "Animator3D_Death")
    arm = bpy.data.objects[arm_name]
    bpy.context.scene.frame_set(1)
    z_root_start = arm.pose.bones["Root"].head.z
    z_tip_start = arm.pose.bones["Spine7"].tail.z
    bpy.context.scene.frame_set(47)
    z_tip_47 = arm.pose.bones["Spine7"].tail.z
    bpy.context.scene.frame_set(48)
    z_root_end = arm.pose.bones["Root"].head.z
    z_tip_48 = arm.pose.bones["Spine7"].tail.z

    assert z_root_end < z_root_start - 0.05
    assert z_tip_48 < z_tip_start - 0.25
    assert abs(z_tip_48 - z_tip_47) < 1e-4
