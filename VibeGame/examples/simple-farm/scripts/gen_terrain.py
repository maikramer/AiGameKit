#!/usr/bin/env python3
"""Generate the simple-farm heightmap (`public/assets/terrain/farm_valley.ahgt`).

The relief carries the whole map: there are no terraces any more. The previous
version stamped four abutting `<TerrainPad>` plateaux with a 0.6 m falloff,
which is a ~2 m step over 0.6 m — about 75°, far past the character
controller's 45° limit. The valley was four islands joined by three staircases.
Now the ground is continuous and every slope inside the playable interior stays
under ~25°, so the player can walk anywhere; pads survive only under the town
and the farm plot, with a wide falloff.

Zones (world x/z ∈ [-256, 256], matching `world-size="512"`):

    NW  farm fields (low, calm)   |  N/NE  forest, rising to ~22 m
    ----------------------------- + -----------------------------
    SW  meadow + lake (lowest)    |  SE    quarry, rocky and higher

`worldSize` in the metadata is authoritative — `ahgt-loader.ts` ignores the
`world-size` XML attribute — so the two must agree or the mesh reads the wrong
slice of the field.

Grid is 1025 samples over 512 m = **0.5 m per texel**.

Format is AHGT (see `src/plugins/terrain/ahgt-format.ts`): 16-byte header, u32
metadata length, UTF-8 JSON metadata, then a deflate-compressed uint16 grid.
The loader sniffs for the RFC1950 wrapper, so Python's `zlib.compress` is fine.

Usage:
    python3 scripts/gen_terrain.py
"""

from __future__ import annotations

import json
import math
import struct
import zlib
from pathlib import Path

GRID = 1025
WORLD_SIZE = 512.0
MAX_HEIGHT = 40.0
SEED = 20260820

OUT = Path(__file__).resolve().parent.parent / "public/assets/terrain/farm_valley.ahgt"

AHGT_MAGIC = 0x54474841  # "AHGT" little-endian
AHGT_VERSION = 1


def _hash01(ix: int, iy: int, salt: int) -> float:
    """Deterministic value noise lattice — no numpy, no external deps."""
    h = (ix * 374761393 + iy * 668265263 + salt * 2246822519 + SEED) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFFFF) / 0xFFFFFF


def _smoothstep(t: float) -> float:
    return t * t * (3 - 2 * t)


def value_noise(x: float, y: float, salt: int) -> float:
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    sx, sy = _smoothstep(fx), _smoothstep(fy)
    a = _hash01(ix, iy, salt)
    b = _hash01(ix + 1, iy, salt)
    c = _hash01(ix, iy + 1, salt)
    d = _hash01(ix + 1, iy + 1, salt)
    return (a * (1 - sx) + b * sx) * (1 - sy) + (c * (1 - sx) + d * sx) * sy


def fbm(x: float, y: float, octaves: int = 4) -> float:
    total, amp, freq, norm = 0.0, 1.0, 1.0, 0.0
    for o in range(octaves):
        total += amp * value_noise(x * freq, y * freq, o)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


def _ramp(v: float, lo: float, hi: float) -> float:
    """Smooth 0→1 across [lo, hi]; flat outside. Zone masks are built from these."""
    if hi == lo:
        return 0.0 if v < lo else 1.0
    return _smoothstep(min(1.0, max(0.0, (v - lo) / (hi - lo))))


# Every amplitude below is paired with a wavelength that keeps its slope gentle:
# a smooth bump of amplitude A over wavelength L peaks near 2A/L. The forest
# rise (12 m / 150 m) is ~9°, the quarry roughness (5 m / 45 m) ~13°, the rim
# (22 m / 54 m) ~22° — all inside the controller's 45° budget with margin, so
# nothing in the interior becomes an invisible wall.
BASE_HEIGHT = 10.0


def height_at(wx: float, wz: float) -> float:
    """Metres. Continuous relief: low centre/south, forest rise NE, quarry SE."""
    # Rolling ground everywhere, ±2.4 m over a long wavelength.
    h = BASE_HEIGHT + (fbm(wx / 145.0, wz / 145.0) - 0.5) * 4.8

    # North rises into the forest; the south stays open and low.
    h += _ramp(wz, -40.0, 190.0) * 12.0

    # South-east quarry: a broad shoulder plus shorter-wavelength roughness so
    # the rock props sit on ground that already reads as broken.
    quarry = _ramp(wx, 40.0, 130.0) * _ramp(-wz, 10.0, 110.0)
    h += quarry * (9.0 + (fbm(wx / 45.0, wz / 45.0, octaves=3) - 0.5) * 10.0)

    # South-west bowl holds the lake — pull it below the base plane.
    h -= _ramp(-wx, 20.0, 120.0) * _ramp(-wz, 40.0, 150.0) * 4.0

    # Rim closes the map. Starts at 0.79 of the half-extent (~202 m) so the
    # playable interior is the full 400 m across.
    r = max(abs(wx), abs(wz)) / (WORLD_SIZE / 2)
    h += _ramp(r, 0.79, 1.0) * 22.0

    return h


def main() -> None:
    quantized = bytearray(GRID * GRID * 2)
    step = WORLD_SIZE / (GRID - 1)
    half = WORLD_SIZE / 2

    for gz in range(GRID):
        wz = -half + gz * step
        row = gz * GRID
        for gx in range(GRID):
            wx = -half + gx * step
            h = height_at(wx, wz)
            n = min(1.0, max(0.0, h / MAX_HEIGHT))
            struct.pack_into("<H", quantized, (row + gx) * 2, round(n * 65535))

    payload = zlib.compress(bytes(quantized), 6)
    meta = json.dumps(
        {
            "worldSize": WORLD_SIZE,
            "maxHeight": MAX_HEIGHT,
            "originX": 0,
            "originZ": 0,
        }
    ).encode("utf-8")

    header = struct.pack("<IHHHHI", AHGT_MAGIC, AHGT_VERSION, GRID, GRID, 0, 0)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(header + struct.pack("<I", len(meta)) + meta + payload)

    print(f"{OUT}: {GRID}x{GRID} @ {step:.3f} m/texel, {OUT.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
