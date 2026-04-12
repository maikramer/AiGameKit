from __future__ import annotations

import sys
from pathlib import Path

from rich import box
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .cli_rich import RICH_CLICK, click  # noqa: F401 — rich-click antes dos comandos
from .export import export_heightmap, export_metadata
from .pipeline import TerrainPipeline, run_pipeline

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="terraingen")
def cli() -> None:
    """TerrainGen — procedural terrain generation (diamond-square, erosion, rivers, lakes)."""


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
@click.option("--size", type=int, default=2048, show_default=True, help="Heightmap resolution (larger = more detail)")
@click.option(
    "--world-size",
    type=float,
    default=512.0,
    show_default=True,
    help="World extent in meters (X/Z); larger values add macro-scale variety",
)
@click.option("--max-height", type=float, default=50.0, show_default=True, help="Max terrain height in meters")
@click.option("--roughness", type=float, default=0.85, show_default=True, help="Diamond-square roughness")
@click.option(
    "--erosion-particles",
    type=int,
    default=80000,
    show_default=True,
    help="Number of erosion particles (more = finer detail, slower)",
)
@click.option(
    "--river-threshold",
    type=int,
    default=3200,
    show_default=True,
    help="Flow accumulation threshold for rivers (lower = more rivers; scale down if grid is small)",
)
@click.option("--no-erosion", is_flag=True, help="Skip erosion step")
@click.option("--no-rivers", is_flag=True, help="Skip river extraction")
@click.option("--no-lakes", is_flag=True, help="Skip lake generation")
@click.option(
    "--lake-min-area",
    type=int,
    default=20000,
    show_default=True,
    help="Minimum lake size in heightmap pixels (higher = fewer, larger lakes)",
)
@click.option(
    "--lake-max-count",
    type=int,
    default=3,
    show_default=True,
    help="Maximum lakes to export (0 = no cap; keeps largest by area; 2-3 typical for open worlds)",
)
@click.option(
    "--valley-depth",
    type=float,
    default=0.12,
    show_default=True,
    help="River valley depth in normalized height (0-1); deeper = more visible channels",
)
@click.option(
    "--valley-width",
    type=int,
    default=5,
    show_default=True,
    help="River valley half-width in heightmap pixels (total width ~ 2*W+1)",
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
    roughness: float,
    erosion_particles: int,
    river_threshold: int,
    no_erosion: bool,
    no_rivers: bool,
    no_lakes: bool,
    lake_min_area: int,
    lake_max_count: int,
    valley_depth: float,
    valley_width: int,
    quiet: bool,
) -> None:
    """Generate a procedural terrain heightmap with optional erosion, rivers, and lakes."""
    if seed is None:
        import numpy as np

        seed = int(np.random.default_rng().integers(1, 999999))

    config = TerrainPipeline(
        size=size,
        roughness=roughness,
        seed=seed,
        erosion_particles=erosion_particles,
        river_threshold=float(river_threshold),
        lake_min_area=lake_min_area,
        lake_max_count=lake_max_count,
        valley_depth=valley_depth,
        valley_width=valley_width,
        skip_erosion=no_erosion,
        skip_rivers=no_rivers,
        skip_lakes=no_lakes,
    )

    steps = [
        "Generating heightmap",
        "Smoothing",
    ]
    if not no_erosion:
        steps.append("Erosion")
    if not no_rivers:
        steps.append("Rivers")
    if not no_lakes:
        steps.append("Lakes")
    steps.append("Exporting")

    if quiet:
        result = run_pipeline(config)
        hmap_path = export_heightmap(result.heightmap, output, size)
        meta_path = export_metadata(result, metadata_path, world_size, max_height)
        if prompt:
            _inject_prompt(meta_path, prompt)
        print(hmap_path)
        print(meta_path)
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Generating heightmap...", total=None)
        result = run_pipeline(config)
        progress.update(task, description="[cyan]Exporting heightmap...")
        hmap_path = export_heightmap(result.heightmap, output, size)
        meta_path = export_metadata(result, metadata_path, world_size, max_height)
        if prompt:
            _inject_prompt(meta_path, prompt)
        progress.update(task, description="[green]Done")

    stats = result.stats
    total_time = stats.get("total", {}).get("time", 0.0)
    h_stats = stats.get("heightmap_stats", {})

    table = Table(title="Terrain Generation Summary", box=box.ROUNDED)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")
    table.add_row("Seed", str(seed))
    table.add_row("Size", f"{size}x{size}")
    table.add_row("World size", f"{world_size}m")
    table.add_row("Time", f"{total_time:.2f}s")
    table.add_row("Height min", f"{h_stats.get('min', 0):.4f}")
    table.add_row("Height max", f"{h_stats.get('max', 0):.4f}")
    table.add_row("Rivers", str(stats.get("river_count", 0)))
    table.add_row("Lakes", str(stats.get("lake_count", 0)))
    table.add_row("Heightmap", str(hmap_path))
    table.add_row("Metadata", str(meta_path))
    if prompt:
        table.add_row("Prompt", prompt)
    console.print(table)


def _inject_prompt(meta_path: Path, prompt_text: str) -> None:
    """Add prompt string to the exported metadata JSON."""
    import json

    meta_path = Path(meta_path)
    with open(meta_path, encoding="utf-8") as f:
        data = json.load(f)
    data["prompt"] = prompt_text
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
