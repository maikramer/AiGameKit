"""Dynamic parameter optimizer for Text3D and Paint3D based on category target_faces.

Adjusts octree_resolution, steps, num_chunks, and paint parameters so generation
is calibrated to the asset's complexity tier instead of using a one-size-fits-all preset.
The safety-net decimation in ``_simplify_mesh_to_target()`` remains active.
"""

from __future__ import annotations

from dataclasses import dataclass

from .profile import Text3DProfile


@dataclass(frozen=True)
class OptimizedText3DParams:
    """Optimal Text3D generation parameters for a given target_faces tier."""

    steps: int
    octree_resolution: int
    num_chunks: int


@dataclass(frozen=True)
class OptimizedPaint3DParams:
    """Optimal Paint3D parameters for a given target_faces tier.

    ``paint_style`` is ``"perlin"`` for simple categories (fast, no AI paint),
    or ``None`` to keep the default Hunyuan painting pipeline.
    """

    paint_style: str | None = None
    paint_max_views: int | None = None
    paint_view_resolution: int | None = None
    paint_texture_size: int | None = None


# ---------------------------------------------------------------------------
# Tier tables
# ---------------------------------------------------------------------------

_TEXT3D_TIERS: list[tuple[int, int, int, int]] = [
    # (max_target_faces, octree, chunks, steps)
    (1200, 80, 2048, 12),  # rock, food, effects, tool, mineral
    (2500, 128, 4096, 18),  # weapon, chest, furniture
    (5000, 192, 6000, 24),  # vegetation, armor, tree, terrain, vehicle
]

# Fallback tier for targets > 5000 (building, creature, humanoid)
_TEXT3D_HIGH = (256, 8000, 30)

_PAINT_TIERS: list[tuple[int, str | None, int | None, int | None, int | None]] = [
    # (max_target_faces, paint_style, max_views, view_resolution, texture_size)
    (1200, "perlin", None, None, None),
    (2500, None, 2, 384, 2048),
]

_PAINT_HIGH = (None, 4, 512, 4096)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def optimize_text3d_for_target(target_faces: int) -> OptimizedText3DParams:
    """Return optimal Text3D parameters for the given ``target_faces``.

    Args:
        target_faces: Desired face count for the category (e.g. 800 for rock).

    Returns:
        Optimized parameters (octree_resolution, num_chunks, steps).
    """
    for max_faces, octree, chunks, steps in _TEXT3D_TIERS:
        if target_faces <= max_faces:
            return OptimizedText3DParams(steps=steps, octree_resolution=octree, num_chunks=chunks)
    octree, chunks, steps = _TEXT3D_HIGH
    return OptimizedText3DParams(steps=steps, octree_resolution=octree, num_chunks=chunks)


def optimize_paint_for_target(target_faces: int) -> OptimizedPaint3DParams:
    """Return optimal Paint3D parameters for the given ``target_faces``.

    Simple categories (target ≤ 1200) get ``paint_style="perlin"`` — no AI
    painting at all.  Medium targets get fewer views at lower resolution.
    High targets use full-quality AI paint.

    Args:
        target_faces: Desired face count for the category.

    Returns:
        Optimized paint parameters.
    """
    for max_faces, style, views, view_res, tex_size in _PAINT_TIERS:
        if target_faces <= max_faces:
            return OptimizedPaint3DParams(
                paint_style=style,
                paint_max_views=views,
                paint_view_resolution=view_res,
                paint_texture_size=tex_size,
            )
    style, views, view_res, tex_size = _PAINT_HIGH
    return OptimizedPaint3DParams(
        paint_style=style,
        paint_max_views=views,
        paint_view_resolution=view_res,
        paint_texture_size=tex_size,
    )


def should_optimize_text3d(t3: Text3DProfile) -> bool:
    """Return True if the optimizer should compute Text3D params.

    The optimizer only activates when the user has not set any explicit overrides
    (preset, steps, octree_resolution, or num_chunks).
    """
    return t3.steps is None and t3.octree_resolution is None and t3.num_chunks is None and t3.preset is None


def should_optimize_paint(t3: Text3DProfile) -> bool:
    """Return True if paint params should be auto-tuned.

    Only activates when the user has not explicitly set any paint tuning options.
    """
    return t3.paint_max_views is None and t3.paint_view_resolution is None and t3.paint_texture_size is None
