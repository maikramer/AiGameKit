#!/usr/bin/env python3
"""
Text2D — CLI principal (text-to-2D).
"""

import atexit
import contextlib
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

from aigamekit_shared.cli_helpers import (
    add_group_offload_option,
    add_vramd_options,
    delegate_or_prepare,
    needed_mib_for_backend,
    prepare_gpu_exclusive,
)
from aigamekit_shared.hf import hf_home_display_rich
from aigamekit_shared.progress import STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, TOOL_TEXT2D, emit_progress, emit_result
from aigamekit_shared.quality import VALID_QUALITIES
from aigamekit_shared.skill_install import install_my_skill

from .cli_rich import click
from .generator import KleinFluxGenerator, _model_id, default_model_id, model_footprint_key
from .svg import SvgOptions, enhance_prompt_for_svg
from .svg.options import SVG_DETAILS, SVG_ENGINES, SVG_STYLES
from .utils.memory import format_bytes

console = Console()

DEFAULT_OUTPUT_DIR = Path("outputs")
DEFAULT_IMAGE_DIR = DEFAULT_OUTPUT_DIR / "images"


def _image_format_for(path: Path | str) -> str:
    """Formato PIL a partir da extensão; desconhecidas caem em PNG.

    ``save_image`` usa ``format=`` explícito: sem este mapa um ``-o foo.webp``
    gravava bytes PNG dentro de um ficheiro ``.webp`` (o mesmo bug já corrigido
    no adapter do worker).
    """
    ext = Path(path).suffix.lower().lstrip(".")
    return {"jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}.get(ext, "PNG")


def ensure_dirs() -> None:
    DEFAULT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def _svg_paths_for_raster(primary_svg: Path, num_images: int) -> list[Path]:
    """``foo.svg``, ``foo_1.svg``, … para ``-n > 1``."""

    paths = [primary_svg]
    for idx in range(1, max(1, int(num_images))):
        paths.append(primary_svg.with_name(f"{primary_svg.stem}_{idx}{primary_svg.suffix}"))
    return paths


def _svg_summary(result: Any, *, engine_hint: str | None = None) -> dict[str, Any]:
    """Resumo uniforme para resultados in-process (SvgResult) e vramd (path)."""

    from .svg.options import SvgResult
    from .svg.validate import count_svg_shapes_file

    if isinstance(result, SvgResult):
        return {
            "path": str(result.path),
            "engine": result.engine,
            "path_count": result.path_count,
            "bytes": result.bytes,
            "seconds": result.seconds,
            "warning": result.warning,
        }

    path = Path(result)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {
        "path": str(path),
        "engine": engine_hint or "starvector",
        "path_count": count_svg_shapes_file(path),
        "bytes": size,
        "seconds": None,
        "warning": None,
    }


def _print_svg_results(results: list[dict[str, Any]]) -> None:
    for item in results:
        size = format_bytes(int(item["bytes"])) if item.get("bytes") else "?"
        engine = item.get("engine") or "?"
        secs = f" · {item['seconds']:.1f}s" if item.get("seconds") is not None else ""
        console.print(
            f"[bold green]✓[/bold green] SVG: [cyan]{item['path']}[/cyan] "
            f"[dim]({engine}, {item['path_count']} paths, {size}{secs})[/dim]"
        )
        if item.get("warning"):
            console.print(f"[yellow]⚠ SVG: {item['warning']}[/yellow]")


def _svg_stage(
    images: list[Path],
    svg_paths: list[Path],
    opts: SvgOptions,
    *,
    prompt: str | None,
    seed: int | None,
    category: str | None,
    enabled: bool,
    priority: str | None,
    stream: bool,
    gpu_ids: list[int] | None,
    verbose: bool,
    engine: Any | None = None,
) -> list[dict[str, Any]]:
    """Corre a fase SVG (VTracer local ou StarVector vramd/in-process)."""

    from dataclasses import replace

    from .svg.api import vectorize_file
    from .svg.engines import get_engine, resolve_engine, starvector_available

    pairs = [
        (Path(image), Path(svg))
        for image, svg in zip(images, svg_paths, strict=True)
        if Path(image).is_file()
    ]
    if not pairs:
        return []

    opts.validate()
    resolved = resolve_engine(opts.engine, category=category)
    if resolved == "starvector" and opts.engine == "auto" and not starvector_available(require_cached=True):
        console.print("[yellow]SVG: StarVector não está cacheado — a usar VTracer.[/yellow]")
        resolved = "vtracer"

    results: list[dict[str, Any]] = []
    if resolved == "vtracer":
        for image, svg_path in pairs:
            result = vectorize_file(
                image,
                svg_path,
                replace(opts, engine="vtracer"),
                prompt=prompt,
                seed=seed,
                verbose=verbose,
            )
            results.append(_svg_summary(result))
        return results

    # StarVector: tenta o backend text2svg, depois in-process.
    remaining = list(pairs)
    if enabled:
        from aigamekit_shared.cli_helpers import try_vramd_delegation

        from .svg.vramd import build_vectorize_request

        for image, svg_path in list(pairs):
            payload = build_vectorize_request(
                image=image,
                output=svg_path,
                opts=replace(opts, engine="starvector"),
                gpu_ids=gpu_ids,
            )
            handled = try_vramd_delegation(
                "text2svg",
                payload,
                t_start=time.time(),
                noun="SVG",
                console=console,
                enabled=True,
                priority=priority,
                stream=stream,
            )
            if not handled:
                break
            results.append(_svg_summary(svg_path, engine_hint="starvector"))
            remaining.remove((image, svg_path))

    if remaining:
        engine_obj = engine
        owns_engine = engine_obj is None
        try:
            if owns_engine and opts.device != "cpu":
                from aigamekit_shared.cli_helpers import (
                    needed_mib_for_backend,
                    prepare_gpu_exclusive,
                )

                prepare_gpu_exclusive(
                    needed_mib=needed_mib_for_backend("text2svg", quant_mode="none"),
                    allow_shared=True,
                    kill_others=False,
                    backend="text2svg",
                    quant_mode="none",
                    console=console,
                )
            engine_obj = engine_obj or get_engine(
                "starvector",
                model_id=opts.model_id,
                device=opts.device,
                max_tokens=opts.max_tokens,
                verbose=verbose,
            )
            engine_obj.warmup()
            for image, svg_path in remaining:
                result = vectorize_file(
                    image,
                    svg_path,
                    replace(opts, engine="starvector"),
                    engine=engine_obj,
                    prompt=prompt,
                    seed=seed,
                    verbose=verbose,
                )
                results.append(_svg_summary(result))
        except Exception as exc:
            if opts.engine == "auto" and not opts.strict:
                console.print(f"[yellow]SVG: StarVector falhou ({exc}); a usar VTracer.[/yellow]")
                for image, svg_path in remaining:
                    if svg_path.is_file():
                        continue
                    result = vectorize_file(
                        image,
                        svg_path,
                        replace(opts, engine="vtracer"),
                        prompt=prompt,
                        seed=seed,
                        verbose=verbose,
                    )
                    results.append(_svg_summary(result))
            else:
                raise
        finally:
            if owns_engine and engine_obj is not None:
                engine_obj.unload()
    return results


def _batch_svg_entries(
    item_id: str,
    raster_path: Path,
    svg_path: Path,
    num_images: int,
    opts: SvgOptions,
    *,
    prompt: str | None,
    seed: int | None,
    category: str | None,
) -> list[dict[str, Any]]:
    """Uma entrada por imagem existente (``-n > 1`` → ``_1``, ``_2``…)."""

    entries: list[dict[str, Any]] = []
    for idx in range(max(1, int(num_images))):
        raster = (
            raster_path
            if idx == 0
            else raster_path.with_name(f"{raster_path.stem}_{idx}{raster_path.suffix}")
        )
        if not raster.is_file():
            continue
        svg = (
            svg_path
            if idx == 0
            else svg_path.with_name(f"{svg_path.stem}_{idx}{svg_path.suffix}")
        )
        entries.append(
            {
                "id": item_id,
                "raster_path": raster,
                "svg_path": svg,
                "opts": opts,
                "prompt": prompt,
                "seed": seed,
                "category": category,
            }
        )
    return entries


def _batch_svg_pass(
    entries: list[dict[str, Any]],
    *,
    priority: str | None,
    stream: bool,
    gpu_ids: list[int] | None,
    no_vramd: bool,
    verbose: bool,
    force: bool,
) -> None:
    """Fase SVG do batch (uma linha `phase=svg` por imagem)."""

    if not entries:
        return

    from dataclasses import replace

    from .svg.api import vectorize_file
    from .svg.engines import get_engine, resolve_engine

    vtracer_entries: list[dict[str, Any]] = []
    star_entries: list[dict[str, Any]] = []

    for entry in entries:
        opts: SvgOptions = entry["opts"]
        svg_path: Path = entry["svg_path"]
        if not force and svg_path.is_file():
            emit_result(
                entry["id"], TOOL_TEXT2D, STATUS_SKIPPED, phase="svg", output=str(svg_path)
            )
            continue
        resolved = resolve_engine(opts.engine, category=entry.get("category"))
        if resolved == "starvector":
            star_entries.append(entry)
        else:
            vtracer_entries.append(entry)

    def _ok(entry: dict[str, Any], result: Any, started: float) -> None:
        emit_result(
            entry["id"],
            TOOL_TEXT2D,
            STATUS_OK,
            phase="svg",
            output=str(result.path),
            seconds=round(time.time() - started, 3),
            svg_engine=result.engine,
            path_count=result.path_count,
            svg_bytes=result.bytes,
        )

    def _err(entry: dict[str, Any], exc: Exception) -> None:
        emit_result(
            entry["id"], TOOL_TEXT2D, STATUS_ERROR, phase="svg", error=str(exc)
        )

    for entry in vtracer_entries:
        started = time.time()
        try:
            result = vectorize_file(
                entry["raster_path"],
                entry["svg_path"],
                replace(entry["opts"], engine="vtracer"),
                prompt=entry["prompt"],
                seed=entry["seed"],
                verbose=verbose,
            )
            _ok(entry, result, started)
        except Exception as exc:
            _err(entry, exc)

    if not star_entries:
        return

    remaining = list(star_entries)
    if not no_vramd:
        from aigamekit_shared.cli_helpers import try_vramd_delegation

        from .svg.vramd import build_vectorize_request

        for entry in list(star_entries):
            started = time.time()
            payload = build_vectorize_request(
                image=entry["raster_path"],
                output=entry["svg_path"],
                opts=replace(entry["opts"], engine="starvector"),
                gpu_ids=gpu_ids,
            )
            try:
                handled = try_vramd_delegation(
                    "text2svg",
                    payload,
                    t_start=started,
                    noun="SVG",
                    console=console,
                    enabled=True,
                    priority=priority,
                    stream=stream,
                )
            except click.ClickException as exc:
                _err(entry, exc)
                remaining.remove(entry)
                continue
            if not handled:
                break
            emit_result(
                entry["id"],
                TOOL_TEXT2D,
                STATUS_OK,
                phase="svg",
                output=str(entry["svg_path"]),
                seconds=round(time.time() - started, 3),
                svg_engine="starvector",
            )
            remaining.remove(entry)

    if not remaining:
        return

    # O FLUX dos itens in-process tem de sair antes do StarVector.
    _batch_cleanup()
    engine = None
    err_console = Console(stderr=True)
    device = remaining[0]["opts"].device or "auto"
    try:
        if device != "cpu":
            from aigamekit_shared.cli_helpers import (
                needed_mib_for_backend,
                prepare_gpu_exclusive,
            )

            prepare_gpu_exclusive(
                needed_mib=needed_mib_for_backend("text2svg", quant_mode="none"),
                allow_shared=True,
                kill_others=False,
                backend="text2svg",
                quant_mode="none",
                console=err_console,
            )
        engine = get_engine("starvector", device=device, verbose=verbose)
        engine.warmup()
    except Exception as exc:
        for entry in remaining:
            if entry["opts"].engine == "auto" and not entry["opts"].strict:
                started = time.time()
                try:
                    result = vectorize_file(
                        entry["raster_path"],
                        entry["svg_path"],
                        replace(entry["opts"], engine="vtracer"),
                        prompt=entry["prompt"],
                        seed=entry["seed"],
                        verbose=verbose,
                    )
                    _ok(entry, result, started)
                except Exception as fallback_exc:
                    _err(entry, fallback_exc)
            else:
                _err(entry, exc)
        return

    try:
        for entry in remaining:
            started = time.time()
            try:
                result = vectorize_file(
                    entry["raster_path"],
                    entry["svg_path"],
                    replace(entry["opts"], engine="starvector"),
                    engine=engine,
                    prompt=entry["prompt"],
                    seed=entry["seed"],
                    verbose=verbose,
                )
                _ok(entry, result, started)
            except Exception as exc:
                if entry["opts"].engine == "auto" and not entry["opts"].strict:
                    started = time.time()
                    try:
                        result = vectorize_file(
                            entry["raster_path"],
                            entry["svg_path"],
                            replace(entry["opts"], engine="vtracer"),
                            prompt=entry["prompt"],
                            seed=entry["seed"],
                            verbose=verbose,
                        )
                        _ok(entry, result, started)
                    except Exception as fallback_exc:
                        _err(entry, fallback_exc)
                else:
                    _err(entry, exc)
    finally:
        engine.unload()


@click.group()
@click.version_option(version="0.1.0", prog_name="text2d")
@click.option("--verbose", "-v", is_flag=True, help="Logs detalhados")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Text2D — imagens a partir de texto (FLUX.3 Image Klein SDNQ)."""
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
@click.option("--output", "-o", type=click.Path(), help="Ficheiro de saída (.png, .jpg ou .webp)")
@click.option(
    "--width",
    "-W",
    default=None,
    type=int,
    help="Largura (múltiplo de 16 recomendado). Default: 1024, ou o tamanho da 1.ª referência com --ref.",
)
@click.option(
    "--height",
    "-H",
    default=None,
    type=int,
    help="Altura. Default: 1024, ou o tamanho da 1.ª referência com --ref.",
)
@click.option("--steps", "-s", default=16, show_default=True, help="Passos de inferência (faixa treinada: 4-16)")
@click.option(
    "--guidance",
    "-g",
    "guidance_scale",
    default=None,
    type=float,
    help="[Ignorado] FLUX.3 Klein é guidance-distilled (sem CFG); mantido por compatibilidade.",
)
@click.option("--seed", type=int, default=None, help="Seed para reprodutibilidade")
@click.option("--cpu", is_flag=True, help="Forçar CPU")
@click.option(
    "--model",
    "-m",
    "model_id",
    default=None,
    help="ID Hugging Face (default: black-forest-labs/flux-3-image-klein, ou TEXT2D_MODEL_ID)",
)
@click.option(
    "--ref",
    "-r",
    "refs",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False),
    help=(
        "Imagem de referência (edit/composição; até 10). No prompt, cite-as como "
        "'a primeira imagem', 'a segunda imagem'... Geração sai no tamanho da 1.ª "
        "referência salvo -W/-H explícitos."
    ),
)
@click.option(
    "--boxes",
    "boxes_arg",
    default=None,
    help=(
        "Layout por boxes: JSON inline ou path para ficheiro JSON. Cada box: "
        "{id, desc, bbox:[y0,x0,y1,x1] 0-1} (+ from/src_bbox/tgt_bbox para edits)."
    ),
)
@click.option(
    "--ref-silhouette",
    "ref_silhouette",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help=(
        "Açúcar: extrai a silhueta (rembg) da imagem e usa-a como referência — "
        "consistência de forma entre poses (animação 2D)."
    ),
)
@click.option(
    "--num-images",
    "-n",
    default=1,
    show_default=True,
    type=click.IntRange(min=1),
    help="Imagens por pedido (sufixos _1, _2... no output).",
)
@click.option(
    "--upsample",
    "upsample",
    is_flag=True,
    default=False,
    show_default=True,
    help=(
        "Prompt upsampler 9B (flux-3-image-klein-pup): expande o prompt curto numa "
        "caption completa (recomendado pela BFL; +pesos e +10-20 s por imagem)."
    ),
)
@click.option(
    "--pose",
    "pose",
    default=None,
    help=(
        "Pose do personagem: nome da biblioteca (ex.: walk_contact_front, run_air, jump_up) "
        "ou texto livre describing pernas/braços. Ver `text2d poses`."
    ),
)
@click.option(
    "--pose-view",
    type=click.Choice(["side", "front"]),
    default="side",
    show_default=True,
    help="Vocabulário de pose: side (perfil: frente/trás) ou front (de frente: esquerda/direita).",
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
    help="Medir tempos, CPU, RAM e VRAM (JSONL opcional: AIGAMEKIT_PROFILE_LOG).",
)
@click.option(
    "--gpu-ids",
    "gpu_ids_str",
    default=None,
    help="IDs das GPUs para split multi-GPU (ex: '0,1'). Auto-deteta se omitido com ≥2 GPUs.",
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
    type=click.Choice(["icon"]),
    default=None,
    help="Categoria de asset (icon: 512², 4 passos, styling de ícone).",
)
@click.option(
    "--transparent/--no-transparent",
    default=False,
    show_default=True,
    help="Remover fundo (rembg/U2Net); requer saída .png ou .webp.",
)
@click.option(
    "--svg/--no-svg",
    "svg",
    default=False,
    show_default=True,
    help="Gerar também um SVG ao lado do PNG (o raster continua a ser guardado).",
)
@click.option(
    "--svg-output",
    type=click.Path(),
    default=None,
    help="Path do SVG (default: <stem>.svg ao lado do PNG).",
)
@click.option(
    "--svg-engine",
    type=click.Choice(list(SVG_ENGINES)),
    default="auto",
    show_default=True,
    help="auto: StarVector para --category icon (se cacheado), senão VTracer.",
)
@click.option(
    "--svg-style",
    type=click.Choice(list(SVG_STYLES)),
    default="flat",
    show_default=True,
    help="Estilo vectorial (flat/line/detailed).",
)
@click.option(
    "--svg-colors",
    type=click.IntRange(2, 64),
    default=8,
    show_default=True,
    help="Paleta do raster antes do tracing (2-64).",
)
@click.option(
    "--svg-detail",
    type=click.Choice(list(SVG_DETAILS)),
    default="medium",
    show_default=True,
    help="Detalhe do tracing (low/medium/high).",
)
@click.option(
    "--svg-enhance/--no-svg-enhance",
    "svg_enhance",
    default=True,
    show_default=False,
    help="Reescrever o prompt para arte vector-friendly (default: ligado).",
)
@click.option(
    "--svg-max-tokens",
    type=click.IntRange(min=64),
    default=4000,
    show_default=True,
    help="Máximo de tokens SVG do StarVector.",
)
@click.option("--svg-seed", type=int, default=None, help="Seed do StarVector (VTracer é determinístico).")
@click.option(
    "--svg-device",
    type=click.Choice(["auto", "cuda", "cpu"]),
    default="auto",
    show_default=True,
    help="Dispositivo do StarVector (auto: CUDA se existir).",
)
@click.option("--svg-preview", is_flag=True, help="Gravar <stem>_preview.png rasterizado (requer cairosvg).")
@click.option(
    "--svg-transparent/--no-svg-transparent",
    "svg_transparent",
    default=None,
    help="Fundo transparente no SVG (default: segue --transparent/alpha da imagem).",
)
@click.option(
    "--svg-meta/--no-svg-meta",
    "svg_meta",
    default=True,
    show_default=False,
    help="Escrever <stem>.svg.json com prompt/engine/params/paths (default: ligado).",
)
@click.option("--svg-strict", is_flag=True, help="Falhar se o SVG for inválido ou exceder --svg-max-kb.")
@click.option(
    "--svg-max-kb",
    type=click.IntRange(min=1),
    default=2048,
    show_default=True,
    help="Orçamento de bytes do SVG (warning; --svg-strict falha).",
)
@click.option("--svg-keep-raster", is_flag=True, help="Com -o *.svg, manter o PNG intermédio.")
@click.option(
    "--hw-auto/--no-hw-auto",
    "hw_auto",
    default=True,
    show_default=True,
    help=(
        "Auto-detecção de hardware: liga CPU offload (e o modelo 4B) em GPUs "
        "pequenas, mantém 9B/full-GPU/multi-GPU nas grandes. Flags explícitas "
        "ganham. Env: TEXT2D_HW_AUTO=0."
    ),
)
@add_group_offload_option()
@click.option(
    "--compile/--no-compile",
    "torch_compile",
    default=False,
    show_default=True,
    help=(
        "torch.compile no transformer (Inductor). Cold-start lento; compensa em "
        "batch/server. Com offload model_cpu é ignorado; com group_stream usa mode=default. "
        "Env: AIGAMEKIT_TORCH_COMPILE=1."
    ),
)
@click.option(
    "--compile-mode",
    "torch_compile_mode",
    type=click.Choice(["default", "reduce-overhead", "max-autotune"]),
    default="default",
    show_default=True,
    help=("Modo Inductor. reduce-overhead/max-autotune = CUDA graphs (só full-GPU). Com offload cai para default."),
)
@click.option(
    "--step-cache",
    "step_cache",
    type=click.Choice(["off", "auto", "first_block", "taylorseer"]),
    default="off",
    show_default=True,
    help="Step cache (FirstBlock/TaylorSeer). Só full-GPU. Env: AIGAMEKIT_STEP_CACHE.",
)
@click.option(
    "--channels-last/--no-channels-last",
    "channels_last",
    default=False,
    show_default=True,
    help="Memory format NHWC (channels_last) no VAE/transformer — Ampere+ conv path.",
)
@add_vramd_options
@click.pass_context
def generate_cmd(
    ctx: click.Context,
    prompt: str,
    output: str | None,
    width: int | None,
    height: int | None,
    steps: int,
    guidance_scale: float | None,
    seed: int | None,
    cpu: bool,
    model_id: str | None,
    refs: tuple[str, ...],
    boxes_arg: str | None,
    ref_silhouette: str | None,
    num_images: int,
    upsample: bool,
    pose: str | None,
    pose_view: str,
    verbose_flag: bool,
    profile: bool,
    gpu_ids_str: str | None,
    quality: str,
    category: str | None,
    transparent: bool,
    svg: bool,
    svg_output: str | None,
    svg_engine: str,
    svg_style: str,
    svg_colors: int,
    svg_detail: str,
    svg_enhance: bool,
    svg_max_tokens: int,
    svg_seed: int | None,
    svg_device: str,
    svg_preview: bool,
    svg_transparent: bool | None,
    svg_meta: bool,
    svg_strict: bool,
    svg_max_kb: int,
    svg_keep_raster: bool,
    hw_auto: bool,
    group_offload: bool,
    torch_compile: bool,
    torch_compile_mode: str,
    step_cache: str,
    channels_last: bool,
    vramd_priority: str | None,
    no_vramd: bool,
    vramd_stream: bool,
) -> None:
    """Gera uma imagem a partir do PROMPT (com --ref vira edit/composição)."""
    import tempfile

    from aigamekit_shared.gpu import warn_if_vram_occupied
    from aigamekit_shared.profiler import ProfilerSession
    from aigamekit_shared.profiler.env import env_profile_log_path

    verbose = bool(ctx.obj.get("VERBOSE")) or verbose_flag

    # Alloc conf anti-fragmentação por modo (antes da 1ª alocação CUDA; com
    # group offload, max_split_size_mb causa fragmentação sob churn de onloads).
    from .hardware import apply_alloc_conf_early

    apply_alloc_conf_early(group_offload)

    # QualityEngine: soft resolution — fills defaults when user didn't specify.
    _src = click.core.ParameterSource
    _user_set_width = ctx.get_parameter_source("width") not in (_src.DEFAULT,)
    _user_set_height = ctx.get_parameter_source("height") not in (_src.DEFAULT,)
    _user_set_steps = ctx.get_parameter_source("steps") not in (_src.DEFAULT,)
    _user_set_guidance = ctx.get_parameter_source("guidance_scale") not in (_src.DEFAULT,)

    from aigamekit_shared.quality import QualityEngine

    _qengine = QualityEngine()
    _qresolved = _qengine.resolve("text2d", quality=quality, category=category)
    if not _user_set_width and "width" in _qresolved.params:
        width = _qresolved.params["width"]
    if not _user_set_height and "height" in _qresolved.params:
        height = _qresolved.params["height"]
    if not _user_set_steps and "steps" in _qresolved.params:
        steps = _qresolved.params["steps"]
    if not _user_set_guidance and "guidance" in _qresolved.params:
        guidance_scale = _qresolved.params["guidance"]

    # Referências: paths validados pelo click; silhueta é extraída ANTES da
    # delegação (o worker vramd lê o path do ficheiro temporário). Paths
    # absolutos: o worker vramd corre noutro cwd (relativo → "não encontrada").
    ref_paths: list[str] = [str(Path(r).resolve()) for r in refs]
    _sil_tmpdir: str | None = None
    if ref_silhouette:
        from .silhouette import extract_silhouette_file

        _sil_tmpdir = tempfile.mkdtemp(prefix="text2d-sil-")
        sil_path = extract_silhouette_file(ref_silhouette, Path(_sil_tmpdir) / "silhouette.png")
        ref_paths.append(str(sil_path))
        console.print(f"[dim]Silhueta extraída de {ref_silhouette} → {sil_path}[/dim]")
    if len(ref_paths) > 10:
        raise click.ClickException(f"Máximo de 10 imagens de referência (recebidas {len(ref_paths)})")

    # Boxes: JSON inline ou ficheiro (valida schema antes de qualquer GPU).
    from .boxes import parse_boxes

    try:
        box_list = parse_boxes(boxes_arg, num_refs=len(ref_paths))
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        raise click.ClickException(f"--boxes inválido: {e}") from e
    # No pipeline distilled só o bloco prompt_upsampler consome ``boxes``: sem
    # --upsample o diffusers descarta-as ("Unexpected input") e o layout é no-op.
    if box_list and not upsample:
        raise click.ClickException("--boxes precisa de --upsample (o layout é aplicado pelo prompt upsampler)")

    # Com referências e sem W/H explícitos: o tamanho sai da 1.ª referência
    # (comportamento do modelo) — não sobrepor o default do tier de qualidade.
    if ref_paths and not (_user_set_width or _user_set_height):
        width = None
        height = None

    # Validar saída transparente ANTES de qualquer trabalho de GPU (a imagem
    # RGBA final só pode ser gravada em PNG).
    if transparent and output is not None and Path(output).suffix.lower() not in (".png", ".webp"):
        raise click.ClickException("--transparent requer saída .png ou .webp")

    # Categoria icon: styling de app-icon no prompt (idempotente; a tabela da
    # consola abaixo mostra o prompt final augmentado).
    if category == "icon":
        from .icons import augment_prompt_for_icon

        prompt = augment_prompt_for_icon(prompt)

    # Pose: nome da biblioteca de poses ou texto livre (pernas/braços).
    if pose:
        from .poses import pose_names, pose_prompt

        pose_text = pose_prompt(pose, view=pose_view) if pose in pose_names() else pose
        prompt = f"{prompt.rstrip('.')}, {pose_text}"

    # ── SVG: resolver raster/SVG e o prompt antes do payload vramd ─────
    safe = "".join(c if c.isalnum() else "_" for c in prompt[:40]) or "single"
    user_output = output
    output_was_svg = bool(user_output and Path(user_output).suffix.lower() == ".svg")
    svg_requested = bool(svg) or output_was_svg
    svg_opts: SvgOptions | None = None
    svg_path: Path | None = None
    raster_is_temp = False

    if svg_requested:
        svg_opts = SvgOptions(
            engine=svg_engine,
            style=svg_style,
            colors=svg_colors,
            detail=svg_detail,
            max_tokens=svg_max_tokens,
            seed=svg_seed if svg_seed is not None else seed,
            device=svg_device,
            preview=svg_preview,
            transparent=bool(svg_transparent) if svg_transparent is not None else transparent,
            enhance=svg_enhance,
            meta=svg_meta,
            strict=svg_strict,
            max_kb=svg_max_kb,
            keep_raster=svg_keep_raster,
        )
        try:
            svg_opts.validate()
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        if svg_output and output_was_svg and Path(svg_output).resolve() != Path(str(user_output)).resolve():
            raise click.ClickException("--svg-output conflitua com -o *.svg")

        if output_was_svg:
            svg_path = Path(str(user_output)).resolve()
            output = str(svg_path.with_suffix(".png"))
            raster_is_temp = not svg_keep_raster
        else:
            if output is None:
                ensure_dirs()
                output = str((DEFAULT_IMAGE_DIR / f"{safe}_{int(time.time())}.png").resolve())
            svg_path = (
                Path(svg_output).resolve()
                if svg_output
                else Path(output).resolve().with_suffix(".svg")
            )

        if svg_enhance:
            prompt = enhance_prompt_for_svg(prompt, style=svg_style, colors=svg_colors)
    elif output is None:
        # Corrige o default antes do payload vramd (o Path(None) rebentava).
        ensure_dirs()
        output = str((DEFAULT_IMAGE_DIR / f"{safe}_{int(time.time())}.png").resolve())

    if not cpu:
        warn_if_vram_occupied()

    # Hardware auto-detection (soft): flags explícitas ganham sempre.
    from .hardware import detect_hardware_profile, hw_auto_enabled

    mem_eff = False
    hwp = None
    if hw_auto and hw_auto_enabled() and not cpu:
        hwp = detect_hardware_profile()
        if hwp.memory_efficient and hwp.device == "cuda":
            mem_eff = True

    mem_eff = mem_eff or cpu
    # Clamp por MÁXIMO (não só o exato 2048²) e só quando o utilizador não
    # fixou resolução — "Flags explícitas ganham" (mesmo guard dos siblings).
    if mem_eff and not (_user_set_width or _user_set_height) and width is not None and height is not None:
        width = min(width, 1024)
        height = min(height, 1024)
    resolved_model = model_id or (hwp.model_id if hwp is not None else _model_id(memory_efficient=mem_eff))
    quant_preset = hwp.quant_preset if hwp is not None else None
    device = "cpu" if cpu else None
    gpu_ids = [int(x.strip()) for x in gpu_ids_str.split(",")] if gpu_ids_str else None
    if gpu_ids is None and hwp is not None and hwp.gpu_ids:
        gpu_ids = hwp.gpu_ids
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_row("[bold]Prompt[/bold]", f"[cyan]{prompt}[/cyan]")
    if ref_paths:
        table.add_row("[bold]Referências[/bold]", f"{len(ref_paths)} imagem(ns)")
    if box_list:
        table.add_row("[bold]Boxes[/bold]", f"{len(box_list)} elemento(s)")
    table.add_row("[bold]Resolução[/bold]", f"{width}x{height}" if width and height else "auto")
    table.add_row("[bold]Passos[/bold]", str(steps))
    if guidance_scale is not None:
        table.add_row("[bold]Guidance[/bold]", f"{guidance_scale} [dim](ignorado: distilled)[/dim]")
    if num_images > 1:
        table.add_row("[bold]Imagens[/bold]", str(num_images))
    if upsample:
        table.add_row("[bold]Upsampler[/bold]", "prompt upsampler 9B (lento, +qualidade de prompt)")
    table.add_row("[bold]Modelo[/bold]", resolved_model)
    if svg_requested and svg_opts is not None:
        table.add_row(
            "[bold]SVG[/bold]",
            f"{svg_opts.engine} · {svg_opts.style} · {svg_opts.colors} cores · detalhe {svg_opts.detail}",
        )
    if hwp is not None:
        table.add_row("[bold]Hardware (auto)[/bold]", hwp.summary())
    _accel = []
    if torch_compile:
        _accel.append(f"compile={torch_compile_mode}")
    if step_cache != "off":
        _accel.append(f"step-cache={step_cache}")
    if channels_last:
        _accel.append("channels_last")
    if _accel:
        table.add_row("[bold]Kernel opts[/bold]", ", ".join(_accel))
    console.print(Panel(table, title="[bold green]Configuração", border_style="green"))

    log_p = env_profile_log_path()
    prof_log = Path(log_p) if log_p else None
    t_start = time.time()

    vramd_payload: dict[str, Any] = {
        "prompt": prompt,
        "output": str(Path(output).resolve()),
        "steps": steps,
        "seed": seed,
        "model_id": resolved_model,
        "torch_compile": torch_compile,
        "torch_compile_mode": torch_compile_mode,
        "channels_last": channels_last,
        "step_cache": step_cache,
        "allow_group_offload": group_offload,
        "upsample": upsample,
    }
    # W/H ausentes = auto (tamanho da 1.ª referência) — não enviar None.
    if width is not None:
        vramd_payload["width"] = width
    if height is not None:
        vramd_payload["height"] = height
    if guidance_scale is not None:
        vramd_payload["guidance"] = guidance_scale
    if ref_paths:
        vramd_payload["refs"] = ref_paths
    if box_list:
        vramd_payload["boxes"] = box_list
    if num_images > 1:
        vramd_payload["num_images"] = num_images
    # Categoria/transparente só viajam quando pedidos (contrato vramd).
    if category is not None:
        vramd_payload["category"] = category
    if transparent:
        vramd_payload["transparent"] = True

    if not cpu and delegate_or_prepare(
        "text2d",
        payload=vramd_payload,
        t_start=t_start,
        noun="Imagem",
        console=console,
        enabled=not no_vramd,
        priority=vramd_priority,
        stream=vramd_stream,
        gpu_ids=gpu_ids,
        memory_efficient=mem_eff,
        quant_preset=quant_preset,
        footprint_key=model_footprint_key(resolved_model),
    ):
        if svg_requested and svg_opts is not None and svg_path is not None:
            raster_paths = [Path(output)] + [
                Path(output).with_name(f"{Path(output).stem}_{idx}{Path(output).suffix}")
                for idx in range(1, max(1, num_images))
            ]
            raster_paths = [path for path in raster_paths if path.is_file()]
            try:
                results = _svg_stage(
                    raster_paths,
                    _svg_paths_for_raster(svg_path, len(raster_paths)),
                    svg_opts,
                    prompt=prompt,
                    seed=seed,
                    category=category,
                    enabled=not no_vramd,
                    priority=vramd_priority,
                    stream=vramd_stream,
                    gpu_ids=gpu_ids,
                    verbose=verbose,
                )
                _print_svg_results(results)
                if raster_is_temp:
                    for path in raster_paths:
                        path.unlink(missing_ok=True)
            except click.ClickException:
                raise
            except Exception as exc:
                console.print(f"[yellow]SVG falhou: {exc}[/yellow]")
                if svg_strict or svg_engine == "starvector":
                    raise click.ClickException(f"SVG: {exc}") from exc
        return

    # Fallback in-process: coordenação VRAM (paridade Text3D/Paint3D).
    if not cpu:
        from .hardware import group_offload_needed_mib, group_offload_will_engage

        _qmode = quant_preset if quant_preset not in (None, "none") else ("sdnq-int4" if mem_eff else "none")
        if group_offload_will_engage():
            # GO+streams: pico ≈ ativação + trânsito de grupos — não exigir
            # pesos+ativação ao ensure_vram (recusava jobs que correm bem).
            # W/H None (auto por referência) → estimar pelo lado 1024 (conservador).
            needed_mib = group_offload_needed_mib(width or 1024, height or 1024)
        else:
            needed_mib = needed_mib_for_backend("text2d", quant_mode=_qmode, memory_efficient=mem_eff)
        prepare_gpu_exclusive(
            needed_mib=needed_mib,
            allow_shared=True,
            kill_others=False,
            backend="text2d",
            quant_mode=_qmode,
            console=console,
        )

    item_id = safe

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
                memory_efficient=mem_eff,
                verbose=verbose,
                model_id=resolved_model,
                gpu_ids=gpu_ids,
                quant_preset=quant_preset,
                torch_compile=torch_compile,
                torch_compile_mode=torch_compile_mode,
                step_cache=step_cache,
                channels_last=channels_last,
                group_offload=group_offload,
                upsample=upsample,
            )

            with (
                prof.span("warmup"),
                console.status(
                    "[bold yellow]1/2 — Download HF + carregamento de pesos "
                    "(1ª vez: vários GB/minutos; GPU pode mostrar 0% até ao passo final)",
                    spinner="dots",
                ),
            ):
                gen.warmup()

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
                image, _metadata = gen.generate(
                    prompt=prompt,
                    height=height,
                    width=width,
                    guidance_scale=guidance_scale,
                    num_inference_steps=steps,
                    seed=seed,
                    refs=ref_paths,
                    boxes=box_list,
                    num_images=num_images,
                )
                progress.update(task, description="[green]Concluído")
            emit_progress(item_id, TOOL_TEXT2D, phase="diffusion", percent=100)

            all_images = _metadata.get("images") or [image]

            # Ícone transparente: rembg (U2Net) sobre a imagem gerada (RGBA).
            if transparent:
                from .bg_removal import remove_background

                all_images = [remove_background(img) for img in all_images]

            emit_progress(item_id, TOOL_TEXT2D, phase="save", percent=0)
            with prof.span("save"):
                img_format = _image_format_for(out_path)
                for idx, img in enumerate(all_images):
                    # -n > 1: sufixos _1, _2... no stem (a 1.ª fica no output principal).
                    part_path = out_path if idx == 0 else out_path.with_name(f"{out_path.stem}_{idx}{out_path.suffix}")
                    KleinFluxGenerator.save_image(img, part_path, image_format=img_format)
            emit_progress(item_id, TOOL_TEXT2D, phase="save", percent=100)

        svg_results: list[dict[str, Any]] = []
        if svg_requested and svg_opts is not None and svg_path is not None:
            raster_paths = [out_path] + [
                out_path.with_name(f"{out_path.stem}_{idx}{out_path.suffix}")
                for idx in range(1, max(1, num_images))
            ]
            raster_paths = [path for path in raster_paths if path.is_file()]
            # Liberta o FLUX antes do StarVector; para VTracer é inofensivo.
            with contextlib.suppress(Exception):
                gen.unload()
            svg_results = _svg_stage(
                raster_paths,
                _svg_paths_for_raster(svg_path, len(raster_paths)),
                svg_opts,
                prompt=prompt,
                seed=seed,
                category=category,
                enabled=not no_vramd,
                priority=vramd_priority,
                stream=vramd_stream,
                gpu_ids=gpu_ids,
                verbose=verbose,
            )
            _print_svg_results(svg_results)
            if raster_is_temp:
                for path in raster_paths:
                    path.unlink(missing_ok=True)

        elapsed = time.time() - t_start
        try:
            sz = format_bytes(out_path.stat().st_size)
        except OSError:
            sz = "?"
        console.print(Rule("[bold green]Resultado", style="green"))
        console.print(f"[bold green]✓[/bold green] Imagem: [cyan]{out_path.resolve()}[/cyan] [dim]({sz})[/dim]")
        console.print(f"[dim]Tempo total: {elapsed:.1f}s[/dim]")
        final_output = svg_path if output_was_svg and svg_path is not None else out_path
        emit_result(
            item_id,
            TOOL_TEXT2D,
            STATUS_OK,
            output=str(final_output),
            seconds=elapsed,
            **({"svg": str(svg_path)} if (svg_results and svg_path is not None) else {}),
        )
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


@cli.command("silhouette")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.option("--output", "-o", type=click.Path(), help="PNG de saída (default: <input>_silhouette.png)")
@click.option(
    "--threshold",
    type=click.IntRange(1, 255),
    default=128,
    show_default=True,
    help="Limiar de alpha (0-255) que separa sujeito/fundo.",
)
def silhouette_cmd(input: str, output: str | None, threshold: int) -> None:
    """Extrai a silhueta binária de INPUT (rembg + threshold de alpha).

    A silhueta serve de referência de forma para gerar poses novas com
    consistência (animação 2D): text2d generate "same character running"
    --ref <silhueta> -o pose.png
    """
    from .silhouette import extract_silhouette_file

    out = output or str(Path(input).with_name(f"{Path(input).stem}_silhouette.png"))
    try:
        path = extract_silhouette_file(input, out, threshold=threshold)
    except ImportError as e:
        raise click.ClickException(str(e)) from e
    console.print(f"[bold green]✓[/bold green] Silhueta: [cyan]{path}[/cyan]")


@cli.command("vectorize")
@click.argument("input", type=click.Path(exists=True, dir_okay=False))
@click.option("--output", "-o", type=click.Path(), default=None, help="SVG de saída (default: <input>.svg).")
@click.option(
    "--engine",
    type=click.Choice(["vtracer", "starvector"]),
    default="vtracer",
    show_default=True,
    help="vtracer (CPU, robusto) ou starvector (GPU, ícones/logos).",
)
@click.option("--style", type=click.Choice(list(SVG_STYLES)), default="flat", show_default=True)
@click.option("--colors", type=click.IntRange(2, 64), default=8, show_default=True)
@click.option("--detail", type=click.Choice(list(SVG_DETAILS)), default="medium", show_default=True)
@click.option("--max-kb", type=click.IntRange(min=1), default=2048, show_default=True)
@click.option(
    "--max-tokens",
    type=click.IntRange(min=64),
    default=4000,
    show_default=True,
    help="Limite do StarVector.",
)
@click.option("--seed", type=int, default=None, help="Seed do StarVector.")
@click.option(
    "--device",
    type=click.Choice(["auto", "cuda", "cpu"]),
    default="auto",
    show_default=True,
    help="Dispositivo do StarVector.",
)
@click.option("--gpu-ids", "gpu_ids_str", default=None, help="IDs das GPUs (StarVector usa a 1.ª).")
@click.option(
    "--transparent/--no-transparent",
    "transparent",
    default=None,
    help="Fundo transparente (default: deteta o alpha da imagem).",
)
@click.option("--preview", is_flag=True, help="Gravar <stem>_preview.png (requer cairosvg).")
@click.option("--meta/--no-meta", "meta", default=True, show_default=True, help="Escrever <stem>.svg.json.")
@click.option("--strict", is_flag=True, help="Falhar se o SVG for inválido ou exceder --max-kb.")
@click.option("-v", "--verbose", "verbose_flag", is_flag=True, help="Logs detalhados.")
@add_vramd_options
def vectorize_cmd(
    input: str,
    output: str | None,
    engine: str,
    style: str,
    colors: int,
    detail: str,
    max_kb: int,
    max_tokens: int,
    seed: int | None,
    device: str,
    gpu_ids_str: str | None,
    transparent: bool | None,
    preview: bool,
    meta: bool,
    strict: bool,
    verbose_flag: bool,
    vramd_priority: str | None,
    no_vramd: bool,
    vramd_stream: bool,
) -> None:
    """Vectoriza uma imagem existente (PNG/WebP/JPEG) num SVG validado."""

    from .svg.api import vectorize_file
    from .svg.vramd import build_vectorize_request

    ctx = click.get_current_context(silent=True)
    verbose = bool(ctx.obj.get("VERBOSE")) if ctx is not None and ctx.obj else False
    verbose = verbose or verbose_flag
    opts = SvgOptions(
        engine=engine,
        style=style,
        colors=colors,
        detail=detail,
        max_tokens=max_tokens,
        seed=seed,
        device=device,
        preview=preview,
        transparent=bool(transparent),
        meta=meta,
        strict=strict,
        max_kb=max_kb,
    )
    try:
        opts.validate()
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    source = Path(input).resolve()
    destination = Path(output).resolve() if output else source.with_suffix(".svg")
    gpu_ids = [int(x.strip()) for x in gpu_ids_str.split(",")] if gpu_ids_str else None

    if engine == "starvector" and not no_vramd:
        from aigamekit_shared.cli_helpers import try_vramd_delegation

        payload = build_vectorize_request(
            image=source, output=destination, opts=opts, gpu_ids=gpu_ids
        )
        if try_vramd_delegation(
            "text2svg",
            payload,
            t_start=time.time(),
            noun="SVG",
            console=console,
            enabled=True,
            priority=vramd_priority,
            stream=vramd_stream,
        ):
            _print_svg_results([_svg_summary(destination, engine_hint="starvector")])
            return

    if engine == "starvector" and opts.device != "cpu":
        from aigamekit_shared.cli_helpers import needed_mib_for_backend, prepare_gpu_exclusive

        prepare_gpu_exclusive(
            needed_mib=needed_mib_for_backend("text2svg", quant_mode="none"),
            allow_shared=True,
            kill_others=False,
            backend="text2svg",
            quant_mode="none",
            console=console,
        )

    result = vectorize_file(source, destination, opts, verbose=verbose)
    _print_svg_results([_svg_summary(result)])


@cli.command("poses")
def poses_cmd() -> None:
    """Lista animações e poses (para `--pose`, `walk-cycle` e `animate`)."""
    from .anim import ANIMATIONS
    from .poses import DIRECTIONS, pose_names, pose_prompt
    from .quadruped import quad_pose_names, quad_pose_prompt

    anims = Table(title="[bold blue]Animações (text2d animate --anim)[/bold blue]", box=box.ROUNDED)
    anims.add_column("Animação", style="cyan")
    anims.add_column("Corpo", style="magenta")
    anims.add_column("Fases", style="white")
    for anim in ANIMATIONS.values():
        anims.add_row(anim.name, anim.body, ", ".join(anim.poses))
    console.print(anims)

    t = Table(title="[bold blue]Poses[/bold blue]", box=box.ROUNDED)
    t.add_column("Pose", style="cyan")
    t.add_column("Texto (perfil)", style="white")
    for name in pose_names():
        t.add_row(name, pose_prompt(name, view="side")[:110])
    for name in quad_pose_names():
        t.add_row(name, quad_pose_prompt(name)[:110])
    console.print(t)
    console.print(
        Panel(
            f"[dim]Direções: {', '.join(DIRECTIONS)} (quadrúpedes: east/west)[/dim]\n"
            "[dim]Ex.: text2d animate 'pixel art wolf' --anim quad-gallop --direction east[/dim]\n"
            "[dim]Ex.: text2d generate 'knight' --pose walk_contact_front[/dim]",
            border_style="dim",
        )
    )


def _anim_names() -> list[str]:
    from .anim import ANIMATIONS  # leve (só PIL): catálogo único para o --anim

    return list(ANIMATIONS)


_DIRECTION_CHOICES = ["south", "south_east", "east", "north_east", "north", "north_west", "west", "south_west"]


def _sprite_options(f: Any) -> Any:
    """Opções comuns de ``walk-cycle`` e ``animate`` (a mesma receita de sprites)."""
    f = add_vramd_options(f)
    f = click.option("--cpu", is_flag=True, help="Forçar CPU")(f)
    f = click.option("--measure/--no-measure", default=True, show_default=True, help="Medir a variação entre frames.")(
        f
    )
    f = click.option("--gif/--no-gif", default=True, show_default=True, help="Gerar GIF do ciclo.")(f)
    f = click.option("--sheet/--no-sheet", default=True, show_default=True, help="Gerar folha de sprites.")(f)
    f = click.option(
        "--transparent/--no-transparent",
        default=True,
        show_default=True,
        help="Cortar o fundo (flood fill da borda; rembg se o fundo não for liso).",
    )(f)
    f = click.option("--colors", default=32, show_default=True, type=int, help="Cores da paleta (look 16-bit).")(f)
    f = click.option(
        "--sprite-size",
        default=96,
        show_default=True,
        type=int,
        help="Lado do sprite final (0 = sem downscale).",
    )(f)
    f = click.option("--frame-ms", default=160, show_default=True, type=int, help="Duração de cada frame no GIF.")(f)
    f = click.option(
        "--retries",
        default=1,
        show_default=True,
        type=click.IntRange(min=0, max=4),
        help="Tentativas extra (outra seed) se os frames saírem quase iguais; fica a melhor.",
    )(f)
    f = click.option("--seed", default=100, show_default=True, type=int, help="Seed base.")(f)
    f = click.option("--steps", "-s", default=12, show_default=True, type=int, help="Passos de inferência.")(f)
    f = click.option(
        "--size",
        default=320,
        show_default=True,
        type=int,
        help="Altura de cada célula da folha gerada (largura = 0.8x; 4 células = 1024 px).",
    )(f)
    f = click.option(
        "--ref",
        "reference",
        type=click.Path(exists=True, dir_okay=False),
        default=None,
        help="Imagem de referência do personagem (identidade). Sem ela, é gerado um frame base.",
    )(f)
    f = click.option(
        "--out-dir",
        "-O",
        type=click.Path(),
        default="outputs/sprites",
        show_default=True,
        help="Directório de saída (frames, sprites, sheet, gif).",
    )(f)
    f = click.option(
        "--frames",
        "-n",
        default=4,
        show_default=True,
        type=click.IntRange(min=1, max=16),
        help="Frames do ciclo.",
    )(f)
    f = click.option(
        "--direction",
        type=click.Choice(_DIRECTION_CHOICES),
        default="east",
        show_default=True,
        help="Direção (define a vista e se o set é espelhado).",
    )(f)
    f = click.argument("character")(f)
    return f


def _sprite_renderer(
    *, steps: int, cpu: bool, no_vramd: bool, vramd_priority: str | None, vramd_stream: bool
) -> tuple[Any, Any]:
    """``(render, cleanup)`` dos comandos de sprites: delega ao vramd, fallback in-process."""
    # Gerador in-process (fallback quando o vramd não trata o job).
    _gen: list[Any] = []

    def _fallback_render(prompt: str, path: Path, frame_seed: int, refs: list[Path], width: int, height: int) -> None:
        if not _gen:
            _gen.append(KleinFluxGenerator(device="cpu" if cpu else None, memory_efficient=not cpu))
        img, _ = _gen[0].generate(
            prompt,
            width=width,
            height=height,
            refs=refs or None,
            num_inference_steps=steps,
            seed=frame_seed,
        )
        KleinFluxGenerator.save_image(img, path)

    def _render(prompt: str, path: Path, frame_seed: int, refs: list[Path], width: int, height: int) -> None:
        """Delega ao vramd; se recusar/falhar, gera in-process."""
        payload: dict[str, Any] = {
            "prompt": prompt,
            "output": str(path.resolve()),
            "width": width,
            "height": height,
            "steps": steps,
            "seed": frame_seed,
            "model_id": default_model_id(),
            "refs": [str(Path(r).resolve()) for r in refs],
        }
        if not cpu and delegate_or_prepare(
            "text2d",
            payload=payload,
            t_start=time.time(),
            noun="Sprite",
            console=console,
            enabled=not no_vramd,
            priority=vramd_priority or "batch",
            stream=vramd_stream,
            memory_efficient=not cpu,
            quant_preset=None,
            footprint_key=model_footprint_key(payload["model_id"]),
        ):
            return
        _fallback_render(prompt, path, frame_seed, refs, width, height)

    def _cleanup() -> None:
        for gen in _gen:
            gen.unload()

    return _render, _cleanup


def _run_sprite_animation(
    anim: str,
    character: str,
    direction: str,
    frames: int,
    out_dir: str,
    reference: str | None,
    size: int,
    steps: int,
    seed: int,
    frame_ms: int,
    retries: int,
    sprite_size: int,
    colors: int,
    transparent: bool,
    sheet: bool,
    gif: bool,
    measure: bool,
    cpu: bool,
    vramd_priority: str | None,
    no_vramd: bool,
    vramd_stream: bool,
) -> None:
    """Corpo partilhado de ``walk-cycle`` / ``animate`` (delegação vramd + fallback)."""
    from .sprite import generate_cycle

    render, cleanup = _sprite_renderer(
        steps=steps, cpu=cpu, no_vramd=no_vramd, vramd_priority=vramd_priority, vramd_stream=vramd_stream
    )
    try:
        result = generate_cycle(
            character,
            direction=direction,
            out_dir=out_dir,
            frames=frames,
            animation=anim,
            reference=reference,
            size=size,
            steps=steps,
            seed=seed,
            sprite_size=sprite_size or None,
            colors=colors,
            transparent=transparent,
            make_sheet=sheet,
            make_gif=gif,
            measure=measure,
            frame_ms=frame_ms,
            retries=retries,
            render=render,
        )
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e)) from e
    finally:
        cleanup()

    console.print(
        Panel(
            f"[bold green]✓[/bold green] {len(result['frames'])} frames → "
            f"[cyan]{Path(out_dir).resolve()}[/cyan]\n"
            f"[dim]sprites: {len(result['sprites'])} | atlas: {result['atlas']} | gif: {result['gif']} | "
            f"meta: {result['meta']}[/dim]",
            title=f"[bold green]Sprite sheet — {anim}[/bold green]",
            border_style="green",
        )
    )


@cli.command("walk-cycle")
@_sprite_options
def walk_cycle_cmd(**kwargs: Any) -> None:
    """Gera um ciclo de caminhada (frames + sprites + atlas + GIF) numa direção.

    Atalho de ``text2d animate --anim walk``. Todos os frames saem numa só
    geração guiada pela identidade e por uma folha de silhuetas.
    """
    _run_sprite_animation("walk", **kwargs)


@cli.command("animate")
@click.option(
    "--anim",
    "-a",
    type=click.Choice(list(_anim_names())),
    default="walk",
    show_default=True,
    help="Animação. quad-* = quadrúpedes (só direções de perfil: east/west).",
)
@_sprite_options
def animate_cmd(anim: str, **kwargs: Any) -> None:
    """Gera uma animação 2D (bípede ou quadrúpede) numa direção.

    Ex.: ``text2d animate "pixel art wolf" --anim quad-gallop --direction east``.
    Todos os frames saem numa só geração guiada pela identidade e por uma folha
    de silhuetas (ver ``text2d poses``).
    """
    _run_sprite_animation(anim, **kwargs)


@cli.command("animate-set")
@click.argument("character")
@click.option(
    "--anims",
    default=None,
    help="Animações separadas por vírgula (default: set standard do corpo — ver --body).",
)
@click.option(
    "--directions",
    "-d",
    default="east",
    show_default=True,
    help="Direções separadas por vírgula (ex. east,west,south,north). Espelhadas saem sem GPU.",
)
@click.option(
    "--body",
    type=click.Choice(["biped", "quadruped"]),
    default="biped",
    show_default=True,
    help="Corpo do set default (ignorado com --anims).",
)
@click.option("--out-dir", "-O", type=click.Path(), default="outputs/sprites/set", show_default=True)
@click.option(
    "--ref",
    "reference",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Identidade da personagem parada (reutilizada em todas as animações e vistas).",
)
@click.option("--frames", "-n", default=4, show_default=True, type=click.IntRange(min=1, max=16))
@click.option("--size", default=320, show_default=True, type=int, help="Altura de cada célula gerada.")
@click.option("--steps", "-s", default=12, show_default=True, type=int)
@click.option("--seed", default=100, show_default=True, type=int)
@click.option("--retries", default=1, show_default=True, type=click.IntRange(min=0, max=4))
@click.option("--sprite-size", default=96, show_default=True, type=int, help="Lado do sprite (0 = sem downscale).")
@click.option("--colors", default=32, show_default=True, type=int)
@click.option("--frame-ms", default=160, show_default=True, type=int, help="Duração base (x holds da animação).")
@click.option("--cpu", is_flag=True, help="Forçar CPU")
@add_vramd_options
def animate_set_cmd(
    character: str,
    anims: str | None,
    directions: str,
    body: str,
    out_dir: str,
    reference: str | None,
    frames: int,
    size: int,
    steps: int,
    seed: int,
    retries: int,
    sprite_size: int,
    colors: int,
    frame_ms: int,
    cpu: bool,
    vramd_priority: str | None,
    no_vramd: bool,
    vramd_stream: bool,
) -> None:
    """Gera a personagem completa: várias animações x direções de uma vez.

    Uma identidade (gerada uma vez ou ``--ref``) reutilizada em todas as
    animações e vistas, direções espelhadas sem GPU e um ``set.json`` com o
    índice de ``anim.json`` (frames, durações, pivô) para o motor.

    Ex.: ``text2d animate-set "pixel art knight" -d east,west,south,north``.
    """
    from .sprite import generate_set

    anim_list = [a.strip() for a in anims.split(",") if a.strip()] if anims else None
    dir_list = [d.strip() for d in directions.split(",") if d.strip()]
    bad = [d for d in dir_list if d not in _DIRECTION_CHOICES]
    if bad:
        raise click.BadParameter(f"direções inválidas: {', '.join(bad)} (válidas: {', '.join(_DIRECTION_CHOICES)})")
    render, cleanup = _sprite_renderer(
        steps=steps, cpu=cpu, no_vramd=no_vramd, vramd_priority=vramd_priority, vramd_stream=vramd_stream
    )
    try:
        result = generate_set(
            character,
            out_dir=out_dir,
            animations=anim_list,
            directions=dir_list,
            body=body,
            reference=reference,
            seed=seed,
            render=render,
            frames=frames,
            size=size,
            steps=steps,
            retries=retries,
            sprite_size=sprite_size or None,
            colors=colors,
            frame_ms=frame_ms,
        )
    except (ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e)) from e
    finally:
        cleanup()
    n = sum(len(v) for v in result["animations"].values())
    console.print(
        Panel(
            f"[bold green]✓[/bold green] {n} animações → [cyan]{result['set']}[/cyan]",
            title="[bold green]Sprite set[/bold green]",
            border_style="green",
        )
    )


def _kit_options(f: Any) -> Any:
    """Opções comuns de ``tileset``/``parallax``/``vfx``/``items``."""
    f = add_vramd_options(f)
    f = click.option("--cpu", is_flag=True, help="Forçar CPU")(f)
    f = click.option("--seed", default=100, show_default=True, type=int, help="Seed base.")(f)
    f = click.option("--steps", "-s", default=12, show_default=True, type=int, help="Passos de inferência.")(f)
    f = click.option("--style", default=None, help="Estilo visual (default: pixel art 16-bit).")(f)
    return f


def _run_kit(
    fn: Any,
    *args: Any,
    steps: int,
    cpu: bool,
    no_vramd: bool,
    vramd_priority: str | None,
    vramd_stream: bool,
    style: str | None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Corre uma ferramenta 2D com o renderer de sprites (vramd → in-process)."""
    from .kit2d import DEFAULT_STYLE

    render, cleanup = _sprite_renderer(
        steps=steps, cpu=cpu, no_vramd=no_vramd, vramd_priority=vramd_priority, vramd_stream=vramd_stream
    )
    try:
        return fn(*args, render=render, steps=steps, style=style or DEFAULT_STYLE, **kwargs)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    finally:
        cleanup()


def _done(title: str, body: str) -> None:
    console.print(
        Panel(f"[bold green]✓[/bold green] {body}", title=f"[bold green]{title}[/bold green]", border_style="green")
    )


@cli.command("tileset")
@click.argument("base")
@click.argument("top")
@click.option(
    "--out-dir", "-O", type=click.Path(), default=None, help="Saída (default: outputs/tilesets/<base>_<top>)."
)
@click.option("--tile", default=32, show_default=True, type=int, help="Lado do tile (divisor de 384: 16/24/32/48/64).")
@click.option("--colors", default=24, show_default=True, type=int, help="Cores da paleta comum.")
@click.option(
    "--base-texture",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Textura já gerada do terreno base (reutilizar entre tilesets).",
)
@click.option(
    "--top-texture",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Textura já gerada do terreno de cima.",
)
@_kit_options
def tileset_cmd(
    base: str,
    top: str,
    out_dir: str | None,
    tile: int,
    colors: int,
    base_texture: str | None,
    top_texture: str | None,
    **kw: Any,
) -> None:
    """Tileset de terreno com autotile de 16 tiles (cantos) — Tiled .tsx + JSON.

    Ex.: ``text2d tileset "lush grass" "dirt path" --tile 32``. O modelo só
    desenha as duas texturas; transições, sombra e encaixe são determinísticos.
    """
    from .kit2d import slug
    from .tileset import generate_tileset

    out_dir = out_dir or f"outputs/tilesets/{slug(base)}_{slug(top)}"
    r = _run_kit(
        generate_tileset,
        base,
        top,
        out_dir=out_dir,
        tile=tile,
        colors=colors,
        base_texture=base_texture,
        top_texture=top_texture,
        **kw,
    )
    _done("Tileset", f"16 tiles → [cyan]{r['tileset']}[/cyan] · Tiled [cyan]{r['tsx']}[/cyan]")


@cli.command("parallax")
@click.argument("scene")
@click.option("--out-dir", "-O", type=click.Path(), default=None, help="Saída (default: outputs/parallax/<cena>).")
@click.option(
    "--layers",
    default="sky,far,mid,near",
    show_default=True,
    help="Camadas de trás para a frente (sky, far, mid, near).",
)
@click.option("--height", default=256, show_default=True, type=int, help="Altura de cada camada (largura = 2x).")
@click.option("--colors", default=48, show_default=True, type=int, help="Cores da paleta comum (0 = sem quantizar).")
@click.option("--gif-frames", default=48, show_default=True, type=int, help="Frames do preview.gif (0 = sem GIF).")
@_kit_options
def parallax_cmd(
    scene: str, out_dir: str | None, layers: str, height: int, colors: int, gif_frames: int, **kw: Any
) -> None:
    """Fundo em camadas para parallax, cada uma repetível em x — PNGs + parallax.json.

    Ex.: ``text2d parallax "haunted forest at dusk"``.
    """
    from .kit2d import slug
    from .parallax import generate_parallax

    out_dir = out_dir or f"outputs/parallax/{slug(scene)[:40]}"
    names = [n.strip() for n in layers.split(",") if n.strip()]
    r = _run_kit(
        generate_parallax,
        scene,
        out_dir=out_dir,
        layers=names,
        height=height,
        colors=colors,
        gif_frames=gif_frames,
        **kw,
    )
    _done("Parallax", f"{len(r['layers'])} camadas → [cyan]{r['json']}[/cyan]")


def _effect_names() -> list[str]:
    from .vfx import EFFECTS

    return list(EFFECTS)


@cli.command("vfx")
@click.argument("effect", type=click.Choice(_effect_names()))
@click.option("--subject", default=None, help="Assunto alternativo (ex. 'green acid explosion'), mesmas fases.")
@click.option("--out-dir", "-O", type=click.Path(), default=None, help="Saída (default: outputs/vfx/<efeito>).")
@click.option("--frames", "-n", default=8, show_default=True, type=click.IntRange(2, 8))
@click.option("--sprite-size", default=64, show_default=True, type=int, help="Lado do sprite (0 = sem downscale).")
@click.option("--colors", default=24, show_default=True, type=int)
@click.option(
    "--alpha-levels", default=4, show_default=True, type=int, help="Níveis de alpha (2 = binário, 0 = suave)."
)
@click.option("--frame-ms", default=70, show_default=True, type=int, help="Duração base de cada frame.")
@_kit_options
def vfx_cmd(
    effect: str,
    subject: str | None,
    out_dir: str | None,
    frames: int,
    sprite_size: int,
    colors: int,
    alpha_levels: int,
    frame_ms: int,
    **kw: Any,
) -> None:
    """Animação de efeito (explosão, fogo, fumo, magia, corte, raio, cura, impacto).

    Ex.: ``text2d vfx explosion --subject "green acid explosion"``. Fundo preto
    → alpha pela luz; atlas + GIF + anim.json (blend sugerido).
    """
    from .vfx import generate_vfx

    out_dir = out_dir or f"outputs/vfx/{effect}"
    r = _run_kit(
        generate_vfx,
        effect,
        out_dir=out_dir,
        frames=frames,
        subject=subject,
        sprite_size=sprite_size or None,
        colors=colors,
        alpha_levels=alpha_levels,
        frame_ms=frame_ms,
        **kw,
    )
    _done("VFX", f"{frames} frames → [cyan]{r['gif']}[/cyan] · [cyan]{r['meta']}[/cyan]")


@cli.command("items")
@click.argument("names", required=False)
@click.option(
    "--file",
    "names_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Ficheiro com um item por linha (em vez da lista por vírgulas).",
)
@click.option("--theme", default=None, help="Tema comum (ex. 'dark fantasy', 'sci-fi').")
@click.option("--out-dir", "-O", type=click.Path(), default="outputs/items", show_default=True)
@click.option("--icon-size", default=32, show_default=True, type=int, help="Lado de cada ícone.")
@click.option("--colors", default=48, show_default=True, type=int, help="Cores da paleta do pack.")
@click.option(
    "--ref",
    "reference",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Imagem de estilo para todas as folhas.",
)
@_kit_options
def items_cmd(
    names: str | None,
    names_file: str | None,
    theme: str | None,
    out_dir: str,
    icon_size: int,
    colors: int,
    reference: str | None,
    **kw: Any,
) -> None:
    """Pack de ícones de itens num estilo comum (até 8 por geração) — atlas + items.json.

    Ex.: ``text2d items "health potion, iron sword, wooden shield, gold coin" --theme "dark fantasy"``.
    """
    from .items import generate_items

    if names_file:
        lst = Path(names_file).read_text(encoding="utf-8").splitlines()
    elif names:
        lst = names.split(",")
    else:
        raise click.UsageError("indica os itens (lista por vírgulas) ou --file")
    r = _run_kit(
        generate_items, lst, out_dir=out_dir, theme=theme, icon_size=icon_size, colors=colors, reference=reference, **kw
    )
    miss = f" · sem ícone: {', '.join(r['missing'])}" if r["missing"] else ""
    _done("Items", f"{len(r['icons'])} ícones → [cyan]{r['atlas']}[/cyan]{miss}")


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


def _parse_batch_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    """Parse a text2d batch manifest file.

    Accepts either a JSON array of item objects or a single item object.
    Each item must contain ``id``, ``prompt`` and ``output``; missing keys
    raise ``ValueError`` listing the offending item index.

    Raises:
        json.JSONDecodeError: file is not valid JSON.
        OSError: file cannot be read.
        ValueError: parsed JSON is neither array nor object, or an item is
            missing required keys.
    """
    parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(parsed, dict):
        items: list[dict[str, Any]] = [parsed]
    elif isinstance(parsed, list):
        items = parsed
    else:
        raise ValueError(f"esperado array JSON ou objeto único, recebido {type(parsed).__name__}")
    for i, item in enumerate(items):
        missing = [k for k in ("id", "prompt", "output") if k not in item]
        if missing:
            raise ValueError(f"Item {i} falta: {', '.join(missing)}")
    return items


@cli.command("generate-batch")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.option("--output-dir", "-O", type=click.Path(), default=".", help="Diretório base para outputs relativos.")
@click.option("--width", "-W", default=1024, type=int)
@click.option("--height", "-H", default=1024, type=int)
@click.option(
    "--steps",
    "-s",
    default=16,
    show_default=True,
    type=int,
    help="Passos de inferência (faixa treinada 4-16)",
)
@click.option(
    "--guidance",
    "-g",
    "guidance_scale",
    default=None,
    type=float,
    help="[Ignorado] FLUX.3 Klein é guidance-distilled (sem CFG); mantido por compatibilidade.",
)
@click.option("--cpu", is_flag=True, help="Forçar CPU")
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
@click.option(
    "--hw-auto/--no-hw-auto",
    "hw_auto",
    default=True,
    show_default=True,
    help="Auto-detecção de hardware (offload/modelo/multi-GPU). Env: TEXT2D_HW_AUTO=0.",
)
@add_group_offload_option()
@click.option(
    "--quality",
    type=click.Choice(list(VALID_QUALITIES)),
    default="medium",
    show_default=True,
    help="Quality tier (fast / low / medium / high / highest).",
)
@click.option(
    "--category",
    type=click.Choice(["icon"]),
    default=None,
    help="Categoria global (icon: 512², flat, prompt de ícone). Item pode sobrepor.",
)
@click.option("--transparent", is_flag=True, help="Remover o fundo dos PNGs (requer PNG/WebP).")
@click.option("--svg/--no-svg", "svg", default=False, show_default=True, help="Gerar um SVG ao lado de cada PNG.")
@click.option("--svg-engine", type=click.Choice(list(SVG_ENGINES)), default="auto", show_default=True)
@click.option("--svg-style", type=click.Choice(list(SVG_STYLES)), default="flat", show_default=True)
@click.option("--svg-colors", type=click.IntRange(2, 64), default=8, show_default=True)
@click.option("--svg-detail", type=click.Choice(list(SVG_DETAILS)), default="medium", show_default=True)
@click.option(
    "--svg-enhance/--no-svg-enhance",
    "svg_enhance",
    default=True,
    help="Reescrever o prompt para arte vector-friendly (default: ligado).",
)
@click.option("--svg-max-tokens", type=click.IntRange(min=64), default=4000, show_default=True)
@click.option("--svg-seed", type=int, default=None)
@click.option("--svg-device", type=click.Choice(["auto", "cuda", "cpu"]), default="auto", show_default=True)
@click.option("--svg-preview", is_flag=True, help="Gravar <stem>_preview.png (requer cairosvg).")
@click.option(
    "--svg-meta/--no-svg-meta",
    "svg_meta",
    default=True,
    help="Escrever <stem>.svg.json (default: ligado).",
)
@click.option("--svg-strict", is_flag=True)
@click.option("--svg-max-kb", type=click.IntRange(min=1), default=2048, show_default=True)
@click.option(
    "--compile/--no-compile",
    "torch_compile",
    default=True,
    show_default=True,
    help="torch.compile no transformer (default ON em batch — amortiza cold).",
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
    default=True,
    show_default=True,
    help="channels_last NHWC (default ON em batch).",
)
@click.option("-v", "--verbose", "batch_verbose", is_flag=True)
@add_vramd_options
@click.pass_context
def generate_batch_cmd(
    ctx: click.Context,
    manifest: str,
    output_dir: str,
    width: int,
    height: int,
    steps: int,
    guidance_scale: float | None,
    cpu: bool,
    model_id: str | None,
    gpu_ids_str: str | None,
    force: bool,
    hw_auto: bool,
    group_offload: bool,
    batch_verbose: bool,
    quality: str,
    category: str | None,
    transparent: bool,
    svg: bool,
    svg_engine: str,
    svg_style: str,
    svg_colors: int,
    svg_detail: str,
    svg_enhance: bool,
    svg_max_tokens: int,
    svg_seed: int | None,
    svg_device: str,
    svg_preview: bool,
    svg_meta: bool,
    svg_strict: bool,
    svg_max_kb: int,
    torch_compile: bool,
    torch_compile_mode: str,
    channels_last: bool,
    vramd_priority: str | None,
    no_vramd: bool,
    vramd_stream: bool,
) -> None:
    """Gera múltiplas imagens a partir de um manifesto JSON (JSONL em stdout)."""
    global _batch_gen

    from aigamekit_shared.gpu import warn_if_vram_occupied

    # Alloc conf anti-fragmentação por modo (antes da 1ª alocação CUDA).
    from .hardware import apply_alloc_conf_early

    apply_alloc_conf_early(group_offload)

    manifest_path = Path(manifest)
    out_root = Path(output_dir)
    err_console = Console(stderr=True)

    try:
        items = _parse_batch_manifest(manifest_path)
    except (json.JSONDecodeError, OSError) as exc:
        console.print(f"[red]Manifesto inválido:[/red] {exc}")
        sys.exit(1)
    except ValueError as exc:
        console.print(f"[red]Manifesto inválido:[/red] {exc}")
        sys.exit(1)

    # QualityEngine: soft resolution — fills defaults when user didn't specify.
    _src = click.core.ParameterSource
    _user_set_width = ctx.get_parameter_source("width") not in (_src.DEFAULT,)
    _user_set_height = ctx.get_parameter_source("height") not in (_src.DEFAULT,)
    _user_set_steps = ctx.get_parameter_source("steps") not in (_src.DEFAULT,)
    _user_set_guidance = ctx.get_parameter_source("guidance_scale") not in (_src.DEFAULT,)

    from aigamekit_shared.quality import QualityEngine

    _qengine = QualityEngine()
    _qresolved = _qengine.resolve("text2d", quality=quality)
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

    from .hardware import detect_hardware_profile, hw_auto_enabled

    hwp = None
    mem_eff = False
    if hw_auto and hw_auto_enabled() and not cpu:
        hwp = detect_hardware_profile()
        if hwp.memory_efficient and hwp.device == "cuda":
            mem_eff = True

    mem_eff = mem_eff or cpu
    resolved_model = model_id or (hwp.model_id if hwp is not None else _model_id(memory_efficient=mem_eff))
    quant_preset = hwp.quant_preset if hwp is not None else None
    parsed_gpu_ids = [int(x.strip()) for x in gpu_ids_str.split(",")] if gpu_ids_str else None
    if parsed_gpu_ids is None and hwp is not None and hwp.gpu_ids:
        parsed_gpu_ids = hwp.gpu_ids
    if hwp is not None:
        err_console.print(f"[dim]Hardware (auto): {hwp.summary()}[/dim]")

    pending_inprocess: list[dict[str, Any]] = []
    svg_items: list[dict[str, Any]] = []

    try:
        # vramd por item (paridade Paint3D) — load in-process só se sobrar trabalho.
        for item in items:
            t0 = time.time()
            item_id = item["id"]
            raw_prompt = item["prompt"]
            item_category = item.get("category") or category
            item_transparent = bool(item.get("transparent", transparent))
            out_rel = Path(item["output"])
            requested_path = out_root / out_rel if not out_rel.is_absolute() else out_rel

            # --svg produz um sidecar; -o *.svg implica SVG e raster temporário.
            # O campo ``svg`` do item sobrepõe o flag global.
            item_svg_flag = item.get("svg")
            svg_requested_item = (
                bool(svg) if item_svg_flag is None else bool(item_svg_flag)
            ) or requested_path.suffix.lower() == ".svg"
            item_svg_opts: SvgOptions | None = None
            svg_path: Path | None = None
            raster_path = requested_path
            if svg_requested_item:
                item_svg_opts = SvgOptions(
                    engine=item.get("svg_engine") or svg_engine,
                    style=item.get("svg_style") or svg_style,
                    colors=int(item.get("svg_colors") or svg_colors),
                    detail=item.get("svg_detail") or svg_detail,
                    max_tokens=int(item.get("svg_max_tokens") or svg_max_tokens),
                    seed=(
                        item.get("svg_seed")
                        if item.get("svg_seed") is not None
                        else (svg_seed if svg_seed is not None else item.get("seed"))
                    ),
                    device=svg_device,
                    preview=svg_preview,
                    transparent=item_transparent,
                    enhance=svg_enhance,
                    meta=svg_meta,
                    strict=svg_strict,
                    max_kb=svg_max_kb,
                )
                try:
                    item_svg_opts.validate()
                except ValueError as exc:
                    emit_result(item_id, TOOL_TEXT2D, STATUS_ERROR, error=str(exc))
                    continue
                if requested_path.suffix.lower() == ".svg":
                    svg_path = requested_path
                    raster_path = requested_path.with_suffix(".png")
                else:
                    svg_path = (
                        Path(item["svg_output"])
                        if item.get("svg_output")
                        else requested_path.with_suffix(".svg")
                    )

            prompt = raw_prompt
            if svg_requested_item and svg_enhance and item_svg_opts is not None:
                prompt = enhance_prompt_for_svg(
                    prompt, style=item_svg_opts.style, colors=item_svg_opts.colors
                )

            raster_rel = (
                raster_path.relative_to(out_root)
                if raster_path.is_relative_to(out_root)
                else raster_path
            )

            if not force and raster_path.is_file():
                emit_result(item_id, TOOL_TEXT2D, STATUS_SKIPPED, output=str(raster_rel))
                if svg_requested_item and item_svg_opts is not None and svg_path is not None:
                    svg_items.extend(
                        _batch_svg_entries(
                            item_id,
                            raster_path,
                            svg_path,
                            int(item.get("num_images") or 1),
                            item_svg_opts,
                            prompt=prompt,
                            seed=item.get("seed"),
                            category=item_category,
                        )
                    )
                continue

            item_w = item.get("width", width)
            item_h = item.get("height", height)
            item_steps = item.get("steps", steps)
            item_guidance = item.get("guidance_scale", guidance_scale)
            item_seed = item.get("seed")
            item_refs = [str(Path(r).resolve()) for r in item.get("refs") or []] or None
            item_boxes = item.get("boxes") or None
            item_num_images = int(item.get("num_images") or 1)
            raster_path.parent.mkdir(parents=True, exist_ok=True)

            item_payload: dict[str, Any] = {
                "prompt": prompt,
                "output": str(raster_path.resolve()),
                "width": item_w,
                "height": item_h,
                "steps": item_steps,
                "seed": item_seed,
                "model_id": resolved_model,
                "torch_compile": torch_compile,
                "torch_compile_mode": torch_compile_mode,
                "channels_last": channels_last,
                "allow_group_offload": group_offload,
            }
            if item_guidance is not None:
                item_payload["guidance"] = item_guidance
            if item_refs:
                item_payload["refs"] = item_refs
            if item_boxes:
                item_payload["boxes"] = item_boxes
            if item_num_images > 1:
                item_payload["num_images"] = item_num_images
            if item_category is not None:
                item_payload["category"] = item_category
            if item_transparent:
                item_payload["transparent"] = True

            if not cpu and delegate_or_prepare(
                "text2d",
                payload=item_payload,
                t_start=t0,
                noun="Imagem",
                console=err_console,
                enabled=not no_vramd,
                priority=vramd_priority or "batch",
                stream=vramd_stream,
                gpu_ids=parsed_gpu_ids,
                memory_efficient=mem_eff,
                quant_preset=quant_preset,
                footprint_key=model_footprint_key(resolved_model),
            ):
                elapsed = time.time() - t0
                emit_result(item_id, TOOL_TEXT2D, STATUS_OK, output=str(raster_rel), seconds=round(elapsed, 3))
                if svg_requested_item and item_svg_opts is not None and svg_path is not None:
                    svg_items.extend(
                        _batch_svg_entries(
                            item_id,
                            raster_path,
                            svg_path,
                            item_num_images,
                            item_svg_opts,
                            prompt=prompt,
                            seed=item_seed,
                            category=item_category,
                        )
                    )
                continue

            pending_inprocess.append(
                {
                    "id": item_id,
                    "prompt": prompt,
                    "out_rel": out_rel,
                    "raster_rel": raster_rel,
                    "raster_path": raster_path,
                    "svg_path": svg_path,
                    "svg_opts": item_svg_opts,
                    "svg_requested": svg_requested_item,
                    "category": item_category,
                    "width": item_w,
                    "height": item_h,
                    "steps": item_steps,
                    "guidance": item_guidance,
                    "seed": item_seed,
                    "refs": item_refs,
                    "boxes": item_boxes,
                    "num_images": item_num_images,
                }
            )

        if not pending_inprocess:
            _batch_svg_pass(
                svg_items,
                priority=vramd_priority or "batch",
                stream=vramd_stream,
                gpu_ids=parsed_gpu_ids,
                no_vramd=no_vramd,
                verbose=batch_verbose,
                force=force,
            )
            return

        if not cpu:
            from .hardware import group_offload_needed_mib, group_offload_will_engage

            _qmode = quant_preset if quant_preset not in (None, "none") else ("sdnq-int4" if mem_eff else "none")
            if group_offload_will_engage():
                # GO+streams: pico ≈ ativação + trânsito de grupos; needed pela
                # maior resolução pendente (itens podem sobrepor o default).
                _max_side = max([max(it["width"], it["height"]) for it in pending_inprocess] or [width, height])
                needed_mib = group_offload_needed_mib(_max_side, _max_side)
            else:
                needed_mib = needed_mib_for_backend("text2d", quant_mode=_qmode, memory_efficient=mem_eff)
            prepare_gpu_exclusive(
                needed_mib=needed_mib,
                allow_shared=True,
                kill_others=False,
                backend="text2d",
                quant_mode=_qmode,
                console=err_console,
            )

        gen = KleinFluxGenerator(
            device="cpu" if cpu else None,
            memory_efficient=mem_eff,
            verbose=batch_verbose,
            model_id=resolved_model,
            gpu_ids=parsed_gpu_ids,
            quant_preset=quant_preset,
            torch_compile=torch_compile,
            torch_compile_mode=torch_compile_mode,
            channels_last=channels_last,
            group_offload=group_offload,
        )
        _batch_gen = gen

        with err_console.status("[bold yellow]A carregar pipeline...", spinner="dots"):
            gen.warmup()

        signal.signal(signal.SIGTERM, _batch_signal_handler)
        signal.signal(signal.SIGINT, _batch_signal_handler)

        for it in pending_inprocess:
            t0 = time.time()
            item_id = it["id"]
            try:
                emit_progress(item_id, TOOL_TEXT2D, phase="diffusion", percent=0)
                image, _metadata = gen.generate(
                    prompt=it["prompt"],
                    height=it["height"],
                    width=it["width"],
                    guidance_scale=it["guidance"],
                    num_inference_steps=it["steps"],
                    seed=it["seed"],
                    refs=it.get("refs"),
                    boxes=it.get("boxes"),
                    num_images=it.get("num_images") or 1,
                )
                emit_progress(item_id, TOOL_TEXT2D, phase="diffusion", percent=100)

                emit_progress(item_id, TOOL_TEXT2D, phase="save", percent=0)
                all_images = _metadata.get("images") or [image]
                img_format = _image_format_for(it["raster_path"])
                for idx, img in enumerate(all_images):
                    part_path = (
                        it["raster_path"]
                        if idx == 0
                        else it["raster_path"].with_name(
                            f"{it['raster_path'].stem}_{idx}{it['raster_path'].suffix}"
                        )
                    )
                    KleinFluxGenerator.save_image(img, part_path, image_format=img_format)
                emit_progress(item_id, TOOL_TEXT2D, phase="save", percent=100)

                elapsed = time.time() - t0
                emit_result(item_id, TOOL_TEXT2D, STATUS_OK, output=str(it["raster_rel"]), seconds=round(elapsed, 3))
                if it.get("svg_requested") and it.get("svg_opts") is not None and it.get("svg_path") is not None:
                    svg_items.extend(
                        _batch_svg_entries(
                            item_id,
                            it["raster_path"],
                            it["svg_path"],
                            int(it.get("num_images") or 1),
                            it["svg_opts"],
                            prompt=it["prompt"],
                            seed=it["seed"],
                            category=it.get("category"),
                        )
                    )
            except Exception as exc:
                emit_result(item_id, TOOL_TEXT2D, STATUS_ERROR, error=str(exc))

        _batch_cleanup()
        _batch_svg_pass(
            svg_items,
            priority=vramd_priority or "batch",
            stream=vramd_stream,
            gpu_ids=parsed_gpu_ids,
            no_vramd=no_vramd,
            verbose=batch_verbose,
            force=force,
        )
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
    from aigamekit_shared.cli_tables import render_info_table

    render_info_table(
        console,
        tool_name="text2d",
        extra_rows=[("Modelo (default)", default_model_id())],
        output_dir=str(DEFAULT_IMAGE_DIR.resolve()),
    )


@cli.command("doctor")
def doctor_cmd() -> None:
    """Verifica ambiente: PyTorch, CUDA, VRAM e cache HF."""
    from aigamekit_shared.gpu import (
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

    from .hardware import detect_hardware_profile, hw_auto_enabled

    _hwp = detect_hardware_profile()
    _state = "" if hw_auto_enabled() else " [yellow](desligado: TEXT2D_HW_AUTO=0)[/yellow]"
    table.add_row("Perfil hardware (auto)", f"{_hwp.summary()}{_state}")

    # Acesso ao repo gated (FLUX Kommunity License): falha comum de 1ª utilização.
    try:
        import diffusers

        table.add_row("diffusers", diffusers.__version__)
        from huggingface_hub import model_info

        model_info(default_model_id())
        table.add_row("Acesso ao modelo", "[green]OK (repo gated acessível)[/green]")
    except ImportError as e:
        table.add_row("diffusers", f"[red]não instalado ({e})[/red]")
    except Exception as e:
        table.add_row(
            "Acesso ao modelo",
            f"[red]SEM ACESSO — {type(e).__name__}[/red]\n"
            f"[yellow]Aceita a licença em https://huggingface.co/{default_model_id()} "
            "e configura HF_TOKEN.[/yellow]",
        )

    # text2svg: VTracer é core; StarVector é opcional (cache + CUDA).
    try:
        import vtracer

        version = getattr(vtracer, "__version__", "") or ""
        table.add_row("vtracer (CPU)", f"[green]OK[/green] {version}".strip())
    except ImportError:
        table.add_row("vtracer (CPU)", "[yellow]não instalado — `pip install vtracer`[/yellow]")

    try:
        import cairosvg  # noqa: F401

        table.add_row("cairosvg (preview)", "[green]OK[/green]")
    except ImportError:
        table.add_row(
            "cairosvg (preview)",
            "[dim]opcional — `pip install 'text2d[svg-preview]'`[/dim]",
        )

    from aigamekit_shared.model_download import is_model_cached

    from .svg.starvector import DEFAULT_MODEL_ID

    sv_cached = is_model_cached(DEFAULT_MODEL_ID)
    table.add_row(
        "StarVector-1B (cache)",
        "[green]OK[/green]"
        if sv_cached
        else "[yellow]não cacheado — o 1.º `--svg-engine starvector` descarrega ~5 GB[/yellow]",
    )

    console.print(table)


@cli.command("models")
def models_cmd() -> None:
    """Modelos suportados."""
    t = Table(title="[bold blue]Modelos", box=box.ROUNDED)
    t.add_column("ID", style="cyan")
    t.add_column("Notas", style="white")
    t.add_row(
        "black-forest-labs/flux-3-image-klein",
        "Padrão: 5.6B guidance-distilled + Qwen3-VL 4B (gated — aceitar a licença Kommunity no Hub). "
        "16 steps, refs até 10 imagens, layout por boxes.",
    )
    t.add_row(
        "black-forest-labs/flux-3-image-klein-base",
        "Base não-distilled (50 steps, CFG 3.5) — para finetunes/LoRA; via TEXT2D_MODEL_ID/--model.",
    )
    t.add_row(
        "black-forest-labs/flux-3-image-klein-pup",
        "Prompt upsampler 9B — carregado só com --upsample (expande o prompt curto em caption completa).",
    )
    t.add_row(
        "starvector/starvector-1b-im2svg",
        "SVG image→SVG (Apache-2.0, ~4-5 GB fp16). Ícones/logos/diagramas; NÃO usar em "
        "ilustrações/painterly. `--svg-engine starvector` ou `text2d vectorize --engine starvector`.",
    )
    t.add_row(
        "bigcode/starcoderbase-1b",
        "LLM base do StarVector (BigCode OpenRAIL-M). NÃO é descarregado: é gated e o "
        "checkpoint StarVector já traz o LLM fine-tuned + tokenizer.",
    )
    t.add_row(
        "vtracer (PyPI)",
        "Tracer clássico CPU (MIT) — default de `--svg`; quantiza a paleta e remove o fundo.",
    )
    console.print(t)
    console.print(
        Panel(
            "[dim]Override global: TEXT2D_MODEL_ID. Referências: --ref (até 10); "
            "silhuetas: text2d silhouette / --ref-silhouette.[/dim]",
            border_style="dim",
        )
    )


@cli.command("serve")
@click.option(
    "--ums-worker",
    is_flag=True,
    help=(
        "Modo worker subprocesso do vramd: lê comandos JSONL do stdin (load / "
        "generate / unload / shutdown) e emite eventos no stdout. Usado pelo "
        "SubprocessWorkerPool do ModelServer — text2d corre no seu próprio "
        "venv e o supervisor (ModelServer/.venv) coordena via JSONL."
    ),
)
def serve(ums_worker: bool) -> None:
    """Modo worker subprocesso do vramd (subprocess-per-backend).

    Sem ``--ums-worker`` não faz nada (futuro: modo server legacy).
    Com ``--ums-worker`` arranca o loop canónico
    :func:`aigamekit_shared.worker_serve.run_worker_loop` com o adapter text2d
    local (:mod:`text2d.worker_serve_adapter`).
    """
    from aigamekit_shared.worker_serve import run_ums_worker_cli
    from text2d.worker_serve_adapter import Adapter

    # Worker vramd: alloc conf por modo antes da 1ª alocação CUDA (o request
    # pode pedir GO; o adapter corrige por-request se o torch ainda não acordou).
    from .hardware import apply_alloc_conf_early

    apply_alloc_conf_early()

    run_ums_worker_cli(Adapter, tool_name="text2d", ums_worker=ums_worker, console=console)


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
