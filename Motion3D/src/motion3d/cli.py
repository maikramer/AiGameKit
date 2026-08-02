#!/usr/bin/env python3
"""Motion3D — CLI text-to-motion (Motius T2M-GPT HumanML3D) · NPZ + bpy GLB."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from click.core import ParameterSource
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aigamekit_shared.cli_helpers import (
    add_ums_options,
    delegate_or_prepare,
    needed_mib_for_backend,
    prepare_gpu_exclusive,
)
from aigamekit_shared.progress import STATUS_OK, TOOL_MOTION3D, emit_result

from . import __version__
from .cli_rich import RICH_CLICK, click  # noqa: F401
from .generator import MotionGenerator
from .hardware import detect_hardware_profile, estimate_peak_mib, hw_auto_enabled
from .ums_payload import build_generate_request
from .weights import CACHE_DIR, HF_REPO, ensure_weights

console = Console()

QUALITY_TIERS = ("fast", "low", "medium", "high", "highest")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="motion3d")
@click.option("--verbose", "-v", is_flag=True, help="Logs detalhados")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Text-to-motion via Motius T2M-GPT (HumanML3D-263) → NPZ / GLB (bpy)."""
    ctx.ensure_object(dict)
    ctx.obj["VERBOSE"] = verbose


@cli.command("generate")
@click.argument("prompt")
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    required=True,
    help="Output .npz (features+joints) or .glb (HML22 source armature; use apply-rigged for skinned)",
)
@click.option("--frames", "max_frames", type=int, default=None, help="Cap decoded motion length")
@click.option("--seed", type=int, default=None, help="RNG seed")
@click.option("--temperature", type=float, default=None, help=">0 enables categorical sampling")
@click.option(
    "--quality",
    type=click.Choice(QUALITY_TIERS, case_sensitive=False),
    default="medium",
    show_default=True,
    help="Quality tier (max_frames / temperature soft defaults)",
)
@click.option("--category", type=str, default=None, help="Asset category (QualityEngine)")
@click.option(
    "--also-npz",
    is_flag=True,
    help="When writing .glb, also save sibling .npz with hml263+joints",
)
@click.option("--gpu-ids", type=str, default=None, help="GPU ids (UMS load opts / in-process)")
@add_ums_options
@click.pass_context
def generate_cmd(
    ctx: click.Context,
    prompt: str,
    output: str,
    max_frames: int | None,
    seed: int | None,
    temperature: float | None,
    quality: str,
    category: str | None,
    also_npz: bool,
    gpu_ids: str | None,
    ums_priority: str,
    no_ums: bool,
    ums_stream: bool,
) -> None:
    """Generate motion from text → NPZ and/or animated GLB (bpy)."""
    # Soft quality defaults (CLI explicit wins).
    if ctx.get_parameter_source("max_frames") == ParameterSource.DEFAULT or max_frames is None:
        try:
            from aigamekit_shared.quality import QualityEngine

            resolved = QualityEngine().resolve(tool="motion3d", quality=quality, category=category)
            if max_frames is None and "max_frames" in resolved.params:
                max_frames = int(resolved.params["max_frames"])
            if (
                ctx.get_parameter_source("temperature") == ParameterSource.DEFAULT
                and temperature is None
                and "temperature" in resolved.params
            ):
                temperature = float(resolved.params["temperature"])
        except Exception:
            pass

    out_path = Path(output).expanduser().resolve()
    gpu_list = _parse_gpu_ids(gpu_ids)
    half = False
    if hw_auto_enabled():
        profile = detect_hardware_profile()
        half = bool(profile.half)
        if ctx.obj.get("VERBOSE"):
            console.print(f"[dim]hw-auto:[/dim] {profile.summary()}")

    payload = build_generate_request(
        prompt=prompt,
        output=str(out_path),
        max_frames=max_frames,
        seed=seed,
        temperature=temperature,
        half_precision=half,
        gpu_ids=gpu_list,
        quality=quality,
        category=category,
        extra={"also_npz": also_npz},
    )

    t0 = time.perf_counter()
    if delegate_or_prepare(
        "motion3d",
        payload=payload,
        t_start=t0,
        noun="Motion",
        console=console,
        enabled=not no_ums,
        priority=ums_priority,
        stream=ums_stream,
        gpu_ids=gpu_list,
        memory_efficient=half,
    ):
        return

    # In-process fallback (after UMS decline / --no-ums).
    needed = needed_mib_for_backend("motion3d", memory_efficient=half)
    prepare_gpu_exclusive(needed_mib=needed, console=console)

    device = "cuda" if _cuda_available() else "cpu"
    console.print(Panel(f"[bold]{prompt}[/bold]", title="Prompt", border_style="cyan"))

    gen = MotionGenerator.get_instance(device=device)
    saved = gen.generate(
        prompt=prompt,
        output=out_path,
        max_frames=max_frames,
        seed=seed,
        temperature=temperature,
        also_npz=also_npz,
        metadata={"quality": quality or "", "category": category or ""},
    )
    elapsed = time.perf_counter() - t0
    emit_result(id="generate", tool=TOOL_MOTION3D, status=STATUS_OK, phase="generate", output=str(saved))
    console.print(
        Panel(
            f"[green]Saved[/green] [cyan]{saved}[/cyan]\n"
            f"[dim]{elapsed:.1f}s · peak~{estimate_peak_mib(half=half)} MiB[/dim]",
            title="Motion3D",
            border_style="green",
        )
    )


@cli.command("export-glb")
@click.argument("npz_path", type=click.Path(exists=True))
@click.option("-o", "--output", type=click.Path(), required=True, help="Output .glb path")
@click.option("--fps", type=int, default=20, show_default=True, help="Bake FPS")
@click.option("--clip", "clip_name", default="t2m_motion", show_default=True, help="Source action name")
@click.option(
    "--in-place/--root-motion",
    "in_place",
    default=True,
    show_default=True,
    help="Loopable clip (no travel/yaw drift) vs keep root motion",
)
def export_glb_cmd(npz_path: str, output: str, fps: int, clip_name: str, in_place: bool) -> None:
    """Convert NPZ ``joints`` → HML22 source GLB (look-at bake, SkinTokens names)."""
    import numpy as np

    from .bpy_export import export_joints_glb

    data = np.load(npz_path, allow_pickle=True)
    if "joints" not in data:
        console.print("[red]NPZ missing 'joints' array[/red]")
        sys.exit(1)
    joints = np.asarray(data["joints"], dtype=np.float32)
    if "fps" in data:
        fps = int(data["fps"])
    saved = export_joints_glb(joints, output, fps=fps, clip_name=clip_name, in_place=in_place)
    console.print(Panel(f"[green]Saved[/green] [cyan]{saved}[/cyan]", title="export-glb"))


@cli.command("apply-rigged")
@click.argument("npz_path", type=click.Path(exists=True))
@click.argument("rigged_glb", type=click.Path(exists=True))
@click.option("-o", "--output", type=click.Path(), required=True, help="Skinned animated GLB")
@click.option("--clip", "clip_name", default="walk", show_default=True, help="Output clip name")
@click.option("--profile", "profile_name", default="hml22", show_default=True, help="Animator3D retarget profile")
@click.option(
    "--keep-source",
    type=click.Path(),
    default=None,
    help="Also write intermediate HML22 source GLB",
)
@click.option(
    "--in-place/--root-motion",
    "in_place",
    default=True,
    show_default=True,
    help="Loopable clip (no travel/yaw drift) vs keep root motion",
)
def apply_rigged_cmd(
    npz_path: str,
    rigged_glb: str,
    output: str,
    clip_name: str,
    profile_name: str,
    keep_source: str | None,
    in_place: bool,
) -> None:
    """Motion3D NPZ → Animator3D retarget onto SkinTokens ``*_rigged.glb``."""
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
        )
    except Exception as exc:
        console.print(f"[red]apply-rigged failed:[/red] {exc}")
        raise SystemExit(1) from exc
    rt = res["retarget"]
    console.print(
        Panel(
            f"[green]Saved[/green] [cyan]{res['output']}[/cyan]\n"
            f"clip={rt.get('clip')!r} bones={rt.get('bones_mapped')} "
            f"frames={rt.get('frames')}",
            title="apply-rigged (Motion3D → Animator3D)",
            border_style="green",
        )
    )


@cli.command("doctor")
def doctor_cmd() -> None:
    """Check deps, bpy, cache layout, UMS reachability, optional weights."""
    table = Table(title="Motion3D doctor", show_header=True)
    table.add_column("Check")
    table.add_column("Status")

    _check_import(table, "torch")
    _check_import(table, "numpy")
    _check_import(table, "safetensors")
    _check_import(table, "huggingface_hub")
    _check_import(table, "transformers", optional=True)
    _check_import(table, "clip", optional=True, label="openai CLIP (optional)")
    _check_import(table, "bpy", optional=False, label="bpy (GLB export)")
    _check_import(table, "animator3d", optional=False, label="animator3d (apply-rigged)")

    table.add_row("HF repo", HF_REPO)
    table.add_row("Retarget profile", "hml22 (Animator3D)")
    table.add_row("Cache dir", str(CACHE_DIR))
    table.add_row("CUDA", "yes" if _cuda_available() else "no")
    table.add_row("Peak estimate", f"~{estimate_peak_mib()} MiB")

    try:
        from aigamekit_shared.model_server import fetch_ums_queue_snapshot

        snap = fetch_ums_queue_snapshot()
        table.add_row("UMS", "[green]up[/green]" if snap is not None else "[yellow]down[/yellow]")
    except Exception as exc:
        table.add_row("UMS", f"[yellow]n/a[/yellow] ({exc})")

    try:
        paths = ensure_weights()
        table.add_row("Weights", f"[green]ok[/green] ({paths.root})")
    except Exception as exc:
        table.add_row("Weights", f"[red]missing[/red] ({exc})")

    console.print(table)


@cli.command("serve")
@click.option(
    "--ums-worker",
    is_flag=True,
    help="Modo worker subprocesso UMS (JSONL stdin/stdout).",
)
def serve(ums_worker: bool) -> None:
    """Modo worker subprocesso do UMS (subprocess-per-backend)."""
    from aigamekit_shared.worker_serve import run_ums_worker_cli
    from motion3d.worker_serve_adapter import Adapter

    run_ums_worker_cli(Adapter, tool_name="motion3d", ums_worker=ums_worker, console=console)


def _parse_gpu_ids(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out or None


def _cuda_available() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        return False


def _check_import(
    table: Table,
    module: str,
    *,
    optional: bool = False,
    label: str | None = None,
) -> None:
    name = label or module
    try:
        __import__(module)
        table.add_row(name, "[green]ok[/green]")
    except ImportError:
        status = "[yellow]optional[/yellow]" if optional else "[red]missing[/red]"
        table.add_row(name, status)


def main() -> None:
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
