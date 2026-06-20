"""Rocks3D CLI — procedural 3D rock generation."""

from __future__ import annotations

import sys

from .cli_rich import click
from .defaults import available_types
from .formation import STYLES

__all__ = ["main"]

_ROCK_TYPES = available_types()


@click.group()
@click.version_option(package_name="rocks3d")
def main() -> None:
    """Rocks3D — procedural 3D rock generation."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)


@main.command()
@click.argument("type_name", type=click.Choice(_ROCK_TYPES))
@click.option("-o", "--output", type=click.Path(), default=None, help="Output GLB path")
@click.option("--seed", type=int, default=None, show_default=True, help="Random seed")
@click.option(
    "--quality",
    type=click.Choice(["fast", "low", "medium", "high", "highest"]),
    default="medium",
    show_default=True,
    help="Quality tier.",
)
@click.option("--category", type=str, default=None, help="Asset category for overrides")
@click.option("--scale", type=float, default=1.0, help="Scale factor")
@click.option("--erosion/--no-erosion", default=True, show_default=True, help="Erosion toggle")
@click.option(
    "--bake/--no-bake",
    default=None,
    help="Seamless bpy bake (default: auto — bpy if available, else trimesh).",
)
@click.pass_context
def generate(
    ctx: click.Context,
    type_name: str,
    output: str | None,
    seed: int | None,
    quality: str,
    category: str | None,
    scale: float,
    erosion: bool,
    bake: bool | None,
) -> None:
    """Generate a procedural 3D rock (pebble or boulder)."""
    import time
    from pathlib import Path

    from rocks3d.build import build_rock_glb

    start = time.time()

    if output is None:
        output = f"{type_name}_rock.glb"
    output_path = Path(output)

    summary = build_rock_glb(
        type_name,
        output_path,
        seed=seed,
        quality=quality,
        scale=scale,
        erosion=erosion,
        use_bpy=bake,
    )

    elapsed = time.time() - start
    click.echo(f"Generated {type_name} rock: {summary['vertices']} vertices, {summary['faces']} faces")
    click.echo(f"Output: {output_path}")
    click.echo(f"Backend: {summary['backend']} | maps: {', '.join(summary['textures'])}")
    click.echo(f"Time: {elapsed:.2f}s")


@main.command()
@click.argument("type_name", type=click.Choice(["pebble", "boulder", "both"]))
@click.option("-n", "--count", type=int, default=5, show_default=True, help="Rocks per type")
@click.option("-o", "--output-dir", type=click.Path(), default="rocks", show_default=True, help="Output directory")
@click.option("--seed", type=int, default=0, show_default=True, help="Starting seed (incremented per rock)")
@click.option(
    "--quality",
    type=click.Choice(["fast", "low", "medium", "high", "highest"]),
    default="medium",
    show_default=True,
    help="Quality tier.",
)
@click.option("--scale", type=float, default=1.0, help="Scale factor")
@click.option("--erosion/--no-erosion", default=True, show_default=True, help="Erosion toggle")
@click.option(
    "--bake/--no-bake",
    default=None,
    help="Seamless bpy bake (default: auto — bpy if available, else trimesh).",
)
def batch(
    type_name: str,
    count: int,
    output_dir: str,
    seed: int,
    quality: str,
    scale: float,
    erosion: bool,
    bake: bool | None,
) -> None:
    """Batch generate rocks with sequential seeds.

    Writes ``<output_dir>/<type>_<seed>.glb`` for each rock. Use ``both`` to
    generate *count* pebbles and *count* boulders.
    """
    import time
    from pathlib import Path

    from rocks3d.build import build_rock_glb

    types = ["pebble", "boulder"] if type_name == "both" else [type_name]
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    start = time.time()
    total = 0
    for t in types:
        for i in range(count):
            s = seed + i
            output_path = out_dir / f"{t}_{s}.glb"
            summary = build_rock_glb(
                t, output_path, seed=s, quality=quality, scale=scale, erosion=erosion, use_bpy=bake
            )
            total += 1
            click.echo(f"  [{total}] {output_path} — {summary['vertices']} verts")

    elapsed = time.time() - start
    click.echo(f"Generated {total} rocks in {out_dir} ({elapsed:.2f}s)")


@main.command()
@click.argument("style", type=click.Choice(list(STYLES)))
@click.option("-o", "--output", type=click.Path(), default=None, help="Output GLB path (file when -n 1, else a directory)")
@click.option("--seed", type=int, default=None, show_default=True, help="Random seed (incremented per item when -n > 1)")
@click.option("-n", "--count", type=int, default=1, show_default=True, help="How many formations to generate")
@click.option("--chunks", type=int, default=None, help="Override chunk count for the style")
@click.option(
    "--quality",
    type=click.Choice(["fast", "low", "medium", "high", "highest"]),
    default="medium",
    show_default=True,
    help="Quality tier (chunk subdivision).",
)
@click.option("--scale", type=float, default=1.0, help="Scale factor")
@click.option(
    "--bake/--no-bake",
    default=None,
    help="Seamless bpy bake (default: auto — bpy if available, else trimesh).",
)
def formation(
    style: str,
    output: str | None,
    seed: int | None,
    count: int,
    chunks: int | None,
    quality: str,
    scale: float,
    bake: bool | None,
) -> None:
    """Generate a scenery rock *formation* (rochedo) by unioning several chunks.

    Styles produce the concave geometry heightmap terrain cannot — overhangs,
    arches, crevices and balanced stacks::

        rocks3d formation arch --seed 7 -o arch.glb
        rocks3d formation outcrop -n 6 -o formations/ --quality high
    """
    import time
    from pathlib import Path

    from rocks3d.build import build_formation_glb

    start = time.time()
    base_seed = 0 if (seed is None and count > 1) else seed

    if count <= 1:
        out_path = Path(output) if output else Path(f"{style}_formation.glb")
        summary = build_formation_glb(
            style, out_path, seed=base_seed, quality=quality, scale=scale, chunks=chunks, use_bpy=bake
        )
        click.echo(f"Generated {style} formation: {summary['vertices']} vertices, {summary['faces']} faces")
        click.echo(f"Output: {out_path}")
        click.echo(f"Backend: {summary['backend']} | maps: {', '.join(summary['textures'])}")
        click.echo(f"Time: {time.time() - start:.2f}s")
        return

    out_dir = Path(output) if output else Path("formations")
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        s = (base_seed or 0) + i
        out_path = out_dir / f"{style}_{s}.glb"
        summary = build_formation_glb(
            style, out_path, seed=s, quality=quality, scale=scale, chunks=chunks, use_bpy=bake
        )
        click.echo(f"  [{i + 1}/{count}] {out_path} — {summary['vertices']} verts")
    click.echo(f"Generated {count} {style} formations in {out_dir} ({time.time() - start:.2f}s)")


if __name__ == "__main__":
    main()
