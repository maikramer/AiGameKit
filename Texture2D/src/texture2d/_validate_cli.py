"""CLI standalone para validação de tileability (portão de CI / pre-commit).

Comando equivalente a ``texture2d validate-tileable <IMAGE>``.

A função :func:`validate_tileable_cmd` é registada em ``cli.py`` via
``cli.add_command(validate_tileable_cmd)``; vive neste módulo autónomo para manter
o validador (puro numpy, sem deps de modelo) desacoplado do gerador.

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
from .tileability import (
    IsolatedDetail,
    Structure,
    TileabilityReport,
    score_isolated_detail,
    score_structure,
    score_tileability,
)

console = Console()

DEFAULT_THRESHOLD = 0.85


def _print_report(
    report: TileabilityReport,
    image: Path,
    threshold: float,
    detail: IsolatedDetail | None = None,
    max_detail: float | None = None,
    structure: Structure | None = None,
    max_banding: float | None = None,
    max_tail_ratio: float | None = None,
) -> None:
    """Imprime um Rich table com o relatório de tileability.

    ``detail`` (pormenor isolado) é opcional para manter a assinatura antiga;
    quando vem, sai como mais uma linha e entra no veredito se houver
    ``max_detail``. O mesmo para ``structure`` (bandas por eixo e cauda) com
    ``max_banding``/``max_tail_ratio``.
    """
    checks = [
        report.score >= threshold,
        detail is None or max_detail is None or detail.spot <= max_detail,
        structure is None or max_banding is None or structure.banding.worst <= max_banding,
        structure is None or max_tail_ratio is None or structure.tail.worst <= max_tail_ratio,
    ]
    verdict = "[bold green]PASS[/bold green]" if all(checks) else "[bold red]FAIL[/bold red]"
    t = Table(title="[bold blue]Tileability Report", box=box.ROUNDED)
    t.add_column("Campo", style="cyan", no_wrap=True)
    t.add_column("Valor", style="white")
    t.add_row("Imagem", str(image))
    t.add_row("Tamanho", f"{report.width}x{report.height}")
    t.add_row("Score", f"{report.score:.4f}")
    t.add_row("Threshold", f"{threshold:.2f}")
    if detail is not None:
        t.add_row("Pormenor isolado", f"{detail.spot:.4f}")
        if max_detail is not None:
            t.add_row("Threshold pormenor", f"{max_detail:.2f}")
    if structure is not None:
        t.add_row("Bandas (linhas / colunas)", f"{structure.banding.rows:.3f} / {structure.banding.cols:.3f}")
        t.add_row("Cauda (clara / escura)", f"{structure.tail.high:.2f} / {structure.tail.low:.2f}")
        if max_banding is not None:
            t.add_row("Threshold bandas", f"{max_banding:.2f}")
        if max_tail_ratio is not None:
            t.add_row("Threshold cauda", f"{max_tail_ratio:.2f}")
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
    "--max-isolated-detail",
    type=float,
    default=None,
    help=(
        "Limiar do pormenor isolado (0..1): um nó, uma mancha que o tiling repetiria "
        "em cada célula. Acima do limiar reprova mesmo com a costura boa. "
        "Recomendado para madeira: 0.12."
    ),
)
@click.option(
    "--max-banding",
    type=float,
    default=None,
    help=(
        "Limiar das bandas por eixo: tábuas com frestas, linhas que atravessam a "
        "textura (medido: 0.85 em tábuas, 0.26-0.44 em veio de madeira direito). "
        "Recomendado para madeira: 0.5."
    ),
)
@click.option(
    "--max-tail-ratio",
    type=float,
    default=None,
    help=(
        "Limiar da cauda pesada: flocos ou pontos claros sobre um fundo liso "
        "(medido: 24 numa cal com flocos, 1.5-3.2 no resto). Recomendado para madeira: 5."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Output em JSON (para integração programática).",
)
def validate_tileable_cmd(
    image: Path,
    threshold: float,
    max_isolated_detail: float | None,
    as_json: bool,
    max_banding: float | None = None,
    max_tail_ratio: float | None = None,
) -> None:
    """Valida se IMAGE é suficientemente tileable (CI-friendly).

    Sai com código 0 se score >= --threshold e nenhum limiar dado for excedido
    (``--max-isolated-detail``, ``--max-banding``, ``--max-tail-ratio``), caso
    contrário 1.
    """
    report = score_tileability(image)
    detail = score_isolated_detail(image)
    structure = score_structure(image)
    passed = (
        report.score >= threshold
        and (max_isolated_detail is None or detail.spot <= max_isolated_detail)
        and (max_banding is None or structure.banding.worst <= max_banding)
        and (max_tail_ratio is None or structure.tail.worst <= max_tail_ratio)
    )

    if as_json:
        payload = {
            "image": str(image),
            "threshold": threshold,
            **report.to_dict(),
            "isolated_detail": detail.to_dict(),
            **structure.to_dict(),
            **({"max_isolated_detail": max_isolated_detail} if max_isolated_detail is not None else {}),
            **({"max_banding": max_banding} if max_banding is not None else {}),
            **({"max_tail_ratio": max_tail_ratio} if max_tail_ratio is not None else {}),
        }
        console.print_json(json.dumps(payload))
    else:
        _print_report(report, image, threshold, detail, max_isolated_detail, structure, max_banding, max_tail_ratio)

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
