from __future__ import annotations

import numpy as np

from terraingen.lakes import LakeData, excavate_lakes, generate_lake_planes


def test_excavate_lakes_limits_carving_to_lake_pixels() -> None:
    heightmap = np.array(
        [
            [10.0, 10.0, 10.0, 10.0, 10.0],
            [10.0, 4.0, 4.0, 4.0, 10.0],
            [10.0, 4.0, 2.0, 4.0, 10.0],
            [10.0, 4.0, 4.0, 4.0, 10.0],
            [10.0, 10.0, 10.0, 10.0, 10.0],
        ],
        dtype=np.float64,
    )

    lake = LakeData(
        center_x=2.0,
        center_z=2.0,
        surface_level=4.0,
        width=1.0,
        depth=1.0,
        area=1,
        shape_points=[(2, 2)],
        pixels=[(2, 2)],
    )

    carved = excavate_lakes(heightmap, [lake])

    assert carved[2, 2] == 3.0
    assert carved[1, 2] == 4.0
    assert carved[2, 1] == 4.0
    assert carved[2, 3] == 4.0
    assert carved[3, 2] == 4.0


def test_generate_lake_planes_one_bbox_per_lake() -> None:
    """Each lake must produce exactly one plane (no overlapping strips)."""
    a = LakeData(
        center_x=5.0,
        center_z=5.0,
        surface_level=0.5,
        width=4.0,
        depth=0.1,
        area=100,
        shape_points=[(4, 4), (4, 6), (6, 4), (6, 6)],
        pixels=[(5, 5)],
    )
    b = LakeData(
        center_x=20.0,
        center_z=20.0,
        surface_level=0.4,
        width=2.0,
        depth=0.05,
        area=50,
        shape_points=[(19, 19), (21, 21)],
        pixels=[(20, 20)],
    )
    planes = generate_lake_planes([a, b])
    assert len(planes) == 2
    assert planes[0].lake_id == 0
    assert planes[1].lake_id == 1
