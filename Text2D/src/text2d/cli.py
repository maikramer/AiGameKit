#!/usr/bin/env python3
"""
Text2D — CLI principal (text-to-2D).
"""

import atexit
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

from gamedev_shared.hf import hf_home_display_rich
from gamedev_shared.progress import STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, TOOL_TEXT2D, emit_progress, emit_result
from gamedev_shared.skill_install import install_my_skill

from .cli_rich import click
from .generator import KleinFluxGenerator, _model_id, default_model_id
from .utils.memory import format_bytes, get_system_info

console = Console()

DEFAULT_OUTPUT_DIR = Path("outputs")
DEFAULT_IMAGE_DIR = DEFAULT_OUTPUT_DIR / "images"


def ensure_dirs() -> None:
    DEFAULT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)


@click.group()
@click.version_option(version="0.1.0", prog_name="text2d")
@click.option("--verbose", "-v", is_flag=True, help="Logs detalhados")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Text2D — imagens a partir de texto (FLUX.2 Klein 4B SDNQ)."""
    ctx.ensure_object(dict)
    ctx.obj["VERBOSE"] = verbose
    # Não criar outputs/ aqui: só quando a saída for a pasta por defeito (generate sem -o).


@cli.group("skill")
def skill_group() -> None:
    """Agent Skills Cursor (instalação no projeto do jogo)."""


@skill_group.command("install")
@click.option(
    "--target",
    "-t",
    type=click.Path(file_okay=False, writable=True, path_type=Path),
    default=".",
    help="Raiz do projeto do jogo (cria .cursor/skills/text2d/)",
)
@click.option("--force", is_flag=True, help="Sobrescrever SKILL.md existente")
def skill_install_cmd(target: Path, force: bool) -> None:
    """Copia SKILL.md para .cursor/skills/text2d/."""
    try:
        dest = install_my_skill(vars(), target, force=force)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e
    except FileExistsError as e:
        raise click.ClickException(f"{e} — usa --force para substituir.") from e
    console.print(
        Panel(
            f"Skill copiada para [bold cyan]{dest}[/bold cyan]",
            title="[bold green]OK[/bold green]",
            border_style="green",
        )
    )


@cli.command("generate")
@click.argument("prompt")
@click.option("--output", "-o", type=click.Path(), help="Ficheiro de saída (.png ou .jpg)")
@click.option("--width", "-W", default=1024, show_default=True, type=int)
@click.option("--height", "-H", default=1024, show_default=True, type=int)
@click.option("--steps", "-s", default=4, show_default=True, help="Passos de inferência")
@click.option(
    "--guidance",
    "-g",
    "guidance_scale",
    default=1.0,
    show_default=True,
    help="Guidance (recomendado 1.0 para checkpoint SDNQ)",
)
@click.option("--seed", type=int, default=None, help="Seed para reprodutibilidade")
@click.option("--cpu", is_flag=True, help="Forçar CPU")
@click.option("--low-vram", is_flag=True, help="CPU offload (menos VRAM)")
@click.option(
    "--model",
    "-m",
    "model_id",
    default=None,
    help="ID Hugging Face (default: 9B SDNQ, 4B com --low-vram, ou TEXT2D_MODEL_ID)",
)
@click.option(
    "--verbose",
    "-v",
    "verbose_flag",
    is_flag=True,
    help="Logs detalhados (ou use: text2d -v generate ...)",
)
@click.option(
    "--profile",
    is_flag=True,
    help="Medir tempos, CPU, RAM e VRAM (JSONL opcional: GAMEDEV_PROFILE_LOG).",
)
@click.option(
    "--gpu-ids",
    "gpu_ids_str",
    default=None,
    help="IDs das GPUs para split multi-GPU (ex: '0,1'). Auto-deteta se omitido com ≥2 GPUs.",
)
@click.pass_context
def generate_cmd(
    ctx: click.Context,
    prompt: str,
    output: str | None,
    width: int,
    height: int,
    steps: int,
    guidance_scale: float,
    seed: int | None,
    cpu: bool,
    low_vram: bool,
    model_id: str | None,
    verbose_flag: bool,
    profile: bool,
    gpu_ids_str: str | None,
) -> None:
    """Gera uma imagem a partir do PROMPT."""
    from gamedev_shared.gpu import warn_if_vram_occupied
    from gamedev_shared.profiler import ProfilerSession
    from gamedev_shared.profiler.env import env_profile_log_path

    verbose = bool(ctx.obj.get("VERBOSE")) or verbose_flag

    if not cpu:
        warn_if_vram_occupied()

    low = low_vram or cpu
    if low and width == 2048 and height == 2048:
        width, height = 1024, 1024
    resolved_model = model_id or _model_id(low_vram=low)
    device = "cpu" if cpu else None
    gpu_ids = [int(x.strip()) for x in gpu_ids_str.split(",")] if gpu_ids_str else None
    console = Console()
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_row("[bold]Prompt[/bold]", f"[cyan]{prompt}[/cyan]")
    table.add_row("[bold]Resolução[/bold]", f"{width}x{height}")
    table.add_row("[bold]Passos[/bold]", str(steps))
    table.add_row("[bold]Guidance[/bold]", str(guidance_scale))
    table.add_row("[bold]Modelo[/bold]", resolved_model)
    console.print(Panel(table, title="[bold green]Configuração", border_style="green"))

    log_p = env_profile_log_path()
    prof_log = Path(log_p) if log_p else None
    t_start = time.time()

    safe = "".join(c if c.isalnum() else "_" for c in prompt[:40])
    item_id = safe or "single"

    try:
        emit_progress(item_id, TOOL_TEXT2D, phase="loading_model", percent=0)
        with ProfilerSession(
            "text2d",
            log_path=prof_log,
            cli_profile=profile,
            model_id=resolved_model,
            params={"width": width, "height": height, "steps": steps, "guidance_scale": guidance_scale, "seed": seed},
        ) as prof:
            gen = KleinFluxGenerator(
                device=device,
                low_vram=low,
                verbose=verbose,
                model_id=model_id,
                gpu_ids=gpu_ids,
            )

            with (
                prof.span("warmup"),
                console.status(
                    "[bold yellow]1/2 — Download HF + carregamento de pesos "
                    "(1ª vez: vários GB/minutos; GPU pode mostrar 0% até ao passo 3/3)",
                    spinner="dots",
                ),
            ):
                gen.warmup()

            if output is None:
                ensure_dirs()
                ts = int(time.time())
                output = str(DEFAULT_IMAGE_DIR / f"{safe}_{ts}.png")
            out_path = Path(output)

            emit_progress(item_id, TOOL_TEXT2D, phase="diffusion", percent=0)
            with (
                prof.span("generate", sync_cuda=True),
                Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                ) as progress,
            ):
                task = progress.add_task("[cyan]2/2 — Inferência na GPU...", total=None)
                image = gen.generate(
                    prompt=prompt,
                    height=height,
                    width=width,
                    guidance_scale=guidance_scale,
                    num_inference_steps=steps,
                    seed=seed,
                )
                progress.update(task, description="[green]Concluído")
            emit_progress(item_id, TOOL_TEXT2D, phase="diffusion", percent=100)

            emit_progress(item_id, TOOL_TEXT2D, phase="save", percent=0)
            with prof.span("save"):
                ext = out_path.suffix.lower().lstrip(".")
                img_format = "JPEG" if ext in ("jpg", "jpeg") else "PNG"
                KleinFluxGenerator.save_image(
                    image,
                    out_path,
                    image_format=img_format if img_format == "JPEG" else "PNG",
                )
            emit_progress(item_id, TOOL_TEXT2D, phase="save", percent=100)

        elapsed = time.time() - t_start
        try:
            sz = format_bytes(out_path.stat().st_size)
        except OSError:
            sz = "?"
        console.print(Rule("[bold green]Resultado", style="green"))
        console.print(f"[bold green]✓[/bold green] Imagem: [cyan]{out_path.resolve()}[/cyan] [dim]({sz})[/dim]")
        console.print(f"[dim]Tempo total: {elapsed:.1f}s[/dim]")
        emit_result(item_id, TOOL_TEXT2D, STATUS_OK, output=str(out_path), seconds=elapsed)
    except ImportError as e:
        console.print(f"\n[bold red]✗[/bold red] {e}")
        emit_result(item_id, TOOL_TEXT2D, STATUS_ERROR, error=str(e))
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[bold red]✗ Erro:[/bold red] {e}")
        if verbose:
            console.print_exception()
        emit_result(item_id, TOOL_TEXT2D, STATUS_ERROR, error=str(e))
        sys.exit(1)


_batch_gen = None


def _batch_cleanup() -> None:
    global _batch_gen
    if _batch_gen is not None:
        _batch_gen.unload()
        _batch_gen = None


def _batch_signal_handler(signum: int, frame: object) -> None:
    _batch_cleanup()
    sys.exit(128 + signum)


atexit.register(_batch_cleanup)


@cli.command("generate-batch")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.option("--output-dir", "-O", type=click.Path(), default=".", help="Diretório base para outputs relativos.")
@click.option("--width", "-W", default=1024, type=int)
@click.option("--height", "-H", default=1024, type=int)
@click.option("--steps", "-s", default=4, type=int, help="Passos de inferência")
@click.option(
    "--guidance",
    "-g",
    "guidance_scale",
    default=1.0,
    type=float,
    help="Guidance scale (recomendado 1.0 para SDNQ)",
)
@click.option("--cpu", is_flag=True, help="Forçar CPU")
@click.option("--low-vram", is_flag=True, help="CPU offload (menos VRAM)")
@click.option(
    "--model",
    "-m",
    "model_id",
    default=None,
    help="ID Hugging Face (default: SDNQ, ou TEXT2D_MODEL_ID)",
)
@click.option(
    "--gpu-ids",
    "gpu_ids_str",
    default=None,
    help="IDs das GPUs para split multi-GPU (ex: '0,1').",
)
@click.option("--force", is_flag=True, help="Regenerar mesmo se o output já existe.")
@click.option("-v", "--verbose", "batch_verbose", is_flag=True)
def generate_batch_cmd(
    manifest: str,
    output_dir: str,
    width: int,
    height: int,
    steps: int,
    guidance_scale: float,
    cpu: bool,
    low_vram: bool,
    model_id: str | None,
    gpu_ids_str: str | None,
    force: bool,
    batch_verbose: bool,
) -> None:
    """Gera múltiplas imagens a partir de um manifesto JSON (JSONL em stdout)."""
    global _batch_gen

    from gamedev_shared.gpu import warn_if_vram_occupied

    manifest_path = Path(manifest)
    out_root = Path(output_dir)

    try:
        items: list[dict[str, Any]] = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        console.print(f"[red]Manifesto inválido:[/red] {exc}")
        sys.exit(1)

    for i, item in enumerate(items):
        missing = [k for k in ("id", "prompt", "output") if k not in item]
        if missing:
            console.print(f"[red]Item {i} falta:[/red] {', '.join(missing)}")
            sys.exit(1)

    if not cpu:
        warn_if_vram_occupied()

    low = low_vram or cpu
    parsed_gpu_ids = [int(x.strip()) for x in gpu_ids_str.split(",")] if gpu_ids_str else None

    try:
        gen = KleinFluxGenerator(
            device="cpu" if cpu else None,
            low_vram=low,
            verbose=batch_verbose,
            model_id=model_id,
            gpu_ids=parsed_gpu_ids,
        )
        _batch_gen = gen

        with Console(stderr=True).status("[bold yellow]A carregar pipeline...", spinner="dots"):
            gen.warmup()

        signal.signal(signal.SIGTERM, _batch_signal_handler)
        signal.signal(signal.SIGINT, _batch_signal_handler)

        need_load = force or any(not (out_root / Path(it["output"])).is_file() for it in items if "output" in it)
        if need_load:
            with Console(stderr=True).status("[bold yellow]A carregar pipeline...", spinner="dots"):
                gen.warmup()

        for item in items:
            t0 = time.time()
            item_id = item["id"]
            prompt = item["prompt"]
            out_rel = Path(item["output"])
            out_path = out_root / out_rel if not out_rel.is_absolute() else out_rel

            if not force and out_path.is_file():
                emit_result(item_id, TOOL_TEXT2D, STATUS_SKIPPED, output=str(out_rel))
                continue

            item_w = item.get("width", width)
            item_h = item.get("height", height)
            item_steps = item.get("steps", steps)
            item_guidance = item.get("guidance_scale", guidance_scale)
            item_seed = item.get("seed")

            try:
                emit_progress(item_id, TOOL_TEXT2D, phase="diffusion", percent=0)
                image = gen.generate(
                    prompt=prompt,
                    height=item_h,
                    width=item_w,
                    guidance_scale=item_guidance,
                    num_inference_steps=item_steps,
                    seed=item_seed,
                )
                emit_progress(item_id, TOOL_TEXT2D, phase="diffusion", percent=100)

                emit_progress(item_id, TOOL_TEXT2D, phase="save", percent=0)
                ext = out_path.suffix.lower().lstrip(".")
                img_format = "JPEG" if ext in ("jpg", "jpeg") else "PNG"
                KleinFluxGenerator.save_image(image, out_path, image_format=img_format)
                emit_progress(item_id, TOOL_TEXT2D, phase="save", percent=100)

                elapsed = time.time() - t0
                emit_result(item_id, TOOL_TEXT2D, STATUS_OK, output=str(out_rel), seconds=round(elapsed, 3))
            except Exception as exc:
                emit_result(item_id, TOOL_TEXT2D, STATUS_ERROR, error=str(exc))

        _batch_cleanup()
    except ImportError as exc:
        console.print(f"\n[bold red]✗[/bold red] {exc}")
        sys.exit(1)
    except Exception as exc:
        console.print(f"\n[bold red]✗ Erro:[/bold red] {exc}")
        if batch_verbose:
            console.print_exception()
        sys.exit(1)


@cli.command("info")
def info_cmd() -> None:
    """Informações do sistema e GPU."""
    console.print(
        Panel.fit(
            "[bold]text2d info[/bold] — ambiente de execução e cache Hugging Face",
            border_style="blue",
        )
    )
    data = get_system_info()
    t = Table(title="[bold blue]Sistema", box=box.ROUNDED)
    t.add_column("Componente", style="cyan", no_wrap=True)
    t.add_column("Valor", style="green")
    t.add_row("Python", data.get("python_version", "N/A"))
    t.add_row("PyTorch", data.get("torch_version", "N/A"))
    t.add_row("CUDA", str(data.get("cuda_available", False)))
    if data.get("cuda_available"):
        t.add_row("CUDA (versão)", str(data.get("cuda_version", "N/A")))
        for i, gpu in enumerate(data.get("gpus", [])):
            t.add_row(f"GPU {i}", str(gpu.get("name", "")))
            t.add_row("  └ VRAM total", format_bytes(gpu.get("total_memory", 0)))
            t.add_row("  └ VRAM livre", format_bytes(gpu.get("free_memory", 0)))
    t.add_row("HF_HOME (cache Hub)", hf_home_display_rich())
    t.add_row("Saída padrão (pasta)", str(DEFAULT_IMAGE_DIR.resolve()))
    t.add_row("Modelo (default)", default_model_id())
    console.print(t)


@cli.command("doctor")
def doctor_cmd() -> None:
    """Verifica ambiente: PyTorch, CUDA, VRAM e cache HF."""
    from gamedev_shared.gpu import (
        DEFAULT_EXCLUSIVE_GPU_MAX_USED_PCT,
        get_system_info,
        gpu_bytes_in_use,
        gpu_total_mib,
    )

    console.print(
        Panel.fit(
            "[bold]text2d doctor[/bold] — PyTorch, CUDA, ambiente",
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
    table.add_row("HF_HOME (cache)", hf_home_display_rich())

    console.print(table)


@cli.command("models")
def models_cmd() -> None:
    """Modelos suportados."""
    t = Table(title="[bold blue]Modelos", box=box.ROUNDED)
    t.add_column("ID", style="cyan")
    t.add_column("Notas", style="white")
    t.add_row(
        "Disty0/FLUX.2-klein-9B-SDNQ-4bit-dynamic-svd-r32",
        "Padrão (high-VRAM), SDNQ 4-bit, 9B parâmetros",
    )
    t.add_row(
        "Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic",
        "Padrão com --low-vram, SDNQ 4-bit, 4B parâmetros",
    )
    t.add_row(
        "black-forest-labs/FLUX.2-klein-4B",
        "Alternativa: BF16 completo, mais VRAM (TEXT2D_MODEL_ID)",
    )
    console.print(t)
    console.print(
        Panel(
            "[dim]Pesos GGUF (Unsloth) em geral usam ComfyUI-GGUF, não este CLI.[/dim]",
            border_style="dim",
        )
    )


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
