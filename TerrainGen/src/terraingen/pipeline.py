from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from terraingen.erosion import apply_erosion
from terraingen.heightmap import apply_smoothing, generate_heightmap
from terraingen.lakes import LakeData, LakePlaneData, excavate_lakes, generate_lake_planes, identify_lakes
from terraingen.rivers import carve_river_valleys, extract_rivers


@dataclass
class TerrainPipeline:
    """Full configuration for terrain generation pipeline."""

    size: int = 2048
    roughness: float = 0.85
    seed: int = 42
    # Smoothing
    smoothing_threshold: float = 1.5
    smoothing_iterations: int = 2
    # Erosion
    erosion_particles: int = 80000
    erosion_rate: float = 0.3
    deposition_rate: float = 0.3
    evaporation_rate: float = 0.01
    erosion_gravity: float = 4.0
    erosion_inertia: float = 0.05
    # Rivers
    river_threshold: float = 3200.0
    valley_depth: float = 0.12
    valley_width: int = 5
    # Lakes (keep counts low — each lake becomes a VibeGame Water entity)
    lake_min_area: int = 20000
    lake_max_depth: float = 0.1
    lake_max_count: int = 3
    # Performance
    skip_erosion: bool = False
    skip_rivers: bool = False
    skip_lakes: bool = False


@dataclass
class PipelineResult:
    """Result from running the terrain generation pipeline."""

    heightmap: np.ndarray
    rivers: list[np.ndarray] = field(default_factory=list)
    lakes: list[LakeData] = field(default_factory=list)
    lake_planes: list[LakePlaneData] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def run_pipeline(config: TerrainPipeline) -> PipelineResult:
    """Execute the full terrain generation pipeline.

    Chains heightmap generation, smoothing, erosion, river extraction/carving,
    and lake identification/excavation in order.  Each step is timed and the
    results are collected in the ``stats`` dict of the returned
    :class:`PipelineResult`.

    Steps can be skipped via ``skip_erosion``, ``skip_rivers``, and
    ``skip_lakes`` flags on *config*.  When a step fails with an exception it
    is caught, logged in ``stats``, and the pipeline continues.

    Args:
        config: Full pipeline configuration.

    Returns:
        PipelineResult with heightmap, rivers, lakes, lake_planes, and stats.
    """
    stats: dict = {}
    total_start = time.time()

    # Step 1: Generate raw heightmap
    t0 = time.time()
    heightmap = generate_heightmap(config.size, config.roughness, config.seed)
    stats["heightmap"] = {"time": time.time() - t0}

    # Step 2: Smoothing
    t0 = time.time()
    heightmap = apply_smoothing(heightmap, config.smoothing_threshold, config.smoothing_iterations)
    stats["smoothing"] = {"time": time.time() - t0}

    # Step 3: Erosion
    if not config.skip_erosion:
        t0 = time.time()
        heightmap = apply_erosion(
            heightmap,
            seed=config.seed,
            num_particles=config.erosion_particles,
            erosion_rate=config.erosion_rate,
            deposition_rate=config.deposition_rate,
            evaporation_rate=config.evaporation_rate,
            gravity=config.erosion_gravity,
            inertia=config.erosion_inertia,
        )
        stats["erosion"] = {"time": time.time() - t0}

    # Step 4: Rivers
    rivers: list[np.ndarray] = []
    if not config.skip_rivers:
        try:
            t0 = time.time()
            rivers = extract_rivers(heightmap, accumulation_threshold=config.river_threshold, seed=config.seed)
            stats["rivers"] = {"time": time.time() - t0}

            if rivers:
                t0 = time.time()
                heightmap = carve_river_valleys(heightmap, rivers, depth=config.valley_depth, width=config.valley_width)
                stats["valley_carving"] = {"time": time.time() - t0}
            else:
                stats["valley_carving"] = {"time": 0.0}
        except Exception as exc:
            stats["rivers"] = {"time": 0.0, "error": str(exc)}
            stats["valley_carving"] = {"time": 0.0}

    # Step 5: Lakes
    lakes: list[LakeData] = []
    lake_planes: list[LakePlaneData] = []
    if not config.skip_lakes:
        try:
            t0 = time.time()
            lakes = identify_lakes(heightmap, min_area=config.lake_min_area, max_depth=config.lake_max_depth)
            lakes.sort(key=lambda L: L.area, reverse=True)
            if config.lake_max_count > 0 and len(lakes) > config.lake_max_count:
                lakes = lakes[: config.lake_max_count]
            stats["lakes"] = {"time": time.time() - t0}

            if lakes:
                heightmap = excavate_lakes(heightmap, lakes)
                lake_planes = generate_lake_planes(lakes)
        except Exception as exc:
            stats["lakes"] = {"time": 0.0, "error": str(exc)}

    # Collect summary stats
    stats["total"] = {"time": time.time() - total_start}
    stats["heightmap_stats"] = {
        "min": float(heightmap.min()),
        "max": float(heightmap.max()),
        "mean": float(heightmap.mean()),
        "std": float(heightmap.std()),
    }
    stats["river_count"] = len(rivers)
    stats["lake_count"] = len(lakes)

    return PipelineResult(
        heightmap=heightmap,
        rivers=rivers,
        lakes=lakes,
        lake_planes=lake_planes,
        stats=stats,
    )
