from __future__ import annotations

import json

import numpy as np

from terraingen.export import export_metadata
from terraingen.lakes import LakePlaneData
from terraingen.pipeline import PipelineResult


def test_export_metadata_centers_lake_plane_coordinates(tmp_path) -> None:
    result = PipelineResult(
        heightmap=np.zeros((4, 4), dtype=np.float64),
        lake_planes=[
            LakePlaneData(
                lake_id=0,
                pos_x=2.0,
                pos_y=0.5,
                pos_z=2.0,
                size_x=2.0,
                size_z=2.0,
            )
        ],
    )

    output_path = tmp_path / "terrain.json"
    export_metadata(result, output_path, world_size=40.0, max_height=10.0)

    with open(output_path, encoding="utf-8") as fh:
        data = json.load(fh)

    plane = data["lake_planes"][0]
    assert plane["pos_x"] == 0.0
    assert plane["pos_z"] == 0.0
    assert plane["pos_y"] == 5.0
    assert plane["size_x"] == 20.0
    assert plane["size_z"] == 20.0
