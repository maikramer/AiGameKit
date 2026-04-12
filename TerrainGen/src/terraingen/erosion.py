from __future__ import annotations

import numpy as np


def _bilinear_height(heightmap: np.ndarray, x: float, y: float) -> float:
    """Sample height at fractional coordinates using bilinear interpolation."""
    h, w = heightmap.shape
    x0 = int(x)
    y0 = int(y)
    x1 = x0 + 1
    y1 = y0 + 1
    x0c = min(max(x0, 0), h - 1)
    x1c = min(max(x1, 0), h - 1)
    y0c = min(max(y0, 0), w - 1)
    y1c = min(max(y1, 0), w - 1)
    fx = x - x0
    fy = y - y0
    h00 = heightmap[x0c, y0c]
    h10 = heightmap[x1c, y0c]
    h01 = heightmap[x0c, y1c]
    h11 = heightmap[x1c, y1c]
    return h00 * (1 - fx) * (1 - fy) + h10 * fx * (1 - fy) + h01 * (1 - fx) * fy + h11 * fx * fy


def _compute_gradient(heightmap: np.ndarray, x: float, y: float) -> tuple[float, float]:
    """Compute height gradient at fractional position (x, y).

    Returns (dx, dy) pointing downhill (negative height change direction).
    """
    eps = 1.0
    h_px = _bilinear_height(heightmap, x + eps, y)
    h_py = _bilinear_height(heightmap, x, y + eps)
    h_mx = _bilinear_height(heightmap, x - eps, y)
    h_my = _bilinear_height(heightmap, x, y - eps)
    dx = (h_px - h_mx) / (2.0 * eps)
    dy = (h_py - h_my) / (2.0 * eps)
    return dx, dy


def _erode_4neighbors(heightmap: np.ndarray, x: float, y: float, amount: float, radius: int = 3) -> None:
    """Remove sediment from up to 4 neighboring cells near (x, y).

    Erosion is proportional to height difference (taller neighbors lose more).
    """
    ix = int(x)
    iy = int(y)
    h, w = heightmap.shape
    weights: list[float] = []
    neighbors: list[tuple[int, int]] = []
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            nx = ix + dx
            ny = iy + dy
            if 0 <= nx < h and 0 <= ny < w:
                dist_sq = dx * dx + dy * dy
                if dist_sq == 0:
                    continue
                wt = max(0.0, radius - (dx * dx + dy * dy) ** 0.5)
                if wt > 0:
                    weights.append(wt)
                    neighbors.append((nx, ny))
    if not neighbors:
        return
    total_w = sum(weights)
    if total_w <= 0:
        return
    for i, (nx, ny) in enumerate(neighbors):
        heightmap[nx, ny] -= amount * (weights[i] / total_w)


def _deposit_4neighbors(heightmap: np.ndarray, x: float, y: float, amount: float) -> None:
    """Deposit sediment to 4 neighboring cells near (x, y).

    Deposition is proportional to inverse height difference (lower neighbors gain more).
    """
    ix = int(x)
    iy = int(y)
    h, w = heightmap.shape
    weights: list[float] = []
    neighbors: list[tuple[int, int]] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx = ix + dx
            ny = iy + dy
            if 0 <= nx < h and 0 <= ny < w:
                wt = 1.0
                weights.append(wt)
                neighbors.append((nx, ny))
    if not neighbors:
        return
    total_w = sum(weights)
    for i, (nx, ny) in enumerate(neighbors):
        heightmap[nx, ny] += amount * (weights[i] / total_w)


def apply_erosion(
    heightmap: np.ndarray,
    seed: int = 42,
    num_particles: int = 50000,
    erosion_rate: float = 0.3,
    deposition_rate: float = 0.3,
    evaporation_rate: float = 0.01,
    gravity: float = 4.0,
    inertia: float = 0.05,
    min_water: float = 0.01,
    max_capacity_factor: float = 0.3,
    min_capacity: float = 0.001,
    max_steps_per_particle: int = 100,
) -> np.ndarray:
    """Apply particle-based hydraulic erosion to a heightmap.

    Simulates water droplets flowing downhill, eroding high areas and
    depositing sediment in valleys. Produces realistic valley formation
    and ridge sharpening.

    Args:
        heightmap: 2D array of elevations (float64). Not modified in-place.
        seed: Random seed for reproducibility.
        num_particles: Number of water droplets to simulate.
        erosion_rate: How much sediment a droplet can erode per step.
        deposition_rate: How quickly sediment is deposited when capacity is exceeded.
        evaporation_rate: Fraction of water lost per step (0-1).
        gravity: Acceleration due to gravity, affects velocity buildup.
        inertia: How much old velocity is retained (0-1, higher = straighter paths).
        min_water: Kill droplet when water drops below this threshold.
        max_capacity_factor: Multiplier for sediment capacity calculation.
        min_capacity: Minimum sediment capacity regardless of conditions.
        max_steps_per_particle: Maximum lifetime steps per droplet.

    Returns:
        New heightmap array (same shape, float64) with erosion applied.
    """
    result = heightmap.copy()
    rng = np.random.default_rng(seed)
    h, w = result.shape

    h_min = float(result.min())
    h_max = float(result.max())
    h_range = h_max - h_min if h_max > h_min else 1.0
    weights = (result - h_min) / h_range
    flat_weights = weights.ravel()
    cum_weights = np.cumsum(flat_weights)
    total_weight = cum_weights[-1]

    for _ in range(num_particles):
        r = rng.random() * total_weight
        idx = int(np.searchsorted(cum_weights, r))
        idx = min(idx, h * w - 1)
        px = float(idx // w)
        py = float(idx % w)

        dir_x = 0.0
        dir_y = 0.0
        velocity = 1.0
        water = 1.0
        sediment = 0.0

        for _ in range(max_steps_per_particle):
            ix = int(px)
            iy = int(py)
            if ix < 0 or ix >= h - 1 or iy < 0 or iy >= w - 1:
                break

            grad_x, grad_y = _compute_gradient(result, px, py)

            dir_x = dir_x * inertia - grad_x * (1 - inertia)
            dir_y = dir_y * inertia - grad_y * (1 - inertia)

            length = (dir_x * dir_x + dir_y * dir_y) ** 0.5
            if length < 1e-10:
                dir_x = rng.random() - 0.5
                dir_y = rng.random() - 0.5
                length = (dir_x * dir_x + dir_y * dir_y) ** 0.5
            dir_x /= length
            dir_y /= length

            new_px = px + dir_x
            new_py = py + dir_y

            nix = int(new_px)
            niy = int(new_py)
            if nix < 0 or nix >= h - 1 or niy < 0 or niy >= w - 1:
                break

            height_old = _bilinear_height(result, px, py)
            height_new = _bilinear_height(result, new_px, new_py)
            height_diff = height_new - height_old

            # capacity = max(speed * water * factor, min_capacity)
            speed = (dir_x * dir_x + dir_y * dir_y) ** 0.5 * velocity
            capacity = max(speed * water * max_capacity_factor, min_capacity)

            if sediment > capacity or height_diff > 0:
                if height_diff > 0:
                    # Uphill: deposit enough sediment to fill the climb
                    deposit_amount = min(height_diff, sediment)
                else:
                    deposit_amount = deposition_rate * (sediment - capacity)
                sediment -= deposit_amount
                _deposit_4neighbors(result, px, py, deposit_amount)
            else:
                erode_amount = min(erosion_rate * (capacity - sediment), -height_diff)
                erode_amount = max(erode_amount, 0.0)
                sediment += erode_amount
                _erode_4neighbors(result, px, py, erode_amount)

            # v = sqrt(max(0, v² + Δh·g)), clamped to avoid stagnation
            velocity = max(0.0, velocity * velocity + height_diff * gravity) ** 0.5
            velocity = max(velocity, 0.1)

            water *= 1.0 - evaporation_rate
            if water < min_water:
                break

            px = new_px
            py = new_py

    return result
