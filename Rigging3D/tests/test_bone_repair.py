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
