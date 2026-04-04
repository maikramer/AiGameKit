#!/usr/bin/env python3
"""
Texture2D — CLI principal (texturas 2D seamless).
"""

import sys
import time
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

from gamedev_shared.hf import get_hf_token, hf_home_display_rich

from .cli_rich import RICH_CLICK, click  # noqa: F401 — rich-click antes dos comandos
from .generator import TextureGenerator, default_model_id
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
    """Texture2D — texturas 2D seamless via HF Inference API (Flux LoRA)."""
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
@click.option("--width", "-W", default=1024, show_default=True, type=int)
@click.option("--height", "-H", default=1024, show_default=True, type=int)
@click.option("--steps", "-s", default=50, show_default=True, help="Passos de inferência")
@click.option(
    "--guidance",
    "-g",
    "guidance_scale",
    default=7.5,
    show_default=True,
    help="Guidance scale",
)
@click.option("--seed", type=int, default=None, help="Seed (None = aleatório)")
@click.option(
    "--negative-prompt",
    "-n",
    "negative_prompt",
    default="",
    help="Prompt negativo",
)
@click.option(
    "--preset",
    "-p",
    default=None,
    type=click.Choice(["None", *list_presets()], case_sensitive=False),
    help="Preset de material",
)
@click.option("--cfg-scale", default=None, type=float, help="CFG scale (default = guidance)")
@click.option("--lora-strength", default=1.0, show_default=True, type=float, help="Força do LoRA")
@click.option(
    "--model",
    "-m",
    "model_id",
    default=None,
    help="ID do modelo HF (default: Flux-Seamless-Texture-LoRA)",
)
@click.option(
    "--verbose",
    "-v",
    "verbose_flag",
    is_flag=True,
    help="Logs detalhados",
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
    negative_prompt: str,
    preset: str | None,
    cfg_scale: float | None,
    lora_strength: float,
    model_id: str | None,
    verbose_flag: bool,
) -> None:
    """Gera uma textura seamless a partir do PROMPT."""
    verbose = bool(ctx.obj.get("VERBOSE")) or verbose_flag

    table = Table(show_header=False, box=box.SIMPLE)
    table.add_row("[bold]Prompt[/bold]", f"[cyan]{prompt}[/cyan]")
    table.add_row("[bold]Resolução[/bold]", f"{width}x{height}")
    table.add_row("[bold]Passos[/bold]", str(steps))
    table.add_row("[bold]Guidance[/bold]", str(guidance_scale))
    if preset and preset != "None":
        table.add_row("[bold]Preset[/bold]", preset)
    table.add_row("[bold]Modelo[/bold]", model_id or default_model_id())
    console.print(Panel(table, title="[bold green]Configuração", border_style="green"))

    try:
        gen = TextureGenerator(model_id=model_id)

        if output is None:
            ensure_dirs()
            ts = int(time.time())
            safe = "".join(c if c.isalnum() else "_" for c in prompt[:40])
            output = str(DEFAULT_TEXTURE_DIR / f"{safe}_{ts}.png")
        out_path = Path(output)

        start = time.time()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Gerando textura via API...", total=None)
            image, metadata = gen.generate(
                prompt=prompt,
                negative_prompt=negative_prompt,
                guidance_scale=guidance_scale,
                num_inference_steps=steps,
                seed=seed,
                width=width,
                height=height,
                cfg_scale=cfg_scale,
                lora_strength=lora_strength,
                preset=preset,
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

        elapsed = time.time() - start
        try:
            sz = format_bytes(saved.stat().st_size)
        except OSError:
            sz = "?"

        console.print(Rule("[bold green]Resultado", style="green"))
        console.print(f"[bold green]\u2713[/bold green] Textura: [cyan]{saved.resolve()}[/cyan] [dim]({sz})[/dim]")
        console.print(f"[dim]Seed: {metadata.get('seed', '?')}[/dim]")
        console.print(f"[dim]Tempo: {elapsed:.1f}s[/dim]")

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
            str(preset.get("num_inference_steps", 50)),
            str(preset.get("guidance_scale", 7.5)),
        )
    console.print(t)


@cli.command("batch")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-d", type=click.Path(path_type=Path), default=None)
@click.option("--preset", "-p", default=None, help="Preset aplicado a todos os prompts")
@click.option("--width", "-W", default=1024, type=int)
@click.option("--height", "-H", default=1024, type=int)
@click.option("--steps", "-s", default=50, type=int)
@click.option("--guidance", "-g", "guidance_scale", default=7.5, type=float)
@click.option("--model", "-m", "model_id", default=None)
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
) -> None:
    """Gera texturas em batch a partir de um ficheiro de prompts (um por linha)."""
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

    gen = TextureGenerator(model_id=model_id)
    base_params = {
        "guidance_scale": guidance_scale,
        "num_inference_steps": steps,
        "width": width,
        "height": height,
    }
    if preset and preset != "None":
        base_params["preset"] = preset

    from .image_processor import save_image

    ok_count = 0
    for image, metadata, idx in gen.generate_batch(prompts, base_params):
        if image is None:
            console.print(f"  [red]\u2717[/red] {idx + 1}/{len(prompts)}: {metadata.get('error', '?')}")
            continue

        ts = int(time.time())
        safe = "".join(c if c.isalnum() else "_" for c in prompts[idx][:30])
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


@cli.command("info")
def info_cmd() -> None:
    """Informações de configuração e ambiente."""
    console.print(
        Panel.fit(
            "[bold]texture2d info[/bold] — configuração e ambiente",
            border_style="blue",
        )
    )

    t = Table(title="[bold blue]Configuração", box=box.ROUNDED)
    t.add_column("Item", style="cyan", no_wrap=True)
    t.add_column("Valor", style="green")

    t.add_row("Modelo (default)", default_model_id())

    token = get_hf_token()
    t.add_row("HF Token", "[green]configurado[/green]" if token else "[red]não definido[/red]")
    t.add_row("HF_HOME (cache Hub)", hf_home_display_rich())
    t.add_row("Saída padrão", str(DEFAULT_TEXTURE_DIR.resolve()))
    t.add_row("Presets disponíveis", str(len(TEXTURE_PRESETS)))
    t.add_row("Python", sys.version.split()[0])

    console.print(t)


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
