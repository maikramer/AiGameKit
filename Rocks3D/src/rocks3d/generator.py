"""Procedural rock mesh generation.

Builds believable rocks by blending three signals on a subdivided
icosphere:

1. **Smooth lumps** — multi-octave FBM (simplex) displacement gives the
   overall organic silhouette.
2. **Angular facets** — a convex *fracture polytope* (the sphere cut by a
   handful of random planes) introduces the flat faces and sharp edges
   that read as real stone rather than a smooth potato.
3. **Surface detail** — a high-frequency FBM octave adds fine roughness.

The two main signals are blended by ``preset.facet_strength`` so the same
code spans rounded pebbles and angular boulders.  A light Taubin pass and
an optional flat base finish the mesh.
"""

from __future__ import annotations

import numpy as np

from rocks3d.defaults import RockPreset, get_preset
from rocks3d.noise import fbm3


def _fracture_radius(directions: np.ndarray, seed: int, plane_count: int) -> np.ndarray:
    """Support radius of a random convex fracture polytope per direction.

    The polytope is the intersection of ``plane_count`` outward-facing
    half-spaces.  For a unit *direction* ``u`` the bounding radius is
    ``min_k(d_k / (u · n_k))`` over planes the direction points towards.
    This yields the flat faces and crisp edges of fractured stone.

    Args:
        directions: ``(N, 3)`` array of unit vertex directions.
        seed: Random seed selecting plane normals and offsets.
        plane_count: Number of cutting planes.  Too few leaves the
            polytope unbounded in some directions, so results are clamped.

    Returns:
        ``(N,)`` array of polytope radii (relative to a unit sphere).
    """
    rng = np.random.RandomState(seed)
    normals = rng.normal(size=(plane_count, 3))
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    offsets = rng.uniform(0.70, 1.0, size=plane_count)

    dot = np.clip(directions @ normals.T, 1e-2, None)  # (N, K)
    radii = (offsets[None, :] / dot).min(axis=1)
    return np.clip(radii, 0.45, 1.25)


def _flatten_base(vertices: np.ndarray, fraction: float) -> np.ndarray:
    """Clamp the lowest *fraction* of vertices to a single ground plane.

    Keeps the mesh topology (and therefore watertightness) intact while
    giving the rock a flat-ish bottom so it sits naturally on terrain.

    Args:
        vertices: ``(N, 3)`` vertex array (modified in place and returned).
        fraction: Fraction of the height range (0..1) to flatten.

    Returns:
        The same ``vertices`` array.
    """
    if fraction <= 0.0:
        return vertices
    y = vertices[:, 1]
    cutoff = np.percentile(y, fraction * 100.0)
    vertices[y < cutoff, 1] = cutoff
    return vertices


def generate_rock(
    type_name: str | None = None,
    seed: int | None = None,
    quality: str = "medium",
    preset: "RockPreset | None" = None,  # noqa: F821, UP037
) -> "trimesh.Trimesh":  # noqa: F821, UP037
    """Generate a procedural rock mesh.

    Creates an icosphere and reshapes it by blending an FBM-displaced
    sphere with a convex fracture polytope (for angular facets), then adds
    a high-frequency detail octave, applies non-uniform scale, lightly
    smooths, and optionally flattens the base.

    Args:
        type_name: Rock type — must be ``"pebble"`` or ``"boulder"``.
        seed: Random seed for reproducible output.  When ``None``, a
            random seed is drawn from ``numpy``'s default RNG.
        quality: Quality tier (``fast|low|medium|high|highest``) controlling
            subdivision and noise detail via :func:`get_preset`.

    Returns:
        A :class:`trimesh.Trimesh` with displaced vertices.

    Raises:
        ValueError: If *type_name* is not a recognised rock type.
    """
    import trimesh

    # A caller (e.g. the formation builder) may hand us a fully-formed preset
    # to use as a chunk; otherwise resolve one from the named type + quality.
    if preset is None:
        from rocks3d.defaults import available_types

        valid_types = available_types()
        if type_name not in valid_types:
            raise ValueError(f"Unknown rock type '{type_name}'. Must be one of: {', '.join(valid_types)}")
        preset = get_preset(type_name, quality)

    if seed is None:
        seed = int(np.random.default_rng().integers(0, 2**32))

    mesh = trimesh.creation.icosphere(subdivisions=preset.subdivisions, radius=1.0)
    verts = mesh.vertices.astype(np.float64)
    directions = verts / np.linalg.norm(verts, axis=1, keepdims=True)

    # 1. Smooth organic silhouette from FBM displacement.
    lump = fbm3(
        verts,
        octaves=preset.octaves,
        frequency=preset.frequency,
        seed=seed,
        noise_type=preset.noise_type,
    )
    r_sphere = 1.0 + lump * preset.amplitude

    # 2. Angular facets from a random convex fracture polytope.
    if preset.facet_strength > 0.0:
        r_facet = _fracture_radius(directions, seed=seed, plane_count=preset.plane_count)
        radius = (1.0 - preset.facet_strength) * r_sphere + preset.facet_strength * r_facet
    else:
        radius = r_sphere

    # 3. High-frequency surface detail.
    detail = fbm3(verts, octaves=3, frequency=preset.frequency * 4.0, seed=seed + 50, noise_type=preset.noise_type)
    radius = radius + detail * preset.detail_amp

    verts = directions * radius[:, np.newaxis]
    verts *= np.array(preset.scale_xyz, dtype=np.float64)
    verts *= preset.radius
    mesh.vertices = verts

    if preset.smooth and preset.smooth_iterations > 0:
        import trimesh.smoothing

        trimesh.smoothing.filter_taubin(mesh, iterations=preset.smooth_iterations)

    mesh.vertices = _flatten_base(mesh.vertices, preset.base_flatten)

    return mesh
