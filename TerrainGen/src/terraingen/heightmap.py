from __future__ import annotations

import numpy as np


def _square_fill(
    hm: np.ndarray,
    rng: np.random.Generator,
    rows: np.ndarray,
    cols: np.ndarray,
    half: int,
    gs: int,
    scale: float,
) -> None:
    """Average cardinal neighbours + noise for a set of square-step points."""
    total = np.zeros(rows.shape, dtype=np.float64)
    count = np.zeros(rows.shape, dtype=np.float64)

    m = cols - half >= 0
    total[m] += hm[rows[m], cols[m] - half]
    count[m] += 1

    m = cols + half < gs
    total[m] += hm[rows[m], cols[m] + half]
    count[m] += 1

    m = rows - half >= 0
    total[m] += hm[rows[m] - half, cols[m]]
    count[m] += 1

    m = rows + half < gs
    total[m] += hm[rows[m] + half, cols[m]]
    count[m] += 1

    flat = ((total / count) + (rng.random(rows.shape) - 0.5) * scale).ravel()
    hm[rows.ravel(), cols.ravel()] = flat


def generate_heightmap(size: int = 2048, roughness: float = 0.85, seed: int = 42) -> np.ndarray:
    """Generate a heightmap using diamond-square subdivision.

    Args:
        size: Output resolution (square).
        roughness: Controls how fast fine-detail amplitude decays per octave.
                   ~0.5 = very rough, ~1.0 = smooth rolling hills.
        seed: RNG seed for reproducibility.

    Returns:
        Array of shape (*size*, *size*), dtype float64.
    """
    rng = np.random.default_rng(seed)

    n = 1
    while n < size:
        n *= 2
    gs = n + 1

    hm = np.zeros((gs, gs), dtype=np.float64)
    hm[0, 0] = rng.random()
    hm[0, n] = rng.random()
    hm[n, 0] = rng.random()
    hm[n, n] = rng.random()

    step = n
    scale = 1.0
    decay = 2.0 ** (-roughness)

    while step > 1:
        half = step // 2

        # --- Diamond step (vectorised) ---
        tl = hm[0 : gs - step : step, 0 : gs - step : step]
        tr = hm[0 : gs - step : step, step:gs:step]
        bl = hm[step:gs:step, 0 : gs - step : step]
        br = hm[step:gs:step, step:gs:step]
        avg = (tl + tr + bl + br) * 0.25
        hm[half : gs - half : step, half : gs - half : step] = avg + (rng.random(avg.shape) - 0.5) * scale

        # --- Square step (vectorised, two independent sub-passes) ---
        ra, ca = np.meshgrid(np.arange(0, gs, step), np.arange(half, gs, step), indexing="ij")
        _square_fill(hm, rng, ra, ca, half, gs, scale)

        rb, cb = np.meshgrid(np.arange(half, gs, step), np.arange(0, gs, step), indexing="ij")
        _square_fill(hm, rng, rb, cb, half, gs, scale)

        step = half
        scale *= decay

    return hm[:size, :size]


def differentiate(heightmap: np.ndarray) -> np.ndarray:
    """Compute gradient map from height differences with 3 neighbors."""
    h, w = heightmap.shape
    grad = np.zeros((h, w), dtype=np.float64)
    for x in range(h):
        for y in range(w):
            if x > 0 and y > 0:
                p = heightmap[x, y]
                west = heightmap[x - 1, y] - p
                northwest = heightmap[x - 1, y - 1] - p
                north = heightmap[x, y - 1] - p
                grad[x, y] = (west + northwest + north) / 3.0
    return grad


def _filter(input: np.ndarray, gradient: np.ndarray, threshold: float) -> np.ndarray:
    h, w = input.shape
    output = np.copy(input)
    for x in range(h):
        for y in range(w):
            if x > 0 and y > 0 and abs(gradient[x, y] * 100.0) < threshold:
                output[x, y] = (input[x - 1, y] + input[x - 1, y - 1] + input[x, y - 1] + input[x, y]) / 4.0
    return output


def smooth(heightmap: np.ndarray, gradient: np.ndarray, threshold: float, iterations: int) -> np.ndarray:
    """Iterative low-pass filter based on gradient threshold."""
    result = heightmap
    for _ in range(iterations):
        result = _filter(result, gradient, threshold)
    return result


def apply_smoothing(heightmap: np.ndarray, threshold: float = 0.8, iterations: int = 4) -> np.ndarray:
    """Apply gradient-based smoothing pass: differentiate, filter, re-differentiate.

    Returns smoothed heightmap.
    """
    gradient = differentiate(heightmap)
    result = smooth(heightmap, gradient, threshold, iterations)
    return result
