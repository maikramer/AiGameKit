from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from terraingen.pipeline import PipelineResult


def export_heightmap(heightmap: np.ndarray, output_path: str | Path, size: int = 2048) -> Path:
    """Export a heightmap array as an 8-bit grayscale PNG.

    Normalizes values to 0-255, optionally resizes to *size* x *size*, and
    saves using Pillow (LANCZOS resampling).

    Args:
        heightmap: 2D float64 array of terrain elevations.
        output_path: Destination file path for the PNG.
        size: Target image dimension (default 2048).

    Returns:
        Path to the written PNG file.
    """
    h_min = float(heightmap.min())
    h_max = float(heightmap.max())
    if h_max - h_min < 1e-12:
        normalized = np.zeros_like(heightmap, dtype=np.uint8)
    else:
        normalized = ((heightmap - h_min) / (h_max - h_min) * 255).astype(np.uint8)

    img = Image.fromarray(normalized, mode="L")

    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="PNG")
    return output_path


def export_metadata(
    result: PipelineResult,
    output_path: str | Path,
    world_size: float = 256.0,
    max_height: float = 50.0,
) -> Path:
    """Export PipelineResult metadata as a human-readable JSON file.

    Converts pixel-space data (rivers, lakes, lake planes) to world
    coordinates using *world_size* and *max_height*.

    Args:
        result: Pipeline result containing heightmap, rivers, lakes, and stats.
        output_path: Destination file path for the JSON.
        world_size: World-space extent along X and Z axes.
        max_height: World-space maximum terrain height.

    Returns:
        Path to the written JSON file.
    """
    h = result.heightmap
    heightmap_size = h.shape[0]
    half_world = world_size * 0.5

    # --- Rivers ---
    rivers_data: list[dict] = []
    for i, river in enumerate(result.rivers):
        # river path is [[row, col], ...]
        source = [int(river[0, 1]), int(river[0, 0])]  # [col, row] → [x_pixel, z_pixel]
        path = [[int(pt[1]), int(pt[0])] for pt in river]  # each [col, row]
        rivers_data.append(
            {
                "id": i,
                "source": source,
                "path": path,
                "length": len(river),
            }
        )

    # --- Lakes ---
    lakes_data: list[dict] = []
    for i, lake in enumerate(result.lakes):
        center_pixel = [round(lake.center_x), round(lake.center_z)]  # [col, row] = [x, z]
        surface_height = lake.surface_level * max_height
        lakes_data.append(
            {
                "id": i,
                "center_pixel": center_pixel,
                "surface_level": round(float(lake.surface_level), 6),
                "surface_height": round(surface_height, 6),
                "area_pixels": int(lake.area),
                "depth": round(float(lake.depth), 6),
            }
        )

    # --- Lake planes (pixel → world) ---
    lake_planes_data: list[dict] = []
    for plane in result.lake_planes:
        # Pixel to world in terrain-centered coordinates (-half_world .. +half_world).
        world_x = plane.pos_x / heightmap_size * world_size - half_world
        world_y = plane.pos_y * max_height  # surface_level * max_height
        world_z = plane.pos_z / heightmap_size * world_size - half_world
        world_sx = plane.size_x / heightmap_size * world_size
        world_sz = plane.size_z / heightmap_size * world_size
        lake_planes_data.append(
            {
                "lake_id": plane.lake_id,
                "pos_x": round(world_x, 4),
                "pos_y": round(world_y, 4),
                "pos_z": round(world_z, 4),
                "size_x": round(world_sx, 4),
                "size_z": round(world_sz, 4),
            }
        )

    # --- Stats ---
    stats = result.stats
    total_time = stats.get("total", {}).get("time", 0.0)
    h_stats = stats.get("heightmap_stats", {})

    metadata = {
        "version": "1.0",
        "terrain": {
            "size": heightmap_size,
            "world_size": world_size,
            "max_height": max_height,
            "height_min": float(h.min()),
            "height_max": float(h.max()),
            "height_mean": float(h.mean()),
        },
        "rivers": rivers_data,
        "lakes": lakes_data,
        "lake_planes": lake_planes_data,
        "stats": {
            "generation_time_seconds": round(total_time, 3),
            "heightmap_min": float(h_stats.get("min", h.min())),
            "heightmap_max": float(h_stats.get("max", h.max())),
            "river_count": stats.get("river_count", len(result.rivers)),
            "lake_count": stats.get("lake_count", len(result.lakes)),
        },
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return output_path
