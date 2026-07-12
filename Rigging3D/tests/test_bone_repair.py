from __future__ import annotations

from types import SimpleNamespace

import bpy  # noqa: F401
from mathutils import Vector
from rigging3d.bone_repair import _repair_edit_bones


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
