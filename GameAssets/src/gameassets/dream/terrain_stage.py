"""Terrain generation stage for the dream pipeline — calls terrain3d via subprocess."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gamedev_shared.subprocess_utils import resolve_binary, run_cmd


@dataclass
class TerrainConfig:
    """Configuration for terrain generation.

    Args:
        seed: Random seed for deterministic output.
        prompt: Textual description of the terrain (metadata only; model is unconditional).
        world_size: World-space extent (meters).
        max_height: Maximum terrain height (meters).
        size: Heightmap resolution in pixels.
    """

    seed: int = 42
    prompt: str = ""
    world_size: float = 768.0
    max_height: float = 50.0
    size: int = 2048


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
    """Runs terrain3d as a subprocess to produce a heightmap and metadata."""

    def run(self, config: TerrainConfig, output_dir: Path) -> TerrainResult:
        """Execute the terrain generation pipeline.

        Args:
            config: Terrain parameters.
            output_dir: Directory where heightmap.png and terrain.json are written.

        Returns:
            TerrainResult with paths to the generated files.

        Raises:
            FileNotFoundError: If the terrain3d binary is not found.
            RuntimeError: If terrain3d exits with a non-zero status.
        """
        terrain3d_bin = resolve_binary("TERRAIN3D_BIN", "terrain3d")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        heightmap_path = output_dir / "heightmap.png"
        metadata_path = output_dir / "terrain.json"

        argv = [
            terrain3d_bin,
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
            "--quiet",
        ]

        if config.prompt:
            argv.extend(["--prompt", config.prompt])

        result = run_cmd(argv)

        if not result.ok:
            raise RuntimeError(f"terrain3d failed (exit {result.returncode}):\n{result.stderr or result.stdout}")

        if not heightmap_path.exists():
            raise RuntimeError(f"Heightmap not created: {heightmap_path}")
        if not metadata_path.exists():
            raise RuntimeError(f"Metadata not created: {metadata_path}")

        return TerrainResult(heightmap_path=heightmap_path, metadata_path=metadata_path)
