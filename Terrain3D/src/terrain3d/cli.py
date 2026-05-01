from __future__ import annotations

import sys

from gamedev_shared.quality import VALID_QUALITIES

from rich import box
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .cli_rich import RICH_CLICK, click  # noqa: F401 — rich-click before commands
from .export import export_heightmap, export_metadata
from .generator import TerrainConfig, generate_terrain

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="terrain3d")
def cli() -> None:
    """Terrain3D — AI terrain generation via diffusion models."""


@cli.command("generate")
@click.option("--prompt", type=str, default=None, help="Terrain description (stored as metadata)")
@click.option("--seed", type=int, default=None, help="Random seed (default: random)")
@click.option(
    "--output", type=click.Path(), default="heightmap.png", show_default=True, help="Heightmap PNG output path"
)
@click.option(
    "--metadata",
    "metadata_path",
    type=click.Path(),
    default="terrain.json",
    show_default=True,
    help="JSON metadata output path",
)
@click.option("--size", type=int, default=2048, show_default=True, help="Heightmap resolution (px)")
@click.option(
    "--world-size",
    type=float,
    default=512.0,
    show_default=True,
    help="World extent in meters (X/Z)",
)
@click.option("--max-height", type=float, default=50.0, show_default=True, help="Max terrain height in meters")
@click.option(
    "--quality",
    type=click.Choice(list(VALID_QUALITIES)),
    default="medium",
    show_default=True,
    help="Quality tier — controls size, world-size, coarse-window via QualityEngine.",
)
@click.option("--device", default=None, help="Device (cuda/cpu, auto-detect by default)")
@click.option(
    "--dtype",
    type=click.Choice(["fp32", "bf16", "fp16"]),
    default="fp32",
    show_default=True,
    help="Model precision",
)
@click.option("--cache-size", default="100M", show_default=True, help="Cache size (e.g. 100M, 1G)")
@click.option(
    "--coarse-window",
    type=int,
    default=4,
    show_default=True,
    help="Number of coarse tiles (~7.7km each for 30m)",
)
@click.option(
    "--mode",
    type=click.Choice(["island", "continental"]),
    default="island",
    show_default=True,
    help="Terrain mode: island (falloff) or continental (raw)",
)
@click.option(
    "--island-falloff",
    type=float,
    default=0.35,
    show_default=True,
    help="Island falloff radius (0.1-0.5)",
)
@click.option(
    "--island-noise-scale",
    type=float,
    default=0.15,
    show_default=True,
    help="Perlin noise amplitude for coast variation",
)
@click.option(
    "--island-noise-freq",
    type=float,
    default=3.0,
    show_default=True,
    help="Perlin noise frequency for coast variation",
)
@click.option(
    "--smooth-iterations",
    type=int,
    default=3,
    show_default=True,
    help="Taubin smoothing iterations (0=off)",
)
@click.option(
    "--elevation-gamma",
    type=float,
    default=1.2,
    show_default=True,
    help="Gamma exponent for elevation (1.0=neutral)",
)
@click.option(
    "--elevation-contrast",
    type=float,
    default=0.1,
    show_default=True,
    help="Sigmoid contrast for elevation (0=off)",
)
@click.option("--quiet", is_flag=True, help="Suppress progress output")
def generate_cmd(
    prompt: str | None,
    seed: int | None,
    output: str,
    metadata_path: str,
    size: int,
    world_size: float,
    max_height: float,
    quality: str,
    device: str | None,
    dtype: str,
    cache_size: str,
    coarse_window: int,
    mode: str,
    island_falloff: float,
    island_noise_scale: float,
    island_noise_freq: float,
    smooth_iterations: int,
    elevation_gamma: float,
    elevation_contrast: float,
    quiet: bool,
) -> None:
    """Generate an AI terrain heightmap via diffusion."""

    # QualityEngine: soft resolution — fills defaults when user didn't specify.
    from click.core import ParameterSource

    ctx = click.get_current_context()

    _user_set_size = ctx.get_parameter_source("size") != ParameterSource.DEFAULT
    _user_set_world_size = ctx.get_parameter_source("world_size") != ParameterSource.DEFAULT
    _user_set_coarse_window = ctx.get_parameter_source("coarse_window") != ParameterSource.DEFAULT
    _user_set_mode = ctx.get_parameter_source("mode") != ParameterSource.DEFAULT
    _user_set_island_falloff = ctx.get_parameter_source("island_falloff") != ParameterSource.DEFAULT
    _user_set_island_noise_scale = ctx.get_parameter_source("island_noise_scale") != ParameterSource.DEFAULT
    _user_set_island_noise_freq = ctx.get_parameter_source("island_noise_freq") != ParameterSource.DEFAULT
    _user_set_smooth_iterations = ctx.get_parameter_source("smooth_iterations") != ParameterSource.DEFAULT
    _user_set_elevation_gamma = ctx.get_parameter_source("elevation_gamma") != ParameterSource.DEFAULT
    _user_set_elevation_contrast = ctx.get_parameter_source("elevation_contrast") != ParameterSource.DEFAULT

    try:
        from gamedev_shared.quality import QualityEngine

        _qengine = QualityEngine()
        _qresolved = _qengine.resolve("terrain3d", quality=quality)
        if not _user_set_size and "size" in _qresolved.params:
            size = _qresolved.params["size"]
        if not _user_set_world_size and "world_size" in _qresolved.params:
            world_size = _qresolved.params["world_size"]
        if not _user_set_coarse_window and "coarse_window" in _qresolved.params:
            coarse_window = _qresolved.params["coarse_window"]
        if not _user_set_mode and "mode" in _qresolved.params:
            mode = _qresolved.params["mode"]
        if not _user_set_island_falloff and "island_falloff" in _qresolved.params:
            island_falloff = _qresolved.params["island_falloff"]
        if not _user_set_island_noise_scale and "island_noise_scale" in _qresolved.params:
            island_noise_scale = _qresolved.params["island_noise_scale"]
        if not _user_set_island_noise_freq and "island_noise_freq" in _qresolved.params:
            island_noise_freq = _qresolved.params["island_noise_freq"]
        if not _user_set_smooth_iterations and "smooth_iterations" in _qresolved.params:
            smooth_iterations = _qresolved.params["smooth_iterations"]
        if not _user_set_elevation_gamma and "elevation_gamma" in _qresolved.params:
            elevation_gamma = _qresolved.params["elevation_gamma"]
        if not _user_set_elevation_contrast and "elevation_contrast" in _qresolved.params:
            elevation_contrast = _qresolved.params["elevation_contrast"]
    except Exception:
        pass  # QualityEngine unavailable — continue with CLI defaults

    if seed is None:
        import numpy as np

        seed = int(np.random.default_rng().integers(1, 999999))

    config = TerrainConfig(
        seed=seed,
        size=size,
        world_size=world_size,
        max_height=max_height,
        device=device,
        dtype=dtype if dtype != "fp32" else None,
        cache_size=cache_size,
        coarse_window=coarse_window,
        prompt=prompt,
        mode=mode,
        island_falloff=island_falloff,
        island_noise_scale=island_noise_scale,
        island_noise_freq=island_noise_freq,
        smooth_iterations=smooth_iterations,
        elevation_gamma=elevation_gamma,
        elevation_contrast=elevation_contrast,
    )

    if quiet:
        result = generate_terrain(config)
        hmap_path = export_heightmap(result.heightmap, output, size)
        meta_path = export_metadata(result, metadata_path)
        print(hmap_path)
        print(meta_path)
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Generating terrain...", total=None)
        result = generate_terrain(config)
        progress.update(task, description="[cyan]Exporting heightmap...")
        hmap_path = export_heightmap(result.heightmap, output, size)
        meta_path = export_metadata(result, metadata_path)
        progress.update(task, description="[green]Done")

    stats = result.stats
    gen_time = stats.get("generation_time_seconds", 0.0)

    table = Table(title="Terrain Generation Summary", box=box.ROUNDED)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")
    table.add_row("Seed", str(seed))
    table.add_row("Model", stats.get("model_id", "unknown"))
    table.add_row("Size", f"{size}x{size}")
    table.add_row("World size", f"{world_size}m")
    table.add_row("Time", f"{gen_time:.2f}s")
    table.add_row("Height min", f"{result.heightmap.min():.4f}")
    table.add_row("Height max", f"{result.heightmap.max():.4f}")
    table.add_row("Height mean", f"{result.heightmap.mean():.4f}")
    table.add_row("Height std", f"{result.heightmap.std():.4f}")
    table.add_row("Mode", mode)
    table.add_row("Heightmap", str(hmap_path))
    table.add_row("Metadata", str(meta_path))
    if prompt:
        table.add_row("Prompt", prompt)
    console.print(table)


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
