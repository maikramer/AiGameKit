"""Exercise the wing and swimming presets on inferred rigs with arbitrary bone names."""

from __future__ import annotations

import pytest

bpy = pytest.importorskip("bpy")


def make_armature(segments):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm = bpy.data.objects.new("Creature", bpy.data.armatures.new("Bones"))
    bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for name, head, tail, parent in segments:
        bone = arm.data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        if parent:
            bone.parent = arm.data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def sample_tips(arm, action_name, frames, bones):
    arm.animation_data.action = None
    for track in arm.animation_data.nla_tracks:
        track.mute = track.strips[0].action.name != action_name
    tips = []
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        tips.append([arm.pose.bones[name].tail.copy() for name in bones])
    return tips


def test_spread_wings_lift_together_and_fold_behind_body():
    from animator3d.bpy_ops import bird_keyframes

    arm = make_armature(
        [
            ("body", (0, 0, 0), (0, 0.4, 0), None),
            ("a", (0.1, 0, 0), (1, 0, 0), "body"),
            ("b", (-0.1, 0, 0), (-1, 0, 0), "body"),
        ]
    )
    for motion in ("hover", "perch"):
        bird_keyframes(arm.name, frame_start=1, frame_end=25, motion=motion, action_name=motion)
    start, up, down, end = sample_tips(arm, "hover", (1, 4, 10, 25), ("WingMid.L", "WingMid.R"))
    assert all(high.z - low.z > 0.8 for high, low in zip(up, down, strict=True))
    assert all((a - b).length < 1e-5 for a, b in zip(start, end, strict=True))
    (folded,) = sample_tips(arm, "perch", (1,), ("WingMid.L", "WingMid.R"))
    assert all(abs(tip.x) < 0.5 and tip.y > 0.7 for tip in folded)


def test_four_wings_have_separate_weighted_and_animated_rear_hinges():
    from animator3d.bpy_ops import bird_keyframes

    segments = [("body", (0, 0, 0), (0, 0.2, 0), None)]
    for side, suffix in ((1, "L"), (-1, "R")):
        for y, lobe in ((0.2, "rear"), (0.0, "fore")):
            segments.append((lobe + suffix, (side * 0.1, 0, 0), (side, y, 0), "body"))
    arm = make_armature(segments)
    mesh = bpy.data.meshes.new("Wing surfaces")
    mesh.from_pydata([(x, y, 0) for x in (0.95, -0.95) for y in (0.0, 0.2)], [], [])
    obj = bpy.data.objects.new("Membranes", mesh)
    bpy.context.collection.objects.link(obj)
    obj.modifiers.new("Skin", "ARMATURE").object = arm
    for i, name in enumerate(("foreL", "rearL", "foreR", "rearR")):
        obj.vertex_groups.new(name=name).add([i], 1.0, "REPLACE")
    for motion in ("hover", "soar"):
        bird_keyframes(arm.name, frame_start=1, frame_end=25, motion=motion, action_name=motion, four_wings=True)
    joints = ("WingMid.L", "HindwingMid.L", "WingMid.R", "HindwingMid.R")
    assert len(arm.data.bones) == len(segments) + 4
    for i, name in enumerate(joints):
        assert obj.vertex_groups[name].weight(i) == pytest.approx(1.0)
    a, b, end = sample_tips(arm, "hover", (1, 4, 25), joints)
    assert all(up.z - start.z > 0.5 for start, up in zip(a, b, strict=True))
    assert all((first - last).length < 1e-5 for first, last in zip(a, end, strict=True))
    assert abs(a[0].z - a[1].z) > 0.08


def test_swim_wave_moves_tail_laterally_and_closes():
    from animator3d.bpy_ops import swim_keyframes

    arm = make_armature(
        [(f"segment{i}", (0, i * 0.2, 0), (0, (i + 1) * 0.2, 0), f"segment{i - 1}" if i else None) for i in range(5)]
    )
    swim_keyframes(arm.name, frame_start=1, frame_end=25, action_name="swim")
    samples = sample_tips(arm, "swim", range(1, 26), ("segment0", "segment4"))
    assert all(abs(nose.x) < 1e-6 and abs(tail.z) < 1e-6 for nose, tail in samples)
    excursion = [tail.x for _, tail in samples]
    assert max(excursion) - min(excursion) > 0.1
    assert (samples[-1][1] - samples[0][1]).length < 1e-5


def test_quadruped_walk_alternates_diagonal_paws_without_tucking_both_hindlegs():
    from animator3d.bpy_ops import quadruped_keyframes

    segments = [("body", (0, -0.3, 0.5), (0, 0.3, 0.5), None)]
    for i, (x, y) in enumerate([(-0.1, -0.3), (0.1, -0.3), (-0.1, 0.3), (0.1, 0.3)]):
        segments.extend(
            [
                (f"top{i}", (x, y, 0.5), (x, y, 0.25), "body"),
                (f"paw{i}", (x, y, 0.25), (x, y, 0.0), f"top{i}"),
            ]
        )
    arm = make_armature(segments)
    quadruped_keyframes(arm.name, frame_start=1, frame_end=49, motion="walk", action_name="walk")
    a, b, end = sample_tips(arm, "walk", (7, 19, 31), ("paw0", "paw1", "paw2", "paw3"))
    motions = [v.y - u.y for u, v in zip(a, b, strict=True)]
    assert motions[0] * motions[1] < 0 and motions[0] * motions[3] > 0
    assert all(abs(d) > 0.2 for d in motions)
    assert max(p.z for pose in (a, b, end) for p in pose) < 0.05
