"""The hand envelope includes fingers and clears the lap in seated gestures."""

from __future__ import annotations

import numpy as np
import pytest
from animator3d.hand_clearance import _hand_goal, _stroke_goal


@pytest.mark.parametrize("side", [-1.0, 1.0])
def test_fingers_clear_belly_and_knee_on_both_sides(side: float) -> None:
    shoulder = np.array([side * 0.22, 0.0, 1.0])
    wrist = np.array([side * 0.20, 0.09, 0.78])
    offsets = np.array([[-0.03, -0.04, -0.06], [0.03, 0.04, 0.01]])
    torso = np.array([[side * 0.16, 0.20, 0.76]])
    lap = np.array([[side * 0.16, 0.28, 0.75]])
    goal = _hand_goal(wrist, shoulder, 0.50, side, 0.44, wrist + offsets, torso, lap)
    assert goal is not None
    assert (goal + offsets)[:, 1].min() > torso[0, 1]
    assert (goal + offsets)[:, 2].min() > lap[0, 2]
    assert np.linalg.norm(goal - shoulder) < 0.50


def test_hand_already_in_front_keeps_its_position() -> None:
    wrist = np.array([0.20, 0.42, 0.80])
    torso = np.array([[0.18, 0.15, 0.80]])
    assert _hand_goal(wrist, np.array([0.22, 0.0, 1.0]), 0.60, 1, 0.44, wrist[None], torso, np.empty((0, 3))) is None


@pytest.mark.parametrize("side", [-1.0, 1.0])
def test_row_returns_beside_ribs_then_extends_in_front(side: float) -> None:
    shoulder = np.array([side * 0.20, 0.0, 0.0])
    length = 0.50
    palm = np.array([[-0.04, -0.04, -0.08], [0.04, 0.04, 0.02]])
    ribs = np.array([[side * 0.24, 0.06, -0.22]])
    lap = np.empty((0, 3))
    finish, catch = [_stroke_goal("row", t, shoulder, length, side, palm, ribs, lap) for t in (0.0, 1.0)]
    assert catch[1] - finish[1] > 0.75 * length
    assert side * finish[0] - 0.04 > side * ribs[0, 0]
    assert abs(catch[0]) < abs(finish[0])
    assert np.linalg.norm(catch - shoulder) < length


@pytest.mark.parametrize("phase", [0.0, 0.5, 1.0])
def test_hit_wrists_remain_low_during_recoil_and_recovery(phase: float) -> None:
    shoulder = np.array([0.22, 0.0, 0.0])
    length = 0.43
    empty = np.empty((0, 3))
    goal = _stroke_goal("hit", phase, shoulder, length, 1, np.zeros((1, 3)), empty, empty)
    assert goal[2] <= shoulder[2] - 0.60 * length
    assert np.linalg.norm(goal - shoulder) < length


def test_relaxed_wrist_limits_bend_independent_of_quaternion_sign() -> None:
    pytest.importorskip("bpy")
    from animator3d.hand_clearance import _relaxed_wrist
    from mathutils import Quaternion

    rotation = Quaternion((0, 1, 1), np.deg2rad(72))
    for q in (rotation, -rotation):
        assert np.rad2deg(_relaxed_wrist(q).angle) == pytest.approx(16, abs=1e-3)
    subtle = _relaxed_wrist(Quaternion((1, 0, 0), np.deg2rad(10)))
    assert 0 < np.rad2deg(subtle.angle) < 10


def test_shoulder_depression_and_forearm_roll_preserve_joint_lengths() -> None:
    bpy = pytest.importorskip("bpy")
    from animator3d.hand_clearance import _level_palm, _relax_collar, _roll_forearm
    from mathutils import Quaternion, Vector

    armature = bpy.data.armatures.new("gesture_test")
    arm = bpy.data.objects.new("gesture_test", armature)
    bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        parent = None
        for name, head, tail in [
            ("chest", (0, 0, 0), (0, 0, 1)),
            ("collar", (0, 0, 1), (0.2, 0, 0.98)),
            ("upper", (0.2, 0, 0.98), (0.42, 0, 0.9)),
            ("lower", (0.42, 0, 0.9), (0.58, -0.15, 0.85)),
            ("hand", (0.58, -0.15, 0.85), (0.65, -0.2, 0.83)),
        ]:
            bone = armature.edit_bones.new(name)
            bone.head, bone.tail, bone.parent = head, tail, parent
            parent = bone
        bpy.ops.object.mode_set(mode="OBJECT")
        collar, upper, lower, hand = (arm.pose.bones[name] for name in ("collar", "upper", "lower", "hand"))
        # Raise the shoulder in armature space regardless of the bone roll.
        rest = collar.matrix.to_quaternion()
        collar.rotation_quaternion = rest.inverted() @ Quaternion((0, -1, 0), 0.35) @ rest
        bpy.context.view_layer.update()
        raised = upper.head.copy()
        length = (raised - collar.head).length
        _relax_collar(collar, upper, np.array([0, 0, 1]))
        assert upper.bone.head_local.z < upper.head.z < raised.z
        assert (upper.head - collar.head).length == pytest.approx(length)
        wrist = hand.head.copy()
        neutral = hand.rotation_quaternion.copy()
        axis = (hand.head - lower.head).normalized()
        palm = axis.cross(Vector((0, 0, 1))).normalized()
        local = hand.matrix.to_quaternion().inverted() @ palm
        goal = Quaternion(axis, 0.7) @ palm
        _roll_forearm(lower, hand, local, goal)
        assert (hand.head - wrist).length < 1e-5
        assert hand.rotation_quaternion.dot(neutral) == pytest.approx(1)
        assert (hand.matrix.to_quaternion() @ local).angle(goal) < palm.angle(goal)
        down = Vector((0, 0, -1))
        _roll_forearm(lower, hand, local, down)
        _level_palm(hand, local, down)
        assert (hand.matrix.to_quaternion() @ local).dot(down) > 0.9999
        assert (hand.head - wrist).length < 1e-5
    finally:
        if arm.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.data.objects.remove(arm, do_unlink=True)
        bpy.data.armatures.remove(armature)
