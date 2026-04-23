"""Resumo textual e exportação JSONL."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


def write_jsonl_event(fp: TextIO, event: dict[str, Any]) -> None:
    """Escreve uma linha JSON (sem pretty-print)."""
    fp.write(json.dumps(event, ensure_ascii=False) + "\n")
    fp.flush()


def append_jsonl(path: Path | str, event: dict[str, Any]) -> None:
    """Anexa um evento a um ficheiro JSONL."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        write_jsonl_event(f, event)


def print_summary_table(events: list[dict[str, Any]], file: TextIO | None = None) -> None:
    """Imprime tabela de resumo (Rich se disponível; senão texto simples)."""
    out = file or sys.stdout
    if not events:
        print("[profiler] Nenhum evento registado.", file=out)
        return

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console(file=out, force_terminal=False)
        table = Table(title="Profiler — spans", show_header=True, header_style="bold")
        table.add_column("span", style="cyan")
        table.add_column("ms", justify="right")
        table.add_column("ΔRSS MB", justify="right")
        table.add_column("ΔCUDA MB", justify="right")

        for ev in events:
            name = str(ev.get("span", "?"))
            ms = ev.get("duration_ms", "")
            drss = ev.get("rss_delta_mb", "")
            dcuda = ev.get("cuda_allocated_delta_mb", "")
            if drss == "" or drss is None:
                drss = "—"
            if dcuda == "" or dcuda is None:
                dcuda = "—"
            table.add_row(name, f"{ms}", f"{drss}", f"{dcuda}")
        console.print(table)
    except ImportError:
        print("Profiler — spans", file=out)
        print("-" * 72, file=out)
        for ev in events:
            print(
                f"  {ev.get('span', '?')}: {ev.get('duration_ms', '')} ms "
                f"rss_delta={ev.get('rss_delta_mb', '—')} cuda_Δ={ev.get('cuda_allocated_delta_mb', '—')}",
                file=out,
            )


def print_gpu_summary(events: list[dict[str, Any]], file: TextIO | None = None) -> None:
    """Imprime tabela de max VRAM por GPU (multi-GPU)."""
    out = file or sys.stdout
    if not events:
        return

    # Collect per-device max allocated from cuda_before/cuda_after + cuda_all
    gpu_max: dict[int | str, float] = {}
    gpu_names: dict[int | str, str] = {}

    for ev in events:
        for key in ("cuda_before", "cuda_after"):
            snap = ev.get(key)
            if not snap or not snap.get("cuda_available"):
                continue
            dev = snap.get("cuda_device")
            if dev is None:
                continue
            alloc = snap.get("cuda_allocated_mb", 0) or 0
            peak = snap.get("cuda_peak_allocated_mb", 0) or 0
            val = max(alloc, peak)
            gpu_max[dev] = max(gpu_max.get(dev, 0.0), val)
            name = snap.get("cuda_device_name", "")
            if name:
                gpu_names[dev] = name

        # NEW: cuda_all is a list of snapshots for all GPUs
        for snap in ev.get("cuda_all", []):
            if not snap or not snap.get("cuda_available"):
                continue
            dev = snap.get("cuda_device")
            if dev is None:
                continue
            alloc = snap.get("cuda_allocated_mb", 0) or 0
            peak = snap.get("cuda_peak_allocated_mb", 0) or 0
            val = max(alloc, peak)
            gpu_max[dev] = max(gpu_max.get(dev, 0.0), val)
            name = snap.get("cuda_device_name", "")
            if name:
                gpu_names[dev] = name

    if not gpu_max:
        return

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console(file=out, force_terminal=False)
        table = Table(title="Profiler — VRAM máxima por GPU", show_header=True, header_style="bold")
        table.add_column("GPU", style="cyan")
        table.add_column("Nome")
        table.add_column("VRAM máx (MB)", justify="right")
        table.add_column("VRAM máx (GB)", justify="right")

        for dev in sorted(gpu_max.keys()):
            mb = round(gpu_max[dev], 1)
            gb = round(mb / 1024, 2)
            table.add_row(str(dev), gpu_names.get(dev, "—"), f"{mb}", f"{gb}")
        console.print(table)
    except ImportError:
        print("Profiler — VRAM máxima por GPU", file=out)
        print("-" * 50, file=out)
        for dev in sorted(gpu_max.keys()):
            mb = round(gpu_max[dev], 1)
            gb = round(mb / 1024, 2)
            print(f"  GPU {dev} ({gpu_names.get(dev, '')}): {mb} MB ({gb} GB)", file=out)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
