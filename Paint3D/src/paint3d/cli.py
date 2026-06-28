#!/usr/bin/env python3
"""
Paint3D — Texturização 3D com Hunyuan3D-Paint 2.1 (PBR no GLB).

Otimizado para GPUs com 6GB VRAM (RTX 4050 Laptop).
SDNQ uint8 + VAE tiling aplicados automaticamente.

Uso::

    paint3d texture mesh.glb -i ref.png
    paint3d texture mesh.glb -i ref.png -o saida.glb --upscale
    paint3d doctor
    paint3d info
"""

import atexit
import contextlib
import json
import os
import signal
import sys
import time
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from gamedev_shared.gpu import (
    clear_cuda_memory,
    enforce_exclusive_gpu,
    format_bytes,
    kill_gpu_compute_processes_aggressive,
)
from gamedev_shared.hf import hf_home_display_rich
from gamedev_shared.progress import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SKIPPED,
    TOOL_PAINT3D,
    emit_progress,
    emit_result,
)
from gamedev_shared.quality import VALID_QUALITIES

from . import defaults as _defaults
from .cli_rich import click

console = Console()


def _env_bool(env_var: str, cli_wants: bool) -> bool:
    v = os.environ.get(env_var, "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return cli_wants


def _enable_sage_attention(requested: bool) -> bool:
    """Liga SageAttention (attention INT8, Ampere+) via PAINT3D_USE_SAGEATTN.

    Tem de correr antes do primeiro import dos módulos hy3dpaint (o binding
    acontece no import). Devolve o estado efectivo.
    """
    if not requested:
        return False
    try:
        import sageattention  # noqa: F401
    except ImportError:
        console.print(
            "[yellow]sageattention não instalado — a continuar com SDPA (pip install sageattention).[/yellow]"
        )
        return False
    os.environ["PAINT3D_USE_SAGEATTN"] = "1"
    return True


def _prepare_gpu(allow_shared: bool, kill_others: bool, low_vram: bool = False) -> None:
    from gamedev_shared.gpu import warn_if_vram_occupied

    kill = _env_bool("PAINT3D_GPU_KILL_OTHERS", kill_others)
    allow = allow_shared or _env_bool("PAINT3D_ALLOW_SHARED_GPU", False)
    if kill:
        console.print(
            Panel(
                "[bold]Terminar processos GPU alvo[/bold]\n"
                "[dim]Desliga com --no-gpu-kill-others ou PAINT3D_GPU_KILL_OTHERS=0[/dim]",
                border_style="yellow",
            )
        )
        for line in kill_gpu_compute_processes_aggressive(exclude_pid=os.getpid()):
            console.print(f"[dim]{line}[/dim]")
        clear_cuda_memory()
        time.sleep(0.5)
    try:
        enforce_exclusive_gpu(allow_shared=allow)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    warn_if_vram_occupied()


@click.group()
@click.version_option(version="0.1.0", prog_name="paint3d")
@click.option("--verbose", "-v", is_flag=True, help="Logs detalhados")
@click.pass_context
def cli(ctx, verbose):
    """Paint3D — texturização 3D com Hunyuan3D-Paint 2.1 (PBR).

    \b
        paint3d texture mesh.glb -i ref.png
        paint3d texture mesh.glb -i ref.png -o saida.glb --upscale
        paint3d doctor
    """
    ctx.ensure_object(dict)
    ctx.obj["VERBOSE"] = verbose


@cli.command()
@click.argument("mesh_file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--image",
    "-i",
    "image_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Imagem de referência (alinhada com a mesh).",
)
@click.option("--output", "-o", type=click.Path(), help="Ficheiro GLB de saída (textura embutida).")
@click.option(
    "--upscale/--no-upscale",
    default=_defaults.DEFAULT_UPSCALE,
    show_default=True,
    help="Upscale IA da textura (Real-ESRGAN). Requer: pip install spandrel.",
)
@click.option(
    "--upscale-factor",
    default=_defaults.DEFAULT_UPSCALE_FACTOR,
    show_default=True,
    type=click.Choice(["2", "4"], case_sensitive=False),
    help="Factor de upscale (2 = 1024→2048, 4 = 1024→4096).",
)
@click.option("--max-views", default=None, show_default=False, type=int)
@click.option("--view-resolution", default=None, show_default=False, type=int)
@click.option(
    "--render-size",
    default=None,
    type=int,
    help="Resolução de rasterização para back-projection (padrão: 2048). Maior = mais detalhe no bake.",
)
@click.option(
    "--texture-size",
    default=None,
    type=int,
    help="Resolução do atlas UV (padrão: 4096). Maior = textura final mais nítida.",
)
@click.option(
    "--bake-exp",
    default=_defaults.DEFAULT_PAINT_BAKE_EXP,
    show_default=True,
    type=int,
    help="Expoente de blending entre vistas (upstream=4: suave, 6: nítido, menos sangramento).",
)
@click.option(
    "--smooth/--no-smooth",
    default=_defaults.DEFAULT_SMOOTH,
    show_default=True,
    help="Suavizar textura com filtro bilateral (remove artefatos de costura sem alterar resolução).",
)
@click.option(
    "--smooth-passes",
    default=_defaults.DEFAULT_SMOOTH_PASSES,
    show_default=True,
    type=int,
    help="Número de passadas do filtro bilateral (mais = mais suave).",
)
@click.option("-v", "--verbose", "texture_verbose", is_flag=True, help="Logs detalhados.")
@click.option(
    "--low-vram-mode",
    is_flag=True,
    help=(
        "Modo baixa VRAM: SDNQ uint8 + CFG chunking + ref-UNet offload "
        "(com hw-auto: 6v@512, render 1536, tex 3072; sem: 4v@384)."
    ),
)
@click.option(
    "--preserve-origin/--no-preserve-origin",
    default=True,
    show_default=True,
    help="Reposicionar saída: base do AABB em Y=0 e XZ centrado (convenção Text3D / pés).",
)
@click.option("--allow-shared-gpu", is_flag=True, help="Permite GPU com outros processos.")
@click.option(
    "--gpu-kill-others/--no-gpu-kill-others",
    default=False,
    help="DEPRECATED: terminates competing GPU processes; will be removed in a future version. Default: off.",
)
@click.option("--profile", is_flag=True, help="Medir tempos e VRAM.")
@click.option(
    "--gpu-ids",
    default=None,
    show_default=False,
    help="IDs de GPU para multi-GPU, separados por vírgula (ex: 0,1).",
)
@click.option(
    "--quality",
    type=click.Choice(list(VALID_QUALITIES)),
    default="medium",
    show_default=True,
    help="Quality tier (fast / low / medium / high / highest).",
)
@click.option(
    "--category",
    type=str,
    default=None,
    help="Asset category for automatic tuning (e.g., humanoid, weapon, prop).",
)
@click.option(
    "--hw-auto/--no-hw-auto",
    "hw_auto",
    default=True,
    show_default=True,
    help=(
        "Auto-detecção de hardware: liga low-VRAM (SDNQ uint8, 6v@512) em GPUs "
        "<10GB; FP16 nas grandes/multi-GPU. Flags explícitas ganham. "
        "Env: PAINT3D_HW_AUTO=0."
    ),
)
@click.option(
    "--sage-attn",
    "sage_attention",
    is_flag=True,
    default=False,
    help="SageAttention (attention INT8, Ampere+; requer pacote sageattention).",
)
@click.pass_context
def texture(
    ctx,
    mesh_file,
    image_file,
    output,
    upscale,
    upscale_factor,
    max_views,
    view_resolution,
    render_size,
    texture_size,
    bake_exp,
    smooth,
    smooth_passes,
    texture_verbose,
    low_vram_mode,
    preserve_origin,
    allow_shared_gpu,
    gpu_kill_others,
    profile,
    gpu_ids,
    quality,
    category,
    hw_auto,
    sage_attention,
):
    """Texturizar mesh com Hunyuan3D-Paint 2.1 → GLB com PBR."""
    from .painter import paint_file_to_file

    verbose = bool(ctx.obj.get("VERBOSE")) or texture_verbose
    sage_attention = _enable_sage_attention(sage_attention)

    # QualityEngine: soft resolution — fills defaults when user didn't specify.
    _src = click.core.ParameterSource
    _user_set_views = ctx.get_parameter_source("max_views") not in (_src.DEFAULT,)
    _user_set_view_res = ctx.get_parameter_source("view_resolution") not in (_src.DEFAULT,)
    _user_set_render_size = ctx.get_parameter_source("render_size") not in (_src.DEFAULT,)
    _user_set_tex_size = ctx.get_parameter_source("texture_size") not in (_src.DEFAULT,)
    _user_set_bake_exp = ctx.get_parameter_source("bake_exp") not in (_src.DEFAULT,)
    _user_set_smooth = ctx.get_parameter_source("smooth") not in (_src.DEFAULT,)
    _user_set_smooth_passes = ctx.get_parameter_source("smooth_passes") not in (_src.DEFAULT,)

    from gamedev_shared.quality import QualityEngine

    _qengine = QualityEngine()
    _qresolved = _qengine.resolve("paint3d", quality=quality, category=category)
    if not _user_set_views and "max_views" in _qresolved.params:
        max_views = _qresolved.params["max_views"]
    if not _user_set_view_res and "view_resolution" in _qresolved.params:
        view_resolution = _qresolved.params["view_resolution"]
    if not _user_set_render_size and "render_size" in _qresolved.params:
        render_size = _qresolved.params["render_size"]
    if not _user_set_tex_size and "texture_size" in _qresolved.params:
        texture_size = _qresolved.params["texture_size"]
    if not _user_set_bake_exp and "bake_exp" in _qresolved.params:
        bake_exp = _qresolved.params["bake_exp"]
    if not _user_set_smooth and "smooth" in _qresolved.params:
        smooth = _qresolved.params["smooth"]
    if not _user_set_smooth_passes and "smooth_passes" in _qresolved.params:
        smooth_passes = _qresolved.params["smooth_passes"]

    # Hardware auto-detection (soft): --low-vram-mode explícito ganha sempre.
    from .hardware import detect_hardware_profile, hw_auto_enabled

    hwp = None
    if hw_auto and hw_auto_enabled():
        hwp = detect_hardware_profile()
        if not low_vram_mode and hwp.low_vram and hwp.device == "cuda":
            low_vram_mode = True
        # Apply hw-auto overrides (soft — only when user didn't explicitly set)
        if hwp is not None:
            if not _user_set_views and hwp.max_views is not None:
                max_views = hwp.max_views
            if not _user_set_view_res and hwp.view_resolution is not None:
                view_resolution = hwp.view_resolution
            if not _user_set_render_size and hwp.render_size is not None:
                render_size = hwp.render_size
            if not _user_set_tex_size and hwp.texture_size is not None:
                texture_size = hwp.texture_size

    mesh_path = Path(mesh_file)
    if output is None:
        output = mesh_path.with_name(f"{mesh_path.stem}_textured.glb")

    os.environ.setdefault(
        "PYTORCH_CUDA_ALLOC_CONF",
        "expandable_segments:True,max_split_size_mb:64,garbage_collection_threshold:0.6",
    )
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

    # Resolve defaults BEFORE building the config panel (avoids "None vistas @ Nonepx")
    if max_views is None:
        max_views = _defaults.LOW_VRAM_MAX_VIEWS if low_vram_mode else _defaults.DEFAULT_PAINT_MAX_VIEWS
    if view_resolution is None:
        view_resolution = (
            _defaults.LOW_VRAM_VIEW_RESOLUTION if low_vram_mode else _defaults.DEFAULT_PAINT_VIEW_RESOLUTION
        )

    rs_label = render_size or ("1024 (low-vram)" if low_vram_mode else "2048")
    ts_label = texture_size or ("2048 (low-vram)" if low_vram_mode else "4096")

    info_table = Table(show_header=False, box=box.SIMPLE)
    info_table.add_row("[bold]Mesh[/bold]", f"[cyan]{mesh_path}[/cyan]")
    info_table.add_row("[bold]Imagem[/bold]", f"[cyan]{image_file}[/cyan]")
    info_table.add_row("[bold]Saída[/bold]", f"[cyan]{output}[/cyan]")
    quant_label = "SDNQ uint8 (low-vram)" if low_vram_mode else "FP16 (sem quantização)"
    attn_label = " · sage-attn" if sage_attention else ""
    info_table.add_row(
        "[bold]Config[/bold]",
        f"{max_views} vistas @ {view_resolution}px · {quant_label} · VAE tiling{attn_label}",
    )
    info_table.add_row(
        "[bold]Bake[/bold]",
        f"render={rs_label} · texture={ts_label} · bake_exp={bake_exp}",
    )
    if smooth:
        info_table.add_row("[bold]Smooth[/bold]", f"bilateral x {smooth_passes} passes")
    if upscale:
        info_table.add_row("[bold]Upscale[/bold]", f"Real-ESRGAN {upscale_factor}x")
    if hwp is not None:
        info_table.add_row("[bold]Hardware (auto)[/bold]", hwp.summary())
    console.print(Panel(info_table, title="[bold green]Hunyuan3D-Paint 2.1", border_style="green"))

    _prepare_gpu(allow_shared_gpu, gpu_kill_others, low_vram=low_vram_mode)

    parsed_gpu_ids = None
    if gpu_ids is not None:
        try:
            parsed_gpu_ids = [int(x.strip()) for x in gpu_ids.split(",") if x.strip()]
        except ValueError as exc:
            raise click.ClickException(f"--gpu-ids inválido: '{gpu_ids}'. Esperado: 0,1") from exc

    from gamedev_shared.profiler import ProfilerSession
    from gamedev_shared.profiler.env import env_profile_log_path

    log_p = env_profile_log_path()
    prof_log = Path(log_p) if log_p else None

    try:
        start = time.time()
        item_id = mesh_path.stem
        emit_progress(item_id, TOOL_PAINT3D, phase="loading_model", percent=0)
        with ProfilerSession("paint3d", log_path=prof_log, cli_profile=profile) as prof:  # noqa: F841
            with console.status("[bold yellow]A carregar modelos (1ª vez: download HF)...", spinner="dots"):
                out = paint_file_to_file(
                    mesh_path,
                    image_file,
                    output,
                    max_num_view=max_views,
                    view_resolution=view_resolution,
                    render_size=render_size,
                    texture_size=texture_size,
                    bake_exp=bake_exp,
                    verbose=verbose,
                    preserve_origin=preserve_origin,
                    low_vram=low_vram_mode,
                    gpu_ids=parsed_gpu_ids,
                )
            emit_progress(item_id, TOOL_PAINT3D, phase="multiview_render", percent=100)
            emit_progress(item_id, TOOL_PAINT3D, phase="bake", percent=100)
            emit_progress(item_id, TOOL_PAINT3D, phase="export", percent=100)

            if smooth:
                from .texture_smooth import smooth_trimesh_texture
                from .utils.mesh_io import load_mesh_trimesh, save_glb

                with console.status(
                    "[bold yellow]Suavizando textura (filtro bilateral)...",
                    spinner="dots",
                ):
                    mesh = load_mesh_trimesh(out)
                    mesh = smooth_trimesh_texture(
                        mesh,
                        passes=smooth_passes,
                        diameter=_defaults.DEFAULT_SMOOTH_DIAMETER,
                        sigma_color=_defaults.DEFAULT_SMOOTH_SIGMA_COLOR,
                        sigma_space=_defaults.DEFAULT_SMOOTH_SIGMA_SPACE,
                        verbose=verbose,
                    )
                    save_glb(mesh, out)

            if upscale:
                from .texture_upscale import upscale_trimesh_texture
                from .utils.mesh_io import load_mesh_trimesh, save_glb

                clear_cuda_memory()
                with console.status(
                    f"[bold yellow]Upscale textura (Real-ESRGAN {upscale_factor}x)...",
                    spinner="dots",
                ):
                    mesh = load_mesh_trimesh(out)
                    mesh = upscale_trimesh_texture(mesh, scale=int(upscale_factor), verbose=verbose)
                    save_glb(mesh, out)

        out_p = Path(out).resolve()
        elapsed = time.time() - start
        try:
            sz = format_bytes(out_p.stat().st_size)
        except OSError:
            sz = "?"
        emit_result(item_id, TOOL_PAINT3D, STATUS_OK, output=str(out_p), seconds=round(elapsed, 2))
        console.print(Rule("[bold green]Resultado", style="green"))
        console.print(f"[bold green]✓[/bold green] GLB texturizado: [cyan]{out_p}[/cyan] [dim]({sz})[/dim]")
        console.print(f"\n[dim]Tempo: {elapsed:.1f}s[/dim]")
    except Exception as e:
        elapsed = time.time() - start
        emit_result(item_id, TOOL_PAINT3D, STATUS_ERROR, error=str(e), seconds=round(elapsed, 2))
        console.print(f"\n[bold red]✗ Erro:[/bold red] {e!s}")
        if verbose:
            console.print_exception()
        sys.exit(1)


@cli.command("texture-batch")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.option("--output-dir", "-O", type=click.Path(), default=".", help="Diretório base para outputs.")
@click.option("--max-views", default=None, type=int)
@click.option("--view-resolution", default=None, type=int)
@click.option("--render-size", default=None, type=int)
@click.option("--texture-size", default=None, type=int)
@click.option("--bake-exp", default=_defaults.DEFAULT_PAINT_BAKE_EXP, type=int)
@click.option("--smooth/--no-smooth", default=_defaults.DEFAULT_SMOOTH)
@click.option("--smooth-passes", default=_defaults.DEFAULT_SMOOTH_PASSES, type=int)
@click.option("--low-vram-mode", is_flag=True)
@click.option("--preserve-origin/--no-preserve-origin", default=True)
@click.option("--allow-shared-gpu", is_flag=True)
@click.option("--gpu-kill-others/--no-gpu-kill-others", default=False)
@click.option("--force", is_flag=True, help="Regenerar mesmo se o output já existe.")
@click.option("--gpu-ids", default=None)
@click.option(
    "--hw-auto/--no-hw-auto",
    "hw_auto",
    default=True,
    show_default=True,
    help="Auto-detecção de hardware (low-VRAM em GPUs <10GB). Env: PAINT3D_HW_AUTO=0.",
)
@click.option(
    "--sage-attn",
    "sage_attention",
    is_flag=True,
    default=False,
    help="SageAttention (attention INT8, Ampere+; requer pacote sageattention).",
)
@click.option(
    "--quality",
    type=click.Choice(list(VALID_QUALITIES)),
    default="medium",
    show_default=True,
    help="Quality tier (fast / low / medium / high / highest).",
)
@click.option(
    "--category",
    type=str,
    default=None,
    help="Asset category for automatic tuning (e.g., humanoid, weapon, prop).",
)
@click.option("-v", "--verbose", "batch_verbose", is_flag=True)
@click.pass_context
def texture_batch(
    ctx,
    manifest,
    output_dir,
    max_views,
    view_resolution,
    render_size,
    texture_size,
    bake_exp,
    smooth,
    smooth_passes,
    low_vram_mode,
    preserve_origin,
    allow_shared_gpu,
    gpu_kill_others,
    gpu_ids,
    force,
    hw_auto,
    sage_attention,
    quality,
    category,
    batch_verbose,
):
    """Texturizar batch via manifest JSON. Saída JSONL em stdout."""
    from .painter import PaintBatchProcessor, _fit_glb_aabb_to_reference
    from .texture_smooth import smooth_trimesh_texture
    from .utils.mesh_io import load_mesh_trimesh, save_glb

    verbose = bool(ctx.obj.get("VERBOSE")) or batch_verbose
    _enable_sage_attention(sage_attention)

    from .hardware import detect_hardware_profile, hw_auto_enabled

    _src = click.core.ParameterSource
    _user_set_views = ctx.get_parameter_source("max_views") not in (_src.DEFAULT,)
    _user_set_view_res = ctx.get_parameter_source("view_resolution") not in (_src.DEFAULT,)
    _user_set_render_size = ctx.get_parameter_source("render_size") not in (_src.DEFAULT,)
    _user_set_tex_size = ctx.get_parameter_source("texture_size") not in (_src.DEFAULT,)
    _user_set_bake_exp = ctx.get_parameter_source("bake_exp") not in (_src.DEFAULT,)
    _user_set_smooth = ctx.get_parameter_source("smooth") not in (_src.DEFAULT,)
    _user_set_smooth_passes = ctx.get_parameter_source("smooth_passes") not in (_src.DEFAULT,)

    from gamedev_shared.quality import QualityEngine

    _qengine = QualityEngine()
    _qresolved = _qengine.resolve("paint3d", quality=quality, category=category)
    if not _user_set_views and "max_views" in _qresolved.params:
        max_views = _qresolved.params["max_views"]
    if not _user_set_view_res and "view_resolution" in _qresolved.params:
        view_resolution = _qresolved.params["view_resolution"]
    if not _user_set_render_size and "render_size" in _qresolved.params:
        render_size = _qresolved.params["render_size"]
    if not _user_set_tex_size and "texture_size" in _qresolved.params:
        texture_size = _qresolved.params["texture_size"]
    if not _user_set_bake_exp and "bake_exp" in _qresolved.params:
        bake_exp = _qresolved.params["bake_exp"]
    if not _user_set_smooth and "smooth" in _qresolved.params:
        smooth = _qresolved.params["smooth"]
    if not _user_set_smooth_passes and "smooth_passes" in _qresolved.params:
        smooth_passes = _qresolved.params["smooth_passes"]

    hwp = None
    if hw_auto and hw_auto_enabled():
        hwp = detect_hardware_profile()
        if not low_vram_mode and hwp.low_vram and hwp.device == "cuda":
            low_vram_mode = True
        if hwp is not None:
            if not _user_set_views and hwp.max_views is not None:
                max_views = hwp.max_views
            if not _user_set_view_res and hwp.view_resolution is not None:
                view_resolution = hwp.view_resolution
            if not _user_set_render_size and hwp.render_size is not None:
                render_size = hwp.render_size
            if not _user_set_tex_size and hwp.texture_size is not None:
                texture_size = hwp.texture_size
        Console(stderr=True).print(f"[dim]Hardware (auto): {hwp.summary()}[/dim]")

    os.environ.setdefault(
        "PYTORCH_CUDA_ALLOC_CONF",
        "expandable_segments:True,max_split_size_mb:64,garbage_collection_threshold:0.6",
    )
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

    if max_views is None:
        max_views = _defaults.LOW_VRAM_MAX_VIEWS if low_vram_mode else _defaults.DEFAULT_PAINT_MAX_VIEWS
    if view_resolution is None:
        view_resolution = (
            _defaults.LOW_VRAM_VIEW_RESOLUTION if low_vram_mode else _defaults.DEFAULT_PAINT_VIEW_RESOLUTION
        )

    _prepare_gpu(allow_shared_gpu, gpu_kill_others, low_vram=low_vram_mode)

    parsed_gpu_ids = None
    if gpu_ids is not None:
        try:
            parsed_gpu_ids = [int(x.strip()) for x in gpu_ids.split(",") if x.strip()]
        except ValueError as exc:
            raise click.ClickException(f"--gpu-ids inválido: '{gpu_ids}'. Esperado: 0,1") from exc

    manifest_path = Path(manifest).resolve()
    manifest_dir = manifest_path.parent
    out_base = Path(output_dir).resolve()

    try:
        items = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise click.ClickException(f"Erro ao ler manifest: {exc}") from exc

    if not isinstance(items, list):
        raise click.ClickException("Manifest deve ser uma lista JSON de objetos.")

    for idx, item in enumerate(items):
        for key in ("id", "mesh", "image", "output"):
            if key not in item:
                raise click.ClickException(f"Item {idx}: campo obrigatório '{key}' em falta.")

    _batch_proc: PaintBatchProcessor | None = None

    def _cleanup() -> None:
        nonlocal _batch_proc
        if _batch_proc is not None:
            _batch_proc.__exit__(None, None, None)
            _batch_proc = None

    def _signal_handler(signum: int, frame: object) -> None:
        _cleanup()
        sys.exit(128 + signum)

    atexit.register(_cleanup)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    need_load = force or any(not (out_base / it["output"]).is_file() for it in items if "output" in it)

    try:
        if need_load:
            _batch_proc = PaintBatchProcessor(
                max_num_view=max_views,
                view_resolution=view_resolution,
                render_size=render_size,
                texture_size=texture_size,
                bake_exp=bake_exp,
                verbose=verbose,
                # Preservação feita em glTF sobre o ficheiro final (frame correto); o
                # preserve por bpy do processor era frágil/em frame errado.
                preserve_origin=False,
                low_vram=low_vram_mode,
                gpu_ids=parsed_gpu_ids,
            )
            _batch_proc.__enter__()

        for item in items:
            item_id = item["id"]
            mesh_path = (manifest_dir / item["mesh"]).resolve()
            image_path = (manifest_dir / item["image"]).resolve()
            output_path = (out_base / item["output"]).resolve()
            t0 = time.time()

            if not force and output_path.is_file():
                emit_result(item_id, TOOL_PAINT3D, STATUS_SKIPPED, output=str(output_path))
                continue

            try:
                if not mesh_path.is_file():
                    raise FileNotFoundError(f"Mesh não encontrada: {mesh_path}")
                if not image_path.is_file():
                    raise FileNotFoundError(f"Imagem não encontrada: {image_path}")

                output_path.parent.mkdir(parents=True, exist_ok=True)

                emit_progress(item_id, TOOL_PAINT3D, phase="loading_model", percent=0)
                mesh_obj = load_mesh_trimesh(mesh_path)
                emit_progress(item_id, TOOL_PAINT3D, phase="loading_model", percent=100)

                def _paint_step(phase, pct, _id=item_id):
                    emit_progress(_id, TOOL_PAINT3D, phase=phase, percent=pct)

                textured = _batch_proc.paint_mesh(mesh_obj, str(image_path), step_callback=_paint_step)

                if smooth:
                    textured = smooth_trimesh_texture(
                        textured,
                        passes=smooth_passes,
                        diameter=_defaults.DEFAULT_SMOOTH_DIAMETER,
                        sigma_color=_defaults.DEFAULT_SMOOTH_SIGMA_COLOR,
                        sigma_space=_defaults.DEFAULT_SMOOTH_SIGMA_SPACE,
                        verbose=verbose,
                    )

                emit_progress(item_id, TOOL_PAINT3D, phase="export", percent=0)
                save_glb(textured, output_path)
                if preserve_origin:
                    _fit_glb_aabb_to_reference(output_path, mesh_path, verbose=verbose)
                emit_progress(item_id, TOOL_PAINT3D, phase="export", percent=100)
                elapsed = time.time() - t0
                emit_result(item_id, TOOL_PAINT3D, STATUS_OK, output=str(output_path), seconds=round(elapsed, 2))
            except Exception as exc:
                elapsed = time.time() - t0
                emit_result(item_id, TOOL_PAINT3D, STATUS_ERROR, error=str(exc), seconds=round(elapsed, 2))
    finally:
        _cleanup()
        with contextlib.suppress(Exception):
            atexit.unregister(_cleanup)


@cli.command("quick")
@click.argument("mesh_file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--output",
    "-o",
    "output_path",
    required=True,
    type=click.Path(),
    help="GLB de saída (cor por vértice; sem IA).",
)
@click.option(
    "--style",
    type=click.Choice(["solid", "perlin"], case_sensitive=False),
    required=True,
    help="solid = cor única; perlin = FBM 3D no tom (tipo pedra).",
)
@click.option(
    "--color",
    default="#888888",
    show_default=True,
    help="Cor sólida #RGB ou #RRGGBB (só style=solid).",
)
@click.option(
    "--tint",
    default="#7a7268",
    show_default=True,
    help="Tom base #RRGGBB (só style=perlin).",
)
@click.option(
    "--frequency",
    default=4.0,
    show_default=True,
    type=float,
    help="Escala espacial do ruído (perlin).",
)
@click.option(
    "--octaves",
    default=4,
    show_default=True,
    type=int,
    help="Camadas FBM (perlin).",
)
@click.option("--seed", default=0, show_default=True, type=int, help="Seed reprodutível.")
@click.option(
    "--contrast",
    default=0.55,
    show_default=True,
    type=float,
    help="Quanto o ruído modula o tom [0-1] (perlin).",
)
@click.option(
    "--preserve-origin/--no-preserve-origin",
    default=True,
    show_default=True,
    help="Reposicionar AABB como em paint texture (base Y=0, XZ centrados).",
)
def quick_cmd(
    mesh_file,
    output_path,
    style,
    color,
    tint,
    frequency,
    octaves,
    seed,
    contrast,
    preserve_origin,
):
    """Textura rápida sem Hunyuan: cor sólida ou ruído procedural (segundos, só CPU)."""
    from pathlib import Path

    from .quick_bake import bake_perlin_vertex, bake_solid_color

    mesh_in = Path(mesh_file)
    mesh_out = Path(output_path)
    if style.lower() == "solid":
        out = bake_solid_color(
            mesh_in,
            mesh_out,
            color_hex=color,
            preserve_origin=preserve_origin,
        )
    else:
        out = bake_perlin_vertex(
            mesh_in,
            mesh_out,
            frequency=frequency,
            octaves=octaves,
            seed=seed,
            tint_hex=tint,
            contrast=contrast,
            preserve_origin=preserve_origin,
        )
    console.print(
        Panel(
            f"[green]OK[/green] → [cyan]{out}[/cyan]  (style={style})",
            title="[bold]paint3d quick",
            border_style="green",
        )
    )


@cli.command("vertex-pbr")
@click.argument("mesh_file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--output",
    "-o",
    "output_path",
    required=True,
    type=click.Path(),
    help="GLB de saída com PBR (baseColor + normal + ORM).",
)
@click.option(
    "--texture-size",
    default=1024,
    show_default=True,
    type=int,
    help="Resolução do mapa difuso (unwrap UV + raster das cores de vértice).",
)
@click.option(
    "--materialize-bin",
    default="materialize",
    show_default=True,
    help="Executável CLI Materialize (wgpu).",
)
@click.option(
    "--preset",
    "-p",
    default="default",
    show_default=True,
    type=click.Choice(
        ["default", "skin", "floor", "metal", "fabric", "wood", "stone"],
        case_sensitive=False,
    ),
    help="Preset Materialize (p.ex. fabric para folhagem, metal para cristal).",
)
@click.option("-v", "--verbose", is_flag=True, help="Passar -v ao materialize.")
def vertex_pbr_cmd(mesh_file, output_path, texture_size, materialize_bin, preset, verbose):
    """Cor por vértice → difuso (UV) → Materialize → GLB PBR (sem Hunyuan)."""
    from pathlib import Path

    from .vertex_pbr import vertex_color_glb_to_pbr_glb

    out = vertex_color_glb_to_pbr_glb(
        Path(mesh_file),
        Path(output_path),
        texture_size=texture_size,
        materialize_bin=materialize_bin,
        materialize_preset=preset,
        verbose=verbose,
    )
    console.print(
        Panel(
            f"[green]OK[/green] → [cyan]{out}[/cyan]",
            title="[bold]paint3d vertex-pbr",
            border_style="green",
        )
    )


@cli.command("doctor")
def doctor():
    """Verifica ambiente: PyTorch, CUDA, VRAM, modelos e rasterizador."""
    from gamedev_shared.gpu import (
        DEFAULT_EXCLUSIVE_GPU_MAX_USED_PCT,
        get_system_info,
        gpu_bytes_in_use,
        gpu_total_mib,
    )

    from .painter import check_hunyuan3d21_environment, check_paint_rasterizer_available

    console.print(
        Panel.fit(
            "[bold]paint3d doctor[/bold] — PyTorch, CUDA, Paint 2.1",
            border_style="blue",
        )
    )
    info_data = get_system_info()
    table = Table(title="[bold blue]Diagnóstico", box=box.ROUNDED)
    table.add_column("Item", style="cyan", no_wrap=True)
    table.add_column("Estado", style="green")

    alloc = os.environ.get("PYTORCH_CUDA_ALLOC_CONF", "")
    table.add_row("PYTORCH_CUDA_ALLOC_CONF", alloc or "[dim](não definido)[/dim]")
    table.add_row("PyTorch", info_data.get("torch_version", "N/A"))
    table.add_row("CUDA", str(info_data.get("cuda_available", False)))
    if info_data.get("cuda_available"):
        table.add_row("CUDA versão", info_data.get("cuda_version", "N/A"))
        for i, gpu in enumerate(info_data.get("gpus", [])):
            table.add_row(
                f"GPU {i}",
                f"{gpu['name']} — {format_bytes(gpu['total_memory'])} total, {format_bytes(gpu['free_memory'])} livre",
            )
        used = gpu_bytes_in_use(0)
        if used is not None:
            total = gpu_total_mib(0)
            pct_now = (used / (total * 1024 * 1024) * 100) if total else 0
            table.add_row(
                "GPU em uso",
                f"~{used / (1024**2):.0f} MiB ({pct_now:.0f}%; limite: {DEFAULT_EXCLUSIVE_GPU_MAX_USED_PCT:.0%})",
            )

    try:
        check_paint_rasterizer_available()
        import custom_rasterizer

        backend = "nvdiffrast (shim)" if getattr(custom_rasterizer, "IS_NVDIFFRAST_SHIM", False) else "nativo"
        table.add_row("Rasterizador", f"[green]OK — {backend}[/green]")
    except RuntimeError as e:
        msg = str(e).split("\n")[0][:120]
        table.add_row("Rasterizador", f"[yellow]{msg}[/yellow]")

    ok21, msg21 = check_hunyuan3d21_environment()
    table.add_row("Hunyuan3D-2.1", f"[{'green' if ok21 else 'yellow'}]{msg21.split(chr(10))[0][:120]}[/]")

    from .hardware import detect_hardware_profile, hw_auto_enabled

    _hwp = detect_hardware_profile()
    _state = "" if hw_auto_enabled() else " [yellow](desligado: PAINT3D_HW_AUTO=0)[/yellow]"
    table.add_row("Perfil hardware (auto)", f"{_hwp.summary()}{_state}")

    console.print(table)


@cli.command()
def info():
    """Informações do sistema e GPU."""
    from gamedev_shared.gpu import get_system_info

    console.print(Panel.fit("[bold]paint3d info[/bold]", border_style="blue"))
    info_data = get_system_info()

    table = Table(title="[bold blue]Sistema", box=box.ROUNDED)
    table.add_column("Componente", style="cyan", no_wrap=True)
    table.add_column("Valor", style="green")

    table.add_row("Python", info_data.get("python_version", "N/A"))
    table.add_row("PyTorch", info_data.get("torch_version", "N/A"))
    table.add_row("CUDA", str(info_data.get("cuda_available", False)))

    if info_data.get("cuda_available"):
        table.add_row("CUDA versão", info_data.get("cuda_version", "N/A"))
        for i, gpu in enumerate(info_data.get("gpus", [])):
            table.add_row(f"GPU {i}", gpu["name"])
            table.add_row("  VRAM total", format_bytes(gpu["total_memory"]))
            table.add_row("  VRAM livre", format_bytes(gpu["free_memory"]))

    table.add_row("HF cache", hf_home_display_rich())
    console.print(table)


def main():
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
