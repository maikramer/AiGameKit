"""Noise evaluation for procedural rock displacement.

Provides simplex noise, fractal Brownian motion (FBM), and stubs for
future Perlin and Worley noise implementations.
"""

from __future__ import annotations

import numpy as np


def simplex3(x: float, y: float, z: float, seed: int = 0) -> float:
    """Evaluate 3D simplex noise at a single point.

    Args:
        x: X coordinate.
        y: Y coordinate.
        z: Z coordinate.
        seed: Random seed for reproducibility.

    Returns:
        Noise value in approximately [-1, 1].
    """
    from opensimplex import OpenSimplex

    return OpenSimplex(seed=seed).noise3(x, y, z)


def perlin3(x: float, y: float, z: float, seed: int = 0) -> float:
    """Evaluate 3D Perlin noise at a single point.

    .. note::
        Not yet implemented. Planned for v2.

    Args:
        x: X coordinate.
        y: Y coordinate.
        z: Z coordinate.
        seed: Random seed for reproducibility.

    Raises:
        NotImplementedError: Always — planned for v2.
    """
    raise NotImplementedError("perlin3 is not yet implemented. Use simplex3 for now. Planned for v2.")


def worley3(x: float, y: float, z: float, seed: int = 0) -> float:
    """Evaluate 3D Worley (cellular) noise at a single point.

    .. note::
        Not yet implemented. Planned for v2.

    Args:
        x: X coordinate.
        y: Y coordinate.
        z: Z coordinate.
        seed: Random seed for reproducibility.

    Raises:
        NotImplementedError: Always — planned for v2.
    """
    raise NotImplementedError("worley3 is not yet implemented. Use simplex3 for now. Planned for v2.")


def _simplex3_batch(xs: np.ndarray, ys: np.ndarray, zs: np.ndarray, seed: int) -> np.ndarray:
    """Vectorised simplex3 over arrays using opensimplex."""
    from opensimplex import OpenSimplex

    gen = OpenSimplex(seed=seed)
    n = len(xs)
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        out[i] = gen.noise3(float(xs[i]), float(ys[i]), float(zs[i]))
    return out


def fbm3(
    positions: np.ndarray,
    octaves: int = 4,
    frequency: float = 1.0,
    lacunarity: float = 2.0,
    persistence: float = 0.5,
    seed: int = 0,
    noise_type: str = "simplex",
) -> np.ndarray:
    """Fractal Brownian Motion over 3D positions.

    Combines multiple octaves of noise: each octave doubles the frequency
    and halves the amplitude (controlled by ``lacunarity`` and
    ``persistence``).

    Args:
        positions: ``(N, 3)`` array of 3D coordinates.
        octaves: Number of noise octaves to sum.
        frequency: Base frequency of the first octave.
        lacunarity: Frequency multiplier per octave.
        persistence: Amplitude multiplier per octave.
        seed: Base seed — each octave derives ``seed + octave_index``.
        noise_type: Noise algorithm (only ``"simplex"`` supported).

    Returns:
        ``(N,)`` array of noise values.

    Raises:
        ValueError: If *noise_type* is not ``"simplex"``.
    """
    if noise_type != "simplex":
        raise ValueError(f"Unsupported noise_type={noise_type!r}. Only 'simplex' is currently implemented.")

    positions = np.asarray(positions, dtype=np.float64)
    if positions.ndim == 1:
        positions = positions.reshape(1, 3)
    if positions.shape[1] != 3:
        raise ValueError(f"Expected positions with shape (N, 3), got {positions.shape}")

    xs, ys, zs = positions[:, 0], positions[:, 1], positions[:, 2]
    result = np.zeros(len(xs), dtype=np.float64)
    amplitude = 1.0
    current_freq = frequency
    max_amplitude = 0.0

    for i in range(octaves):
        octave_seed = seed + i
        result += amplitude * _simplex3_batch(
            xs * current_freq,
            ys * current_freq,
            zs * current_freq,
            octave_seed,
        )
        max_amplitude += amplitude
        amplitude *= persistence
        current_freq *= lacunarity

    if max_amplitude > 0:
        result /= max_amplitude

    return result
