"""Ruído 3D (valor + trilinear + FBM) sem PyTorch — para ``paint3d quick`` estilo pedra."""

from __future__ import annotations

import numpy as np


def _v01(ix: np.ndarray, iy: np.ndarray, iz: np.ndarray, seed: int) -> np.ndarray:
    """Scalar pseudo-aleatório determinístico em [-1, 1] por canto da célula."""
    x = (
        np.sin(
            ix.astype(np.float64) * 12.9898
            + iy.astype(np.float64) * 78.233
            + iz.astype(np.float64) * 37.719
            + float(seed) * 0.173
        )
        * 43758.5453123
    )
    return 2.0 * (x - np.floor(x)) - 1.0


def _noise3_trilinear(v: np.ndarray, seed: int) -> np.ndarray:
    """
    Ruído de valor 3D suavizado (N,3).
    Una função por vértice, C¹ dentro de cada cubo.
    """
    v = np.asarray(v, dtype=np.float64).reshape(-1, 3)
    n = v.shape[0]
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    i = np.floor(v).astype(np.int64)
    f = v - i
    u = f * f * (3.0 - 2.0 * f)

    ox = np.array([0, 1, 0, 1, 0, 1, 0, 1], dtype=np.int64)
    oy = np.array([0, 0, 1, 1, 0, 0, 1, 1], dtype=np.int64)
    oz = np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=np.int64)

    corners = np.zeros((n, 8), dtype=np.float64)
    for c in range(8):
        corners[:, c] = _v01(
            i[:, 0] + ox[c], i[:, 1] + oy[c], i[:, 2] + oz[c], seed
        )

    ux, uy, uz = u[:, 0], u[:, 1], u[:, 2]
    c = corners
    x00 = c[:, 0] * (1 - ux) + c[:, 1] * ux
    x01 = c[:, 4] * (1 - ux) + c[:, 5] * ux
    x10 = c[:, 2] * (1 - ux) + c[:, 3] * ux
    x11 = c[:, 6] * (1 - ux) + c[:, 7] * ux
    y0 = x00 * (1 - uy) + x10 * uy
    y1 = x01 * (1 - uy) + x11 * uy
    return y0 * (1 - uz) + y1 * uz


def fbm3(
    points: np.ndarray,
    *,
    frequency: float = 4.0,
    octaves: int = 4,
    seed: int = 0,
) -> np.ndarray:
    """FBM 3D em ~[-1, 1] para vértices (N,3)."""
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    n = p.shape[0]
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    offset = np.array(
        [
            float(seed % 997) * 0.41,
            float(seed % 991) * 0.37,
            float(seed % 983) * 0.43,
        ],
        dtype=np.float64,
    )
    p = (p + offset) * float(frequency)

    amp = 1.0
    freq_mul = 1.0
    max_amp = 0.0
    total = np.zeros(n, dtype=np.float64)
    mo = max(1, min(int(octaves), 8))
    for o in range(mo):
        total += amp * _noise3_trilinear(p * freq_mul, seed + o * 17)
        max_amp += amp
        amp *= 0.5
        freq_mul *= 2.0

    if max_amp > 1e-9:
        total /= max_amp
    return np.clip(total, -1.0, 1.0)


def normalize_to_unit_cube(points: np.ndarray) -> np.ndarray:
    """Centra e escala vértices para um cubo ~[-1,1]³ (melhor sampling do ruído)."""
    if points.size == 0:
        return points
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    lo = p.min(axis=0)
    hi = p.max(axis=0)
    c = (lo + hi) * 0.5
    p = p - c
    e = (hi - lo).max()
    if e < 1e-9:
        return p
    return p / (0.5 * e)
