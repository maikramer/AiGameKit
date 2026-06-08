from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RockPreset:
    """Immutable preset for procedural rock generation.

    Attributes:
        name: Human-readable preset identifier (e.g. ``"pebble"``).
        subdivisions: Ico-sphere subdivision level controlling polygon density.
        radius: Base bounding radius of the rock mesh.
        scale_xyz: Non-uniform scale along X, Y, Z axes.
        noise_type: Noise algorithm (``"simplex"``).
        octaves: Noise detail layers (higher = more fine detail).
        frequency: Base spatial frequency of the noise.
        amplitude: Displacement strength relative to radius.
        erosion_passes: Simulated erosion smoothing iterations.
        smooth: Apply final smooth pass to the mesh.
        color_range: (min, max) hex colors for vertex colouring.
        facet_strength: Blend weight (0..1) of the planar-fracture polytope
            against the smooth displaced sphere. ``0`` = rounded blob,
            ``~0.55`` = angular faceted rock, ``>0.8`` = sharp crystalline.
        plane_count: Number of random cutting planes used to build the
            convex fracture polytope. More planes = rounder/busier.
        detail_amp: Amplitude of the high-frequency surface-detail octave
            layered on top, relative to radius.
        smooth_iterations: Taubin smoothing iterations (lower preserves
            crevices and sharp edges; high over-smooths into a blob).
        base_flatten: Fraction (0..1) of the lowest vertices flattened to a
            ground plane so the rock sits naturally. ``0`` disables.
        erosion_strength: Blend (0..1) of the hydraulic-erosion result. Low
            values keep erosion superficial so it weathers edges without
            rounding the rock into a water-worn pebble.
    """

    name: str
    subdivisions: int
    radius: float
    scale_xyz: tuple[float, float, float]
    noise_type: str
    octaves: int
    frequency: float
    amplitude: float
    erosion_passes: int
    smooth: bool
    color_range: tuple[str, str]
    facet_strength: float = 0.0
    plane_count: int = 12
    detail_amp: float = 0.05
    smooth_iterations: int = 3
    base_flatten: float = 0.0
    erosion_strength: float = 0.35


# ---------------------------------------------------------------------------
# Base presets
# ---------------------------------------------------------------------------

PEBBLE = RockPreset(
    name="pebble",
    subdivisions=2,
    radius=0.1,
    scale_xyz=(1.0, 0.7, 0.9),
    noise_type="simplex",
    octaves=4,
    frequency=2.5,
    amplitude=0.20,
    erosion_passes=0,
    smooth=True,
    color_range=("#8B8B83", "#6B6B5A"),
    facet_strength=0.38,
    plane_count=10,
    detail_amp=0.05,
    smooth_iterations=4,
    base_flatten=0.10,
)

BOULDER = RockPreset(
    name="boulder",
    subdivisions=4,
    radius=1.0,
    scale_xyz=(1.0, 0.8, 0.85),
    noise_type="simplex",
    octaves=5,
    frequency=2.2,
    amplitude=0.18,
    erosion_passes=1,
    smooth=True,
    color_range=("#7A7A6F", "#5A5A4F"),
    facet_strength=0.55,
    plane_count=15,
    detail_amp=0.05,
    smooth_iterations=2,
    base_flatten=0.08,
    erosion_strength=0.30,
)

_PRESETS: dict[str, RockPreset] = {
    PEBBLE.name: PEBBLE,
    BOULDER.name: BOULDER,
}

# ---------------------------------------------------------------------------
# Quality overrides
# ---------------------------------------------------------------------------
# Negative values are *relative* adjustments (subtracted from base).
# Zero / absent keys mean "use base value unchanged".
# ---------------------------------------------------------------------------

QUALITY_OVERRIDES: dict[str, dict[str, Any]] = {
    "fast": {"subdivisions": -1, "octaves": -1, "erosion_passes": 0},
    "low": {"subdivisions": 0, "octaves": 0},
    "medium": {},
    "high": {"subdivisions": 1, "octaves": 1},
    "highest": {"subdivisions": 2, "octaves": 2, "erosion_passes": 1},
}


def get_preset(type_name: str, quality: str = "medium") -> RockPreset:
    """Return a :class:`RockPreset` with quality-level adjustments applied.

    Args:
        type_name: Rock type identifier (``"pebble"`` or ``"boulder"``).
        quality: Quality tier (``fast|low|medium|high|highest``).

    Returns:
        New ``RockPreset`` with overrides merged into the base values.

    Raises:
        ValueError: If *type_name* is not a known preset.
    """
    type_name = type_name.lower()

    base = _PRESETS.get(type_name)
    if base is None:
        raise ValueError(f"Unknown rock type '{type_name}'. Available: {', '.join(sorted(_PRESETS))}")

    overrides = QUALITY_OVERRIDES.get(quality, {})

    # Merge: additive for int fields, passthrough for everything else.
    merged = {
        "name": f"{base.name}-{quality}",
        "subdivisions": _apply_delta(base.subdivisions, overrides.get("subdivisions")),
        "radius": base.radius,
        "scale_xyz": base.scale_xyz,
        "noise_type": base.noise_type,
        "octaves": _apply_delta(base.octaves, overrides.get("octaves")),
        "frequency": base.frequency,
        "amplitude": base.amplitude,
        "erosion_passes": _apply_delta(base.erosion_passes, overrides.get("erosion_passes")),
        "smooth": base.smooth,
        "color_range": base.color_range,
        "facet_strength": base.facet_strength,
        "plane_count": base.plane_count,
        "detail_amp": base.detail_amp,
        "smooth_iterations": base.smooth_iterations,
        "base_flatten": base.base_flatten,
        "erosion_strength": base.erosion_strength,
    }

    return RockPreset(**merged)


def _apply_delta(base: int, delta: Any) -> int:
    """Apply a quality-override delta to an integer base value.

    Negative *delta* values are subtracted; zero means no change.
    Non-int deltas are ignored (return base unchanged).
    """
    if not isinstance(delta, (int, float)):
        return base
    return base + int(delta)
