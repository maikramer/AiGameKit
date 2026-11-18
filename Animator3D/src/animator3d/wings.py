"""Articulate the outstretched wings of a canonical bird or four-winged insect."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Wing:
    side: int
    shoulder: str
    middle: str
    hind: bool


def _outward_chains(arm, side, center):
    bones = arm.data.bones
    extent = max(side * (b.tail_local.x - center) for b in bones)
    chains = {}
    for tip in bones:
        if tip.children or side * (tip.tail_local.x - center) < 0.55 * extent:
            continue
        chain = []
        bone = tip
        while bone and side * (bone.tail_local.x - bone.head_local.x) > 0.012 * extent:
            chain.append(bone)
            bone = bone.parent
        if not chain:
            continue
        chain.reverse()
        root = chain[0]
        old = chains.get(root.name)
        if old is None or side * (tip.tail_local.x - old[-1].tail_local.x) > 0:
            chains[root.name] = chain
    return sorted(chains.values(), key=lambda c: side * c[-1].tail_local.x, reverse=True)


def _split_segment(arm, source, name):
    """Insert a deform joint and crossfade the source weights across its hinge."""
    import bpy

    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bones = arm.data.edit_bones
    segment = bones[source]
    head, tail = segment.head.copy(), segment.tail.copy()
    pivot = head.lerp(tail, 0.5)
    children = list(segment.children)
    middle = bones.new(name)
    middle.head, middle.tail, middle.roll = pivot, tail, segment.roll
    # Detach connected children before shortening their old parent.
    for child in children:
        child.parent = middle
    segment.tail = pivot
    middle.parent = segment
    middle.use_connect = True
    bpy.ops.object.mode_set(mode="OBJECT")

    axis = tail - head
    for mesh in bpy.context.scene.objects:
        if mesh.type != "MESH" or mesh.find_armature() != arm:
            continue
        group = mesh.vertex_groups.get(source)
        if group is None:
            continue
        distal = mesh.vertex_groups.new(name=name)
        to_arm = arm.matrix_world.inverted() @ mesh.matrix_world
        for vert in mesh.data.vertices:
            weight = next((g.weight for g in vert.groups if g.group == group.index), 0.0)
            if weight <= 0:
                continue
            t = (to_arm @ vert.co - head).dot(axis) / axis.length_squared
            t = min(1.0, max(0.0, (t - 0.25) * 2))
            share = t * t * (3 - 2 * t)
            if share > 0:
                group.add([vert.index], weight * (1 - share), "REPLACE")
                distal.add([vert.index], weight * share, "REPLACE")


def prepare_wings(arm, *, four_wings):
    """Find independently weighted lobes and give each a mid-span joint."""
    center = (min(b.head_local.x for b in arm.data.bones) + max(b.head_local.x for b in arm.data.bones)) * 0.5
    planned = []
    for side, suffix in ((-1, "R"), (1, "L")):
        count = 2 if four_wings else 1
        chains = _outward_chains(arm, side, center)[:count]
        if len(chains) != count:
            raise ValueError(f"Expected {count} outward wing chains on side {suffix}")
        # Canonical +Z in glTF is -Y in Blender. The rear lobe lies aft (+Y).
        chains.sort(key=lambda chain: (chain[0].head_local.y + chain[-1].tail_local.y) * 0.5)
        for index, chain in enumerate(chains):
            hind = index == 1
            name = ("HindwingMid" if hind else "WingMid") + "." + suffix
            midpoint = (chain[0].head_local.x + chain[-1].tail_local.x) * 0.5
            segment = min(chain, key=lambda b: abs((b.head_local.x + b.tail_local.x) * 0.5 - midpoint))
            planned.append((Wing(side, chain[0].name, name, hind), segment.name))
    # Edit mode invalidates Bone wrappers, hence only names cross this boundary.
    for wing, segment_name in planned:
        if wing.middle not in arm.data.bones:
            _split_segment(arm, segment_name, wing.middle)
    return [wing for wing, _ in planned]


def stroke(t, motion, *, insect, hind):
    """Return shoulder lift/sweep and relative mid-wing bend/sweep, in radians."""
    phase = 2 * math.tau * t - (0.23 if hind else 0)
    beat, lag = math.sin(phase), math.cos(phase)
    if insect:
        if motion == "soar":
            return 0.30 + 0.20 * beat, 0.01, -0.05 * lag, 0.0
        return 0.35 + (0.81 if hind else 0.92) * beat, 0.025 * beat, -0.14 * lag, 0.015 * beat
    if motion == "perch":
        return -0.32 + 0.025 * math.sin(math.tau * t), 1.23, -0.11, 0.23
    if motion == "soar":
        bank = math.sin(math.tau * t)
        return 0.025 + 0.14 * bank, 0.03, -0.065 * math.cos(math.tau * t), 0.04
    # The tip trails the shoulder at reversal. Sweep it aft during recovery
    # and extend it again for the downstroke.
    lift = 0.07 + 0.96 * beat
    sweep = 0.10 + 0.10 * lag
    bend = -0.37 * lag - 0.13 * beat
    tip_sweep = 0.25 + 0.22 * lag
    if motion == "land":
        settle = t * t * (3 - 2 * t)
        lift, sweep = (1 - settle) * lift - settle * 0.32, (1 - settle) * sweep + settle * 1.23
        bend, tip_sweep = (1 - settle) * bend - settle * 0.11, (1 - settle) * tip_sweep + settle * 0.23
    return lift, sweep, bend, tip_sweep
