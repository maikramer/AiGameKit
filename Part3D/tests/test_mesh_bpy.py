"""Testes do fix_mesh bpy (sem pymeshlab)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("bpy")


def test_fix_mesh_removes_tiny_floater() -> None:
    import trimesh
    from part3d.utils.mesh_bpy import fix_mesh

    # Mesh principal densa + ilha minúscula (floater)
    main = trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide().subdivide()
    floater = trimesh.creation.box(extents=[0.01, 0.01, 0.01])
    floater.apply_translation([5.0, 5.0, 5.0])
    combined = trimesh.util.concatenate([main, floater])

    out = fix_mesh(combined)
    assert len(out.faces) > 0
    assert len(out.faces) < len(combined.faces)
    assert len(out.faces) >= len(main.faces) // 2


def test_fix_mesh_preserves_simple_box() -> None:
    import trimesh
    from part3d.utils.mesh_bpy import fix_mesh

    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    out = fix_mesh(box)
    assert len(out.faces) >= 8
    assert len(out.vertices) >= 8


def test_fix_mesh_empty_passthrough() -> None:
    import trimesh
    from part3d.utils.mesh_bpy import fix_mesh

    empty = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64), process=False)
    out = fix_mesh(empty)
    assert len(out.vertices) == 0
