#!/usr/bin/env python3
"""Texture2D — CLI principal (texturas 2D seamless via SD1.5 + circular padding)."""

from __future__ import annotations

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

from gamedev_shared.cli_helpers import add_ums_options, try_ums_delegation
from gamedev_shared.gpu import get_system_info
from gamedev_shared.hf import hf_home_display_rich
from gamedev_shared.path_utils import safe_filename
from gamedev_shared.quality import VALID_QUALITIES

from ._validate_cli import validate_tileable_cmd
from .cli_rich import RICH_CLICK, click  # noqa: F401 — rich-click antes dos comandos
from .generator import DEFAULT_GUIDANCE, DEFAULT_RESOLUTION, DEFAULT_STEPS, TextureGenerator, default_model_id
from .presets import TEXTURE_PRESETS, list_presets
from .utils import format_bytes

console = Console()

DEFAULT_OUTPUT_DIR = Path("outputs")
DEFAULT_TEXTURE_DIR = DEFAULT_OUTPUT_DIR / "textures"


def ensure_dirs() -> None:
    DEFAULT_TEXTURE_DIR.mkdir(parents=True, exist_ok=True)


@click.group()
@click.version_option(version="0.1.0", prog_name="texture2d")
@click.option("--verbose", "-v", is_flag=True, help="Logs detalhados")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Texture2D — texturas 2D seamless (Stable Diffusion v1.5 + circular padding)."""
    ctx.ensure_object(dict)
    ctx.obj["VERBOSE"] = verbose


@cli.group("skill")
def skill_group() -> None:
    """Agent Skills Cursor (instalação no projeto do jogo)."""


@skill_group.command("install")
@click.option(
    "--target",
    "-t",
    type=click.Path(file_okay=False, writable=True, path_type=Path),
    default=".",
    help="Raiz do projeto do jogo (cria .cursor/skills/texture2d/)",
)
@click.option("--force", is_flag=True, help="Sobrescrever SKILL.md existente")
def skill_install_cmd(target: Path, force: bool) -> None:
    """Copia SKILL.md para .cursor/skills/texture2d/."""
    from gamedev_shared.skill_install import install_my_skill

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
@click.option("--output", "-o", type=click.Path(), help="Ficheiro de saída (.png)")
@click.option("--width", "-W", default=DEFAULT_RESOLUTION, show_default=True, type=int)
@click.option("--height", "-H", default=DEFAULT_RESOLUTION, show_default=True, type=int)
@click.option("--steps", "-s", default=DEFAULT_STEPS, show_default=True, help="Passos de inferência")
@click.option(
    "--guidance",
    "-g",
    "guidance_scale",
    default=DEFAULT_GUIDANCE,
    show_default=True,
    help="Guidance scale (CFG)",
)
@click.option("--seed", type=int, default=None, help="Seed (None = aleatório)")
@click.option(
    "--negative-prompt",
    "-n",
    "negative_prompt",
    default="",
    help="Prompt negativo (CFG nativo do SD1.5)",
)
@click.option(
    "--preset",
    "-p",
    default=None,
    type=click.Choice(["None", *list_presets()], case_sensitive=False),
    help="Preset de material",
)
@click.option(
    "--model",
    "-m",
    "model_id",
    default=None,
    help="ID do modelo HF (default: stable-diffusion-v1-5/stable-diffusion-v1-5)",
)
@click.option(
    "--verbose",
    "-v",
    "verbose_flag",
    is_flag=True,
    help="Logs detalhados",
)
@click.option("--cpu", is_flag=True, help="Forçar CPU")
@click.option(
    "--gpu-ids",
    "gpu_ids_str",
    default=None,
    help="IDs das GPUs (ex: '0,1'). Auto-deteta se omitido com ≥2 GPUs.",
)
@click.option(
    "--quality",
    type=click.Choice(list(VALID_QUALITIES)),
    default="medium",
    show_default=True,
    help="Quality tier (fast / low / medium / high / highest).",
)
@click.option(
    "--hw-auto/--no-hw-auto",
    "hw_auto",
    default=True,
    show_default=True,
    help=(
        "Auto-detecção de hardware (device + multi-GPU). Sem efeito de offload/clamp "
        "(SD1.5 cabe em qualquer GPU CUDA). Env: TEXTURE2D_HW_AUTO=0."
    ),
)
@click.option(
    "--ground",
    type=click.Choice(["auto", "on", "off"], case_sensitive=False),
    default="auto",
    show_default=True,
    help=(
        "Modo chão top-down: 'auto' deteta chão/terreno e aplica modificadores "
        "de viewpoint/iluminação/escala (corrige grama isométrica, zoom macro e "
        "relevo 3D); 'on' força; 'off' desliga."
    ),
)
@click.option(
    "--compile/--no-compile",
    "torch_compile",
    default=False,
    show_default=True,
    help="torch.compile no UNet (Inductor). Cold lento; útil em batch/server. Env: GAMEDEV_TORCH_COMPILE=1.",
)
@click.option(
    "--compile-mode",
    "torch_compile_mode",
    type=click.Choice(["default", "reduce-overhead", "max-autotune"]),
    default="default",
    show_default=True,
    help="Modo Inductor. reduce-overhead/max-autotune = CUDA graphs (full-GPU).",
)
@click.option(
    "--channels-last/--no-channels-last",
    "channels_last",
    default=False,
    show_default=True,
    help="Memory format NHWC (channels_last) no VAE/UNet — Ampere+ conv path.",
)
@add_ums_options
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
    negative_prompt: str,
    preset: str | None,
    model_id: str | None,
    verbose_flag: bool,
    cpu: bool,
    gpu_ids_str: str | None,
    quality: str,
    hw_auto: bool,
    ground: str,
    torch_compile: bool,
    torch_compile_mode: str,
    channels_last: bool,
    ums_priority: str | None,
    no_ums: bool,
    ums_stream: bool,
) -> None:
    """Gera uma textura seamless a partir do PROMPT (SD1.5 + circular padding)."""
    from gamedev_shared.gpu import warn_if_vram_occupied

    verbose = bool(ctx.obj.get("VERBOSE")) or verbose_flag

    # QualityEngine: soft resolution — fills defaults when user didn't specify.
    _src = click.core.ParameterSource
    _user_set_width = ctx.get_parameter_source("width") not in (_src.DEFAULT,)
    _user_set_height = ctx.get_parameter_source("height") not in (_src.DEFAULT,)
    _user_set_steps = ctx.get_parameter_source("steps") not in (_src.DEFAULT,)
    _user_set_guidance = ctx.get_parameter_source("guidance_scale") not in (_src.DEFAULT,)

    from gamedev_shared.quality import QualityEngine

    _qengine = QualityEngine()
    _qresolved = _qengine.resolve("texture2d", quality=quality)
    if not _user_set_width and "width" in _qresolved.params:
        width = _qresolved.params["width"]
    if not _user_set_height and "height" in _qresolved.params:
        height = _qresolved.params["height"]
    if not _user_set_steps and "steps" in _qresolved.params:
        steps = _qresolved.params["steps"]
    if not _user_set_guidance and "guidance" in _qresolved.params:
        guidance_scale = _qresolved.params["guidance"]

    if not cpu:
        warn_if_vram_occupied()

    # Hardware auto-detection (soft): SD1.5 cabe em qualquer GPU — só detetamos
    # device/multi-GPU para display. Sem offload/clamp.
    from .hardware import detect_hardware_profile, hw_auto_enabled

    hwp = None
    if hw_auto and hw_auto_enabled() and not cpu:
        hwp = detect_hardware_profile()

    device = "cpu" if cpu else None
    gpu_ids = [int(x.strip()) for x in gpu_ids_str.split(",")] if gpu_ids_str else None
    if gpu_ids is None and hwp is not None and hwp.gpu_ids:
        gpu_ids = hwp.gpu_ids
    resolved_model = model_id or default_model_id()

    table = Table(show_header=False, box=box.SIMPLE)
    table.add_row("[bold]Prompt[/bold]", f"[cyan]{prompt}[/cyan]")
    table.add_row("[bold]Backend[/bold]", "Stable Diffusion v1.5 (circular padding)")
    table.add_row("[bold]Resolução[/bold]", f"{width}x{height}")
    table.add_row("[bold]Passos[/bold]", str(steps))
    table.add_row("[bold]Guidance[/bold]", str(guidance_scale))
    if preset and preset != "None":
        table.add_row("[bold]Preset[/bold]", preset)
    table.add_row("[bold]Modelo[/bold]", resolved_model)
    if hwp is not None:
        table.add_row("[bold]Hardware (auto)[/bold]", hwp.summary())
    console.print(Panel(table, title="[bold green]Configuração", border_style="green"))

    t_start = time.time()

    if (
        not cpu
        and output is not None
        and try_ums_delegation(
            "texture2d",
            {
                "prompt": prompt,
                "output": str(Path(output).resolve()),
                "width": width,
                "height": height,
                "steps": steps,
                "guidance": guidance_scale,
                "seed": seed,
                "negative_prompt": negative_prompt,
                "preset": preset,
                "ground": ground,
            },
            t_start=t_start,
            noun="Textura",
            console=console,
            enabled=not no_ums,
            priority=ums_priority,
            stream=ums_stream,
        )
    ):
        return

    # Fallback: per-tool legacy server (deprecated).
    if not cpu and output is not None:
        from . import client

        if client.is_available():
            console.print("[dim]A delegar ao model server ativo...[/dim]")
            result = client.send_generate_request(
                prompt=prompt,
                output=output,
                width=width,
                height=height,
                steps=steps,
                guidance=guidance_scale,
                seed=seed,
                negative_prompt=negative_prompt,
                preset=preset,
                ground=ground,
            )
            if result and result.get("status") == "ok":
                elapsed = time.time() - t_start
                try:
                    sz = format_bytes(Path(result["output"]).stat().st_size)
                except OSError:
                    sz = "?"
                console.print(Rule("[bold green]Resultado (via server)", style="green"))
                console.print(
                    f"[bold green]\u2713[/bold green] Textura: [cyan]{result['output']}[/cyan] [dim]({sz})[/dim]"
                )
                console.print(f"[dim]Seed: {result.get('seed', '?')}[/dim]")
                console.print(f"[dim]Tempo total: {elapsed:.1f}s[/dim]")
                return
            elif result and result.get("status") == "error":
                console.print(f"[yellow]Server erro: {result.get('error', '?')} — fallback in-process[/yellow]")
            # Se None (server não respondeu), continua para fallback in-process

    try:
        gen: Any = TextureGenerator(
            device=device,
            verbose=verbose,
            model_id=model_id,
            gpu_ids=gpu_ids,
            torch_compile=torch_compile,
            torch_compile_mode=torch_compile_mode,
            channels_last=channels_last,
        )

        with console.status(
            "[bold yellow]1/2 — Download HF + carregamento de pesos "
            "(1ª vez: ~2 GB/minutos; GPU pode mostrar 0% até ao passo 3/3)",
            spinner="dots",
        ):
            gen.warmup()

        if output is None:
            ensure_dirs()
            ts = int(time.time())
            safe = safe_filename(prompt)
            output = str(DEFAULT_TEXTURE_DIR / f"{safe}_{ts}.png")
        out_path = Path(output)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]2/2 — Inferência na GPU...", total=None)
            image, metadata = gen.generate(
                prompt=prompt,
                negative_prompt=negative_prompt,
                guidance_scale=guidance_scale,
                num_inference_steps=steps,
                seed=seed,
                width=width,
                height=height,
                preset=preset,
                ground=ground,
            )
            progress.update(task, description="[green]Concluído")

        from .image_processor import save_image

        saved = save_image(
            image,
            prompt=metadata.get("prompt_final", prompt),
            params=metadata,
            output_dir=out_path.parent,
            filename=out_path.name,
        )

        elapsed = time.time() - t_start
        try:
            sz = format_bytes(saved.stat().st_size)
        except OSError:
            sz = "?"
        console.print(Rule("[bold green]Resultado", style="green"))
        console.print(f"[bold green]\u2713[/bold green] Textura: [cyan]{saved.resolve()}[/cyan] [dim]({sz})[/dim]")
        console.print(f"[dim]Seed: {metadata.get('seed', '?')}[/dim]")
        console.print(f"[dim]Tempo total: {elapsed:.1f}s[/dim]")

    except ImportError as e:
        console.print(f"\n[bold red]\u2717[/bold red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[bold red]\u2717 Erro:[/bold red] {e}")
        if verbose:
            console.print_exception()
        sys.exit(1)


@cli.command("presets")
def presets_cmd() -> None:
    """Lista presets de materiais disponíveis."""
    t = Table(title="[bold blue]Presets de Texturas", box=box.ROUNDED)
    t.add_column("Nome", style="cyan", no_wrap=True)
    t.add_column("Prompt", style="white")
    t.add_column("Steps", style="green", justify="right")
    t.add_column("Guidance", style="green", justify="right")

    for name, preset in TEXTURE_PRESETS.items():
        t.add_row(
            name,
            preset["prompt"][:60] + "..." if len(preset["prompt"]) > 60 else preset["prompt"],
            str(preset.get("num_inference_steps", DEFAULT_STEPS)),
            str(preset.get("guidance_scale", DEFAULT_GUIDANCE)),
        )
    console.print(t)


@cli.command("batch")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-d", type=click.Path(path_type=Path), default=None)
@click.option("--preset", "-p", default=None, help="Preset aplicado a todos os prompts")
@click.option("--width", "-W", default=DEFAULT_RESOLUTION, type=int)
@click.option("--height", "-H", default=DEFAULT_RESOLUTION, type=int)
@click.option("--steps", "-s", default=DEFAULT_STEPS, type=int)
@click.option("--guidance", "-g", "guidance_scale", default=DEFAULT_GUIDANCE, type=float)
@click.option("--model", "-m", "model_id", default=None)
@click.option(
    "--gpu-ids",
    "gpu_ids_str",
    default=None,
    help="IDs das GPUs (ex: '0,1')",
)
@click.option(
    "--quality",
    type=click.Choice(list(VALID_QUALITIES)),
    default="medium",
    show_default=True,
    help="Quality tier (fast / low / medium / high / highest).",
)
@click.option(
    "--hw-auto/--no-hw-auto",
    "hw_auto",
    default=True,
    show_default=True,
    help="Auto-detecção de hardware (device + multi-GPU). Env: TEXTURE2D_HW_AUTO=0.",
)
@click.option(
    "--ground",
    type=click.Choice(["auto", "on", "off"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Modo chão top-down (auto deteta chão/terreno; on força; off desliga).",
)
@click.option(
    "--compile/--no-compile",
    "torch_compile",
    default=False,
    show_default=True,
    help="torch.compile no UNet (Inductor).",
)
@click.option(
    "--compile-mode",
    "torch_compile_mode",
    type=click.Choice(["default", "reduce-overhead", "max-autotune"]),
    default="default",
    show_default=True,
    help="Modo Inductor.",
)
@click.option(
    "--channels-last/--no-channels-last",
    "channels_last",
    default=False,
    show_default=True,
    help="channels_last NHWC no VAE/UNet.",
)
@click.pass_context
def batch_cmd(
    ctx: click.Context,
    file: Path,
    output_dir: Path | None,
    preset: str | None,
    width: int,
    height: int,
    steps: int,
    guidance_scale: float,
    model_id: str | None,
    gpu_ids_str: str | None,
    quality: str,
    hw_auto: bool,
    ground: str,
    torch_compile: bool,
    torch_compile_mode: str,
    channels_last: bool,
) -> None:
    """Gera texturas em batch a partir de um ficheiro de prompts (um por linha)."""
    # QualityEngine: soft resolution — fills defaults when user didn't specify.
    _src = click.core.ParameterSource
    _user_set_width = ctx.get_parameter_source("width") not in (_src.DEFAULT,)
    _user_set_height = ctx.get_parameter_source("height") not in (_src.DEFAULT,)
    _user_set_steps = ctx.get_parameter_source("steps") not in (_src.DEFAULT,)
    _user_set_guidance = ctx.get_parameter_source("guidance_scale") not in (_src.DEFAULT,)

    from gamedev_shared.quality import QualityEngine

    _qengine = QualityEngine()
    _qresolved = _qengine.resolve("texture2d", quality=quality)
    if not _user_set_width and "width" in _qresolved.params:
        width = _qresolved.params["width"]
    if not _user_set_height and "height" in _qresolved.params:
        height = _qresolved.params["height"]
    if not _user_set_steps and "steps" in _qresolved.params:
        steps = _qresolved.params["steps"]
    if not _user_set_guidance and "guidance" in _qresolved.params:
        guidance_scale = _qresolved.params["guidance"]

    # Hardware auto-detection (soft): SD1.5 cabe em qualquer GPU — sem offload/clamp.
    from .hardware import detect_hardware_profile, hw_auto_enabled

    hwp = None
    if hw_auto and hw_auto_enabled():
        hwp = detect_hardware_profile()
    if hwp is not None:
        Console(stderr=True).print(f"[dim]Hardware (auto): {hwp.summary()}[/dim]")

    gpu_ids = [int(x.strip()) for x in gpu_ids_str.split(",")] if gpu_ids_str else None
    if gpu_ids is None and hwp is not None and hwp.gpu_ids:
        gpu_ids = hwp.gpu_ids

    prompts = [
        line.strip()
        for line in file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    if not prompts:
        raise click.ClickException("Ficheiro sem prompts válidos.")

    console.print(f"[bold]Batch:[/bold] {len(prompts)} prompts de [cyan]{file}[/cyan]")

    out = output_dir or DEFAULT_TEXTURE_DIR
    out.mkdir(parents=True, exist_ok=True)

    gen: Any = TextureGenerator(
        verbose=bool(ctx.obj.get("VERBOSE")),
        model_id=model_id,
        gpu_ids=gpu_ids,
        torch_compile=torch_compile,
        torch_compile_mode=torch_compile_mode,
        channels_last=channels_last,
    )
    base_params: dict[str, Any] = {
        "guidance_scale": guidance_scale,
        "num_inference_steps": steps,
        "width": width,
        "height": height,
        "ground": ground,
    }
    if preset and preset != "None":
        base_params["preset"] = preset

    from .image_processor import save_image

    ok_count = 0
    for image, metadata, idx in gen.generate_batch(prompts, **base_params):
        if image is None:
            console.print(f"  [red]\u2717[/red] {idx + 1}/{len(prompts)}: {metadata.get('error', '?')}")
            continue

        ts = int(time.time())
        safe = safe_filename(prompts[idx])
        fname = f"{safe}_{ts}.png"
        saved = save_image(
            image,
            prompt=metadata.get("prompt_final", prompts[idx]),
            params=metadata,
            output_dir=out,
            filename=fname,
        )
        ok_count += 1
        console.print(f"  [green]\u2713[/green] {idx + 1}/{len(prompts)}: [cyan]{saved.name}[/cyan]")

    console.print(
        Panel(
            f"[bold]{ok_count}/{len(prompts)}[/bold] texturas geradas em [cyan]{out.resolve()}[/cyan]",
            title="[bold green]Batch concluído",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# Model server — mantém o pipeline SD1.5 + circular padding carregado na VRAM.
# Gerações subsequentes delegam automaticamente (~3-5s vs cold start).
# ---------------------------------------------------------------------------


@cli.command("server")
@click.option(
    "--socket",
    "socket_path",
    type=click.Path(),
    default=None,
    help="Path do Unix socket (default: ~/.cache/gamedev/texture2d-server.sock)",
)
@click.option(
    "--idle-timeout",
    "idle_timeout_min",
    default=30,
    show_default=True,
    type=int,
    help="Minutos de idle antes de encerrar (liberta VRAM).",
)
@click.option("--verbose", "-v", is_flag=True, help="Logs detalhados")
def server_cmd(socket_path: str | None, idle_timeout_min: int, verbose: bool) -> None:
    """[DEPRECATED] Server per-tool. Preferir ``gamedev-model-server start`` (UMS)."""
    from gamedev_shared.model_server import server_socket_path

    from . import server

    console.print(
        "[yellow]Deprecated:[/yellow] use [cyan]gamedev-model-server start[/cyan] "
        "(Unified Model Server). Este server per-tool fica só como fallback."
    )
    _default_sock = server_socket_path("texture2d")
    if server.is_server_running(socket_path or _default_sock):
        console.print("[yellow]Server já está ativo neste socket.[/yellow]")
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold]Texture2D Model Server[/bold]\n"
            f"Socket: [cyan]{socket_path or _default_sock}[/cyan]\n"
            f"Idle timeout: [green]{idle_timeout_min} min[/green]\n\n"
            f"[dim]O pipeline carrega no 1.º pedido (cold start). Depois, "
            f"``texture2d generate`` delega automaticamente.[/dim]",
            border_style="blue",
        )
    )

    try:
        server.start_server(
            socket_path=socket_path,
            idle_timeout_min=idle_timeout_min,
            verbose=verbose,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Server interrompido.[/yellow]")
    except Exception as e:
        console.print(f"\n[bold red]\u2717 Erro no server:[/bold red] {e}")
        if verbose:
            console.print_exception()
        sys.exit(1)


@cli.command("server-status")
def server_status_cmd() -> None:
    """Mostra o estado do model server."""
    from gamedev_shared.model_server import server_socket_path

    from . import server

    status = server.get_server_status(server_socket_path("texture2d"))
    if status is None:
        console.print("[yellow]Model server não está ativo.[/yellow]")
        console.print("[dim]Arranca com: texture2d server[/dim]")
        sys.exit(1)

    t = Table(title="[bold blue]Model Server", box=box.ROUNDED)
    t.add_column("Campo", style="cyan", no_wrap=True)
    t.add_column("Valor", style="green")
    t.add_row("PID", str(status.get("pid", "?")))
    t.add_row("Socket", str(status.get("socket", "?")))
    t.add_row("Modelo carregado", "✓ sim" if status.get("model_loaded") else "✗ não (cold start pendente)")
    t.add_row("Pedidos servidos", str(status.get("requests_served", 0)))
    t.add_row("Tool", str(status.get("tool", "?")))
    console.print(t)


@cli.command("server-stop")
def server_stop_cmd() -> None:
    """Para o model server (graceful shutdown)."""
    from gamedev_shared.model_server import _pid_path, server_socket_path

    from . import server

    _default_sock = server_socket_path("texture2d")
    if not server.is_server_running(_default_sock):
        console.print("[yellow]Server não está ativo.[/yellow]")
        _default_sock.unlink(missing_ok=True)
        _pid_path(_default_sock).unlink(missing_ok=True)
        sys.exit(0)

    console.print("[dim]A enviar comando de shutdown...[/dim]")
    if server.stop_server(_default_sock):
        console.print("[bold green]\u2713 Server parado.[/bold green]")
    else:
        console.print("[bold red]\u2717 Não foi possível parar o server.[/bold red]")
        sys.exit(1)


@cli.command("info")
def info_cmd() -> None:
    """Informações de configuração e ambiente."""
    console.print(
        Panel.fit(
            "[bold]texture2d info[/bold] — ambiente de execução e cache Hugging Face",
            border_style="blue",
        )
    )

    data = get_system_info()
    t = Table(title="[bold blue]Sistema", box=box.ROUNDED)
    t.add_column("Componente", style="cyan", no_wrap=True)
    t.add_column("Valor", style="green")

    t.add_row("Modelo (default)", default_model_id())
    t.add_row("Backend", "Stable Diffusion v1.5 + circular padding")
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
    t.add_row("Saída padrão", str(DEFAULT_TEXTURE_DIR.resolve()))
    t.add_row("Presets disponíveis", str(len(TEXTURE_PRESETS)))

    from .hardware import detect_hardware_profile, hw_auto_enabled

    _hwp = detect_hardware_profile()
    _state = "" if hw_auto_enabled() else " [yellow](desligado: TEXTURE2D_HW_AUTO=0)[/yellow]"
    t.add_row("Perfil hardware (auto)", f"{_hwp.summary()}{_state}")

    console.print(t)


cli.add_command(validate_tileable_cmd)


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
