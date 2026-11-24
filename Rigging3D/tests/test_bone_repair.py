from __future__ import annotations

from types import SimpleNamespace

import bpy  # noqa: F401
from mathutils import Matrix, Vector
from rigging3d.bone_repair import _repair_edit_bones, _straighten_elbows


def _bone(
    name: str,
    head: tuple[float, float, float],
    tail: tuple[float, float, float],
    children: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        head=Vector(head),
        tail=Vector(tail),
        children=children or [],
    )


def test_repair_aligns_single_child_chain() -> None:
    child = _bone("LeftLeg", (0, 0, -1), (0, 0, -2))
    parent = _bone("LeftUpLeg", (0, 0, 0), (1, 0, 0), [child])
    arm = SimpleNamespace(data=SimpleNamespace(edit_bones=[parent, child]))

    fixed = _repair_edit_bones(arm, min_gap=0.01)

    assert fixed == ["LeftUpLeg"]
    assert parent.tail == child.head


def test_repair_preserves_aligned_branch_and_leaf_bones() -> None:
    child_a = _bone("Spine", (0, 0, 1), (0, 0, 2))
    child_b = _bone("LeftUpLeg", (1, 0, -1), (1, 0, -2))
    aligned = _bone("Chest", (0, 0, 0), (0, 0, 1), [child_a])
    branch = _bone("Hips", (0, 0, 0), (0, 1, 0), [child_a, child_b])
    arm = SimpleNamespace(data=SimpleNamespace(edit_bones=[aligned, branch, child_a, child_b]))

    fixed = _repair_edit_bones(arm, min_gap=0.01)

    assert fixed == []
    assert aligned.tail == Vector((0, 0, 1))
    assert branch.tail == Vector((0, 1, 0))


def _arm_rig(elbow: tuple[float, float, float]) -> tuple[SimpleNamespace, dict[str, SimpleNamespace]]:
    # Braço esquerdo em A-pose ao longo de +X (ombro → pulso reto), Blender: +Y = trás.
    bones = {
        "upperarm_l": _bone("upperarm_l", (0, 0, 0), elbow),
        "lowerarm_l": _bone("lowerarm_l", elbow, (0.3, 0, 0)),
        "hand_l": _bone("hand_l", (0.3, 0, 0), (0.35, 0, 0)),
    }
    arm = SimpleNamespace(matrix_world=Matrix.Identity(4), data=SimpleNamespace(edit_bones=bones))
    return arm, bones


def test_straighten_elbow_removes_upward_kink() -> None:
    # Hero: cotovelo 3 cm acima de um braço reto → antebraço dobrava ao contrário.
    arm, bones = _arm_rig((0.15, 0.0, 0.03))

    fixed = _straighten_elbows(arm)

    assert fixed == ["lowerarm_l"]
    assert (bones["lowerarm_l"].head - Vector((0.15, 0, 0))).length < 1e-6
    assert bones["upperarm_l"].tail == bones["lowerarm_l"].head


def test_straighten_elbow_keeps_backward_bend() -> None:
    arm, bones = _arm_rig((0.15, 0.02, -0.02))

    _straighten_elbows(arm)

    assert (bones["lowerarm_l"].head - Vector((0.15, 0.02, 0))).length < 1e-6


def test_straighten_elbow_ignores_really_bent_arm() -> None:
    arm, bones = _arm_rig((0.15, 0.0, 0.2))

    assert _straighten_elbows(arm) == []
    assert bones["lowerarm_l"].head == Vector((0.15, 0, 0.2))


def test_repair_asymmetric_limb_chains_relocates_duplicate_and_mirrors_weights() -> None:
    """Duplicate overlapping leg + missing mirror leg -> relocated with clean mirrored weights."""
    from rigging3d.bone_repair import _repair_asymmetric_limb_chains

    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm_data = bpy.data.armatures.new("Arm")
    arm = bpy.data.objects.new("Scorpion", arm_data)
    bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm

    bpy.ops.object.mode_set(mode="EDIT")
    ebs = arm_data.edit_bones

    def add_bone(name: str, h, t, parent: str | None) -> None:
        eb = ebs.new(name)
        eb.head = Vector(h)
        eb.tail = Vector(t)
        if parent:
            eb.parent = ebs[parent]

    def add_leg(prefix: str, x: float, sign_y: float, n_segs: int = 5) -> None:
        pts = [
            (x, sign_y * 0.10, 0.11),
            (x, sign_y * 0.15, 0.09),
            (x, sign_y * 0.23, 0.19),
            (x, sign_y * 0.30, 0.12),
            (x, sign_y * 0.31, 0.05),
            (x, sign_y * 0.32, -0.02),
        ]
        parent = "Root"
        for i in range(n_segs):
            bn = prefix if i == 0 else f"{prefix}_{i}"
            add_bone(bn, pts[i], pts[i + 1], parent)
            parent = bn

    add_bone("Root", (0.25, 0.0, 0.14), (0.18, 0.0, 0.14), None)
    # 4 clean legs on -Y side (front -> back along X).
    for idx, lx in enumerate((-0.08, 0.15, 0.35, 0.46), start=1):
        add_leg(f"LegA{idx}", lx, -1.0, 5)
    # +Y side: Leg 1 & 4 clean, Leg 2 MISSING, Leg 3 has TWO overlapping chains (DupLeg + KeepLeg).
    add_leg("LegB1", -0.08, +1.0, 5)
    add_leg("DupLeg", 0.35, +1.0, 4)  # overlaps KeepLeg at x=0.35!
    add_leg("KeepLeg", 0.355, +1.0, 4)
    add_leg("LegB4", 0.46, +1.0, 5)
    # Symmetric pincers, with PincerB tip sticking sideways into space.
    for prefix, sy, stray in (("PincerA", -1.0, False), ("PincerB", +1.0, True)):
        add_bone(prefix, (-0.04, sy * 0.10, 0.11), (-0.12, sy * 0.18, 0.11), "Root")
        add_bone(f"{prefix}_1", (-0.12, sy * 0.18, 0.11), (-0.20, sy * 0.22, 0.15), prefix)
        add_bone(f"{prefix}_2", (-0.20, sy * 0.22, 0.15), (-0.35, sy * 0.20, 0.16), f"{prefix}_1")
        tail_pt = (-0.33, 0.02, 0.15) if stray else (-0.48, sy * 0.18, 0.16)
        add_bone(f"{prefix}_3", (-0.35, sy * 0.20, 0.16), tail_pt, f"{prefix}_2")
    bpy.ops.object.mode_set(mode="OBJECT")

    # Build symmetric point-cloud mesh covering all 8 legs + both pincers + body.
    verts: list[tuple[float, float, float]] = []
    vert_owners: list[str] = []
    # Carapace body verts
    for x in (-0.1, 0.1, 0.3, 0.45):
        for y in (-0.08, 0.0, 0.08):
            for z in (0.08, 0.20):
                verts.append((x, y, z))
                vert_owners.append("Root")
    # Symmetric leg vertices on both -Y and +Y sides (including the unrigged Leg 2 on +Y!).
    leg_xs = (-0.08, 0.15, 0.35, 0.46)
    seg_pts = [(0.12, 0.10), (0.18, 0.14), (0.24, 0.19), (0.30, 0.11), (0.32, 0.03)]
    for li, lx in enumerate(leg_xs):
        for sy in (-1.0, +1.0):
            for si, (ly, lz) in enumerate(seg_pts):
                for dx in (-0.008, 0.0, 0.008):
                    verts.append((lx + dx, sy * ly, lz))
                    if sy < 0:
                        bn = f"LegA{li + 1}" if si == 0 else f"LegA{li + 1}_{si}"
                    elif li == 0:
                        bn = "LegB1" if si == 0 else f"LegB1_{si}"
                    elif li == 1:
                        # Unrigged Leg 2 had leaked weights from Root / DupLeg_3!
                        bn = "Root" if si < 3 else "DupLeg_3"
                    elif li == 2:
                        # Shared Leg 3 had mixed DupLeg / KeepLeg weights.
                        bn = (
                            ("DupLeg" if si == 0 else f"DupLeg_{min(si, 3)}")
                            if si % 2 == 0
                            else ("KeepLeg" if si == 0 else f"KeepLeg_{min(si, 3)}")
                        )
                    else:
                        bn = "LegB4" if si == 0 else f"LegB4_{si}"
                    vert_owners.append(bn)
    # Pincer vertices (symmetric forward tips at x=-0.48, y=±0.18).
    for sy in (-1.0, +1.0):
        p_name = "PincerA_3" if sy < 0 else "PincerB_3"
        for px in (-0.36, -0.42, -0.48):
            verts.append((px, sy * 0.18, 0.16))
            vert_owners.append(p_name)

    mesh_data = bpy.data.meshes.new("Mesh")
    mesh_data.from_pydata(verts, [], [])
    mesh_obj = bpy.data.objects.new("Mesh", mesh_data)
    bpy.context.collection.objects.link(mesh_obj)
    for b in arm.data.bones:
        mesh_obj.vertex_groups.new(name=b.name)
    for vi, owner in enumerate(vert_owners):
        mesh_obj.vertex_groups[owner].add([vi], 1.0, "REPLACE")

    fixed = _repair_asymmetric_limb_chains(arm, [mesh_obj])

    assert "DupLeg" in fixed
    assert "DupLeg_4" in arm.data.bones  # 5th segment created to match LegA2!
    assert "PincerB_3" in fixed
    # Relocated DupLeg_2 knee now sits at mirrored LegA2_2 (+0.15, +0.23, +0.19).
    assert (arm.data.bones["DupLeg_2"].head_local - Vector((0.15, 0.23, 0.19))).length < 1e-4
    # Stray PincerB_3 tail now points forward to (-0.48, +0.18, +0.16).
    assert (arm.data.bones["PincerB_3"].tail_local - Vector((-0.48, 0.18, 0.16))).length < 1e-4
    # Vertices of +Y Leg 2 now belong to DupLeg*, NOT Root!
    vg_names = {vg.index: vg.name for vg in mesh_obj.vertex_groups}
    leg2_pos_owners = [
        vg_names[max(v.groups, key=lambda g: g.weight).group]
        for v in mesh_obj.data.vertices
        if abs(v.co.x - 0.15) < 0.02 and v.co.y > 0.15
    ]
    assert leg2_pos_owners and all(name.startswith("DupLeg") for name in leg2_pos_owners)
    # Shared +Y Leg 3 vertices were cleanly transferred from DupLeg to KeepLeg!
    leg3_pos_owners = [
        vg_names[max(v.groups, key=lambda g: g.weight).group]
        for v in mesh_obj.data.vertices
        if abs(v.co.x - 0.35) < 0.02 and v.co.y > 0.15
    ]
    assert leg3_pos_owners and all(name.startswith("KeepLeg") for name in leg3_pos_owners)
