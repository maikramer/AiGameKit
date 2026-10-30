#!/usr/bin/env python3
"""Motion3D — CLI text-to-motion (SprongMotion-14M) · NPZ + BVH + GLB (bpy).

CPU only: the model is a 33 MB GGUF run by a vendored C11 runtime. No torch,
no GPU, no vramd.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from click.core import ParameterSource
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aigamekit_shared.progress import STATUS_OK, TOOL_MOTION3D, emit_result

from . import __version__
from .cli_rich import RICH_CLICK, click  # noqa: F401
from .generator import DEFAULT_ATTEMPTS, MotionGenerator
from .skeleton import DEFAULT_FPS, MAX_SECONDS, MIN_SECONDS, SOURCE_TRACK
from .sprong import GenerateOptions, RuntimeBuildError, SprongError, ensure_runtime, runtime_info
from .weights import HF_REPO, MODEL_FILE, ModelIntegrityError, ensure_model_path, model_info

console = Console()

QUALITY_TIERS = ("fast", "low", "medium", "high", "highest")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="motion3d")
@click.option("--verbose", "-v", is_flag=True, help="Logs detalhados")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Text-to-motion (SprongMotion-14M, CPU) → NPZ joints @ 20fps / BVH / GLB."""
    ctx.ensure_object(dict)
    ctx.obj["VERBOSE"] = verbose
    if verbose:
        try:
            from aigamekit_shared.logging import configure_logging

            configure_logging("motion3d", level="DEBUG")
        except Exception:
            pass


def _quality_defaults(quality: str, category: str | None) -> dict[str, object]:
    """Soft defaults from the shared QualityEngine (empty when unavailable)."""
    try:
        from aigamekit_shared.quality import QualityEngine

        resolved = QualityEngine().resolve(tool="motion3d", quality=quality, category=category)
        return dict(resolved.params)
    except Exception:
        return {}


def _option_from_cli(
    ctx: click.Context,
    name: str,
    value: object,
    quality_params: dict[str, object],
    *,
    cast: type = float,
) -> object:
    """Use the CLI value when the user typed it, else the quality tier's default."""
    explicit = ctx.get_parameter_source(name) not in (None, ParameterSource.DEFAULT)
    if not explicit and name in quality_params and quality_params[name] is not None:
        try:
            return cast(quality_params[name])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return value
    return value


def _generator(model: str | None, threads: int | None, json_output: bool) -> MotionGenerator:
    status = None if json_output else (lambda message: console.print(f"[dim]{message}[/dim]"))
    return MotionGenerator.get_instance(model, thread_count=threads, on_status=status)


def _emit_error(tool_message: str, exc: Exception) -> None:
    console.print(f"[red]{tool_message}[/red] {exc}")
    raise SystemExit(1) from exc


@cli.command("generate")
@click.argument("prompt")
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    required=True,
    help="Output .npz (default), .bvh ou .glb (armature source, bpy)",
)
@click.option(
    "--duration",
    "--seconds",
    "duration",
    type=float,
    default=None,
    help=f"Comprimento do clip em segundos ({MIN_SECONDS}-{MAX_SECONDS:g})",
)
@click.option("--frames", "max_frames", type=int, default=None, help="Alternativa a --duration (frames @ 20fps)")
@click.option("--seed", type=int, default=None, help="Seed RNG (mesmo seed + mesmo build = clip idêntico)")
@click.option(
    "--attempts",
    type=int,
    default=DEFAULT_ATTEMPTS,
    show_default=True,
    help="Gera N seeds e escolhe a de melhor score (foot slide / penetração / jerk)",
)
@click.option("--steps", type=int, default=None, help="Passos de unmasking da camada base (1-64)")
@click.option("--residual-layers", type=int, default=None, help="Camadas residuais (0-5; 0 = rascunho rápido)")
@click.option("--text-guidance", type=float, default=None, help="CFG do texto (0-20)")
@click.option("--residual-guidance", type=float, default=None, help="CFG residual (0-20)")
@click.option("--temperature", type=float, default=None, help="Temperatura de amostragem (0.01-10)")
@click.option("--top-k", type=int, default=None, help="Amostrar dos k códigos mais prováveis (0 = todos)")
@click.option("--loop/--no-loop", "loop", default=None, help="Ciclo sem costura (walk/run/idle)")
@click.option("--foot-lock/--no-foot-lock", "foot_lock", default=None, help="Pés plantados ficam no sítio")
@click.option(
    "--in-place/--root-motion",
    "in_place",
    default=False,
    show_default=True,
    help="Clip sem viagem horizontal (o apply-rigged já o faz por defeito)",
)
@click.option("--threads", type=int, default=None, help="Threads do runtime (0 = todos os cores)")
@click.option("--model", "model_path", type=click.Path(exists=True), default=None, help="GGUF alternativo")
@click.option("--bvh", type=click.Path(), default=None, help="Escrever também o BVH do runtime")
@click.option("--also-npz", is_flag=True, help="Com -o *.glb, guardar também o NPZ irmão")
@click.option(
    "--quality",
    type=click.Choice(QUALITY_TIERS, case_sensitive=False),
    default="medium",
    show_default=True,
    help="Tier de qualidade (soft defaults de duração/steps/residual layers)",
)
@click.option("--category", type=str, default=None, help="Categoria de asset (QualityEngine)")
@click.option("--json", "json_output", is_flag=True, help="Resultado em JSON (uma linha)")
@click.pass_context
def generate_cmd(
    ctx: click.Context,
    prompt: str,
    output: str,
    duration: float | None,
    max_frames: int | None,
    seed: int | None,
    attempts: int,
    steps: int | None,
    residual_layers: int | None,
    text_guidance: float | None,
    residual_guidance: float | None,
    temperature: float | None,
    top_k: int | None,
    loop: bool | None,
    foot_lock: bool | None,
    in_place: bool,
    threads: int | None,
    model_path: str | None,
    bvh: str | None,
    also_npz: bool,
    quality: str,
    category: str | None,
    json_output: bool,
) -> None:
    """Gera movimento a partir de texto → NPZ / BVH / GLB (bpy)."""
    if not prompt.strip():
        console.print("[red]prompt vazio[/red]")
        raise SystemExit(2)

    quality_params = _quality_defaults(quality, category)
    duration = _option_from_cli(ctx, "duration", duration, quality_params, cast=float)  # type: ignore[assignment]
    attempts = _option_from_cli(ctx, "attempts", attempts, quality_params, cast=int)  # type: ignore[assignment]
    steps = _option_from_cli(ctx, "steps", steps, quality_params, cast=int)  # type: ignore[assignment]
    residual_layers = _option_from_cli(ctx, "residual_layers", residual_layers, quality_params, cast=int)  # type: ignore[assignment]
    text_guidance = _option_from_cli(ctx, "text_guidance", text_guidance, quality_params, cast=float)  # type: ignore[assignment]

    options = GenerateOptions(
        steps=steps,
        residual_layers=residual_layers,
        text_guidance=text_guidance,
        residual_guidance=residual_guidance,
        temperature=temperature,
        top_k=top_k,
        loop=loop,
        foot_lock=foot_lock,
        in_place=True if in_place else None,
    )

    generator = _generator(model_path, threads, json_output)
    started = time.perf_counter()
    try:
        saved = generator.generate(
            prompt,
            output,
            seconds=duration,
            max_frames=max_frames,
            seed=seed,
            attempts=max(1, int(attempts)),
            options=options,
            also_npz=also_npz,
            bvh=bvh,
            metadata={"quality": quality, "category": category or ""},
        )
    except (ModelIntegrityError, RuntimeBuildError, SprongError, ValueError, OSError) as exc:
        _emit_error("generate falhou:", exc)
        return
    elapsed = time.perf_counter() - started
    quality_report = generator.last_quality.as_dict() if generator.last_quality is not None else {}

    emit_result(
        id="generate",
        tool=TOOL_MOTION3D,
        status=STATUS_OK,
        phase="generate",
        output=str(saved),
        seconds=elapsed,
    )
    if json_output:
        payload = {"output": str(saved), "prompt": prompt, "seconds": elapsed, **quality_report}
        console.print(json.dumps(payload), soft_wrap=True)
        return
    console.print(
        Panel(
            f"[green]Saved[/green] [cyan]{saved}[/cyan]\n"
            f"[dim]{elapsed:.2f}s · seed {generator.last_seed} · {attempts} tentativa(s)[/dim]"
            + (f"\n[dim]score {quality_report.get('score')} · {quality_report}[/dim]" if quality_report else ""),
            title="Motion3D (SprongMotion-14M, CPU)",
            border_style="green",
        )
    )


@cli.command("export-glb")
@click.argument("npz_path", type=click.Path(exists=True))
@click.option("-o", "--output", type=click.Path(), required=True, help="Output .glb path")
@click.option("--fps", type=int, default=None, help="Bake FPS (default: fps do NPZ)")
@click.option("--clip", "clip_name", default=SOURCE_TRACK, show_default=True, help="Nome da action no source")
@click.option(
    "--in-place/--root-motion",
    "in_place",
    default=True,
    show_default=True,
    help="Clip loopable (sem viagem/yaw) vs manter root motion",
)
def export_glb_cmd(npz_path: str, output: str, fps: int | None, clip_name: str, in_place: bool) -> None:
    """NPZ → GLB source (armature SprongMotion + bake look-at)."""
    from .bpy_export import export_joints_glb
    from .npz_io import read_clip

    try:
        motion = read_clip(npz_path)
        saved = export_joints_glb(
            motion.joints,
            output,
            fps=int(fps or motion.fps or DEFAULT_FPS),
            clip_name=clip_name,
            offsets=motion.offsets,
            in_place=in_place,
        )
    except Exception as exc:
        _emit_error("export-glb falhou:", exc)
        return
    console.print(Panel(f"[green]Saved[/green] [cyan]{saved}[/cyan]", title="export-glb"))


@cli.command("apply-rigged")
@click.argument("npz_path", type=click.Path(exists=True))
@click.argument("rigged_glb", type=click.Path(exists=True))
@click.option("-o", "--output", type=click.Path(), required=True, help="GLB skinned animado")
@click.option("--clip", "clip_name", default="walk", show_default=True, help="Nome do clip de saída")
@click.option("--profile", "profile_name", default="sprong22", show_default=True, help="Perfil de retarget")
@click.option("--keep-source", type=click.Path(), default=None, help="Também escrever o GLB source intermédio")
@click.option(
    "--in-place/--root-motion",
    "in_place",
    default=True,
    show_default=True,
    help="Clip loopable (sem viagem/yaw) vs manter root motion",
)
@click.option(
    "--arm-neutral",
    type=click.Choice(["auto", "on", "off"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Calibração suave de braços: auto salta clips de braços erguidos (chop/reach)",
)
@click.option("--max-lean", "max_lean_deg", type=float, default=None, help="Limite de inclinação do torso (graus)")
@click.option(
    "--hands-together",
    "hands_together_m",
    type=float,
    default=None,
    help="Manter os pulsos a menos de N metros (prop de duas mãos: machado, cajado)",
)
@click.option("--plant-feet", is_flag=True, default=False, help="Congelar o stance na horizontal (ações paradas)")
def apply_rigged_cmd(
    npz_path: str,
    rigged_glb: str,
    output: str,
    clip_name: str,
    profile_name: str,
    keep_source: str | None,
    in_place: bool,
    arm_neutral: str,
    max_lean_deg: float | None,
    hands_together_m: float | None,
    plant_feet: bool,
) -> None:
    """NPZ → Animator3D retarget para um SkinTokens ``*_rigged.glb``."""
    from .apply_rigged import apply_npz_to_rigged

    keep = Path(keep_source) if keep_source else None
    try:
        res = apply_npz_to_rigged(
            npz_path,
            rigged_glb,
            output,
            clip_name=clip_name,
            profile_name=profile_name,
            keep_source=keep,
            in_place=in_place,
            arm_neutral=arm_neutral,
            max_lean_deg=max_lean_deg,
            hands_together_m=hands_together_m,
            plant_feet=plant_feet,
        )
    except Exception as exc:
        _emit_error("apply-rigged falhou:", exc)
        return
    rt = res["retarget"]
    console.print(
        Panel(
            f"[green]Saved[/green] [cyan]{res['output']}[/cyan]\n"
            f"clip={rt.get('clip')!r} bones={rt.get('bones_mapped')} frames={rt.get('frames')}",
            title="apply-rigged (Motion3D → Animator3D)",
            border_style="green",
        )
    )


@cli.command("pack-rigged")
@click.argument("rigged_glb", type=click.Path(exists=True))
@click.option("-o", "--output", type=click.Path(), required=True, help="GLB skinned multi-clip")
@click.option(
    "-m",
    "--motion",
    "motions",
    multiple=True,
    required=True,
    help="Clip como name=path.npz (repetível). Ex: -m walk=walk.npz -m jump=jump.npz",
)
@click.option("--profile", "profile_name", default="sprong22", show_default=True, help="Perfil de retarget")
@click.option("--keep-sources", type=click.Path(), default=None, help="Diretório para os GLBs source intermédios")
@click.option("--active", "active_clip", default=None, help="Clip como action ativa (default: primeiro -m)")
@click.option(
    "--in-place/--root-motion",
    "in_place",
    default=True,
    show_default=True,
    help="Clips loopable (sem viagem/yaw) vs manter root motion",
)
@click.option(
    "--arm-neutral",
    type=click.Choice(["auto", "on", "off"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Calibração suave de braços: auto salta clips de braços erguidos",
)
@click.option("--max-lean", "max_lean_deg", type=float, default=None, help="Limite de inclinação do torso (graus)")
@click.option("--hands-together", "hands_together_m", type=float, default=None, help="Pulsos a menos de N metros")
@click.option("--plant-feet", is_flag=True, default=False, help="Congelar o stance na horizontal")
def pack_rigged_cmd(
    rigged_glb: str,
    output: str,
    motions: tuple[str, ...],
    profile_name: str,
    keep_sources: str | None,
    active_clip: str | None,
    in_place: bool,
    arm_neutral: str,
    max_lean_deg: float | None,
    hands_together_m: float | None,
    plant_feet: bool,
) -> None:
    """Vários NPZs num só ``*_rigged.glb`` (walk/run/jump, …).

    Cada ``--motion name=path.npz`` vira uma animação glTF com nome no mesmo
    mesh/skin (export ACTIONS, mesmo contrato do Animator3D game-pack).
    """
    from .apply_rigged import apply_npzs_to_rigged

    keep = Path(keep_sources) if keep_sources else None
    try:
        res = apply_npzs_to_rigged(
            list(motions),
            rigged_glb,
            output,
            profile_name=profile_name,
            keep_sources_dir=keep,
            in_place=in_place,
            arm_neutral=arm_neutral,
            max_lean_deg=max_lean_deg,
            hands_together_m=hands_together_m,
            plant_feet=plant_feet,
            active_clip=active_clip,
        )
    except Exception as exc:
        _emit_error("pack-rigged falhou:", exc)
        return

    lines = [f"[green]Saved[/green] [cyan]{res['output']}[/cyan]", f"active={res['active']!r}"]
    for item in res["clips"]:
        if "error" in item:
            lines.append(f"  {item.get('clip')!r}: [red]{item['error']}[/red]")
        else:
            lines.append(f"  {item.get('clip')!r}: bones={item.get('bones_mapped')} frames={item.get('frames')}")
    console.print(Panel("\n".join(lines), title="pack-rigged (Motion3D → multi-clip GLB)", border_style="green"))


@cli.command("download-model")
@click.option("--force", is_flag=True, help="Voltar a descarregar mesmo com cache válida")
def download_model_cmd(force: bool) -> None:
    """Descarrega o GGUF (~33 MB) para a cache do HuggingFace e valida o SHA256."""
    try:
        path = ensure_model_path(force_download=force, on_status=lambda message: console.print(f"[dim]{message}[/dim]"))
    except ModelIntegrityError as exc:
        _emit_error("download-model falhou:", exc)
        return
    console.print(Panel(f"[green]ok[/green] [cyan]{path}[/cyan]", title=f"{HF_REPO}/{MODEL_FILE}"))


@cli.command("doctor")
@click.option("--json", "json_output", is_flag=True, help="Diagnóstico em JSON")
@click.option("--build", "build_runtime", is_flag=True, help="Forçar rebuild do runtime C")
def doctor_cmd(json_output: bool, build_runtime: bool) -> None:
    """Verifica compilador, runtime C, modelo (sha256), numpy, bpy e perfil de retarget."""
    report: dict[str, object] = {"runtime": runtime_info(), "model": model_info()}

    build_error: str | None = None
    if build_runtime:
        try:
            ensure_runtime(rebuild=True, on_status=lambda message: None if json_output else console.print(message))
        except RuntimeBuildError as exc:
            build_error = str(exc)

    imports: dict[str, str] = {}
    for module in ("numpy", "bpy", "animator3d", "huggingface_hub"):
        try:
            __import__(module)
            imports[module] = "ok"
        except Exception as exc:  # diagnostics only
            imports[module] = f"missing ({exc.__class__.__name__})"
    report["imports"] = imports

    try:
        from animator3d import retarget as rt

        rt.load_profile("sprong22")
        report["profile"] = "sprong22 ok"
    except Exception as exc:  # diagnostics only
        report["profile"] = f"missing ({exc})"
    if build_error:
        report["build_error"] = build_error

    if json_output:
        console.print(json.dumps(report, default=str), soft_wrap=True)
        return

    table = Table(title="Motion3D doctor (SprongMotion-14M)", show_header=True)
    table.add_column("Check")
    table.add_column("Estado")
    runtime = report["runtime"]
    assert isinstance(runtime, dict)
    table.add_row("runtime", f"{runtime.get('version', '?')} ({runtime.get('platform', '?')})")
    table.add_row("compilador", str(runtime.get("compiler")))
    lib_state = str(runtime.get("lib_state"))
    colour = "green" if lib_state == "ok" else "yellow"
    table.add_row("libsprongmotion", f"[{colour}]{lib_state}[/{colour}] {runtime.get('lib', '')}")
    model = report["model"]
    assert isinstance(model, dict)
    state = str(model.get("state"))
    colour = "green" if state == "ok" else "yellow"
    table.add_row("modelo", f"[{colour}]{state}[/{colour}] {model.get('path', model.get('repo'))}")
    table.add_row("sha256 esperado", str(model.get("expected_sha256", ""))[:16] + "…")
    for module, status in imports.items():
        colour = "green" if status == "ok" else "yellow"
        table.add_row(module, f"[{colour}]{status}[/{colour}]")
    table.add_row("perfil retarget", str(report.get("profile")))
    if build_error:
        table.add_row("build", f"[red]{build_error}[/red]")
    console.print(table)


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    sys.exit(main())
