#!/usr/bin/env python3
"""
Text2Sound — CLI principal (text-to-audio).
"""

import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

try:
    from importlib.metadata import version as _pkg_version

    _CLI_VERSION = _pkg_version("text2sound")
except Exception:
    from text2sound import __version__ as _CLI_VERSION

from click.core import ParameterSource
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

from gamedev_shared.hf import get_hf_token, hf_home_display_rich
from gamedev_shared.profiler.session import ProfilerSession, profile_span

from .audio_processor import SUPPORTED_FORMATS, save_audio
from .cli_rich import RICH_CLICK, click  # noqa: F401 — rich-click antes dos comandos
from .generator import (
    DEFAULT_CFG_SCALE,
    DEFAULT_DURATION,
    DEFAULT_SAMPLER,
    DEFAULT_SIGMA_MAX,
    DEFAULT_SIGMA_MIN,
    DEFAULT_STEPS,
    AudioGenerator,
)
from .models import (
    ModelSpec,
    ProfileName,
    get_spec,
    resolve_model_from_profile,
)
from .presets import AUDIO_PRESETS, get_preset, list_presets
from .utils import format_bytes, format_duration, generate_output_path, resolve_effective_seed

console = Console()

DEFAULT_OUTPUT_DIR = Path("outputs")
DEFAULT_AUDIO_DIR = DEFAULT_OUTPUT_DIR / "audio"


def ensure_dirs() -> None:
    DEFAULT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def _quiet_third_party_tqdm(verbose: bool) -> Generator[None, None, None]:
    """Reduz ruído de barras tqdm (Hub/weights) quando não está em modo verbose."""
    if verbose:
        yield
        return
    prev = os.environ.get("TQDM_DISABLE")
    os.environ["TQDM_DISABLE"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("TQDM_DISABLE", None)
        else:
            os.environ["TQDM_DISABLE"] = prev


def _apply_spec_inference_defaults(
    ctx: click.Context,
    spec: ModelSpec,
    duration: float,
    steps: int,
    cfg_scale: float,
    sigma_min: float,
    sigma_max: float,
    sampler: str,
) -> tuple[float, int, float, float, float, str]:
    """Aplica defaults do ``ModelSpec`` quando o parâmetro veio do default do Click."""
    if ctx.get_parameter_source("duration") == ParameterSource.DEFAULT:
        duration = min(duration, spec.max_seconds)
    if ctx.get_parameter_source("steps") == ParameterSource.DEFAULT:
        steps = spec.default_steps
    if ctx.get_parameter_source("cfg_scale") == ParameterSource.DEFAULT:
        cfg_scale = spec.default_cfg
    if ctx.get_parameter_source("sigma_min") == ParameterSource.DEFAULT:
        sigma_min = spec.default_sigma_min
    if ctx.get_parameter_source("sigma_max") == ParameterSource.DEFAULT:
        sigma_max = spec.default_sigma_max
    if ctx.get_parameter_source("sampler") == ParameterSource.DEFAULT:
        sampler = spec.default_sampler
    return duration, steps, cfg_scale, sigma_min, sigma_max, sampler


@click.group()
@click.version_option(version=_CLI_VERSION, prog_name="text2sound")
@click.option("--verbose", "-v", is_flag=True, help="Logs detalhados")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Text2Sound — text-to-audio · Open 1.0 (música) ou Open Small (efeitos), 44.1 kHz."""
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
    help="Raiz do projeto do jogo (cria .cursor/skills/text2sound/)",
)
@click.option("--force", is_flag=True, help="Sobrescrever SKILL.md existente")
def skill_install_cmd(target: Path, force: bool) -> None:
    """Copia SKILL.md para .cursor/skills/text2sound/."""
    try:
        from gamedev_shared.skill_install import install_agent_skill

        skill_dir = Path(__file__).parent / "cursor_skill"
        dest = install_agent_skill(
            target,
            tool_name="text2sound",
            skill_source=skill_dir,
            force=force,
        )
    except ImportError:
        raise click.ClickException("gamedev-shared não encontrado — instale com pip install -e ../Shared") from None
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
@click.option(
    "--profile",
    type=click.Choice(["music", "effects"]),
    default="music",
    show_default=True,
    help="music = Open 1.0 (até ~47s); effects = Open Small (até ~11s, efeitos)",
)
@click.option("--output", "-o", type=click.Path(), help="Ficheiro de saída")
@click.option(
    "--duration",
    "-d",
    default=DEFAULT_DURATION,
    show_default=True,
    type=float,
    help="Duração em segundos (máx. depende do modelo: 47 música, 11 efeitos)",
)
@click.option(
    "--steps",
    "-s",
    default=DEFAULT_STEPS,
    show_default=True,
    type=click.IntRange(8, 150),
    help="Passos de difusão (8+; Open Small usa ~8 por padrão com --profile effects)",
)
@click.option(
    "--cfg-scale",
    "-c",
    default=DEFAULT_CFG_SCALE,
    show_default=True,
    type=click.FloatRange(1.0, 15.0),
    help="Guidance scale (CFG)",
)
@click.option("--seed", type=int, default=None, help="Seed (None = aleatório)")
@click.option(
    "--format",
    "-f",
    "fmt",
    default="wav",
    show_default=True,
    type=click.Choice(list(SUPPORTED_FORMATS), case_sensitive=False),
    help="Formato de saída",
)
@click.option(
    "--preset",
    "-p",
    default=None,
    type=click.Choice(["None", *list_presets()], case_sensitive=False),
    help="Preset de áudio",
)
@click.option(
    "--sigma-min",
    default=DEFAULT_SIGMA_MIN,
    show_default=True,
    type=float,
    help="Sigma mínimo (noise schedule)",
)
@click.option(
    "--sigma-max",
    default=DEFAULT_SIGMA_MAX,
    show_default=True,
    type=float,
    help="Sigma máximo (noise schedule)",
)
@click.option(
    "--sampler",
    default=DEFAULT_SAMPLER,
    show_default=True,
    help="Tipo de sampler",
)
@click.option(
    "--trim/--no-trim",
    default=True,
    show_default=True,
    help="Remover silêncio no início e no fim do clip",
)
@click.option(
    "--model",
    "-m",
    "model_id",
    default=None,
    help=("Modelo: ID HF ou alias (music, full, effects, small, sfx). Tem prioridade sobre --profile."),
)
@click.option(
    "--half/--no-half",
    "half_precision",
    default=None,
    help="Float16 (auto: ativado em GPUs <= 8 GB VRAM)",
)
@click.option(
    "--low-vram",
    is_flag=True,
    default=False,
    help="Enable low-VRAM mode (auto float16, reduced settings)",
)
@click.option(
    "--verbose",
    "-v",
    "verbose_flag",
    is_flag=True,
    help="Logs detalhados",
)
@click.option(
    "--profiler",
    "profiler_flag",
    is_flag=True,
    help="Gravar métricas de performance (perf DB + JSONL)",
)
@click.pass_context
def generate_cmd(
    ctx: click.Context,
    prompt: str,
    profile: ProfileName,
    output: str | None,
    duration: float,
    steps: int,
    cfg_scale: float,
    seed: int | None,
    fmt: str,
    preset: str | None,
    sigma_min: float,
    sigma_max: float,
    sampler: str,
    trim: bool,
    model_id: str | None,
    half_precision: bool | None,
    low_vram: bool,
    verbose_flag: bool,
    profiler_flag: bool,
) -> None:
    """Gera áudio a partir do PROMPT de texto."""
    verbose = bool(ctx.obj.get("VERBOSE")) or verbose_flag

    if preset and preset != "None":
        try:
            preset_data = get_preset(preset)
        except KeyError as e:
            raise click.ClickException(str(e)) from e
        prompt = f"{prompt}, {preset_data['prompt']}" if prompt.strip() else preset_data["prompt"]
        duration = preset_data.get("duration", duration)
        steps = preset_data.get("steps", steps)
        cfg_scale = preset_data.get("cfg_scale", cfg_scale)

    try:
        resolved_model_id = resolve_model_from_profile(profile, model_id)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    spec = get_spec(resolved_model_id)

    if not preset or preset == "None":
        duration, steps, cfg_scale, sigma_min, sigma_max, sampler = _apply_spec_inference_defaults(
            ctx,
            spec,
            duration,
            steps,
            cfg_scale,
            sigma_min,
            sigma_max,
            sampler,
        )
    elif duration > spec.max_seconds:
        raise click.ClickException(
            f"Duração {duration}s excede o máximo deste modelo ({spec.max_seconds}s). Use --profile music ou reduza -d."
        )

    if duration < 0.5 or duration > spec.max_seconds:
        raise click.ClickException(f"Duração deve estar entre 0.5 e {spec.max_seconds}s para {spec.hf_id}.")

    effective_seed = resolve_effective_seed(seed)

    table = Table(show_header=False, box=box.SIMPLE)
    table.add_row("[bold]Perfil[/bold]", profile)
    table.add_row("[bold]Modelo[/bold]", f"[cyan]{resolved_model_id}[/cyan]")
    table.add_row("[bold]Prompt[/bold]", f"[cyan]{prompt}[/cyan]")
    table.add_row("[bold]Duração[/bold]", f"{duration}s ({format_duration(duration)})")
    table.add_row("[bold]Passos[/bold]", str(steps))
    table.add_row("[bold]CFG Scale[/bold]", str(cfg_scale))
    table.add_row("[bold]Formato[/bold]", fmt.upper())
    table.add_row("[bold]Sampler[/bold]", sampler)
    if seed is not None:
        table.add_row("[bold]Seed[/bold]", str(seed))
    else:
        table.add_row("[bold]Seed[/bold]", f"[dim]aleatório → {effective_seed}[/dim]")
    if preset and preset != "None":
        table.add_row("[bold]Preset[/bold]", preset)
    console.print(Panel(table, title="[bold green]Configuração", border_style="green"))

    _prof_params = {
        "profile": profile,
        "duration": duration,
        "steps": steps,
        "cfg_scale": cfg_scale,
        "sampler": sampler,
        "sigma_min": sigma_min,
        "sigma_max": sigma_max,
        "trim": trim,
    }
    with ProfilerSession(
        "text2sound",
        cli_profile=profiler_flag,
        model_id=resolved_model_id,
        params=_prof_params,
    ):
        try:
            gen = AudioGenerator.get_instance(
                model_id=resolved_model_id,
                half_precision=half_precision,
                low_vram=low_vram,
            )

            if output is None:
                ensure_dirs()
                out_path = generate_output_path(prompt, DEFAULT_AUDIO_DIR, fmt)
            else:
                out_path = Path(output)

            start = time.time()
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("[cyan]Carregando modelo...", total=None)
                with profile_span("load"), _quiet_third_party_tqdm(verbose):
                    gen.load()
                progress.update(task, description="[cyan]Gerando áudio...")

                with profile_span("generate"), _quiet_third_party_tqdm(verbose):
                    result = gen.generate(
                        prompt=prompt,
                        duration=duration,
                        steps=steps,
                        cfg_scale=cfg_scale,
                        seed=effective_seed,
                        sigma_min=sigma_min,
                        sigma_max=sigma_max,
                        sampler_type=sampler,
                    )

                progress.update(task, description="[cyan]Processando e gravando...")

                metadata = {
                    "prompt": prompt,
                    "profile": profile,
                    "model_id": resolved_model_id,
                    "duration": duration,
                    "steps": steps,
                    "cfg_scale": cfg_scale,
                    "seed": seed,
                    "seed_effective": effective_seed,
                    "sampler": sampler,
                    "sigma_min": sigma_min,
                    "sigma_max": sigma_max,
                    "trim": trim,
                    "half_precision": half_precision,
                    "half_precision_effective": gen.half_precision,
                    "format": fmt,
                    "sample_rate": result.sample_rate,
                    "device": result.device,
                    "text2sound_version": _CLI_VERSION,
                }
                if preset and preset != "None":
                    metadata["preset"] = preset

                with profile_span("save"):
                    saved = save_audio(
                        audio=result.audio,
                        sample_rate=result.sample_rate,
                        output_path=out_path,
                        fmt=fmt,
                        trim=trim,
                        metadata=metadata,
                    )

                progress.update(task, description="[green]Concluído")

            elapsed = time.time() - start
            try:
                sz = format_bytes(saved.stat().st_size)
            except OSError:
                sz = "?"

            console.print(Rule("[bold green]Resultado", style="green"))
            console.print(f"[bold green]\u2713[/bold green] Áudio: [cyan]{saved.resolve()}[/cyan] [dim]({sz})[/dim]")
            console.print(
                f"[dim]Sample rate: {result.sample_rate} Hz · "
                f"Duração: {format_duration(duration)} · "
                f"Seed: {effective_seed}[/dim]"
            )
            console.print(f"[dim]Tempo: {elapsed:.1f}s[/dim]")

        except click.ClickException:
            raise
        except Exception as e:
            console.print(f"\n[bold red]\u2717 Erro:[/bold red] {e}")
            if verbose:
                console.print_exception()
            sys.exit(1)


@cli.command("batch")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--profile",
    type=click.Choice(["music", "effects"]),
    default="music",
    show_default=True,
    help="music ou effects (Open Small, até ~11s)",
)
@click.option(
    "--output-dir",
    "-O",
    type=click.Path(path_type=Path),
    default=None,
    help="Pasta de saída (em generate, -d é duração — aqui use -O)",
)
@click.option("--preset", "-p", default=None, help="Preset aplicado a todos os prompts")
@click.option(
    "--duration",
    default=DEFAULT_DURATION,
    type=float,
    help="Duração por clip (máx. depende do modelo)",
)
@click.option("--steps", "-s", default=DEFAULT_STEPS, type=click.IntRange(8, 150))
@click.option("--cfg-scale", "-c", default=DEFAULT_CFG_SCALE, type=float)
@click.option("--sigma-min", default=DEFAULT_SIGMA_MIN, type=float)
@click.option("--sigma-max", default=DEFAULT_SIGMA_MAX, type=float)
@click.option("--sampler", default=DEFAULT_SAMPLER, type=str)
@click.option("--format", "-f", "fmt", default="wav", type=click.Choice(list(SUPPORTED_FORMATS)))
@click.option("--trim/--no-trim", default=True)
@click.option("--model", "-m", "model_id", default=None, help="ID HF ou alias (music, effects, small, …)")
@click.option(
    "--half/--no-half",
    "half_precision",
    default=None,
    help="Float16 (auto em GPUs modestas)",
)
@click.option(
    "--low-vram",
    is_flag=True,
    default=False,
    help="Enable low-VRAM mode (auto float16, reduced settings)",
)
@click.option(
    "--seed",
    type=int,
    default=None,
    help="Seed base por clip: usa seed, seed+1, seed+2, … (omitir = aleatório por linha)",
)
@click.pass_context
def batch_cmd(
    ctx: click.Context,
    file: Path,
    profile: ProfileName,
    output_dir: Path | None,
    preset: str | None,
    duration: float,
    steps: int,
    cfg_scale: float,
    sigma_min: float,
    sigma_max: float,
    sampler: str,
    fmt: str,
    trim: bool,
    model_id: str | None,
    half_precision: bool | None,
    low_vram: bool,
    seed: int | None,
) -> None:
    """Gera áudios em batch a partir de um ficheiro de prompts (um por linha)."""
    verbose = bool(ctx.obj.get("VERBOSE"))
    prompts = [
        line.strip()
        for line in file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    if not prompts:
        raise click.ClickException("Ficheiro sem prompts válidos.")

    if preset and preset != "None":
        try:
            preset_data = get_preset(preset)
        except KeyError as e:
            raise click.ClickException(str(e)) from e
        duration = preset_data.get("duration", duration)
        steps = preset_data.get("steps", steps)
        cfg_scale = preset_data.get("cfg_scale", cfg_scale)

    try:
        resolved_model_id = resolve_model_from_profile(profile, model_id)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    spec = get_spec(resolved_model_id)

    if not preset or preset == "None":
        duration, steps, cfg_scale, sigma_min, sigma_max, sampler = _apply_spec_inference_defaults(
            ctx,
            spec,
            duration,
            steps,
            cfg_scale,
            sigma_min,
            sigma_max,
            sampler,
        )
    elif duration > spec.max_seconds:
        raise click.ClickException(f"Duração {duration}s excede o máximo deste modelo ({spec.max_seconds}s).")

    if duration < 0.5 or duration > spec.max_seconds:
        raise click.ClickException(f"Duração deve estar entre 0.5 e {spec.max_seconds}s para {spec.hf_id}.")

    console.print(f"[bold]Batch:[/bold] {len(prompts)} prompts de [cyan]{file}[/cyan]")
    console.print(f"[dim]Modelo: {resolved_model_id} · perfil: {profile}[/dim]")

    out = output_dir or DEFAULT_AUDIO_DIR
    out.mkdir(parents=True, exist_ok=True)

    gen = AudioGenerator.get_instance(
        model_id=resolved_model_id,
        half_precision=half_precision,
        low_vram=low_vram,
    )
    with _quiet_third_party_tqdm(verbose):
        gen.load()

    ok_count = 0
    for idx, prompt_text in enumerate(prompts):
        full_prompt = f"{prompt_text}, {preset_data['prompt']}" if preset and preset != "None" else prompt_text

        line_seed = int(seed) + idx if seed is not None else resolve_effective_seed(None)

        try:
            with _quiet_third_party_tqdm(verbose):
                result = gen.generate(
                    prompt=full_prompt,
                    duration=duration,
                    steps=steps,
                    cfg_scale=cfg_scale,
                    seed=line_seed,
                    sigma_min=sigma_min,
                    sigma_max=sigma_max,
                    sampler_type=sampler,
                )

            out_path = generate_output_path(prompt_text, out, fmt)
            metadata = {
                "prompt": full_prompt,
                "profile": profile,
                "model_id": resolved_model_id,
                "duration": duration,
                "steps": steps,
                "cfg_scale": cfg_scale,
                "seed": seed,
                "seed_effective": line_seed,
                "sampler": sampler,
                "sigma_min": sigma_min,
                "sigma_max": sigma_max,
                "trim": trim,
                "half_precision": half_precision,
                "half_precision_effective": gen.half_precision,
                "format": fmt,
                "sample_rate": result.sample_rate,
                "device": result.device,
                "batch_index": idx,
                "text2sound_version": _CLI_VERSION,
            }
            if preset and preset != "None":
                metadata["preset"] = preset

            saved = save_audio(
                audio=result.audio,
                sample_rate=result.sample_rate,
                output_path=out_path,
                fmt=fmt,
                trim=trim,
                metadata=metadata,
            )
            ok_count += 1
            console.print(f"  [green]\u2713[/green] {idx + 1}/{len(prompts)}: [cyan]{saved.name}[/cyan]")
        except Exception as e:
            console.print(f"  [red]\u2717[/red] {idx + 1}/{len(prompts)}: {e}")

    console.print(
        Panel(
            f"[bold]{ok_count}/{len(prompts)}[/bold] áudios gerados em [cyan]{out.resolve()}[/cyan]",
            title="[bold green]Batch concluído",
            border_style="green",
        )
    )


@cli.command("presets")
def presets_cmd() -> None:
    """Lista presets de áudio disponíveis."""
    t = Table(title="[bold blue]Presets de Áudio (Game Dev)", box=box.ROUNDED)
    t.add_column("Nome", style="cyan", no_wrap=True)
    t.add_column("Prompt", style="white", max_width=55)
    t.add_column("Duração", style="green", justify="right")
    t.add_column("Steps", style="green", justify="right")
    t.add_column("CFG", style="green", justify="right")

    for name in list_presets():
        p = AUDIO_PRESETS[name]
        prompt_text = p["prompt"]
        if len(prompt_text) > 55:
            prompt_text = prompt_text[:52] + "..."
        t.add_row(
            name,
            prompt_text,
            f"{p['duration']}s",
            str(p["steps"]),
            str(p["cfg_scale"]),
        )
    console.print(t)


@cli.command("info")
def info_cmd() -> None:
    """Informações de configuração, ambiente e GPU."""
    console.print(
        Panel.fit(
            "[bold]text2sound info[/bold] — configuração e ambiente",
            border_style="blue",
        )
    )

    t = Table(title="[bold blue]Configuração", box=box.ROUNDED)
    t.add_column("Item", style="cyan", no_wrap=True)
    t.add_column("Valor", style="green")

    t.add_row("Música (default)", "stabilityai/stable-audio-open-1.0 — até ~47s")
    t.add_row("Efeitos", "stabilityai/stable-audio-open-small — até ~11s, steps~8, pingpong")
    t.add_row("Sample rate", "44100 Hz")
    t.add_row("Canais", "Estéreo (2)")

    token = get_hf_token()
    t.add_row("HF Token", "[green]configurado[/green]" if token else "[red]não definido[/red]")
    t.add_row(
        "HF_HOME (cache Hub)",
        hf_home_display_rich(
            default_label="[dim]~/.cache/huggingface (padrão)[/dim]",
        ),
    )
    t.add_row("Saída padrão", str(DEFAULT_AUDIO_DIR.resolve()))
    t.add_row("Presets disponíveis", str(len(AUDIO_PRESETS)))
    t.add_row("Python", sys.version.split()[0])

    try:
        import torch

        t.add_row("PyTorch", torch.__version__)
        t.add_row(
            "CUDA",
            f"{torch.version.cuda} (GPU: {torch.cuda.get_device_name(0)})"
            if torch.cuda.is_available()
            else "[yellow]não disponível (CPU)[/yellow]",
        )

        if torch.cuda.is_available():
            try:
                from gamedev_shared.gpu import get_gpu_info

                gpus = get_gpu_info()
                for gpu in gpus:
                    t.add_row(
                        f"GPU {gpu.get('index', '?')}",
                        f"{gpu.get('name', '?')} — "
                        f"{gpu.get('memory_free_str', '?')} livres / "
                        f"{gpu.get('memory_total_str', '?')} total",
                    )
            except (ImportError, Exception):
                pass
    except ImportError:
        t.add_row("PyTorch", "[red]não instalado[/red]")

    console.print(t)


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
