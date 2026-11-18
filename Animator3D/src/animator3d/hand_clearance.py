"""Fit two-handed front gestures to the depth of the skinned character."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .ik import two_bone_knee_position


def _mesh_samples(arm: Any, regions: list[set[str]]) -> list:
    """Cache the vertex indices on the torso, palms and fingers across skinned meshes."""
    import bpy

    samples = []
    for mesh in bpy.context.scene.objects:
        if mesh.type != "MESH" or mesh.find_armature() != arm:
            continue
        groups = [
            next((i for i, names in enumerate(regions) if group.name in names), -1) for group in mesh.vertex_groups
        ]
        indices: list[list[int]] = [[] for _ in regions]
        for vertex in mesh.data.vertices:
            if vertex.groups:
                dominant = max(vertex.groups, key=lambda item: item.weight)
                region = groups[dominant.group]
                if region >= 0:
                    indices[region].append(vertex.index)
        if any(indices):
            samples.append((mesh, indices))
    return samples


def _surfaces(samples: list, arm: Any, graph: Any, axes: np.ndarray, origin: np.ndarray) -> list:
    regions: list[list[np.ndarray]] = [[] for _ in samples[0][1]]
    for mesh, indices in samples:
        obj = mesh.evaluated_get(graph)
        # Constructive modifiers can change topology. Original skin indices
        # are valid only on a mesh with the same vertex layout.
        if len(obj.data.vertices) != len(mesh.data.vertices):
            continue
        coords = np.empty((len(obj.data.vertices), 3), dtype=np.float32)
        obj.data.vertices.foreach_get("co", coords.ravel())
        matrix = np.asarray(arm.matrix_world.inverted() @ obj.matrix_world)
        local = coords @ (axes @ matrix[:3, :3]).T + axes @ (matrix[:3, 3] - origin)
        for region, selected in zip(regions, indices, strict=True):
            if selected:
                region.append(local[selected])
    return [np.concatenate(region) if region else np.empty((0, 3)) for region in regions]


def _aim_segment(bone: Any, child: Any | None, endpoint: Any) -> None:
    """Rotate about the joint without overwriting its pose location, scale or roll."""
    import bpy

    current = (child.head if child is not None else bone.tail) - bone.head
    desired = endpoint - bone.head
    if min(current.length, desired.length) < 1e-7:
        return
    world = bone.matrix.to_quaternion()
    delta = current.rotation_difference(desired)
    bone.rotation_quaternion = bone.rotation_quaternion @ world.inverted() @ delta @ world
    bpy.context.view_layer.update()


def _relax_collar(collar: Any, upper: Any, up: np.ndarray) -> None:
    """Ease a shrug toward the bind slope while following the animated chest."""
    from mathutils import Vector

    direction = upper.head - collar.head
    parent = collar.parent
    if parent is None or direction.length < 1e-7:
        return
    chest_rotation = parent.matrix.to_quaternion() @ parent.bone.matrix_local.to_quaternion().inverted()
    rest = chest_rotation @ (upper.bone.head_local - collar.bone.head_local)
    vertical = Vector(up)
    elevation = direction.dot(vertical)
    excess = elevation - rest.dot(vertical)
    if excess <= 0:
        return
    horizontal = direction - elevation * vertical
    if horizontal.length < 1e-7:
        return
    # Lower the shoulder by rotating the clavicle, keeping bone length and
    # the forward reach of the shoulder girdle as the chest moves.
    height = elevation - min(0.48 * excess, 0.24 * direction.length)
    span = math.sqrt(max(0.0, direction.length_squared - height * height))
    endpoint = collar.head + horizontal.normalized() * span + vertical * height
    _aim_segment(collar, upper, endpoint)


def _relaxed_wrist(rotation: Any) -> Any:
    """Retain a little grip motion in the local bind frame of the solved forearm."""
    from mathutils import Quaternion

    neutral = Quaternion()
    rotation = rotation.normalized()
    rotation.make_compatible(neutral)
    angle = rotation.angle
    return neutral.slerp(rotation, min(0.38, math.radians(16) / max(angle, 1e-7)))


def _roll_forearm(lower: Any, hand: Any, palm_axis: Any, toward: Any) -> None:
    """Turn the rowing grip about the elbow-to-wrist line without moving the wrist."""
    import bpy
    from mathutils import Quaternion, Vector

    axis = (hand.head - lower.head).normalized()
    palm = hand.matrix.to_quaternion() @ palm_axis
    target = Vector(toward)
    palm -= axis * axis.dot(palm)
    target -= axis * axis.dot(target)
    if min(palm.length, target.length) < 1e-5:
        return
    angle = math.atan2(axis.dot(palm.cross(target)), palm.dot(target))
    turn = Quaternion(axis, angle)
    orientation = lower.matrix.to_quaternion()
    lower.rotation_quaternion = lower.rotation_quaternion @ orientation.inverted() @ turn @ orientation
    bpy.context.view_layer.update()


def _level_palm(hand: Any, palm_axis: Any, toward: Any) -> None:
    """Finish pronation at the wrist after the forearm has supplied the main twist."""
    import bpy
    from mathutils import Vector

    orientation = hand.matrix.to_quaternion()
    normal = orientation @ palm_axis
    tilt = normal.rotation_difference(Vector(toward))
    hand.rotation_quaternion = hand.rotation_quaternion @ orientation.inverted() @ tilt @ orientation
    bpy.context.view_layer.update()


def _reach(upper: Any, lower: Any, hand: Any, goal: np.ndarray, pole: np.ndarray) -> None:
    from mathutils import Vector

    root, elbow, wrist = (np.array(bone.head) for bone in (upper, lower, hand))
    l1 = float(np.linalg.norm(elbow - root))
    l2 = float(np.linalg.norm(wrist - elbow))
    if min(l1, l2) < 1e-6:
        return
    middle = two_bone_knee_position(root, goal, l1, l2, pole)
    palm = hand.matrix.to_quaternion()
    _aim_segment(upper, lower, Vector(middle))
    _aim_segment(lower, hand, Vector(goal))
    # Keep the authored grip after changing the elbow bend.
    hand.rotation_quaternion = hand.rotation_quaternion @ hand.matrix.to_quaternion().inverted() @ palm


def _hand_goal(
    wrist: np.ndarray,
    shoulder: np.ndarray,
    length: float,
    side: float,
    width: float,
    palm: np.ndarray,
    torso: np.ndarray,
    lap: np.ndarray,
) -> np.ndarray | None:
    """Return a reachable wrist target in body coordinates (lateral, forward, up)."""
    # Work in front of the corresponding shoulder at a comfortable
    # grip width. A wide stroke is still allowed to open outwards.
    lat = wrist[0] - side * np.clip(side * wrist[0], 0.0, 0.10 * width)
    margin = 0.025 * length
    footprint = palm - wrist
    low = footprint.min(axis=0)
    high = footprint.max(axis=0)
    nearby = torso[
        (torso[:, 0] >= lat + low[0] - margin)
        & (torso[:, 0] <= lat + high[0] + margin)
        & (torso[:, 2] >= wrist[2] + low[2] - margin)
        & (torso[:, 2] <= wrist[2] + high[2] + margin)
    ]
    depth = wrist[1]
    # Shift the catch and the pull together instead of flattening
    # every intersecting sample against a single contact plane.
    if len(nearby):
        floor = nearby[:, 1].max() - low[1] + margin
        depth = max(depth, floor + 0.42 * max(0.0, wrist[1] - shoulder[1]))
    depth = min(depth, shoulder[1] + 0.96 * length)
    height = wrist[2]
    # Seated poses bring the knees into the same workspace.
    # Clear them vertically with the complete finger footprint.
    under_hand = lap[
        (lap[:, 0] >= lat + low[0] - margin)
        & (lap[:, 0] <= lat + high[0] + margin)
        & (lap[:, 1] >= depth + low[1] - margin)
        & (lap[:, 1] <= depth + high[1] + margin)
        & (lap[:, 2] < shoulder[2])
    ]
    if len(under_hand):
        height = max(height, under_hand[:, 2].max() - low[2] + margin)
    if wrist[1] >= depth and wrist[2] >= height:
        return None
    remaining = max(0.0, (0.985 * length) ** 2 - (depth - shoulder[1]) ** 2)
    lat = shoulder[0] + np.clip(lat - shoulder[0], -np.sqrt(remaining), np.sqrt(remaining))
    vertical = np.sqrt(max(0.0, remaining - (lat - shoulder[0]) ** 2))
    height = shoulder[2] + np.clip(height - shoulder[2], -vertical, vertical)
    return np.array([lat, depth, height])


def _stroke_goal(
    kind: str,
    progress: float,
    shoulder: np.ndarray,
    length: float,
    side: float,
    footprint: np.ndarray,
    torso: np.ndarray,
    lap: np.ndarray,
) -> np.ndarray:
    """Route the wrist around the ribs on the pull and keep a hit guard low."""
    if kind == "row":
        depth = (0.12 + 0.80 * progress) * length
        height = (-0.43 + 0.17 * progress) * length
        lateral = shoulder[0] * (1.12 - 0.30 * progress)
    else:
        depth = (0.15 + 0.38 * progress) * length
        height = (-0.82 + 0.18 * progress) * length
        lateral = shoulder[0] * (1.10 - 0.23 * progress)

    low, high = footprint.min(axis=0), footprint.max(axis=0)
    margin = 0.035 * length
    # Near the flank, allow space for the entire palm. Taper the lateral
    # correction beyond its footprint so passing the belly does not snap a
    # wrist sideways as a new row of mesh vertices enters the footprint.
    if len(torso):
        center = np.array([depth, height])
        lower = center + low[1:] - margin
        upper = center + high[1:] + margin
        gap = torso[:, 1:] - np.clip(torso[:, 1:], lower, upper)
        inner_edge = low[0] if side > 0 else -high[0]
        flank = (side * torso[:, 0] - 1.7 * np.linalg.norm(gap, axis=1)).max()
        lateral = side * max(side * lateral, flank - inner_edge + margin)

    below = lap[
        (lap[:, 0] >= lateral + low[0] - margin)
        & (lap[:, 0] <= lateral + high[0] + margin)
        & (lap[:, 1] >= depth + low[1] - margin)
        & (lap[:, 1] <= depth + high[1] + margin)
        & (lap[:, 2] < 0)
    ]
    if len(below):
        height = max(height, below[:, 2].max() - low[2] + margin)

    goal = np.array([lateral, depth, height])
    offset = goal - shoulder
    return shoulder + offset * min(1.0, 0.985 * length / max(np.linalg.norm(offset), 1e-8))


def _fit_stroke_arm(
    chain: list,
    kind: str,
    phase: float,
    palm_axis: Any,
    axes: np.ndarray,
    origin: np.ndarray,
    side: float,
    footprint: np.ndarray,
    torso: np.ndarray,
    lap: np.ndarray,
) -> None:
    import bpy

    upper, lower, hand = chain
    shoulder = axes @ (np.asarray(upper.head) - origin)
    length = (upper.head - lower.head).length + (lower.head - hand.head).length
    grip = hand.matrix.to_quaternion()
    goal = _stroke_goal(kind, phase, shoulder, length, side, footprint, torso, lap)
    pole = side * axes[0] * 0.45 - axes[1] * 0.85 - axes[2] * 0.75
    _reach(upper, lower, hand, origin + goal @ axes, pole)
    hand.rotation_quaternion = _relaxed_wrist(hand.rotation_quaternion)
    bpy.context.view_layer.update()
    if palm_axis is not None:
        _roll_forearm(lower, hand, palm_axis, -axes[2])
        _level_palm(hand, palm_axis, -axes[2])

    # The grip changed after IK. Recheck the fingers using their new orientation.
    rotation = (hand.matrix.to_quaternion() @ grip.inverted()).to_matrix()
    rotated = footprint @ (axes @ np.asarray(rotation) @ axes.T).T
    clear = _stroke_goal(kind, phase, shoulder, length, side, rotated, torso, lap)
    if side * (clear[0] - goal[0]) > 0.005 * length or clear[2] - goal[2] > 0.005 * length:
        wrist_rotation = hand.rotation_quaternion.copy()
        _reach(upper, lower, hand, origin + clear @ axes, pole)
        hand.rotation_quaternion = wrist_rotation
        bpy.context.view_layer.update()
        if palm_axis is not None:
            _level_palm(hand, palm_axis, -axes[2])


def _fit_row_fingers(hand: Any, outward: Any, up: Any) -> None:
    """Close the SkinTokens thumb opposite its two finger groups around a sculling grip."""
    roots = {bone.name.rsplit("Finger", 1)[-1]: bone for bone in hand.children if "HandFinger" in bone.name}
    if not all(str(i) in roots for i in (1, 2, 3)):
        return
    center = (roots["2"].head + roots["3"].head) * 0.5
    reach = center - hand.head
    span = reach.length
    front = reach - outward * reach.dot(outward) - up * reach.dot(up)
    if span < 1e-6 or front.length < 1e-6:
        return
    front.normalize()
    # Seat the wood just ahead of the palm crease and below the lower knuckle.
    socket = center + 0.27 * reach
    height = min(roots["2"].head.dot(up), roots["3"].head.dot(up)) - 0.13 * span
    socket += up * (height - socket.dot(up))
    # Targets are phalanx ends in palm-length units (ahead, above). Fingers
    # fold over the wood while the thumb closes around its near side.
    for digit, station, targets in (
        ("1", -0.22, ((-0.34, 0.05), (-0.29, -0.20), (-0.03, -0.28))),
        ("2", 0.22, ((0.20, 0.30), (0.31, -0.09), (-0.04, -0.28))),
        ("3", -0.05, ((0.18, 0.27), (0.29, -0.08), (-0.05, -0.26))),
    ):
        bone = roots[digit]
        axis_point = socket + outward * (span * station)
        for ahead, above in targets:
            child = next(iter(bone.children), None)
            goal = axis_point + span * (ahead * front + above * up)
            _aim_segment(bone, child, goal)
            bone.keyframe_insert(data_path="rotation_quaternion")
            if not child:
                break
            bone = child


def fit_front_gesture(
    arm: Any, action: Any, mapped: dict[str, str], motion: tuple[str, np.ndarray] | None = None
) -> None:
    """Place a front gesture ahead of the torso while preserving the hand stroke.

    ``mapped`` maps Sprong joint roles to bones on the target. Solve on the
    target after retarget/limits so the clothing and hand dimensions are real.
    """
    import bpy

    chains = []
    hand_groups = []
    for side in ("l", "r"):
        roles = [mapped.get(f"{side}_{joint}", "") for joint in ("shoulder", "elbow", "wrist")]
        chain = [arm.pose.bones.get(name) for name in roles]
        if not all(chain) or chain[1].parent != chain[0] or chain[2].parent != chain[1]:
            return
        chains.append(chain)
        hand_groups.append({chain[2].name, *(bone.name for bone in chain[2].children_recursive)})
    collars = [arm.pose.bones.get(mapped.get(f"{side}_collar", "")) for side in ("l", "r")]
    hips = arm.pose.bones.get(mapped.get("pelvis", ""))
    if hips is None:
        return
    torso_names = {mapped.get(role, "") for role in ("pelvis", "spine1", "spine2", "spine3", "l_collar", "r_collar")}
    legs = {mapped.get(role, "") for role in ("l_hip", "r_hip", "l_knee", "r_knee")}
    samples = _mesh_samples(arm, [torso_names, legs, *hand_groups])
    if not samples:
        return

    # Some rigs label left/right in reverse. Determine the facing sign once
    # from bind-pose toes; shoulder order alone cannot distinguish these rigs.
    rest_shoulders = np.array([chain[0].bone.head_local[:] for chain in chains])
    rest_front = np.cross(rest_shoulders[0] - rest_shoulders[1], rest_shoulders.mean(axis=0) - hips.bone.head_local)
    toes = []
    for side in ("l", "r"):
        ankle = arm.data.bones.get(mapped.get(f"{side}_ankle", ""))
        foot = arm.data.bones.get(mapped.get(f"{side}_foot", ""))
        if foot and ankle:
            toes.append(np.array(foot.head_local - ankle.head_local))
    reference = np.sum(toes, axis=0) if toes else np.array([0, -1, 0])
    facing_sign = -1.0 if np.dot(rest_front, reference) < 0 else 1.0

    # Pose bones are in armature space, including for rigs with unapplied object rotation.
    up = np.asarray(arm.matrix_world.inverted().to_3x3()) @ np.array([0.0, 0.0, 1.0])
    up /= np.linalg.norm(up)
    palm_axes = []
    if motion and motion[0] == "row":
        from mathutils import Vector

        # In a humanoid bind pose the thumb points forward. The corresponding
        # palm normal points down and inward on both A- and T-pose rigs.
        for upper, lower, hand in chains:
            rest_axis = hand.bone.head_local - lower.bone.head_local
            normal = np.cross(rest_axis, rest_front * facing_sign)
            inward = rest_shoulders.mean(axis=0) - np.asarray(upper.bone.head_local)
            if np.dot(normal, inward - up * np.linalg.norm(inward)) < 0:
                normal = -normal
            # Use the actual palm plane when SkinTokens has finger landmarks.
            # The thumb pad identifies its palmar side on either mirrored hand.
            digits = {b.name.rsplit("Finger", 1)[-1]: b.bone for b in hand.children if "HandFinger" in b.name}
            if all(n in digits for n in ("1", "2", "3")):
                wrist = hand.bone.head_local
                inner, outer = (digits[n].head_local for n in ("2", "3"))
                plane = ((inner + outer) * 0.5 - wrist).cross(inner - outer)
                thumb = arm.data.bones.get(digits["1"].name + "_2") or digits["1"]
                palmar = plane.dot(thumb.head_local - wrist)
                if abs(palmar) > 1e-8:
                    normal = plane if palmar > 0.0 else -plane
            palm_axes.append(hand.bone.matrix_local.to_quaternion().inverted() @ Vector(normal).normalized())
    graph = bpy.context.evaluated_depsgraph_get()
    scene = bpy.context.scene
    ad = arm.animation_data
    previous_action, use_nla = ad.action, ad.use_nla
    old_frame, old_subframe = scene.frame_current, scene.frame_subframe
    previous_quats = {}
    try:
        ad.use_nla = False
        ad.action = action
        start, end = math.ceil(action.frame_range[0]), math.floor(action.frame_range[1])
        progress = (
            np.interp(np.linspace(0, 1, end - start + 1), np.linspace(0, 1, len(motion[1])), motion[1])
            if motion
            else None
        )
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            if motion is not None:
                for collar, (upper, _, _) in zip(collars, chains, strict=True):
                    if collar is not None and upper.parent == collar:
                        _relax_collar(collar, upper, up)
                        collar.keyframe_insert(data_path="rotation_quaternion", frame=frame)
            shoulders = np.array([chain[0].head[:] for chain in chains])
            origin = shoulders.mean(axis=0)
            lateral = shoulders[0] - shoulders[1]
            width = np.linalg.norm(lateral)
            forward = np.cross(lateral, origin - np.asarray(hips.head))
            if width < 1e-6 or np.linalg.norm(forward) < 1e-6:
                continue
            lateral /= width
            forward /= np.linalg.norm(forward)
            if motion is not None:
                # Upright game gestures: measure height relative to the shoulders
                # in gravity space so leaning back cannot raise the guard.
                lateral -= np.dot(lateral, up) * up
                if np.linalg.norm(lateral) < 1e-6:
                    continue
                lateral /= np.linalg.norm(lateral)
                forward = np.cross(lateral, up)
            axes = np.array([lateral, forward * facing_sign, np.cross(forward, lateral)])
            torso, lap, *palms = _surfaces(samples, arm, graph, axes, origin)
            if not len(torso):
                continue
            for index, (chain, palm) in enumerate(zip(chains, palms, strict=True)):
                if not len(palm):
                    continue
                upper, lower, hand = chain
                wrist = axes @ (np.asarray(hand.head) - origin)
                side = 1.0 if index == 0 else -1.0
                if motion is not None and progress is not None:
                    _fit_stroke_arm(
                        chain,
                        motion[0],
                        progress[frame - start],
                        palm_axes[index] if palm_axes else None,
                        axes,
                        origin,
                        side,
                        palm - wrist,
                        torso,
                        lap,
                    )
                else:
                    shoulder = axes @ (np.asarray(upper.head) - origin)
                    length = (upper.head - lower.head).length + (lower.head - hand.head).length
                    goal = _hand_goal(wrist, shoulder, length, side, width, palm, torso, lap)
                    if goal is None:
                        continue
                    pole = np.array(lower.head - upper.head) + side * lateral * length * 0.35
                    _reach(upper, lower, hand, origin + goal @ axes, pole)
                for bone in chain:
                    q = bone.rotation_quaternion.copy()
                    if bone.name in previous_quats:
                        q.make_compatible(previous_quats[bone.name])
                    previous_quats[bone.name] = q.copy()
                    bone.rotation_quaternion = q
                    bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        # Fit fingers after arm IK so the authoring pass always sees the same
        # open-hand clearance footprint, including on rigs with unkeyed digits.
        if motion and motion[0] == "row":
            from mathutils import Vector

            vertical = Vector(up)
            for frame in range(start, end + 1):
                scene.frame_set(frame)
                lateral = chains[0][0].head - chains[1][0].head
                lateral -= vertical * lateral.dot(vertical)
                if lateral.length > 1e-6:
                    outward = lateral.normalized()
                    _fit_row_fingers(chains[0][2], outward, vertical)
                    _fit_row_fingers(chains[1][2], -outward, vertical)
    finally:
        ad.action = previous_action
        ad.use_nla = use_nla
        scene.frame_set(old_frame, subframe=old_subframe)
