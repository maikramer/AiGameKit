from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from .generator import TerrainResult


def export_heightmap(heightmap: np.ndarray, output_path: str | Path, size: int = 2048) -> Path:
    """Export a heightmap array as an 8-bit grayscale PNG.

    Normalizes values to 0-255, optionally resizes to *size* x *size*, and
    saves using Pillow (LANCZOS resampling).

    Args:
        heightmap: 2D float64 array of terrain elevations (0-1).
        output_path: Destination file path for the PNG.
        size: Target image dimension (default 2048).

    Returns:
        Path to the written PNG file.
    """
    normalized = (np.clip(heightmap, 0.0, 1.0) * 255).astype(np.uint8)

    img = Image.fromarray(normalized, mode="L")

    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="PNG")
    return output_path


def export_metadata(
    result: TerrainResult,
    output_path: str | Path,
) -> Path:
    """Export terrain metadata as a JSON file compatible with the VibeGame pipeline.

    The schema matches the pipeline output (version 2.0) but with empty
    rivers/lakes arrays and ``generator: "terrain3d"``.

    Args:
        result: TerrainResult from generation.
        output_path: Destination file path for the JSON.

    Returns:
        Path to the written JSON file.
    """
    config = result.config
    h = result.heightmap
    stats = result.stats

    metadata: dict = {
        "version": "2.0",
        "generator": "terrain3d",
        "model_id": stats.get("model_id", config.model_id or "unknown"),
        "terrain": {
            "size": config.size,
            "world_size": config.world_size,
            "max_height": config.max_height,
            "height_min": float(h.min()),
            "height_max": float(h.max()),
            "height_mean": float(h.mean()),
            "height_std": float(h.std()),
        },
        "rivers": [],
        "lakes": [],
        "lake_planes": [],
        "stats": {
            "generation_time_seconds": stats.get("generation_time_seconds", 0.0),
        },
    }

    if config.prompt is not None:
        metadata["prompt"] = config.prompt

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return output_path
