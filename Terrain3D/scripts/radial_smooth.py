#!/usr/bin/env python3
"""Radial centre-smooth for a heightmap PNG.

Smooths the terrain more in the centre and less at the edges, so the playable
central region reads as gentler rolling hills while the borders keep their
rugged character. The original is preserved as a side-by-side backup.

Usage:
    python radial_smooth.py <input.png> <output.png> \
        [--sigma 18] [--inner 0.25] [--outer 0.7]

The blend weight is a smoothstep from 1.0 (full smooth) inside --inner
(fraction of half-diagonal) to 0.0 (no smooth) outside --outer. --sigma is the
Gaussian blur radius in pixels applied to the smoothed copy.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def radial_smooth(
    heightmap: np.ndarray,
    sigma: float,
    inner: float,
    outer: float,
) -> np.ndarray:
    """Blend the heightmap with a Gaussian-blurred copy, weighted by a radial
    falloff that is 1 at the centre and 0 near the corners.

    Args:
        heightmap: 2D float array in [0, 1].
        sigma: Gaussian blur radius (px) for the smoothed copy.
        inner: Distance (fraction of half-diagonal) below which the smooth is
            applied at full strength.
        outer: Distance above which no smoothing is applied.

    Returns:
        Smoothed heightmap, same shape, clipped to the input's value range so
        the overall amplitude is preserved.
    """
    h, w = heightmap.shape
    cy, cx = h / 2.0, w / 2.0
    half_diag = np.hypot(h, w) / 2.0
    y, x = np.mgrid[0:h, 0:w]
    dist = np.sqrt((y - cy) ** 2 + (x - cx) ** 2) / half_diag

    # weight = 1 inside `inner`, 0 outside `outer`, smoothstep in between.
    weight = 1.0 - _smoothstep(inner, outer, dist)

    blurred = gaussian_filter(heightmap, sigma=sigma, mode="reflect")
    out = heightmap * (1.0 - weight) + blurred * weight

    # Preserve amplitude: renormalise to the original [min, max] so the smooth
    # doesn't flatten the overall relief, only the local roughness.
    lo, hi = float(heightmap.min()), float(heightmap.max())
    o_lo, o_hi = float(out.min()), float(out.max())
    if o_hi - o_lo > 1e-12:
        out = (out - o_lo) / (o_hi - o_lo) * (hi - lo) + lo
    return np.clip(out, 0.0, 1.0)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="Input heightmap PNG (grayscale)")
    p.add_argument("output", type=Path, help="Output heightmap PNG")
    p.add_argument("--sigma", type=float, default=18.0, help="Gaussian blur radius (px)")
    p.add_argument(
        "--inner",
        type=float,
        default=0.25,
        help="Fraction of half-diagonal with full smoothing",
    )
    p.add_argument(
        "--outer",
        type=float,
        default=0.7,
        help="Fraction of half-diagonal beyond which no smoothing is applied",
    )
    args = p.parse_args()

    img = Image.open(args.input).convert("L")
    arr = np.asarray(img, dtype=np.float64) / 255.0

    out = radial_smooth(arr, args.sigma, args.inner, args.outer)

    Image.fromarray((out * 255.0).round().astype(np.uint8), mode="L").save(args.output)
    print(
        f"Wrote {args.output} ({img.size[0]}x{img.size[1]}, sigma={args.sigma}, inner={args.inner}, outer={args.outer})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
