from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

from .generator import TerrainResult

# Layout binário do .ahgt (espelha VibeGame/src/plugins/terrain/ahgt-format.ts):
#   offset 0  u32  magic "AHGT" (0x54474841 little-endian)
#   offset 4  u16  version (1)
#   offset 6  u16  grid width
#   offset 8  u16  grid height
#   offset 10 u16  flags (0)
#   offset 12 u32  reserved (0)
#   offset 16 u32  metadata JSON length, depois JSON UTF-8 e grelha uint16 deflactada
AHGT_MAGIC = 0x54474841
AHGT_VERSION = 1


def export_ahgt(
    heightmap: np.ndarray,
    output_path: str | Path,
    world_size: float,
    max_height: float,
) -> Path:
    """Export a heightmap as a binary ``.ahgt`` heightfield (uint16 + deflate).

    Precision is ``max_height / 65535`` (~3 mm over 200 m) versus ~0.78 m for
    the legacy 8-bit PNG path — removes terracing on gentle slopes.  The
    VibeGame terrain plugin parses this format natively (``parseAhgt``).

    Args:
        heightmap: 2D float64 array of terrain elevations (0-1).
        output_path: Destination file path (``.ahgt`` suffix enforced).
        world_size: World extent in meters (stored in the metadata block).
        max_height: Max terrain height in meters (stored in the metadata block).

    Returns:
        Path to the written file.
    """
    h, w = heightmap.shape
    if h > 65535 or w > 65535:
        raise ValueError(f"AHGT grid exceeds 65535x65535: {w}x{h}")

    quantized = np.rint(np.clip(heightmap, 0.0, 1.0) * 65535.0).astype("<u2")
    # Deflate RAW (wbits=-15), sem cabeçalho zlib: o leitor do VibeGame é o
    # `inflateSync` do fflate, que não aceita o wrapper RFC1950 e falha com
    # "invalid block type". `zlib.compress` escreveria esse wrapper.
    deflater = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    compressed = deflater.compress(quantized.tobytes(order="C")) + deflater.flush()

    meta = json.dumps(
        {
            "worldSize": float(world_size),
            "maxHeight": float(max_height),
            "originX": 0,
            "originZ": 0,
        }
    ).encode("utf-8")

    header = struct.pack("<IHHHHI", AHGT_MAGIC, AHGT_VERSION, w, h, 0, 0)
    blob = header + struct.pack("<I", len(meta)) + meta + compressed

    output_path = Path(output_path)
    if output_path.suffix.lower() != ".ahgt":
        output_path = output_path.with_suffix(".ahgt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(blob)
    return output_path


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

    # Sinais de escala horizontal (guard-rail) — aditivos, consumidores antigos ignoram.
    for key in ("native_resolution", "native_extent_m", "horizontal_scale_ratio", "scale_warning"):
        if key in stats and stats[key] is not None:
            metadata["stats"][key] = stats[key]

    # Proveniência para reproduzir a mesma região do mundo infinito — sem o
    # seed, uma regeneração é irreproduzível (lotaria total do relevo).
    if config.seed is not None:
        metadata["seed"] = config.seed
    for key in ("mode", "num_inference_steps", "offset_i", "offset_j"):
        metadata["stats"][key] = getattr(config, key)

    if config.prompt is not None:
        metadata["prompt"] = config.prompt

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return output_path
