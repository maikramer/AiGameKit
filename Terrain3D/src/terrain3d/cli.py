from __future__ import annotations

import sys
import time
from pathlib import Path

from rich import box
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from aigamekit_shared.cli_helpers import (
    add_vramd_options,
    delegate_or_prepare,
    needed_mib_for_backend,
    prepare_gpu_exclusive,
)
from aigamekit_shared.quality import VALID_QUALITIES

from .cli_rich import RICH_CLICK, click  # noqa: F401 — rich-click before commands
from .export import export_ahgt, export_heightmap, export_metadata
from .generator import TerrainConfig, TerrainResult, generate_terrain
from .vramd_payload import build_generate_request

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
    "--num-inference-steps",
    type=int,
    default=20,
    show_default=True,
    help="Coarse diffusion timesteps (mais = geografia mais refinada)",
)
@click.option(
    "--offset-i",
    type=int,
    default=0,
    show_default=True,
    help="Deslocamento da região amostrada (linhas, px) — explora o mundo infinito com o mesmo seed",
)
@click.option(
    "--offset-j",
    type=int,
    default=0,
    show_default=True,
    help="Deslocamento da região amostrada (colunas, px)",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["png", "ahgt"]),
    default="png",
    show_default=True,
    help="Heightmap encoding: png (8-bit legacy) ou ahgt (uint16, sem terracing — VibeGame lê nativamente)",
)
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
    help="Legacy: nº de células coarse (~256 px cada p/ 30m). Sem --size, deriva size = coarse-window x 256.",
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
@add_vramd_options
def generate_cmd(
    prompt: str | None,
    seed: int | None,
    output: str,
    metadata_path: str,
    size: int,
    world_size: float,
    max_height: float,
    num_inference_steps: int,
    offset_i: int,
    offset_j: int,
    output_format: str,
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
    vramd_priority: str | None,
    no_vramd: bool,
    vramd_stream: bool,
) -> None:
    """Generate an AI terrain heightmap via diffusion."""

    # QualityEngine: soft resolution — fills defaults when user didn't specify.
    from click.core import ParameterSource

    ctx = click.get_current_context()

    _user_set_size = ctx.get_parameter_source("size") != ParameterSource.DEFAULT
    _user_set_world_size = ctx.get_parameter_source("world_size") != ParameterSource.DEFAULT
    _user_set_coarse_window = ctx.get_parameter_source("coarse_window") != ParameterSource.DEFAULT
    _user_set_num_steps = ctx.get_parameter_source("num_inference_steps") != ParameterSource.DEFAULT
    _user_set_mode = ctx.get_parameter_source("mode") != ParameterSource.DEFAULT
    _user_set_island_falloff = ctx.get_parameter_source("island_falloff") != ParameterSource.DEFAULT
    _user_set_island_noise_scale = ctx.get_parameter_source("island_noise_scale") != ParameterSource.DEFAULT
    _user_set_island_noise_freq = ctx.get_parameter_source("island_noise_freq") != ParameterSource.DEFAULT
    _user_set_smooth_iterations = ctx.get_parameter_source("smooth_iterations") != ParameterSource.DEFAULT
    _user_set_elevation_gamma = ctx.get_parameter_source("elevation_gamma") != ParameterSource.DEFAULT
    _user_set_elevation_contrast = ctx.get_parameter_source("elevation_contrast") != ParameterSource.DEFAULT

    try:
        from aigamekit_shared.quality import QualityEngine

        _qengine = QualityEngine()
        _qresolved = _qengine.resolve("terrain3d", quality=quality)
        if not _user_set_size and "size" in _qresolved.params:
            size = _qresolved.params["size"]
        if not _user_set_world_size and "world_size" in _qresolved.params:
            world_size = _qresolved.params["world_size"]
        if not _user_set_coarse_window and "coarse_window" in _qresolved.params:
            coarse_window = _qresolved.params["coarse_window"]
        if not _user_set_num_steps and "num_inference_steps" in _qresolved.params:
            num_inference_steps = _qresolved.params["num_inference_steps"]
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

    # coarse_window legacy: sem --size explícito, deriva size = coarse_window x 256
    # (1 célula coarse ≈ 256 px de saída no modelo 30m ≈ 7.7 km).
    if _user_set_coarse_window and not _user_set_size:
        size = int(coarse_window) * 256

    if seed is None:
        import numpy as np

        seed = int(np.random.default_rng().integers(1, 999999))

    config = TerrainConfig(
        seed=seed,
        size=size,
        world_size=world_size,
        max_height=max_height,
        device=device,
        num_inference_steps=num_inference_steps,
        offset_i=offset_i,
        offset_j=offset_j,
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

    def _export(result: TerrainResult) -> tuple[Path, str | None]:
        """Exporta heightmap no formato pedido + metadata; devolve (path, metadata)."""
        if output_format == "ahgt":
            hmap = export_ahgt(result.heightmap, output, world_size, max_height)
        else:
            hmap = export_heightmap(result.heightmap, output, size)
        meta = export_metadata(result, metadata_path) if metadata_path else None
        return hmap, (str(meta) if meta else None)

    t_start = time.time()
    out_resolved = str(Path(output).resolve())
    if delegate_or_prepare(
        "terrain3d",
        payload=build_generate_request(
            output=out_resolved,
            # Absoluto como o heightmap: relativo caía no cwd do worker vramd
            # (herdado do supervisor) em vez da pasta do caller.
            metadata_path=str(Path(metadata_path).resolve()) if metadata_path else metadata_path,
            seed=seed,
            size=size,
            world_size=world_size,
            max_height=max_height,
            mode=mode,
            device=device,
            prompt=prompt,
            dtype=dtype if dtype != "fp32" else None,
            cache_size=cache_size,
            coarse_window=coarse_window,
            num_inference_steps=num_inference_steps,
            offset_i=offset_i,
            offset_j=offset_j,
            island_falloff=island_falloff,
            island_noise_scale=island_noise_scale,
            island_noise_freq=island_noise_freq,
            smooth_iterations=smooth_iterations,
            elevation_gamma=elevation_gamma,
            elevation_contrast=elevation_contrast,
            format=output_format,
        ),
        t_start=t_start,
        noun="Terreno",
        console=console,
        enabled=not no_vramd,
        priority=vramd_priority,
        stream=vramd_stream,
        timeout_sec=1800.0,
    ):
        if quiet:
            print(out_resolved)
            if metadata_path:
                print(metadata_path)
        return

    prepare_gpu_exclusive(
        needed_mib=needed_mib_for_backend("terrain3d"),
        allow_shared=True,
        kill_others=False,
        backend="terrain3d",
        console=console,
    )

    if quiet:
        result = generate_terrain(config)
        hmap_path, meta_path = _export(result)
        print(hmap_path)
        if meta_path:
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
        hmap_path, _ = _export(result)
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
    table.add_row("Steps", str(num_inference_steps))
    if offset_i or offset_j:
        table.add_row("Offset", f"{offset_i},{offset_j}")
    scale_ratio = stats.get("horizontal_scale_ratio")
    if scale_ratio is not None:
        table.add_row("Escala horiz.", f"{scale_ratio:.1f}x")
    table.add_row("Time", f"{gen_time:.2f}s")
    table.add_row("Height min", f"{result.heightmap.min():.4f}")
    table.add_row("Height max", f"{result.heightmap.max():.4f}")
    table.add_row("Height mean", f"{result.heightmap.mean():.4f}")
    table.add_row("Height std", f"{result.heightmap.std():.4f}")
    table.add_row("Mode", mode)
    table.add_row("Heightmap", str(hmap_path))
    if metadata_path:
        table.add_row("Metadata", str(metadata_path))
    if stats.get("scale_warning"):
        table.add_row("Aviso escala", stats["scale_warning"])
    if prompt:
        table.add_row("Prompt", prompt)
    console.print(table)


@cli.command("serve")
@click.option(
    "--ums-worker",
    is_flag=True,
    help=(
        "Modo worker subprocesso do vramd: lê comandos JSONL do stdin (load / "
        "generate / unload / shutdown) e emite eventos no stdout. Usado pelo "
        "SubprocessWorkerPool do ModelServer — terrain3d corre no seu próprio "
        "venv e o supervisor (ModelServer/.venv) coordena via JSONL."
    ),
)
def serve(ums_worker: bool) -> None:
    """Modo worker subprocesso do vramd (subprocess-per-backend).

    Sem ``--ums-worker`` não faz nada (futuro: modo server legacy).
    Com ``--ums-worker`` arranca o loop canónico
    :func:`aigamekit_shared.worker_serve.run_worker_loop` com o adapter terrain3d
    local (:mod:`terrain3d.worker_serve_adapter`).
    """
    from aigamekit_shared.worker_serve import run_ums_worker_cli
    from terrain3d.worker_serve_adapter import Adapter

    run_ums_worker_cli(Adapter, tool_name="terrain3d", ums_worker=ums_worker, console=console)


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
