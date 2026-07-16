from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from part3d.utils.hierarchical import (
    detail_partition_is_useful,
    large_region_candidates,
    merge_detail_partition,
    prune_detail_partition,
)


def test_large_region_candidates_are_area_ranked() -> None:
    mesh = SimpleNamespace(area_faces=np.array([4.0, 4.0, 1.0, 1.0]))
    labels = np.array([0, 0, 1, 1])

    candidates = large_region_candidates(mesh, labels, min_area_frac=0.15, max_regions=2)

    assert [item[0] for item in candidates] == [0, 1]
    assert candidates[0][2] == 0.8


def test_detail_partition_acceptance_rejects_noop_and_debris() -> None:
    areas = np.ones(100)

    assert detail_partition_is_useful(areas, np.array([0] * 60 + [1] * 40))
    assert not detail_partition_is_useful(areas, np.zeros(100, dtype=np.int64))
    assert not detail_partition_is_useful(
        areas,
        np.array([0] * 99 + [1]),
        min_child_frac=0.02,
    )


def test_merge_detail_partition_reuses_parent_and_allocates_new_ids() -> None:
    parent = np.array([5, 5, 5, 5, 9])
    indices = np.array([0, 1, 2, 3])
    children = np.array([3, 3, 8, 8])

    merged = merge_detail_partition(parent, indices, children)

    assert np.array_equal(merged, np.array([5, 5, 10, 10, 9]))


def test_prune_detail_partition_absorbs_tiny_child() -> None:
    mesh = SimpleNamespace(
        area_faces=np.array([1.0, 1.0, 0.1, 1.0, 1.0]),
        face_adjacency=np.array([[0, 1], [1, 2], [2, 3], [3, 4]]),
    )
    labels = np.array([0, 0, 2, 1, 1])

    pruned = prune_detail_partition(mesh, labels, min_child_frac=0.1)

    assert set(pruned) == {0, 1}
    assert pruned[2] in {0, 1}
