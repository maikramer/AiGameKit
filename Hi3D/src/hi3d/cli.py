"""CLI ``hi3d`` — refine, doctor, download-model e worker UMS."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from . import DEFAULT_MODEL_ID, __version__

EXIT_OK = 0
EXIT_ERROR = 1
#: Código próprio para "não disponível neste hardware/instalação" — o orquestrador
#: (GameAssets/Text3D) distingue skip gracioso de falha real.
EXIT_UNAVAILABLE = 3


def _console(verbose: bool) -> Any:
    from rich.console import Console

    return Console(stderr=not verbose)


def _emit_json(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="hi3d")
def cli() -> None:
    """Refinamento de malha image-conditioned com Hi3D v1.0 (SPARC3D)."""


@cli.command("refine")
@click.argument("coarse_mesh", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--image", "-i", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--resolution",
    type=str,
    default="auto",
    show_default=True,
    help="512|1024|1536 (passes 1|2|3) ou 'auto' pela VRAM livre.",
)
@click.option("--steps", "num_inference_steps", type=int, default=25, show_default=True)
@click.option("--seed", type=int, default=0, show_default=True)
@click.option("--model", default=None, help=f"Diretório local ou repo id HF (defeito: {DEFAULT_MODEL_ID}).")
@click.option(
    "--offload/--no-offload",
    "offload",
    default=None,
    help="Offload sequencial de CPU. Omitido = auto pela VRAM livre.",
)
@click.option(
    "--staging/--no-staging",
    default=None,
    help=(
        "Residência por fase (um componente na GPU de cada vez). Omitido = auto "
        "pela VRAM livre. Medido: 136 s -> 58 s a 512³/25 passos numa GPU de 6 GB; "
        "--no-staging usa o offload sequencial do upstream (~0.9 GiB de pico)."
    ),
)
@click.option(
    "--fast-postprocess",
    is_flag=True,
    default=False,
    help="Salta o filtro de componentes do upstream (~2.8 s); o strip_debris pós-decimação já o cobre.",
)
@click.option("--remove-background", is_flag=True, default=False)
@click.option("--target-faces", type=int, default=0, show_default=True, help="Decimar após o refine (0 = manter).")
@click.option("--align/--no-align", "align", default=True, show_default=True, help="Repor escala/origem do coarse.")
@click.option("--local-files-only", is_flag=True, default=False)
@click.option("--dino-model", default=None, help="Repo id/pasta do DINOv2 (offline).")
@click.option("--clip-model", default=None, help="Repo id/pasta do CLIP ViT-L/14 (offline).")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.option("--verbose", "-v", is_flag=True, default=False)
def refine_cmd(
    coarse_mesh: Path,
    image: Path,
    output: Path,
    resolution: str,
    num_inference_steps: int,
    seed: int,
    model: str | None,
    offload: bool | None,
    staging: bool | None,
    fast_postprocess: bool,
    remove_background: bool,
    target_faces: int,
    align: bool,
    local_files_only: bool,
    dino_model: str | None,
    clip_model: str | None,
    as_json: bool,
    verbose: bool,
) -> None:
    """Refina COARSE_MESH com IMAGE e escreve um GLB de geometria.

    \b
        hi3d refine clean.glb -i ref.png -o refined.glb
    """
    from .capability import pick_gpu, plan_for_gpu, query_gpus
    from .refine import refine_mesh, stack_ready

    console = _console(verbose)
    ready, _why = stack_ready()
    plan = plan_for_gpu(pick_gpu(query_gpus()), stack_ready=ready)
    if not plan.available:
        payload = {"ok": False, "available": False, "reason": plan.reason}
        if as_json:
            _emit_json(payload)
        else:
            console.print(f"[yellow]Hi3D indisponível:[/] {plan.reason}")
        raise SystemExit(EXIT_UNAVAILABLE)

    if resolution == "auto":
        res = plan.resolution
    else:
        try:
            res = int(resolution)
        except ValueError:
            raise click.BadParameter(f"resolution inválida: {resolution!r}") from None

    # ``plan.offload`` é informativo (política de VRAM); só o flag explícito do
    # utilizador força o offload sequencial — senão nunca haveria residência por fase.

    def _progress(pct: float | None, msg: str | None) -> None:
        if as_json or verbose:
            return
        if pct is not None:
            bar = "#" * int(pct * 20)
            console.print(f"\r[cyan]{bar:<20}[/] {pct * 100:5.1f}% {msg or ''}", end="")

    try:
        result = refine_mesh(
            coarse_mesh,
            image,
            output,
            resolution=res,
            num_inference_steps=num_inference_steps,
            seed=seed,
            model=model,
            offload=offload,
            staging=staging,
            fast_postprocess=fast_postprocess,
            remove_background=remove_background,
            target_faces=target_faces,
            align_to_coarse=align,
            local_files_only=local_files_only,
            dino_model=dino_model,
            clip_model=clip_model,
            progress=_progress,
        )
    except (RuntimeError, ValueError, OSError) as exc:
        if as_json:
            _emit_json({"ok": False, "available": True, "error": str(exc)})
        else:
            console.print(f"[red]refine falhou:[/] {exc}")
        raise SystemExit(EXIT_ERROR) from None

    payload = {"ok": True, "available": True, **result.as_dict()}
    if as_json:
        _emit_json(payload)
    else:
        console.print()
        console.print(
            f"[green]✓[/] {result.output} [dim]({result.faces} faces, {result.elapsed_sec:.0f}s, "
            f"@{result.resolution}³, {result.mode})[/]"
        )


@cli.command("doctor")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.option("--verbose", "-v", is_flag=True, default=False)
def doctor_cmd(as_json: bool, verbose: bool) -> None:
    """Diagnóstico: stack, GPU, pesos e plano de execução."""
    from .capability import pick_gpu, plan_for_gpu, query_gpus
    from .paths import default_model_dir, is_complete, missing_artifacts, resolve_model_ref, weight_bytes
    from .refine import stack_status

    console = _console(verbose)
    st = stack_status()
    gpus = query_gpus()
    gpu = pick_gpu(gpus)
    ready = all(st.get(m) for m in ("torch", "flash_attn", "spconv", "sparc3d"))
    plan = plan_for_gpu(gpu, stack_ready=ready)
    model_dir = default_model_dir()
    weights = weight_bytes(model_dir) if model_dir.is_dir() else 0

    import platform

    payload: dict[str, Any] = {
        "ok": plan.available,
        "version": __version__,
        "python": platform.python_version(),
        "python_ok": sys.version_info[:2] == (3, 10),
        "modules": st,
        "gpu": None
        if gpu is None
        else {
            "name": gpu.name,
            "total_mib": gpu.total_mib,
            "free_mib": gpu.free_mib,
            "compute_cap": None if gpu.compute_cap is None else list(gpu.compute_cap),
            "supported": gpu.supported,
        },
        "model": {
            "dir": str(model_dir),
            "complete": is_complete(model_dir),
            "weights_gb": round(weights / 1e9, 2),
            "missing": missing_artifacts(model_dir),
            "resolved": resolve_model_ref(None),
        },
        "plan": {
            "available": plan.available,
            "reason": plan.reason,
            "resolution": plan.resolution,
            "offload": plan.offload,
            "staging": plan.staging,
            "mode": "staged" if plan.staging else ("sequential" if plan.offload else "full"),
        },
    }
    if as_json:
        _emit_json(payload)
        return

    console.print("[bold]Hi3D v1.0[/] — diagnóstico")
    console.print(f"  python      {payload['python']} {'ok' if payload['python_ok'] else '[red]esperado 3.10[/]'}")
    for name, ok in st.items():
        console.print(f"  {name:<11} {'[green]ok[/]' if ok else '[red]ausente[/]'}")
    if gpu is None:
        console.print("  gpu         [red]nenhuma NVIDIA visível[/]")
    else:
        cc = "?" if gpu.compute_cap is None else f"{gpu.compute_cap[0]}.{gpu.compute_cap[1]}"
        console.print(f"  gpu         {gpu.name} ({gpu.total_mib} MiB, {gpu.free_mib} MiB livres, CC {cc})")
    console.print(
        f"  modelo      {payload['model']['weights_gb']} GB em {model_dir} "
        f"({'completo' if payload['model']['complete'] else 'incompleto'})"
    )
    if plan.available:
        console.print(
            f"  plano       [green]@{plan.resolution}³[/], "
            + ("residência por fase" if plan.staging else ("offload sequencial" if plan.offload else "tudo na GPU"))
        )
    else:
        console.print(f"  plano       [yellow]indisponível[/]: {plan.reason}")


@cli.command("download-model")
@click.option("--model", default=DEFAULT_MODEL_ID, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
def download_model_cmd(model: str, as_json: bool) -> None:
    """Descarrega os 8 artefactos (~7.7 GB) para a cache do monorepo."""
    from .paths import default_model_dir, is_complete, weight_bytes

    target = default_model_dir(model)
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            model,
            local_dir=str(target),
            allow_patterns=["*.json", "*.safetensors"],
            ignore_patterns=["assets/*"],
        )
    except Exception as exc:
        if as_json:
            _emit_json({"ok": False, "error": str(exc)})
        else:
            click.echo(f"download falhou: {exc}", err=True)
        raise SystemExit(EXIT_ERROR) from None

    payload = {
        "ok": is_complete(target),
        "dir": str(target),
        "weights_gb": round(weight_bytes(target) / 1e9, 2),
    }
    if as_json:
        _emit_json(payload)
    else:
        click.echo(f"modelo em {target} ({payload['weights_gb']} GB)")


@cli.command("serve")
@click.option("--ums-worker", is_flag=True, default=False, help="Modo worker JSONL do vramd.")
def serve_cmd(ums_worker: bool) -> None:
    """Worker persistente do vramd (``serve --ums-worker``)."""
    if not ums_worker:
        raise click.UsageError("use --ums-worker (o vramd é o único cliente deste modo)")
    from .ums import run_worker

    raise SystemExit(run_worker())


def main() -> None:
    """Entry point do console script."""
    cli()


if __name__ == "__main__":
    main()
