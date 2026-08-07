#!/usr/bin/env python3
"""Motion3D — CLI text-to-motion (Tencent HY-Motion-1.0) · NPZ + bpy GLB."""

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
    """Text-to-motion via HY-Motion-1.0 → NPZ joints @ 30fps / GLB (bpy)."""
    ctx.ensure_object(dict)
    ctx.obj["VERBOSE"] = verbose


@cli.command("generate")
@click.argument("prompt")
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    required=True,
    help="Output .npz (joints) or .glb (bpy armature animation)",
)
@click.option("--duration", type=float, default=None, help="Motion length in seconds (@30fps)")
@click.option(
    "--frames", "max_frames", type=int, default=None, help="Cap length in frames (overrides via duration=frames/30)"
)
@click.option("--seed", type=int, default=None, help="RNG seed")
@click.option("--cfg-scale", type=float, default=None, help="Classifier-free guidance scale")
@click.option(
    "--model",
    "model_name",
    type=click.Choice(["lite", "full"], case_sensitive=False),
    default=None,
    help="HY-Motion variant (default: lite; quality=highest → full)",
)
@click.option(
    "--quality",
    type=click.Choice(QUALITY_TIERS, case_sensitive=False),
    default="medium",
    show_default=True,
    help="Quality tier (duration / cfg_scale / Lite|Full soft defaults)",
)
@click.option("--category", type=str, default=None, help="Asset category (QualityEngine)")
@click.option(
    "--also-npz",
    is_flag=True,
    help="When writing .glb, also save sibling .npz with joints",
)
@click.option("--gpu-ids", type=str, default=None, help="GPU ids (UMS load opts / in-process)")
@click.option(
    "--sdnq-preset",
    type=str,
    default=None,
    help="SDNQ preset for DiT (none|sdnq-uint8|sdnq-int4); hw-auto fills when omitted",
)
@add_ums_options
@click.pass_context
def generate_cmd(
    ctx: click.Context,
    prompt: str,
    output: str,
    duration: float | None,
    max_frames: int | None,
    seed: int | None,
    cfg_scale: float | None,
    model_name: str | None,
    quality: str,
    category: str | None,
    also_npz: bool,
    gpu_ids: str | None,
    sdnq_preset: str | None,
    ums_priority: str,
    no_ums: bool,
    ums_stream: bool,
) -> None:
    """Generate motion from text → NPZ and/or animated GLB (bpy)."""
    validation_steps: int | None = None
    if (
        ctx.get_parameter_source("duration") == ParameterSource.DEFAULT
        or duration is None
        or max_frames is None
        or cfg_scale is None
        or model_name is None
    ):
        try:
            from aigamekit_shared.quality import QualityEngine

            resolved = QualityEngine().resolve(tool="motion3d", quality=quality, category=category)
            params = resolved.params
            if duration is None and "duration" in params:
                duration = float(params["duration"])
            if max_frames is None and "max_frames" in params:
                max_frames = int(params["max_frames"])
            if cfg_scale is None and "cfg_scale" in params:
                cfg_scale = float(params["cfg_scale"])
            if model_name is None and "model" in params:
                model_name = str(params["model"])
            if "validation_steps" in params:
                validation_steps = int(params["validation_steps"])
        except Exception:
            pass

    out_path = Path(output).expanduser().resolve()
    gpu_list = _parse_gpu_ids(gpu_ids)
    user_set_model = ctx.get_parameter_source("model_name") != ParameterSource.DEFAULT

    mem_eff = False
    allow_go = False
    offload_text = False
    if hw_auto_enabled():
        profile = detect_hardware_profile()
        # Text2D-style: planner picks Full vs Lite when user did not pass --model.
        # Quality soft-fill is a hint; hw-auto may upgrade Lite→Full on ~6GB+offload.
        if not user_set_model:
            model_name = profile.model
        if sdnq_preset is None and profile.sdnq_preset:
            sdnq_preset = profile.sdnq_preset
        if ctx.get_parameter_source("cfg_scale") == ParameterSource.DEFAULT or cfg_scale is None:
            cfg_scale = profile.cfg_scale
        if validation_steps is None or profile.validation_steps < validation_steps:
            validation_steps = profile.validation_steps
        if duration is None and profile.duration_cap_s is not None:
            duration = profile.duration_cap_s
        elif duration is not None and profile.duration_cap_s is not None:
            duration = min(float(duration), float(profile.duration_cap_s))
        mem_eff = bool(profile.memory_efficient)
        allow_go = bool(profile.allow_group_offload)
        offload_text = bool(profile.offload_text_encoder)
        if ctx.obj.get("VERBOSE"):
            console.print(f"[dim]hw-auto:[/dim] {profile.summary()}")

    if model_name is None:
        model_name = "full" if quality == "highest" else "lite"
    model_name = "full" if str(model_name).lower() == "full" else "lite"

    if sdnq_preset is None:
        sdnq_preset = "sdnq-int4" if mem_eff else "none"

    payload = build_generate_request(
        prompt=prompt,
        output=str(out_path),
        duration=duration,
        max_frames=max_frames,
        seed=seed,
        cfg_scale=cfg_scale,
        model=model_name,
        sdnq_preset=sdnq_preset,
        memory_efficient=mem_eff,
        allow_group_offload=allow_go,
        offload_text_encoder=offload_text,
        validation_steps=validation_steps,
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
        memory_efficient=mem_eff,
        sdnq_preset=sdnq_preset,
    ):
        return

    needed = needed_mib_for_backend("motion3d", memory_efficient=mem_eff, quant_mode=sdnq_preset)
    prepare_gpu_exclusive(needed_mib=needed, console=console)

    device = "cuda" if _cuda_available() else "cpu"
    console.print(Panel(f"[bold]{prompt}[/bold]", title="Prompt", border_style="cyan"))

    gen = MotionGenerator.get_instance(
        device=device,
        model=model_name,  # type: ignore[arg-type]
        sdnq_preset=None if sdnq_preset in (None, "none") else sdnq_preset,
        memory_efficient=mem_eff,
        offload_text_encoder=offload_text,
        validation_steps=validation_steps,
    )
    saved = gen.generate(
        prompt=prompt,
        output=out_path,
        duration=duration,
        max_frames=max_frames,
        seed=seed,
        cfg_scale=cfg_scale,
        also_npz=also_npz,
        metadata={"quality": quality or "", "category": category or "", "model": model_name},
    )
    elapsed = time.perf_counter() - t0
    emit_result(id="generate", tool=TOOL_MOTION3D, status=STATUS_OK, phase="generate", output=str(saved))
    console.print(
        Panel(
            f"[green]Saved[/green] [cyan]{saved}[/cyan]\n"
            f"[dim]{elapsed:.1f}s · {model_name} · peak~"
            f"{estimate_peak_mib(model=model_name, sdnq_preset=sdnq_preset, memory_efficient=mem_eff)} MiB[/dim]",
            title="Motion3D",
            border_style="green",
        )
    )


@cli.command("export-glb")
@click.argument("npz_path", type=click.Path(exists=True))
@click.option("-o", "--output", type=click.Path(), required=True, help="Output .glb path")
@click.option("--fps", type=int, default=30, show_default=True, help="Bake FPS")
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
@click.option(
    "--arm-neutral",
    type=click.Choice(["auto", "on", "off"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Soft hang arm calibration: auto skips on raised-arm clips (chop/reach)",
)
@click.option(
    "--max-lean",
    "max_lean_deg",
    type=float,
    default=None,
    help="Cap torso tilt off vertical in degrees (keeps a swing from folding over)",
)
@click.option(
    "--hands-together",
    "hands_together_m",
    type=float,
    default=None,
    help="Hold both wrists within N meters (two-hand prop: axe, staff, greatsword)",
)
@click.option(
    "--plant-feet",
    is_flag=True,
    default=False,
    help="Freeze the stance horizontally (stationary actions)",
)
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
            arm_neutral=arm_neutral,
            max_lean_deg=max_lean_deg,
            hands_together_m=hands_together_m,
            plant_feet=plant_feet,
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


@cli.command("pack-rigged")
@click.argument("rigged_glb", type=click.Path(exists=True))
@click.option("-o", "--output", type=click.Path(), required=True, help="Skinned multi-clip GLB")
@click.option(
    "-m",
    "--motion",
    "motions",
    multiple=True,
    required=True,
    help="Clip as name=path.npz (repeatable). Ex: -m walk=walk.npz -m jump=jump.npz",
)
@click.option("--profile", "profile_name", default="hml22", show_default=True, help="Animator3D retarget profile")
@click.option(
    "--keep-sources",
    type=click.Path(),
    default=None,
    help="Directory for intermediate HML22 source GLBs",
)
@click.option(
    "--active",
    "active_clip",
    default=None,
    help="Clip name set as active action (default: first --motion)",
)
@click.option(
    "--in-place/--root-motion",
    "in_place",
    default=True,
    show_default=True,
    help="Loopable clips (no travel/yaw drift) vs keep root motion",
)
@click.option(
    "--arm-neutral",
    type=click.Choice(["auto", "on", "off"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Soft hang arm calibration: auto skips on raised-arm clips",
)
@click.option(
    "--max-lean",
    "max_lean_deg",
    type=float,
    default=None,
    help="Cap torso tilt off vertical in degrees",
)
@click.option(
    "--hands-together",
    "hands_together_m",
    type=float,
    default=None,
    help="Hold both wrists within N meters (two-hand prop)",
)
@click.option(
    "--plant-feet",
    is_flag=True,
    default=False,
    help="Freeze the stance horizontally (stationary actions)",
)
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
    """Pack several Motion3D NPZs onto one SkinTokens ``*_rigged.glb``.

    Each ``--motion name=path.npz`` becomes a named glTF animation on the same
    mesh/skin. Export uses ACTIONS mode (same as Animator3D game-pack).
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
        console.print(f"[red]pack-rigged failed:[/red] {exc}")
        raise SystemExit(1) from exc

    lines = [f"[green]Saved[/green] [cyan]{res['output']}[/cyan]", f"active={res['active']!r}"]
    for item in res["clips"]:
        if "error" in item:
            lines.append(f"  {item.get('clip')!r}: [red]{item['error']}[/red]")
        else:
            lines.append(f"  {item.get('clip')!r}: bones={item.get('bones_mapped')} frames={item.get('frames')}")
    console.print(
        Panel(
            "\n".join(lines),
            title="pack-rigged (Motion3D → multi-clip GLB)",
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
    _check_import(table, "yaml", label="PyYAML")
    _check_import(table, "einops", optional=True)
    _check_import(table, "omegaconf", optional=True)
    _check_import(table, "torchdiffeq", optional=True)
    _check_import(table, "huggingface_hub")
    _check_import(table, "transformers", optional=True)
    _check_import(table, "scipy", optional=True)
    _check_import(table, "bpy", optional=False, label="bpy (GLB export)")

    table.add_row("HF repo", HF_REPO)
    table.add_row("Cache dir", str(CACHE_DIR))
    table.add_row("CUDA", "yes" if _cuda_available() else "no")

    peak_lite = estimate_peak_mib(model="lite", sdnq_preset="sdnq-int4", memory_efficient=True)
    table.add_row("Peak estimate (lite/int4)", f"~{peak_lite} MiB")

    try:
        from aigamekit_shared.model_server import fetch_ums_queue_snapshot

        snap = fetch_ums_queue_snapshot()
        table.add_row("UMS", "[green]up[/green]" if snap is not None else "[yellow]down[/yellow]")
    except Exception as exc:
        table.add_row("UMS", f"[yellow]n/a[/yellow] ({exc})")

    try:
        paths = ensure_weights(model="lite")
        table.add_row("Weights (lite)", f"[green]ok[/green] ({paths.ckpt})")
    except Exception as exc:
        table.add_row("Weights (lite)", f"[red]missing[/red] ({exc})")

    try:
        free = _free_vram_mib()
        if free is not None:
            ok = free >= peak_lite
            table.add_row(
                "Free VRAM vs peak",
                f"{'[green]' if ok else '[yellow]'}{free} MiB free / need ~{peak_lite}[/]",
            )
    except Exception:
        pass

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

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _free_vram_mib() -> int | None:
    try:
        from aigamekit_shared.gpu import get_gpu_info

        info = get_gpu_info()
        if not info:
            return None
        # Prefer free MiB from first GPU.
        g0 = info[0] if isinstance(info, list) else info
        free = getattr(g0, "free_mib", None) or (g0.get("free_mib") if isinstance(g0, dict) else None)
        return int(free) if free is not None else None
    except Exception:
        return None


def _check_import(table: Table, name: str, *, optional: bool = False, label: str | None = None) -> None:
    label = label or name
    try:
        __import__(name)
        table.add_row(label, "[green]ok[/green]")
    except Exception as exc:
        status = f"[yellow]missing[/yellow] ({exc})" if optional else f"[red]missing[/red] ({exc})"
        table.add_row(label, status)


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
