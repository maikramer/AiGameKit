"""CLI standalone para validação de tileability (portão de CI / pre-commit).

Comando equivalente a ``texture2d validate-tileable <IMAGE>``.

Nota de integração: ``cli.py`` ainda referencia ``FLUX.1-dev`` (em mid-rewrite
para pattern-diffusion), por isso este subcomando vive num script autónomo para
não colidir com a refacção paralela. Quando ``cli.py`` estiver estável, mover
este comando para lá como ``@cli.command("validate-tileable")`` e registar a
função :func:`validate_tileable_cmd`.

Uso::

    python -m texture2d._validate_cli <IMAGE> [--threshold 0.85]
    python -m texture2d._validate_cli --help
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

from .cli_rich import click
from .tileability import TileabilityReport, score_tileability

console = Console()

DEFAULT_THRESHOLD = 0.85


def _print_report(report: TileabilityReport, image: Path, threshold: float) -> None:
    """Imprime um Rich table com o relatório de tileability."""
    passed = report.score >= threshold
    verdict = "[bold green]PASS[/bold green]" if passed else "[bold red]FAIL[/bold red]"
    t = Table(title="[bold blue]Tileability Report", box=box.ROUNDED)
    t.add_column("Campo", style="cyan", no_wrap=True)
    t.add_column("Valor", style="white")
    t.add_row("Imagem", str(image))
    t.add_row("Tamanho", f"{report.width}x{report.height}")
    t.add_row("Score", f"{report.score:.4f}")
    t.add_row("Threshold", f"{threshold:.2f}")
    t.add_row("Veredito", verdict)
    t.add_row("edge_mse_horizontal", f"{report.edge_mse_horizontal:.4f}")
    t.add_row("edge_mse_vertical", f"{report.edge_mse_vertical:.4f}")
    t.add_row("max_abs_edge_diff", str(report.max_abs_edge_diff))
    console.print(t)


@click.command("validate-tileable")
@click.argument("image", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--threshold",
    "-t",
    type=float,
    default=DEFAULT_THRESHOLD,
    show_default=True,
    help="Score mínimo para passar (0..1). Default 0.85.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output em JSON (para integração programática).",
)
def validate_tileable_cmd(image: Path, threshold: float, as_json: bool) -> None:
    """Valida se IMAGE é suficientemente tileable (CI-friendly).

    Sai com código 0 se score >= --threshold, caso contrário 1.
    """
    report = score_tileability(image)
    passed = report.score >= threshold

    if as_json:
        payload = {"image": str(image), "threshold": threshold, **report.to_dict()}
        console.print_json(json.dumps(payload))
    else:
        _print_report(report, image, threshold)

    sys.exit(0 if passed else 1)


def main() -> None:
    """Ponto de entrada do CLI standalone."""
    try:
        validate_tileable_cmd()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
