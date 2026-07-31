"""Performance analytics — query the centralized perf SQLite DB.

Answers questions like:
- What quantization mode should I use for my GPU?
- How much VRAM does each tool/quant combo use?
- What's the best config that fits in my VRAM without wasting it?
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from aigamekit_shared.perfstore.db import PerfDB

console = Console()


def list_runs(
    *,
    tool: str | None = None,
    limit: int = 20,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """List recent runs from the perf DB."""
    with PerfDB(db_path) as db:
        return db.recent_runs(tool=tool, limit=limit)


def print_runs_table(runs: list[dict[str, Any]]) -> None:
    """Print a Rich table of runs."""
    if not runs:
        console.print("[dim]No runs found in perf DB.[/dim]")
        return

    table = Table(title="Recent Runs", show_lines=True)
    table.add_column("ID", justify="right", style="cyan")
    table.add_column("Tool", style="magenta")
    table.add_column("Started", style="dim")
    table.add_column("Duration", justify="right")
    table.add_column("Status")
    table.add_column("GPU", style="green")
    table.add_column("VRAM (MB)", justify="right")
    table.add_column("Quant", style="yellow")
    table.add_column("Model", style="blue", max_width=30)

    for r in runs:
        status = "[green]OK[/green]" if r.get("success") else "[red]FAIL[/red]"
        dur = f"{r['total_duration_ms']:.0f}ms" if r.get("total_duration_ms") else "—"
        vram = f"{r['gpu_total_vram_mb']:.0f}" if r.get("gpu_total_vram_mb") else "—"
        gpu = str(r.get("gpu_name", ""))[:30] or "—"
        quant = str(r.get("quantization_mode", "")) or "—"
        model = str(r.get("model_id", ""))[:30] or "—"
        started = str(r.get("started_at", ""))[11:19] or "—"

        table.add_row(
            str(r["id"]),
            r["tool"],
            started,
            dur,
            status,
            gpu,
            vram,
            quant,
            model,
        )

    console.print(table)


def print_spans_detail(run_id: int, db_path: str | None = None) -> None:
    """Print spans for a specific run."""
    with PerfDB(db_path) as db:
        spans = db.spans_for_run(run_id)

    if not spans:
        console.print(f"[dim]No spans found for run {run_id}.[/dim]")
        return

    table = Table(title=f"Spans for Run {run_id}", show_lines=True)
    table.add_column("Span", style="cyan")
    table.add_column("Duration", justify="right")
    table.add_column("VRAM Before", justify="right", style="green")
    table.add_column("VRAM After", justify="right", style="green")
    table.add_column("Δ VRAM", justify="right", style="yellow")
    table.add_column("Peak VRAM", justify="right", style="red")
    table.add_column("Free VRAM", justify="right")
    table.add_column("Δ RSS", justify="right", style="dim")

    for s in spans:
        dur = f"{s['duration_ms']:.1f}ms" if s.get("duration_ms") else "—"
        vram_b = f"{s['cuda_allocated_before_mb']:.1f}" if s.get("cuda_allocated_before_mb") is not None else "—"
        vram_a = f"{s['cuda_allocated_after_mb']:.1f}" if s.get("cuda_allocated_after_mb") is not None else "—"
        dvram = f"{s['cuda_allocated_delta_mb']:+.1f}" if s.get("cuda_allocated_delta_mb") is not None else "—"
        peak = f"{s['cuda_peak_after_mb']:.1f}" if s.get("cuda_peak_after_mb") is not None else "—"
        free = f"{s['cuda_free_after_mb']:.1f}" if s.get("cuda_free_after_mb") is not None else "—"
        drss = f"{s['rss_delta_mb']:+.1f}" if s.get("rss_delta_mb") is not None else "—"

        table.add_row(s["span_name"], dur, vram_b, vram_a, dvram, peak, free, drss)

    console.print(table)


def print_summary(
    *,
    tool: str | None = None,
    gpu_name: str | None = None,
    quantization_mode: str | None = None,
    days: int = 30,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Print aggregated summary per tool+quantization."""
    with PerfDB(db_path) as db:
        rows = db.tool_summary(
            tool=tool,
            gpu_name=gpu_name,
            quantization_mode=quantization_mode,
            days=days,
        )

    if not rows:
        console.print("[dim]No data found for the given filters.[/dim]")
        return []

    table = Table(title="Performance Summary", show_lines=True)
    table.add_column("Tool", style="magenta")
    table.add_column("Quantization", style="yellow")
    table.add_column("GPU", style="green")
    table.add_column("VRAM (MB)", justify="right")
    table.add_column("Runs", justify="right")
    table.add_column("Avg Duration", justify="right")
    table.add_column("Success", justify="right", style="green")
    table.add_column("Failures", justify="right", style="red")

    for r in rows:
        avg = f"{r['avg_duration_ms']:.0f}ms" if r.get("avg_duration_ms") else "—"
        vram = f"{r['gpu_total_vram_mb']:.0f}" if r.get("gpu_total_vram_mb") else "—"
        gpu = str(r.get("gpu_name", ""))[:25] or "—"
        table.add_row(
            r["tool"],
            r.get("quantization_mode", "—") or "—",
            gpu,
            vram,
            str(r.get("run_count", 0)),
            avg,
            str(r.get("success_count", 0)),
            str(r.get("fail_count", 0)),
        )

    console.print(table)
    return rows


def print_vram_analysis(
    *,
    tool: str | None = None,
    gpu_name: str | None = None,
    days: int = 30,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Print VRAM usage per tool+quantization+span."""
    with PerfDB(db_path) as db:
        rows = db.vram_by_quantization(tool=tool, gpu_name=gpu_name, days=days)

    if not rows:
        console.print("[dim]No VRAM data found. Run tools with --profile first.[/dim]")
        return []

    table = Table(title="VRAM by Quantization Mode", show_lines=True)
    table.add_column("Tool", style="magenta")
    table.add_column("Quantization", style="yellow")
    table.add_column("GPU", style="green")
    table.add_column("Span", style="cyan")
    table.add_column("Samples", justify="right")
    table.add_column("Peak VRAM", justify="right", style="red")
    table.add_column("Avg VRAM", justify="right")
    table.add_column("Min Free", justify="right", style="green")
    table.add_column("Avg Span", justify="right")

    for r in rows:
        peak = f"{r['peak_vram_mb']:.0f}" if r.get("peak_vram_mb") else "—"
        avg_v = f"{r['avg_vram_mb']:.0f}" if r.get("avg_vram_mb") else "—"
        min_f = f"{r['min_free_mb']:.0f}" if r.get("min_free_mb") else "—"
        avg_s = f"{r['avg_span_ms']:.0f}ms" if r.get("avg_span_ms") else "—"
        gpu = str(r.get("gpu_name", ""))[:25] or "—"
        table.add_row(
            r["tool"],
            r.get("quantization_mode", "—") or "—",
            gpu,
            r["span_name"],
            str(r.get("sample_count", 0)),
            peak,
            avg_v,
            min_f,
            avg_s,
        )

    console.print(table)
    return rows


def print_recommend(
    *,
    tool: str,
    target_vram_mb: float,
    gpu_name: str | None = None,
    days: int = 90,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Recommend best quantization config for a tool and target VRAM."""
    with PerfDB(db_path) as db:
        rows = db.recommend_config(
            tool=tool,
            target_vram_mb=target_vram_mb,
            gpu_name=gpu_name,
            days=days,
        )

    if not rows:
        console.print(
            f"[dim]No data found for tool={tool!r} fitting {target_vram_mb:.0f} MB. "
            "Run benchmarks with --profile first.[/dim]"
        )
        return []

    console.print(
        f"\n[bold green]Recommendations for {tool}[/bold green] "
        f"(target VRAM ≤ {target_vram_mb:.0f} MB, ordered by best utilization):\n"
    )

    table = Table(title=f"Best Configs for {tool}", show_lines=True)
    table.add_column("Quantization", style="yellow")
    table.add_column("GPU", style="green")
    table.add_column("Span", style="cyan")
    table.add_column("Peak VRAM", justify="right", style="red")
    table.add_column("Min Free", justify="right", style="green")
    table.add_column("Margin", justify="right")
    table.add_column("Samples", justify="right")
    table.add_column("Avg Time", justify="right")
    table.add_column("Utilization", justify="right")

    for r in rows:
        peak = r.get("peak_vram_mb", 0)
        free = r.get("min_free_mb", 0)
        margin = r.get("vram_margin_mb", 0)
        pct = (peak / target_vram_mb * 100) if target_vram_mb > 0 else 0
        util_label = f"{pct:.0f}%"
        if pct >= 85:
            util_label = f"[green]{pct:.0f}%[/green] (excellent)"
        elif pct >= 70:
            util_label = f"[yellow]{pct:.0f}%[/yellow] (good)"
        else:
            util_label = f"[dim]{pct:.0f}% (underutilized)[/dim]"

        avg_t = f"{r['avg_span_ms']:.0f}ms" if r.get("avg_span_ms") else "—"
        gpu = str(r.get("gpu_name", ""))[:25] or "—"
        table.add_row(
            r["quantization_mode"],
            gpu,
            r["span_name"],
            f"{peak:.0f} MB",
            f"{free:.0f} MB" if free else "—",
            f"{margin:.0f} MB",
            str(r.get("sample_count", 0)),
            avg_t,
            util_label,
        )

    console.print(table)

    best = rows[0] if rows else None
    if best:
        console.print(
            f"\n[bold]Best pick:[/bold] {best['quantization_mode']} "
            f"(peak {best.get('peak_vram_mb', 0):.0f} MB, "
            f"{best.get('peak_vram_mb', 0) / target_vram_mb * 100:.0f}% utilization)"
        )
        console.print(
            "[dim]Tip: configs with ≥85% utilization give best quality. "
            "If none qualify, try a lower quantization (sdnq-int4) or reduce parameters.[/dim]"
        )

    return rows
