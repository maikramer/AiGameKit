from __future__ import annotations

import numpy as np


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    """Hermite smoothstep interpolation between edge0 and edge1."""
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def island_falloff(
    heightmap: np.ndarray,
    falloff: float = 0.35,
    noise_scale: float = 0.15,
    noise_freq: float = 3.0,
    seed: int = 0,
) -> np.ndarray:
    """Apply island falloff with Perlin-noise organic coastline.

    Multiplies the heightmap by a smooth circular falloff mask whose radius
    is modulated by 1D Perlin noise sampled around the compass angle.  This
    produces an organic coastline with bays and promontories.

    Args:
        heightmap: 2D float64 array in [0, 1].
        falloff: Base radius as fraction of half the smallest dimension (0.1–0.5).
        noise_scale: Amplitude of Perlin modulation on the radius.
        noise_freq: Frequency of the Perlin noise around the circle.
        seed: Random seed for the Perlin noise.

    Returns:
        Heightmap with edges smoothly faded to 0 (ocean).
    """
    h, w = heightmap.shape
    cy, cx = h / 2.0, w / 2.0
    half = min(h, w) / 2.0

    # Distance from center, normalised so corner ≈ sqrt(2)
    y, x = np.mgrid[0:h, 0:w]
    dist = np.sqrt((y - cy) ** 2 + (x - cx) ** 2) / half

    # Angle per pixel
    angles = np.arctan2(y - cy, x - cx)  # [-π, π]

    # --- Perlin noise sampled around the unit circle ---
    # Generate 1D noise profile (N_SAMPLES points around 2π)
    noise_1d = _circular_perlin(seed, noise_freq)

    # Map each pixel's angle to a noise value
    angles_pos = (angles + 2.0 * np.pi) % (2.0 * np.pi)  # [0, 2π]
    noise_2d = _sample_circular_noise(noise_1d, angles_pos)

    # Modulated radius: base falloff + Perlin variation
    r_modulated = falloff + noise_2d * noise_scale

    # Transition zone width (beach / shallow water)
    transition = 0.12

    # Smooth mask: 1 inside island, 0 outside, smooth transition
    mask = 1.0 - _smoothstep(r_modulated - transition, r_modulated, dist)

    return (heightmap * mask).astype(np.float64)


def _circular_perlin(seed: int, noise_freq: float, n_samples: int = 1024) -> np.ndarray:
    """Sample Perlin noise around the unit circle and return 1D profile.

    Samples at n_samples evenly-spaced angles, returning values in [-1, 1].
    """
    from pyfastnoiselite.pyfastnoiselite import FastNoiseLite, NoiseType

    noise_gen = FastNoiseLite(seed)
    noise_gen.noise_type = NoiseType.NoiseType_Perlin

    angles = np.linspace(0, 2.0 * np.pi, n_samples, endpoint=False)
    # Sample on a circle of radius=noise_freq in 2D Perlin space
    xs = np.cos(angles) * noise_freq
    ys = np.sin(angles) * noise_freq

    values = np.array([noise_gen.get_noise(float(x), float(y)) for x, y in zip(xs, ys)])
    return values


def _sample_circular_noise(noise_1d: np.ndarray, angles: np.ndarray) -> np.ndarray:
    """Linearly interpolate 1D circular noise at arbitrary angles.

    Args:
        noise_1d: 1D noise profile (n_samples,), assumed to cover [0, 2π].
        angles: Array of angles in [0, 2π].

    Returns:
        Interpolated noise values matching the shape of *angles*.
    """
    n = len(noise_1d)
    idx_float = angles / (2.0 * np.pi) * n
    idx_floor = np.floor(idx_float).astype(int) % n
    idx_ceil = (idx_floor + 1) % n
    frac = idx_float - np.floor(idx_float)
    return noise_1d[idx_floor] * (1.0 - frac) + noise_1d[idx_ceil] * frac
