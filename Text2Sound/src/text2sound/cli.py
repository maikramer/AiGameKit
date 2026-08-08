#!/usr/bin/env python3
"""
Text2Sound — CLI principal (text-to-audio).
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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

from aigamekit_shared.cli_helpers import (
    add_vramd_options,
    delegate_or_prepare,
    needed_mib_for_backend,
    prepare_gpu_exclusive,
)
from aigamekit_shared.hf import get_hf_token, hf_home_display_rich
from aigamekit_shared.profiler.session import ProfilerSession, profile_span
from aigamekit_shared.progress import STATUS_ERROR, STATUS_OK, TOOL_TEXT2SOUND, emit_progress, emit_result

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


def resolve_seamless_loop_params(
    *,
    user_seamless: bool | None,
    user_crossfade_ms: float,
    category_seamless: bool,
    category_crossfade_ms: float,
) -> tuple[bool, float]:
    """Resolve seamless-loop params with explicit-CLI > category > default precedence.

    Args:
        user_seamless: Tri-state from ``--seamless-loop`` (None = not given → defer to category).
        user_crossfade_ms: Value from ``--crossfade-ms`` (honored when loop is forced on/off).
        category_seamless: seamless_loop resolved from QualityEngine ``--category``.
        category_crossfade_ms: crossfade_ms resolved from QualityEngine/kind_info.

    Returns:
        ``(seamless_loop, crossfade_ms)`` after applying precedence. When the user
        forces the flag, ``user_crossfade_ms`` wins; when deferring, ``category_*`` wins.
    """
    if user_seamless is True:
        return True, float(user_crossfade_ms)
    if user_seamless is False:
        return False, float(user_crossfade_ms)
    return category_seamless, float(category_crossfade_ms)


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
        from aigamekit_shared.skill_install import install_agent_skill

        skill_dir = Path(__file__).parent / "cursor_skill"
        dest = install_agent_skill(
            target,
            tool_name="text2sound",
            skill_source=skill_dir,
            force=force,
        )
    except ImportError:
        raise click.ClickException("aigamekit-shared não encontrado — instale com pip install -e ../Shared") from None
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
    default="ogg",
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
    "--gpu-ids",
    "gpu_ids_str",
    default=None,
    help="IDs das GPUs para split multi-GPU (ex: '0,1')",
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
@click.option(
    "--quality",
    type=click.Choice(["fast", "low", "medium", "high", "highest"]),
    default="medium",
    show_default=True,
    help="Quality tier (resolves optimal params from QualityEngine)",
)
@click.option(
    "--category",
    type=str,
    default=None,
    help="Asset category for automatic audio tuning (e.g., weapon, humanoid)",
)
@click.option(
    "--crop/--no-crop",
    default=False,
    help="Truncate output to the requested -d duration with a fade-out. "
    "The model emits a fixed-length buffer regardless of -d.",
)
@click.option(
    "--fade-out",
    "fade_out",
    default=0.06,
    show_default=True,
    type=click.FloatRange(min=0.0, max=5.0),
    help="Linear fade-out in seconds on the tail when --crop is active (0 = hard cut).",
)
@click.option(
    "--seamless-loop/--no-seamless-loop",
    "seamless_loop_override",
    default=None,
    help="Force seamless-loop crossfade on/off (overrides --category). Default: defer to category.",
)
@click.option(
    "--crossfade-ms",
    "crossfade_ms_override",
    type=int,
    default=500,
    show_default=True,
    help="Crossfade duration in ms when seamless loop is enabled.",
)
@click.option(
    "--loop-edge-trim",
    "loop_edge_trim_s",
    type=click.FloatRange(min=0.0, max=10.0),
    default=0.0,
    show_default=True,
    help="Segundos de intro/outro musicais removidos de cada borda antes do crossfade "
    "de loop (o modelo compõe ataque inicial e fade final — num loop viram "
    "transiente repetido + dip de energia). Só com seamless loop ativo.",
)
@click.option(
    "--hw-auto/--no-hw-auto",
    "hw_auto",
    default=True,
    show_default=True,
    help="Perfil automático por VRAM (fp16, VAE chunked). TEXT2SOUND_HW_AUTO=0 também desliga.",
)
@click.option(
    "--chunked-vae/--no-chunked-vae",
    "chunked_vae",
    default=None,
    help="Força decode do VAE em chunks (auto: ligado em GPUs < 8.5 GB).",
)
# --- Negative prompt ---
@click.option(
    "--negative",
    "negative_prompt",
    default=None,
    help="Negative prompt explícito (anti-guidance). Override do default do audio_kind.",
)
@click.option(
    "--no-negative",
    "disable_negative",
    is_flag=True,
    default=False,
    help="Desliga o negative prompt default do audio_kind (geração positiva pura).",
)
# --- DSP mastering chain (pedalboard) ---
@click.option(
    "--lufs",
    "lufs_target",
    type=click.FloatRange(min=-40.0, max=0.0),
    default=None,
    help="Target LUFS integrated (EBU R128). Ativa mastering chain; substitui peak-normalize.",
)
@click.option(
    "--no-loudness",
    "disable_loudness",
    is_flag=True,
    default=False,
    help="Desliga LUFS normalization do quality tier (volta a peak-normalize legacy).",
)
@click.option(
    "--high-pass",
    "high_pass_hz",
    type=click.FloatRange(min=0.0, max=300.0),
    default=None,
    help="Filtro high-pass em Hz (0 = desligado). Remove DC offset e rumble.",
)
@click.option(
    "--compressor/--no-compressor",
    "compressor_override",
    default=None,
    help="Força compressor on/off. Default: deferred para quality tier / audio_kind.",
)
@click.option(
    "--compressor-preset",
    "compressor_preset_override",
    type=click.Choice(["punch", "glue", "master_glue", "transparent"], case_sensitive=False),
    default=None,
    help="Preset de compressor. Default: do audio_kind (punch/glue/...).",
)
@click.option(
    "--true-peak",
    "true_peak_db",
    type=click.FloatRange(min=-6.0, max=0.0),
    default=None,
    help="Teto do limiter true-peak em dB (ex.: -1.0). Protege contra clipping.",
)
@click.option(
    "--bit-depth",
    "bit_depth",
    type=click.IntRange(16, 24),
    default=16,
    show_default=True,
    help="Bit depth WAV/FLAC (16 ou 24). Sem efeito em OGG.",
)
@click.option(
    "--enhance/--no-enhance",
    "enhance_override",
    default=None,
    help="Enriquecimento determinístico de prompt (descritores + correção). "
    "Default: do quality tier (fast OFF, medium+ ON).",
)
@click.option(
    "--compile/--no-compile",
    "torch_compile",
    default=False,
    show_default=True,
    help=(
        "torch.compile no DiT (Inductor). Cold lento; útil em batch/server. "
        "Com offload model_cpu é ignorado; com group_stream usa mode=default. "
        "Env: AIGAMEKIT_TORCH_COMPILE=1."
    ),
)
@click.option(
    "--compile-mode",
    "torch_compile_mode",
    type=click.Choice(["default", "reduce-overhead", "max-autotune"]),
    default="default",
    show_default=True,
    help="Modo Inductor. reduce-overhead/max-autotune = CUDA graphs (só full-GPU).",
)
@click.option(
    "--channels-last/--no-channels-last",
    "channels_last",
    default=False,
    show_default=True,
    help="Memory format NHWC no VAE/pretransform — Ampere+ conv path.",
)
@add_vramd_options
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
    gpu_ids_str: str | None,
    verbose_flag: bool,
    profiler_flag: bool,
    quality: str,
    category: str | None,
    crop: bool,
    fade_out: float,
    seamless_loop_override: bool | None,
    crossfade_ms_override: int,
    loop_edge_trim_s: float,
    hw_auto: bool,
    chunked_vae: bool | None,
    negative_prompt: str | None,
    disable_negative: bool,
    lufs_target: float | None,
    disable_loudness: bool,
    high_pass_hz: float | None,
    compressor_override: bool | None,
    compressor_preset_override: str | None,
    true_peak_db: float | None,
    bit_depth: int,
    enhance_override: bool | None,
    torch_compile: bool,
    torch_compile_mode: str,
    channels_last: bool,
    vramd_priority: str | None,
    no_vramd: bool,
    vramd_stream: bool,
) -> None:
    """Gera áudio a partir do PROMPT de texto."""
    verbose = bool(ctx.obj.get("VERBOSE")) or verbose_flag

    gpu_ids = [int(x.strip()) for x in gpu_ids_str.split(",")] if gpu_ids_str else None

    # hw-auto: estratégia de memória por VRAM (soft — flags explícitas vencem).
    hw_profile = None
    if hw_auto:
        try:
            from .hardware import detect_hardware_profile, hw_auto_enabled

            if hw_auto_enabled():
                hw_profile = detect_hardware_profile()
        except Exception:
            hw_profile = None
    if hw_profile is not None:
        if half_precision is None:
            half_precision = hw_profile.half if hw_profile.device == "cuda" else None
        if chunked_vae is None:
            chunked_vae = hw_profile.chunked_vae
        if gpu_ids is None and hw_profile.gpu_ids:
            gpu_ids = hw_profile.gpu_ids
    elif not hw_auto and chunked_vae is None:
        chunked_vae = False

    try:
        from aigamekit_shared.gpu import warn_if_vram_occupied

        warn_if_vram_occupied()
    except ImportError:
        pass

    if preset and preset != "None":
        try:
            preset_data = get_preset(preset)
        except KeyError as e:
            raise click.ClickException(str(e)) from e
        prompt = f"{prompt}, {preset_data['prompt']}" if prompt.strip() else preset_data["prompt"]
        # Preset só preenche o que o utilizador não passou: um -d/-s/-c
        # explícito tem de vencer o preset (antes o preset sobrescrevia).
        if ctx.get_parameter_source("duration") == ParameterSource.DEFAULT:
            duration = preset_data.get("duration", duration)
        if ctx.get_parameter_source("steps") == ParameterSource.DEFAULT:
            steps = preset_data.get("steps", steps)
        if ctx.get_parameter_source("cfg_scale") == ParameterSource.DEFAULT:
            cfg_scale = preset_data.get("cfg_scale", cfg_scale)

    # QualityEngine resolution: merge quality + category params (CLI always wins)
    resolved_hints: list[str] = []
    resolved_negative_hints: list[str] = []
    trim_buffer_ms: int = 200  # default buffer
    trim_threshold_db: float = -60.0  # default threshold
    quality_audio_kind: str | None = None
    # Category-resolved seamless-loop defaults (overridden by explicit --seamless-loop below)
    category_seamless: bool = False
    category_crossfade_ms: float = 500.0
    # DSP mastering params resolved from quality tier (overridden by explicit CLI flags)
    resolved_lufs: float | None = None
    resolved_high_pass: float | None = None
    resolved_compressor: bool | None = None
    resolved_true_peak: float | None = None
    resolved_ogg_quality: float | None = None
    resolved_enhance: bool | None = None
    kind_compressor_preset: str | None = None

    try:
        from aigamekit_shared.quality import QualityEngine

        qe = QualityEngine()
        resolved = qe.resolve(tool="text2sound", quality=quality, category=category)

        # Apply resolved params only if CLI used the default
        if ctx.get_parameter_source("steps") == ParameterSource.DEFAULT and "steps" in resolved.params:
            steps = int(resolved.params["steps"])
        if ctx.get_parameter_source("cfg_scale") == ParameterSource.DEFAULT and "cfg_scale" in resolved.params:
            cfg_scale = float(resolved.params["cfg_scale"])
        if ctx.get_parameter_source("sigma_min") == ParameterSource.DEFAULT and "sigma_min" in resolved.params:
            sigma_min = float(resolved.params["sigma_min"])
        if ctx.get_parameter_source("sigma_max") == ParameterSource.DEFAULT and "sigma_max" in resolved.params:
            sigma_max = float(resolved.params["sigma_max"])
        if ctx.get_parameter_source("sampler") == ParameterSource.DEFAULT and "sampler" in resolved.params:
            sampler = str(resolved.params["sampler"])

        # DSP mastering params from the quality tier (soft — CLI flags override)
        if ctx.get_parameter_source("lufs_target") == ParameterSource.DEFAULT and "lufs_target" in resolved.params:
            resolved_lufs = float(resolved.params["lufs_target"])
        if ctx.get_parameter_source("high_pass_hz") == ParameterSource.DEFAULT and "high_pass_hz" in resolved.params:
            resolved_high_pass = float(resolved.params["high_pass_hz"])
        if (
            ctx.get_parameter_source("compressor_override") == ParameterSource.DEFAULT
            and "compressor" in resolved.params
        ):
            resolved_compressor = bool(resolved.params["compressor"])
        if ctx.get_parameter_source("true_peak_db") == ParameterSource.DEFAULT and "true_peak_db" in resolved.params:
            resolved_true_peak = float(resolved.params["true_peak_db"])
        if "ogg_quality" in resolved.params:
            resolved_ogg_quality = float(resolved.params["ogg_quality"])
        if ctx.get_parameter_source("enhance_override") == ParameterSource.DEFAULT and "enhance" in resolved.params:
            resolved_enhance = bool(resolved.params["enhance"])

        resolved_hints = resolved.prompt_hints
        resolved_negative_hints = resolved.negative_prompt_hints
        quality_audio_kind = resolved.audio_kind

        # Auto-select model from quality resolution (if set and CLI didn't override)
        if resolved.model_id is not None and ctx.get_parameter_source("model_id") == ParameterSource.DEFAULT:
            model_id = resolved.model_id

            # Determine trim buffer + compressor preset from audio_kind_info
            if quality_audio_kind:
                try:
                    kind_info = qe.audio_kind_info(quality_audio_kind)
                    trim_buffer_ms = int(kind_info.get("trim_buffer_ms", 200))
                    trim_threshold_db = float(kind_info.get("trim_threshold_db", -60.0))
                    kind_compressor_preset = kind_info.get("compressor_preset")
                    # Seamless loop from audio_kind (overridden later by --seamless-loop if given)
                    if kind_info.get("loop_hint"):
                        category_seamless = True
                        # crossfade_ms: quality profile override > audio_kind default
                        if "crossfade_ms" in resolved.params:
                            category_crossfade_ms = float(resolved.params["crossfade_ms"])
                        elif "crossfade_ms" in kind_info:
                            category_crossfade_ms = float(kind_info["crossfade_ms"])
                except KeyError:
                    pass

    except Exception:
        pass  # QualityEngine unavailable — continue with defaults

    # Precedence: explicit --seamless-loop > --category/QualityEngine > default (False)
    seamless_loop, crossfade_ms = resolve_seamless_loop_params(
        user_seamless=seamless_loop_override,
        user_crossfade_ms=crossfade_ms_override,
        category_seamless=category_seamless,
        category_crossfade_ms=category_crossfade_ms,
    )

    # --- DSP mastering: final precedence (explicit flag > resolved tier > None) ---
    # LUFS: --no-loudness wins over everything; otherwise explicit --lufs > tier.
    if disable_loudness:
        final_lufs: float | None = None
    else:
        final_lufs = lufs_target if lufs_target is not None else resolved_lufs
    # High-pass: explicit > tier.
    final_high_pass = high_pass_hz if high_pass_hz is not None else resolved_high_pass
    # True-peak: explicit > tier (only meaningful when mastering is active).
    final_true_peak = true_peak_db if true_peak_db is not None else resolved_true_peak
    # Compressor on/off: explicit override > tier (only when a preset is available).
    final_compressor = compressor_override if compressor_override is not None else resolved_compressor
    # Compressor preset: explicit override > audio_kind default.
    final_compressor_preset = compressor_preset_override or kind_compressor_preset
    # When the tier disabled compressor (fast), don't force it on just because
    # a kind preset exists — the tier's intent wins unless the user overrides.
    if resolved_compressor is False and compressor_override is None:
        final_compressor_enabled: bool | None = False
    else:
        final_compressor_enabled = final_compressor
    # OGG quality only applies to OGG output.
    final_ogg_quality = resolved_ogg_quality if fmt == "ogg" else None

    # --- Negative prompt: explicit --negative > audio_kind hint; --no-negative disables ---
    if disable_negative:
        effective_negative: str | None = None
    elif negative_prompt:
        effective_negative = negative_prompt
    elif resolved_negative_hints:
        effective_negative = ", ".join(resolved_negative_hints)
    else:
        effective_negative = None

    # --- Prompt enhancement: explicit --enhance > quality tier; deterministic local ---
    final_enhance = enhance_override if enhance_override is not None else resolved_enhance
    enhancement_meta: dict[str, Any] | None = None
    if final_enhance:
        try:
            from .prompt_enhancer import enhance_negative, enhance_prompt

            prompt, enhancement_meta = enhance_prompt(prompt, audio_kind=quality_audio_kind)
            if effective_negative:
                effective_negative = enhance_negative(effective_negative)
        except Exception:
            # Enhancement é best-effort: nunca deve partir a geração.
            enhancement_meta = None

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
    if quality != "medium":
        table.add_row("[bold]Quality[/bold]", quality)
    if category:
        table.add_row("[bold]Category[/bold]", category)
    if quality_audio_kind:
        table.add_row("[bold]Audio Kind[/bold]", quality_audio_kind)
    if seamless_loop:
        table.add_row("[bold]Seamless Loop[/bold]", f"[green]ON[/green] ({crossfade_ms:.0f}ms crossfade)")
    if effective_negative:
        table.add_row("[bold]Negative[/bold]", f"[dim]{effective_negative}[/dim]")
    if enhancement_meta:
        enh_label = enhancement_meta.get("sound_type", "?")
        enh_n = len(enhancement_meta.get("descriptors_added", []))
        table.add_row("[bold]Enhance[/bold]", f"[green]ON[/green] ({enh_label}, +{enh_n} descritores)")
    if final_lufs is not None or final_compressor_enabled or final_high_pass or final_true_peak is not None:
        dsp_parts: list[str] = []
        if final_lufs is not None:
            dsp_parts.append(f"LUFS={final_lufs:.0f}")
        if final_compressor_enabled and final_compressor_preset:
            dsp_parts.append(f"comp={final_compressor_preset}")
        elif final_compressor_enabled:
            dsp_parts.append("comp=on")
        if final_high_pass:
            dsp_parts.append(f"HP={final_high_pass:.0f}Hz")
        if final_true_peak is not None:
            dsp_parts.append(f"TP={final_true_peak:.1f}dB")
        if bit_depth != 16:
            dsp_parts.append(f"{bit_depth}-bit")
        table.add_row("[bold]Mastering[/bold]", " ".join(dsp_parts) or "[dim]off[/dim]")
    if hw_profile is not None:
        table.add_row("[bold]HW Auto[/bold]", hw_profile.summary())
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
        "seamless_loop": seamless_loop,
        "crossfade_ms": crossfade_ms,
        "negative_prompt": effective_negative,
        "lufs_target": final_lufs,
    }
    item_id = Path(output).stem if output else prompt[:40].replace(" ", "_")
    start = time.time()

    if output is not None and delegate_or_prepare(
        "text2sound",
        payload={
            "prompt": prompt,
            "output": str(Path(output).resolve()),
            "duration": duration,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "seed": effective_seed,
            "sigma_min": sigma_min,
            "sigma_max": sigma_max,
            "sampler_type": sampler,
            "negative_prompt": effective_negative,
            "half_precision": half_precision,
        },
        t_start=start,
        noun="Áudio",
        console=console,
        enabled=not no_vramd,
        priority=vramd_priority,
        stream=vramd_stream,
        gpu_ids=gpu_ids,
        memory_efficient=bool(half_precision),
    ):
        return

    prepare_gpu_exclusive(
        needed_mib=needed_mib_for_backend("text2sound", memory_efficient=bool(half_precision)),
        allow_shared=True,
        kill_others=False,
        backend="text2sound",
        quant_mode="none",
        console=console,
    )

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
                gpu_ids=gpu_ids,
                chunked_vae=chunked_vae,
                torch_compile=torch_compile,
                torch_compile_mode=torch_compile_mode,
                channels_last=channels_last,
            )

            if output is None:
                ensure_dirs()
                out_path = generate_output_path(prompt, DEFAULT_AUDIO_DIR, fmt)
            else:
                out_path = Path(output)

            item_id = out_path.stem
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("[cyan]Carregando modelo...", total=None)
                emit_progress(item_id, TOOL_TEXT2SOUND, phase="loading_model", percent=0)
                with profile_span("load"), _quiet_third_party_tqdm(verbose):
                    gen.load()

                progress.update(task, description="[cyan]Gerando áudio...")
                emit_progress(item_id, TOOL_TEXT2SOUND, phase="diffusion", percent=0)

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
                        prompt_hints=resolved_hints or None,
                        negative_prompt=effective_negative,
                    )

                emit_progress(item_id, TOOL_TEXT2SOUND, phase="diffusion", percent=100)
                progress.update(task, description="[cyan]Processando e gravando...")
                emit_progress(item_id, TOOL_TEXT2SOUND, phase="save", percent=0)

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
                if hw_profile is not None:
                    metadata["hw_profile"] = hw_profile.summary()
                if preset and preset != "None":
                    metadata["preset"] = preset
                if quality != "medium":
                    metadata["quality"] = quality
                if category:
                    metadata["category"] = category
                if quality_audio_kind:
                    metadata["audio_kind"] = quality_audio_kind
                if effective_negative:
                    metadata["negative_prompt"] = effective_negative
                if enhancement_meta:
                    metadata["prompt_enhancement"] = enhancement_meta

                with profile_span("save"):
                    saved = save_audio(
                        audio=result.audio,
                        sample_rate=result.sample_rate,
                        output_path=out_path,
                        fmt=fmt,
                        trim=trim,
                        metadata=metadata,
                        trim_buffer_ms=trim_buffer_ms,
                        trim_threshold_db=trim_threshold_db,
                        seamless_loop=seamless_loop,
                        crossfade_ms=crossfade_ms,
                        loop_edge_trim_s=loop_edge_trim_s,
                        crop_seconds=duration if crop else None,
                        fade_out_seconds=fade_out,
                        lufs_target=final_lufs,
                        high_pass_hz=final_high_pass,
                        compressor_preset=final_compressor_preset if final_compressor_enabled else None,
                        compressor_enabled=final_compressor_enabled,
                        true_peak_db=final_true_peak,
                        bit_depth=bit_depth,
                        ogg_quality=final_ogg_quality,
                    )

                emit_progress(item_id, TOOL_TEXT2SOUND, phase="save", percent=100)
                progress.update(task, description="[green]Concluído")

            elapsed = time.time() - start
            try:
                sz = format_bytes(saved.stat().st_size)
            except OSError:
                sz = "?"

            emit_result(
                item_id,
                TOOL_TEXT2SOUND,
                STATUS_OK,
                output=str(saved.resolve()),
                seconds=elapsed,
            )

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
            elapsed = time.time() - start
            emit_result(item_id, TOOL_TEXT2SOUND, STATUS_ERROR, error=str(e), seconds=elapsed)
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
@click.option("--format", "-f", "fmt", default="ogg", type=click.Choice(list(SUPPORTED_FORMATS)))
@click.option("--trim/--no-trim", default=True)
@click.option("--model", "-m", "model_id", default=None, help="ID HF ou alias (music, effects, small, …)")
@click.option(
    "--half/--no-half",
    "half_precision",
    default=None,
    help="Float16 (auto em GPUs modestas)",
)
@click.option(
    "--gpu-ids",
    "gpu_ids_str",
    default=None,
    help="IDs das GPUs para split multi-GPU (ex: '0,1')",
)
@click.option(
    "--seed",
    type=int,
    default=None,
    help="Seed base por clip: usa seed, seed+1, seed+2, … (omitir = aleatório por linha)",
)
@click.option(
    "--crop/--no-crop",
    default=False,
    help="Truncate each output to the -d duration with a fade-out.",
)
@click.option(
    "--fade-out",
    "fade_out",
    default=0.06,
    show_default=True,
    type=click.FloatRange(min=0.0, max=5.0),
    help="Linear fade-out in seconds on the tail when --crop is active.",
)
@click.option(
    "--compile/--no-compile",
    "torch_compile",
    default=False,
    show_default=True,
    help="torch.compile no DiT (Inductor).",
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
    help="channels_last NHWC no VAE/pretransform.",
)
@add_vramd_options
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
    gpu_ids_str: str | None,
    seed: int | None,
    crop: bool,
    fade_out: float,
    torch_compile: bool,
    torch_compile_mode: str,
    channels_last: bool,
    vramd_priority: str | None,
    no_vramd: bool,
    vramd_stream: bool,
) -> None:
    """Gera áudios em batch a partir de um ficheiro de prompts (um por linha)."""
    verbose = bool(ctx.obj.get("VERBOSE"))

    gpu_ids = [int(x.strip()) for x in gpu_ids_str.split(",")] if gpu_ids_str else None

    # Soft hw_auto: half_precision quando não explícito.
    hw_profile = None
    try:
        from .hardware import detect_hardware_profile, hw_auto_enabled

        if hw_auto_enabled():
            hw_profile = detect_hardware_profile()
            if half_precision is None and hw_profile.device == "cuda":
                half_precision = hw_profile.half
    except Exception:
        pass

    prompts = [
        line.strip()
        for line in file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    if not prompts:
        raise click.ClickException("Ficheiro sem prompts válidos.")

    preset_data: dict[str, Any] | None = None
    if preset and preset != "None":
        try:
            preset_data = get_preset(preset)
        except KeyError as e:
            raise click.ClickException(str(e)) from e
        if ctx.get_parameter_source("duration") == ParameterSource.DEFAULT:
            duration = preset_data.get("duration", duration)
        if ctx.get_parameter_source("steps") == ParameterSource.DEFAULT:
            steps = preset_data.get("steps", steps)
        if ctx.get_parameter_source("cfg_scale") == ParameterSource.DEFAULT:
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
    if hw_profile is not None:
        console.print(f"[dim]Hardware (auto): {hw_profile.summary()}[/dim]")

    out = output_dir or DEFAULT_AUDIO_DIR
    out.mkdir(parents=True, exist_ok=True)

    err_console = Console(stderr=True)
    pending: list[dict[str, Any]] = []
    ok_count = 0
    total = len(prompts)
    half_eff = bool(half_precision)

    for idx, prompt_text in enumerate(prompts):
        full_prompt = f"{prompt_text}, {preset_data['prompt']}" if preset_data is not None else prompt_text
        line_seed = int(seed) + idx if seed is not None else resolve_effective_seed(None)
        out_path = generate_output_path(prompt_text, out, fmt)
        item_id = out_path.stem
        item_start = time.time()

        if delegate_or_prepare(
            "text2sound",
            payload={
                "prompt": full_prompt,
                "output": str(out_path.resolve()),
                "duration": duration,
                "steps": steps,
                "cfg_scale": cfg_scale,
                "seed": line_seed,
                "sigma_min": sigma_min,
                "sigma_max": sigma_max,
                "sampler_type": sampler,
                "half_precision": half_precision,
            },
            t_start=item_start,
            noun="Áudio",
            console=err_console,
            enabled=not no_vramd,
            priority=vramd_priority or "batch",
            stream=vramd_stream,
            gpu_ids=gpu_ids,
            memory_efficient=half_eff,
        ):
            ok_count += 1
            elapsed = time.time() - item_start
            emit_result(
                item_id,
                TOOL_TEXT2SOUND,
                STATUS_OK,
                output=str(out_path.resolve()),
                seconds=elapsed,
            )
            console.print(f"  [green]\u2713[/green] {idx + 1}/{total}: [cyan]{out_path.name}[/cyan] [dim](UMS)[/dim]")
            continue

        pending.append(
            {
                "idx": idx,
                "prompt_text": prompt_text,
                "full_prompt": full_prompt,
                "line_seed": line_seed,
                "out_path": out_path,
                "item_id": item_id,
            }
        )

    if pending:
        try:
            from aigamekit_shared.gpu import warn_if_vram_occupied

            warn_if_vram_occupied()
        except ImportError:
            pass
        prepare_gpu_exclusive(
            needed_mib=needed_mib_for_backend("text2sound", memory_efficient=half_eff),
            allow_shared=True,
            kill_others=False,
            backend="text2sound",
            quant_mode="none",
            console=err_console,
        )
        gen = AudioGenerator.get_instance(
            model_id=resolved_model_id,
            half_precision=half_precision,
            gpu_ids=gpu_ids,
            torch_compile=torch_compile,
            torch_compile_mode=torch_compile_mode,
            channels_last=channels_last,
        )
        emit_progress("batch", TOOL_TEXT2SOUND, phase="loading_model", percent=0)
        with _quiet_third_party_tqdm(verbose):
            gen.load()

        for item in pending:
            idx = item["idx"]
            full_prompt = item["full_prompt"]
            line_seed = item["line_seed"]
            out_path = item["out_path"]
            item_id = item["item_id"]
            item_start = time.time()
            try:
                emit_progress(item_id, TOOL_TEXT2SOUND, phase="diffusion", percent=0)
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

                emit_progress(item_id, TOOL_TEXT2SOUND, phase="diffusion", percent=100)

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

                emit_progress(item_id, TOOL_TEXT2SOUND, phase="save", percent=0)
                saved = save_audio(
                    audio=result.audio,
                    sample_rate=result.sample_rate,
                    output_path=out_path,
                    fmt=fmt,
                    trim=trim,
                    metadata=metadata,
                    crop_seconds=duration if crop else None,
                    fade_out_seconds=fade_out,
                )
                emit_progress(item_id, TOOL_TEXT2SOUND, phase="save", percent=100)

                ok_count += 1
                elapsed = time.time() - item_start
                emit_result(
                    item_id,
                    TOOL_TEXT2SOUND,
                    STATUS_OK,
                    output=str(saved.resolve()),
                    seconds=elapsed,
                )
                console.print(f"  [green]\u2713[/green] {idx + 1}/{total}: [cyan]{saved.name}[/cyan]")
            except Exception as e:
                elapsed = time.time() - item_start
                emit_result(item_id, TOOL_TEXT2SOUND, STATUS_ERROR, error=str(e), seconds=elapsed)
                console.print(f"  [red]\u2717[/red] {idx + 1}/{total}: {e}")

    console.print(
        Panel(
            f"[bold]{ok_count}/{total}[/bold] áudios gerados em [cyan]{out.resolve()}[/cyan]",
            title="[bold green]Batch concluído",
            border_style="green",
        )
    )


@cli.command("presets")
def presets_cmd() -> None:
    """Lista presets de áudio disponíveis."""
    from aigamekit_shared.cli_tables import render_presets_table

    render_presets_table(
        console,
        title="Presets de Áudio (Game Dev)",
        presets=AUDIO_PRESETS,
        columns=[
            ("Kind", "magenta", "", lambda p: p.get("kind", "—")),
            ("Prompt", "white", "", lambda p: p["prompt"][:47] + "..." if len(p["prompt"]) > 50 else p["prompt"]),
            ("Duração", "green", "right", lambda p: f"{p['duration']}s"),
            ("Steps", "green", "right", lambda p: str(p["steps"])),
            ("CFG", "green", "right", lambda p: str(p["cfg_scale"])),
        ],
    )


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
    t.add_row("Efeitos", "stabilityai/stable-audio-open-small — até ~11s, steps~8, euler")
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
                from aigamekit_shared.gpu import get_gpu_info

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


@cli.command("serve")
@click.option(
    "--ums-worker",
    is_flag=True,
    help=(
        "Modo worker subprocesso do vramd: lê comandos JSONL do stdin (load / "
        "generate / unload / shutdown) e emite eventos no stdout. Usado pelo "
        "SubprocessWorkerPool do ModelServer — text2sound corre no seu próprio "
        "venv e o supervisor (ModelServer/.venv) coordena via JSONL."
    ),
)
def serve(ums_worker: bool) -> None:
    """Modo worker subprocesso do vramd (subprocess-per-backend).

    Sem ``--ums-worker`` não faz nada (futuro: modo server legacy).
    Com ``--ums-worker`` arranca o loop canónico
    :func:`aigamekit_shared.worker_serve.run_worker_loop` com o adapter text2sound
    local (:mod:`text2sound.worker_serve_adapter`).
    """
    from aigamekit_shared.worker_serve import run_ums_worker_cli
    from text2sound.worker_serve_adapter import Adapter

    run_ums_worker_cli(Adapter, tool_name="text2sound", ums_worker=ums_worker, console=console)


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
