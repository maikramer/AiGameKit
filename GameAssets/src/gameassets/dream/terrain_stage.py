"""Terrain generation stage for the dream pipeline — calls terraingen via subprocess."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gamedev_shared.subprocess_utils import resolve_binary, run_cmd


@dataclass
class TerrainConfig:
    """Configuration for terrain generation.

    Args:
        seed: Random seed for deterministic output.
        prompt: Textual description of the terrain.
        world_size: World-space extent (meters).
        max_height: Maximum terrain height (meters).
        roughness: Diamond-square roughness factor.
        size: Heightmap resolution in pixels.
        river_threshold: Flow accumulation threshold for river extraction (scale with ``size``).
        erosion_particles: Erosion particle count (higher = more detail, slower).
        lake_min_area: Minimum lake size in heightmap pixels (higher = fewer lakes).
        lake_max_count: Maximum lakes to keep (0 = no cap); each lake becomes one Water entity.
    """

    seed: int = 42
    prompt: str = ""
    world_size: float = 768.0
    max_height: float = 50.0
    roughness: float = 0.85
    size: int = 2048
    river_threshold: float = 4000.0
    erosion_particles: int = 80000
    lake_min_area: int = 20000
    lake_max_count: int = 3


@dataclass
class TerrainResult:
    """Output paths from terrain generation.

    Attributes:
        heightmap_path: Path to the generated heightmap PNG.
        metadata_path: Path to the terrain metadata JSON.
    """

    heightmap_path: Path
    metadata_path: Path


class TerrainStage:
    """Runs terraingen as a subprocess to produce a heightmap and metadata."""

    def run(self, config: TerrainConfig, output_dir: Path) -> TerrainResult:
        """Execute the terrain generation pipeline.

        Args:
            config: Terrain parameters.
            output_dir: Directory where heightmap.png and terrain.json are written.

        Returns:
            TerrainResult with paths to the generated files.

        Raises:
            FileNotFoundError: If the terraingen binary is not found.
            RuntimeError: If terraingen exits with a non-zero status.
        """
        terraingen_bin = resolve_binary("TERRAINGEN_BIN", "terraingen")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        heightmap_path = output_dir / "heightmap.png"
        metadata_path = output_dir / "terrain.json"

        argv = [
            terraingen_bin,
            "generate",
            "--seed",
            str(config.seed),
            "--output",
            str(heightmap_path),
            "--metadata",
            str(metadata_path),
            "--size",
            str(config.size),
            "--world-size",
            str(config.world_size),
            "--max-height",
            str(config.max_height),
            "--erosion-particles",
            str(config.erosion_particles),
            "--river-threshold",
            str(int(config.river_threshold)),
            "--lake-min-area",
            str(config.lake_min_area),
            "--lake-max-count",
            str(config.lake_max_count),
            "--quiet",
        ]

        if config.roughness != 0.85:
            argv.extend(["--roughness", str(config.roughness)])

        result = run_cmd(argv)

        if not result.ok:
            raise RuntimeError(f"terraingen failed (exit {result.returncode}):\n{result.stderr or result.stdout}")

        if not heightmap_path.exists():
            raise RuntimeError(f"Heightmap not created: {heightmap_path}")
        if not metadata_path.exists():
            raise RuntimeError(f"Metadata not created: {metadata_path}")

        return TerrainResult(heightmap_path=heightmap_path, metadata_path=metadata_path)
