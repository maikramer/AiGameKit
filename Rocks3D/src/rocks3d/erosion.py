"""Curvature-driven weathering for rock meshes.

Real rock does not weather like a heightfield: water, wind and frost round
off the *convex* features first — exposed edges and protruding bumps — while
flat faces and concave recesses are largely preserved.  Earlier versions ran
a raindrop hydraulic simulation on the Y axis only, which rounded the top of
every rock and turned angular boulders into water-worn pebbles.

This module instead measures local convexity from the surface Laplacian and
nudges convex vertices inward, proportional to how sharply they protrude.
The effect is a believable, *superficial* weathering of edges that keeps the
rock's facets intact.  It is fully vectorised (no per-vertex Python loops) and
deterministic per seed, with a light per-vertex jitter so different seeds
weather slightly differently.
"""

from __future__ import annotations

import numpy as np


def _neighbour_mean(vertices: np.ndarray, edges: np.ndarray, n: int) -> np.ndarray:
    """Average position of each vertex's 1-ring neighbours (vectorised)."""
    src = np.concatenate([edges[:, 0], edges[:, 1]])
    dst = np.concatenate([edges[:, 1], edges[:, 0]])
    summed = np.zeros((n, 3), dtype=np.float64)
    np.add.at(summed, src, vertices[dst])
    counts = np.zeros(n, dtype=np.float64)
    np.add.at(counts, src, 1.0)
    counts[counts == 0] = 1.0
    return summed / counts[:, None]


def apply_erosion(
    mesh: "trimesh.Trimesh",  # noqa: F821, UP037
    seed: int = 0,
    passes: int = 3,
    erosion_rate: float = 0.5,
    strength: float = 1.0,
) -> "trimesh.Trimesh":  # noqa: F821, UP037
    """Weather a rock by rounding its convex edges.

    For each pass the umbrella (Laplacian) vector ``L = mean(neighbours) - v``
    is computed; vertices whose ``L`` points inward (i.e. convex protrusions)
    are moved along ``L`` in proportion to their convexity, so sharp edges
    erode while flat faces stay put.  The final surface is blended back toward
    the original by *strength* to keep the effect superficial.

    Args:
        mesh: Input :class:`trimesh.Trimesh` to weather.
        seed: Random seed for the per-vertex weathering jitter.
        passes: Number of weathering passes (more = rounder edges).
        erosion_rate: Per-pass displacement fraction along the Laplacian.
        strength: Final blend (0..1) toward the eroded surface; ``<1`` keeps
            erosion modest so facets survive.

    Returns:
        New :class:`trimesh.Trimesh` with the same faces and weathered vertices.
    """
    import trimesh

    original = mesh.vertices.copy()
    verts = original.copy()
    faces = mesh.faces
    n = len(verts)

    if passes <= 0 or strength <= 0.0:
        return trimesh.Trimesh(vertices=original, faces=faces.copy(), process=False)

    # Undirected 1-ring edges from triangle faces.
    edges = faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)

    # Vertex normals (recomputed once from the input surface) define "outward".
    normals = np.asarray(mesh.vertex_normals, dtype=np.float64)

    # Per-vertex jitter so different seeds weather slightly differently,
    # without breaking reproducibility for a fixed seed.
    rng = np.random.RandomState(seed)
    jitter = rng.uniform(0.7, 1.3, size=n)

    rate = float(np.clip(erosion_rate, 0.0, 1.0))

    for _ in range(passes):
        lap = _neighbour_mean(verts, edges, n) - verts
        lap_norm = np.linalg.norm(lap, axis=1)
        safe = lap_norm > 1e-12
        lap_hat = np.zeros_like(lap)
        lap_hat[safe] = lap[safe] / lap_norm[safe, None]
        # Convexity: Laplacian points inward (against the normal) on bumps.
        convex = np.clip(-(lap_hat * normals).sum(axis=1), 0.0, 1.0)
        weight = convex * jitter * rate
        verts += lap * weight[:, None]

    # Blend back toward the original surface to keep weathering superficial.
    s = float(np.clip(strength, 0.0, 1.0))
    blended = original + (verts - original) * s

    return trimesh.Trimesh(vertices=blended, faces=faces.copy(), process=False)
