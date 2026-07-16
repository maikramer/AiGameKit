from __future__ import annotations

import pytest

trimesh = pytest.importorskip("trimesh")

from part3d.utils.exclusive_partition import (  # noqa: E402
    exclusive_surface_partition,
    partition_stats,
)


def test_exclusive_drops_overlapping_blob() -> None:
    """Blob inside body AABB assigned to body; ghost faces on blob side drop from body."""
    body = trimesh.creation.box(extents=[1.0, 1.0, 2.0])
    ghost = trimesh.creation.icosphere(subdivisions=1, radius=0.2)
    ghost.apply_translation([0.7, 0.0, 0.0])
    # Contaminate body with a copy of ghost geometry (overlap leftover)
    body_dirty = trimesh.util.concatenate([body, ghost.copy()])
    scene = trimesh.Scene()
    scene.add_geometry(body_dirty, geom_name="part_0", node_name="part_0")
    scene.add_geometry(ghost, geom_name="part_1", node_name="part_1")

    out = exclusive_surface_partition(scene, samples_per_part=2000)
    assert "part_0" in out.geometry and "part_1" in out.geometry
    assert len(out.geometry["part_0"].faces) < len(body_dirty.faces)
    stats = partition_stats(scene, out)
    assert stats["faces_dropped"] > 0


def test_exclusive_noop_single_part() -> None:
    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    scene = trimesh.Scene()
    scene.add_geometry(box, geom_name="part_0", node_name="part_0")
    out = exclusive_surface_partition(scene)
    assert len(out.geometry["part_0"].faces) == len(box.faces)
