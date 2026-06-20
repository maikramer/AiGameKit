"""Procedural rock *formations* — scenery rochedos built by booleaning several
angular chunks together.

A single displaced-icosphere rock (see :mod:`rocks3d.generator`) is, by
construction, roughly convex: the fracture polytope is a convex intersection of
half-spaces and FBM only bumps the surface. That is exactly what heightmap
terrain already gives you — a single-valued, overhang-free surface. To add the
*concave* geometry a heightmap cannot express (overhangs, arches, crevices,
balanced stacks), this module generates a handful of chunks, scatters/stacks
them so they interpenetrate, and unions them into one mesh. The boolean union of
overlapping convex blobs is non-convex, so the result reads as a real rock
formation with caves and overhangs.

Styles:

* ``stack`` — boulders piled into a balancing tower (overhangs at the joints).
* ``outcrop`` — boulders jammed together at ground level (crevices, ledges).
* ``cliff`` — a row of tall tilted slabs forming a rock wall / cliff face.
* ``arch`` — two pillars bridged by a lintel: a real hole underneath.
* ``spire-cluster`` — a cluster of hoodoo-like spires.

Each formation is recentred on XZ and dropped so its lowest point sits at
``y = 0``, ready to place on terrain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from rocks3d.defaults import RockPreset
from rocks3d.generator import generate_rock

if TYPE_CHECKING:
    import trimesh

STYLES: tuple[str, ...] = ("stack", "outcrop", "cliff", "arch", "spire-cluster")

# Chunk subdivision per quality tier. Union cost grows fast with poly count, so
# keep chunks modest; the formation reads from silhouette + overhangs, not from
# micro-detail on each chunk.
_CHUNK_SUBDIV: dict[str, int] = {
    "fast": 2,
    "low": 2,
    "medium": 3,
    "high": 3,
    "highest": 4,
}

_FORMATION_COLOR = ("#75706A", "#4F4A42")


def _chunk_preset(
    *,
    scale_xyz: tuple[float, float, float],
    subdivisions: int,
    facet: float = 0.62,
    planes: int = 14,
    amplitude: float = 0.18,
) -> RockPreset:
    """A base preset for one formation chunk (no base-flatten — the *formation*
    is flattened once, after the union)."""
    return RockPreset(
        name="chunk",
        subdivisions=subdivisions,
        radius=1.0,
        scale_xyz=scale_xyz,
        noise_type="simplex",
        octaves=4,
        frequency=2.2,
        amplitude=amplitude,
        erosion_passes=0,
        smooth=True,
        color_range=_FORMATION_COLOR,
        facet_strength=facet,
        plane_count=planes,
        detail_amp=0.05,
        smooth_iterations=1,
        base_flatten=0.0,
    )


def _place(
    mesh: "trimesh.Trimesh",
    translate: tuple[float, float, float],
    rot_euler: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> "trimesh.Trimesh":
    """Rotate (XYZ euler radians) then translate a chunk in place."""
    import trimesh

    if any(rot_euler):
        mesh.apply_transform(trimesh.transformations.euler_matrix(*rot_euler))
    mesh.apply_translation(translate)
    return mesh


def _combine(pieces: list["trimesh.Trimesh"]) -> "trimesh.Trimesh":
    """Union the chunks into a single solid; fall back to a plain concatenation
    if the boolean engine fails (still renders fine for scenery)."""
    import trimesh

    if len(pieces) == 1:
        return pieces[0]
    try:
        merged = trimesh.boolean.union(pieces)
        if merged is not None and len(merged.vertices) > 0 and len(merged.faces) > 0:
            return merged
    except Exception:  # noqa: BLE001 - boolean engines raise a zoo of errors
        pass
    return trimesh.util.concatenate(pieces)


def _finalize(formation: "trimesh.Trimesh") -> "trimesh.Trimesh":
    """Recentre on XZ and drop the formation so its base sits at ``y = 0``."""
    lo, hi = formation.bounds
    centre = (lo + hi) * 0.5
    formation.apply_translation((-centre[0], -lo[1], -centre[2]))
    return formation


def _chunk(rng: np.random.Generator, **kwargs) -> "trimesh.Trimesh":
    preset = _chunk_preset(**kwargs)
    return generate_rock(preset=preset, seed=int(rng.integers(0, 2**31)))


def generate_formation(
    style: str,
    seed: int | None = None,
    quality: str = "medium",
    chunks: int | None = None,
) -> "trimesh.Trimesh":
    """Generate one rock formation mesh of *style*.

    Args:
        style: One of :data:`STYLES`.
        seed: Reproducible seed (``None`` → random).
        quality: Quality tier — drives chunk subdivision via
            :data:`_CHUNK_SUBDIV`.
        chunks: Override the number of chunks (clamped to a sane range per
            style); ``None`` lets the style pick.

    Returns:
        A single :class:`trimesh.Trimesh`, recentred and sitting on ``y = 0``.

    Raises:
        ValueError: If *style* is unknown.
    """
    style = style.lower()
    if style not in STYLES:
        raise ValueError(f"Unknown formation style '{style}'. Must be one of: {', '.join(STYLES)}")

    rng = np.random.default_rng(seed)
    sub = _CHUNK_SUBDIV.get(quality, 3)
    pieces: list[trimesh.Trimesh] = []

    if style == "stack":
        n = int(np.clip(chunks or rng.integers(3, 6), 2, 8))
        y = 0.0
        for i in range(n):
            r = (1.0 - 0.11 * i) * float(rng.uniform(0.85, 1.05))
            sxyz = (r * rng.uniform(0.9, 1.2), r * rng.uniform(0.6, 0.85), r * rng.uniform(0.9, 1.2))
            m = _chunk(rng, scale_xyz=sxyz, subdivisions=sub, facet=float(rng.uniform(0.55, 0.7)),
                       planes=int(rng.integers(10, 16)))
            h = float(m.bounds[1][1] - m.bounds[0][1])
            jitter = rng.uniform(-0.28, 0.28, 2) * r  # horizontal slip → overhangs
            _place(m, (float(jitter[0]), y + h * 0.4, float(jitter[1])),
                   (float(rng.uniform(-0.2, 0.2)), float(rng.uniform(0, 6.28)), float(rng.uniform(-0.2, 0.2))))
            y += h * 0.55  # overlap so neighbours fuse on union
            pieces.append(m)

    elif style == "outcrop":
        n = int(np.clip(chunks or rng.integers(4, 8), 3, 10))
        for _ in range(n):
            r = float(rng.uniform(0.6, 1.15))
            sxyz = (r * rng.uniform(0.9, 1.35), r * rng.uniform(0.8, 1.35), r * rng.uniform(0.9, 1.35))
            m = _chunk(rng, scale_xyz=sxyz, subdivisions=sub, facet=float(rng.uniform(0.6, 0.78)),
                       planes=int(rng.integers(12, 20)))
            ang = float(rng.uniform(0, 6.28))
            rad = float(rng.uniform(0.0, 0.95))
            _place(m, (np.cos(ang) * rad, float(rng.uniform(-0.1, 0.5)) * r, np.sin(ang) * rad),
                   (float(rng.uniform(-0.5, 0.5)), float(rng.uniform(0, 6.28)), float(rng.uniform(-0.5, 0.5))))
            pieces.append(m)

    elif style == "cliff":
        n = int(np.clip(chunks or rng.integers(4, 7), 3, 9))
        for i in range(n):
            sxyz = (float(rng.uniform(0.7, 1.1)), float(rng.uniform(1.6, 2.6)), float(rng.uniform(0.8, 1.3)))
            m = _chunk(rng, scale_xyz=sxyz, subdivisions=sub, facet=0.72, planes=int(rng.integers(8, 12)))
            x = (i - (n - 1) / 2.0) * float(rng.uniform(1.0, 1.5))
            _place(m, (x, float(rng.uniform(-0.2, 0.2)), float(rng.uniform(-0.3, 0.3))),
                   (float(rng.uniform(-0.25, 0.25)), float(rng.uniform(-0.3, 0.3)), float(rng.uniform(-0.15, 0.15))))
            pieces.append(m)

    elif style == "arch":
        gap = float(rng.uniform(1.2, 1.8))
        pillar_h = 0.0
        for side in (-1.0, 1.0):
            sxyz = (float(rng.uniform(0.7, 0.95)), float(rng.uniform(1.8, 2.4)), float(rng.uniform(0.7, 0.95)))
            m = _chunk(rng, scale_xyz=sxyz, subdivisions=sub, facet=0.68, planes=12)
            _place(m, (side * gap, 0.0, 0.0), (0.0, float(rng.uniform(0, 6.28)), side * 0.1))
            pillar_h = max(pillar_h, float(m.bounds[1][1]))
            pieces.append(m)
        # Lintel: wide enough to overlap both pillars, sitting near their tops —
        # the span leaves a real hole underneath (the concavity heightmaps lack).
        sxyz = (gap * 1.5, float(rng.uniform(0.5, 0.8)), float(rng.uniform(0.7, 1.0)))
        m = _chunk(rng, scale_xyz=sxyz, subdivisions=sub, facet=0.58, planes=10)
        _place(m, (0.0, pillar_h * 0.82, 0.0), (0.0, 0.0, float(rng.uniform(-0.1, 0.1))))
        pieces.append(m)

    else:  # spire-cluster
        n = int(np.clip(chunks or rng.integers(3, 6), 2, 8))
        for _ in range(n):
            sxyz = (float(rng.uniform(0.4, 0.6)), float(rng.uniform(2.0, 3.2)), float(rng.uniform(0.45, 0.65)))
            m = _chunk(rng, scale_xyz=sxyz, subdivisions=sub, facet=0.72, planes=10)
            ang = float(rng.uniform(0, 6.28))
            rad = float(rng.uniform(0.3, 1.1))
            _place(m, (np.cos(ang) * rad, float(rng.uniform(-0.2, 0.3)), np.sin(ang) * rad),
                   (float(rng.uniform(-0.1, 0.1)), float(rng.uniform(0, 6.28)), float(rng.uniform(-0.1, 0.1))))
            pieces.append(m)

    return _finalize(_combine(pieces))
