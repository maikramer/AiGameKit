"""CLI do intrinsic_worker: `serve --ums-worker` (vramd) + `decompose` standalone."""

from __future__ import annotations

import click


@click.group()
@click.version_option(package_name="intrinsic-worker")
def cli() -> None:
    """Decomposição intrínseca (compphoto/Intrinsic) — backend vramd."""


@cli.command("serve")
@click.option("--ums-worker", is_flag=True, help="Modo worker JSONL do vramd (stdin/stdout).")
def serve(ums_worker: bool) -> None:
    """Serve como worker persistente do vramd (ou servidor legado)."""
    from aigamekit_shared.worker_serve import run_ums_worker_cli
    from intrinsic_worker.worker_serve_adapter import Adapter

    run_ums_worker_cli(Adapter, tool_name="intrinsic", ums_worker=ums_worker)


@cli.command("decompose")
@click.argument("image_path", type=click.Path(exists=True))
@click.option(
    "-o",
    "--output-dir",
    default=".",
    show_default=True,
    help="Diretório dos PNGs de saída (albedo/shading/specular).",
)
@click.option("--release", default="v2.1", show_default=True, help="Release dos pesos (v2 | v2.1).")
@click.option("--device", default="cuda", show_default=True, help="Dispositivo (cuda | cpu).")
def decompose(image_path: str, output_dir: str, release: str, device: str) -> None:
    """Decomposição standalone (sem vramd) — carrega o modelo, corre, salva."""
    from intrinsic_worker.decompose import run_decompose

    click.echo(f"A carregar modelo {release} em {device} (pesos ~1.8GB no 1º load)…")
    from intrinsic.pipeline import load_models

    models = load_models(release, device=device)
    click.echo("Modelo carregado; a decompor…")
    paths = run_decompose(models, image_path, output_dir)
    click.echo(f"albedo:    {paths.albedo}")
    click.echo(f"shading:   {paths.shading}")
    click.echo(f"specular:  {paths.specular}")


if __name__ == "__main__":
    cli()
