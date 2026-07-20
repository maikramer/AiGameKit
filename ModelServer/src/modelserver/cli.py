#!/usr/bin/env python3
"""Unified Model Server — CLI principal.

Comandos (alias ``ums`` = ``gamedev-model-server``):
  start|stop|status|queue|wait|cancel|flush|backends|preload|evict|
  stats|debug|bench|doctor

Agentes / humanos: se a GPU estiver ocupada, usa ``status`` / ``queue`` /
``debug`` — **não** mates processos GPU enquanto houver jobs UMS.
``stats --reset`` só limpa contadores (não para o UMS).
``bench`` mede RTT IPC (não submete GPU). Para limpar fila stale:
``ums flush`` ou ``ums cancel --all``.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

try:
    import rich_click as click
except ImportError:  # pragma: no cover
    import click  # type: ignore[no-redef]

from gamedev_shared.model_server import (
    UMS_DO_NOT_KILL_TIP,
    format_ums_holding_summary,
    is_server_running,
    send_request,
)

from . import protocol as P
from .registry import Registry

console = Console()


def _send(request: dict, *, timeout: float = 30.0) -> dict | None:
    """Envia um pedido ao UMS no socket canónico. Retorna None se down."""
    if not is_server_running(P.DEFAULT_SOCKET_PATH):
        return None
    return send_request(request, P.DEFAULT_SOCKET_PATH, timeout_sec=timeout)


def _print_json(resp: dict[str, Any]) -> None:
    console.print_json(json.dumps(resp, ensure_ascii=False, default=str))


def _print_ums_error(resp: dict[str, Any]) -> None:
    """Erro CLI com error_code / hint / ums_debug."""
    code = resp.get("error_code", "?")
    console.print(f"[bold red]✗ [{code}][/bold red] {resp.get('error', resp)}")
    if resp.get("hint"):
        console.print(f"[dim]hint: {resp['hint']}[/dim]")
    dbg = resp.get("ums_debug")
    if dbg:
        console.print(f"[dim]ums_debug: {json.dumps(dbg, ensure_ascii=False, default=str)}[/dim]")


def _print_do_not_kill_tip(*, inflight: int = 0, depth: int = 0) -> None:
    """Aviso estável quando há (ou pode haver) carga GPU via UMS."""
    busy = inflight > 0 or depth > 0
    style = "yellow" if busy else "dim"
    console.print(f"[{style}]{UMS_DO_NOT_KILL_TIP}[/{style}]")


def _short_job_id(job_id: object, *, n: int = 12) -> str:
    jid = str(job_id or "")
    if not jid:
        return "?"
    return jid if len(jid) <= n else f"{jid[:n]}…"


@click.group()
@click.version_option(version="0.1.0", prog_name="gamedev-model-server")
def cli() -> None:
    """Unified Model Server — supervisor único de VRAM para o monorepo GameDev."""


@cli.command("start")
@click.option("--socket", "socket_path", type=click.Path(), default=None, help="Path do Unix socket")
@click.option(
    "--idle-timeout",
    "idle_timeout_min",
    default=P.DEFAULT_IDLE_TIMEOUT_MIN,
    show_default=True,
    type=int,
    help="Minutos de idle antes de encerrar.",
)
@click.option("--verbose", "-v", is_flag=True, help="Logs detalhados")
def start_cmd(socket_path: str | None, idle_timeout_min: int, verbose: bool) -> None:
    """Arranca o Unified Model Server (foreground)."""
    from gamedev_shared.logging import configure_logging

    from .server import UnifiedModelServer

    log_path = configure_logging("ums")
    sock = Path(socket_path) if socket_path else P.DEFAULT_SOCKET_PATH
    if is_server_running(sock):
        console.print("[yellow]UMS já está ativo neste socket.[/yellow]")
        sys.exit(1)

    registry = Registry()
    log_line = f"Log: [cyan]{log_path}[/cyan]\n" if log_path else ""
    console.print(
        Panel.fit(
            f"[bold]Unified Model Server[/bold]\n"
            f"Socket: [cyan]{sock}[/cyan]\n"
            f"Backends: [green]{', '.join(registry.names)}[/green]\n"
            f"Idle timeout: [green]{idle_timeout_min} min[/green]\n"
            f"{log_line}\n"
            f"[dim]Os backends carregam sob procura (lazy). Use 'preload' para "
            f"pré-aquecer um backend específico.[/dim]",
            border_style="blue",
        )
    )

    srv = UnifiedModelServer(
        registry=registry,
        socket_path=sock,
        idle_timeout_min=idle_timeout_min,
        verbose=verbose,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]UMS interrompido.[/yellow]")
    except Exception as e:
        console.print(f"\n[bold red]✗ Erro no UMS:[/bold red] {e}")
        if verbose:
            console.print_exception()
        sys.exit(1)


@cli.command("stop")
def stop_cmd() -> None:
    """Para o UMS (graceful shutdown)."""
    resp = _send({"cmd": P.CMD_SHUTDOWN}, timeout=5.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo.[/yellow]")
        sys.exit(0)
    if resp.get("status") == "ok":
        console.print("[bold green]✓ UMS a encerrar.[/bold green]")
    else:
        console.print(f"[bold red]✗ Falha ao parar UMS:[/bold red] {resp.get('error', resp)}")
        sys.exit(1)


@cli.command("status")
@click.option("--json", "as_json", is_flag=True, help="Dump JSON completo (inclui debug).")
def status_cmd(as_json: bool) -> None:
    """Mostra o estado do UMS e backends carregados."""
    resp = _send({"cmd": P.CMD_STATUS}, timeout=5.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo.[/yellow]")
        console.print("[dim]Arranca com: gamedev-model-server start[/dim]")
        sys.exit(1)

    if as_json:
        _print_json(resp)
        return

    t = Table(title="[bold blue]Unified Model Server", box=box.ROUNDED)
    t.add_column("Campo", style="cyan", no_wrap=True)
    t.add_column("Valor", style="green")
    t.add_row("PID", str(resp.get("pid", "?")))
    t.add_row("Socket", str(resp.get("socket", "?")))
    t.add_row("Backends carregados", f"{resp.get('loaded_count', 0)} ({resp.get('loaded_vram_mib', 0)} MiB)")
    t.add_row("Pedidos servidos", str(resp.get("requests_served", 0)))
    q = resp.get("queue") or {}
    t.add_row(
        "Fila", f"{q.get('queue_depth', 0)} queued / {q.get('inflight', 0)} inflight (max {q.get('max_depth', '?')})"
    )
    t.add_row("Affinity cuts", str(resp.get("max_affinity_cuts", "?")))
    t.add_row("Max inflight", str(resp.get("max_inflight", "?")))
    console.print(t)

    qresp = _send({"cmd": P.CMD_QUEUE}, timeout=5.0)
    if qresp:
        console.print(f"[bold]{format_ums_holding_summary(qresp)}[/bold]")
        _print_do_not_kill_tip(
            inflight=int(qresp.get("inflight") or 0),
            depth=int(qresp.get("queue_depth") or 0),
        )
    else:
        _print_do_not_kill_tip(
            inflight=int(q.get("inflight") or 0),
            depth=int(q.get("queue_depth") or 0),
        )

    backends = resp.get("backends", [])
    if backends:
        bt = Table(title="[bold]Backends", box=box.SIMPLE)
        bt.add_column("Backend", style="cyan")
        bt.add_column("YAML", justify="right")
        bt.add_column("Peak", justify="right")
        bt.add_column("Act+", justify="right")
        bt.add_column("Priority", justify="right")
        bt.add_column("Carregado")
        bt.add_column("Refs", justify="right")
        for b in backends:
            loaded = "[green]✓[/green]" if b.get("loaded") else "[dim]✗[/dim]"
            bt.add_row(
                b["name"],
                str(b["vram_mib"]),
                str(b.get("peak_mib", "?")),
                str(b.get("activation_headroom_mib", "?")),
                str(b["priority"]),
                loaded,
                str(b.get("ref_count", 0)),
            )
        console.print(bt)
        console.print(
            "[dim]Peak = pesos(fp16)+activação+safety (admit/refuse). "
            "Act+ = livre necessário com pesos já carregados.[/dim]"
        )

    dbg = resp.get("debug") or {}
    last_errors = dbg.get("last_errors") or {}
    if last_errors:
        et = Table(title="[bold yellow]Últimos erros (debug)", box=box.SIMPLE)
        et.add_column("Backend", style="cyan")
        et.add_column("last_error", style="yellow")
        for name, err in last_errors.items():
            et.add_row(str(name), str(err)[:120])
        console.print(et)
    elif dbg:
        console.print(
            f"[dim]debug: loaded={dbg.get('loaded_backends', [])} "
            f"depth={dbg.get('queue_depth', 0)} inflight={dbg.get('inflight', 0)}[/dim]"
        )


@cli.command("submit")
@click.argument("backend")
@click.option("--prompt", default="smoke", help="Prompt de smoke-test.")
@click.option("--output", "output_path", default="/tmp/ums-smoke-out.bin", help="Path de output.")
@click.option("--priority", type=click.Choice(["interactive", "batch"]), default="interactive")
@click.option("--wait/--no-wait", default=False, help="Esperar conclusão (poll).")
@click.option("--json", "as_json", is_flag=True, help="Dump JSON.")
def submit_cmd(
    backend: str,
    prompt: str,
    output_path: str,
    priority: str,
    wait: bool,
    as_json: bool,
) -> None:
    """Smoke-test: ``submit`` (e opcionalmente espera) um job no UMS."""
    resp = _send(
        {
            "cmd": P.CMD_SUBMIT,
            "backend": backend,
            "prompt": prompt,
            "output": output_path,
            "priority": priority,
        },
        timeout=30.0,
    )
    if resp is None:
        console.print("[yellow]UMS não está ativo.[/yellow]")
        sys.exit(1)
    if as_json and not wait:
        _print_json(resp)
        if resp.get("status") != "ok":
            sys.exit(1)
        return
    if resp.get("status") != "ok":
        _print_ums_error(resp)
        sys.exit(1)
    job_id = str(resp.get("job_id", ""))
    console.print(
        f"[bold green]✓[/bold green] submit {backend} job={job_id[:8]}… "
        f"pri={resp.get('priority')} pos={resp.get('queue_position', '?')}"
    )
    if not wait:
        return
    # Poll até done.
    deadline = time.monotonic() + 600.0
    while time.monotonic() < deadline:
        poll = _send({"cmd": P.CMD_POLL, "job_id": job_id}, timeout=10.0)
        if poll is None:
            console.print("[yellow]UMS caiu durante wait.[/yellow]")
            sys.exit(1)
        state = poll.get("state")
        if state in (P.JOB_DONE, P.JOB_FAILED, P.JOB_CANCELLED):
            if as_json:
                _print_json(poll)
            else:
                console.print(f"[dim]state={state}[/dim] {poll.get('result') or poll}")
            sys.exit(0 if state == P.JOB_DONE else 1)
        time.sleep(0.2)
    console.print("[bold red]timeout à espera do job[/bold red]")
    sys.exit(1)


@cli.command("cancel")
@click.argument("job_id", required=False, default=None)
@click.option("--all", "cancel_all_flag", is_flag=True, help="Cancela todos (queued + running).")
@click.option(
    "--queued-only",
    is_flag=True,
    help="Com --all: só queued (não pede cancel aos running).",
)
@click.option("--json", "as_json", is_flag=True, help="Dump JSON da resposta.")
def cancel_cmd(
    job_id: str | None,
    cancel_all_flag: bool,
    queued_only: bool,
    as_json: bool,
) -> None:
    """Cancela job (UUID ou prefixo) ou ``--all`` / ``all`` / ``*``."""
    want_all = cancel_all_flag or (job_id is not None and job_id.strip().lower() in ("all", "*"))
    if want_all:
        resp = _send(
            {"cmd": P.CMD_CANCEL, "all": True, "queued_only": queued_only},
            timeout=30.0,
        )
    else:
        if not job_id:
            console.print("[red]Uso: ums cancel <job_id|prefixo> | ums cancel --all[/red]")
            sys.exit(2)
        resp = _send({"cmd": P.CMD_CANCEL, "job_id": job_id}, timeout=10.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo.[/yellow]")
        sys.exit(1)
    if as_json:
        _print_json(resp)
        if resp.get("status") != "ok":
            sys.exit(1)
        return
    if resp.get("status") == "ok":
        if want_all or "count" in resp:
            console.print(
                f"[bold green]✓[/bold green] flush: {resp.get('message', resp)} (count={resp.get('count', '?')})"
            )
        else:
            jid = str(resp.get("job_id") or job_id or "")
            console.print(
                f"[bold green]✓[/bold green] job {_short_job_id(jid)} → "
                f"{resp.get('state', '?')} {resp.get('message', '')}"
            )
        if resp.get("ums_debug"):
            console.print(f"[dim]ums_debug: {json.dumps(resp['ums_debug'], ensure_ascii=False)}[/dim]")
    else:
        _print_ums_error(resp)
        sys.exit(1)


@cli.command("flush")
@click.option(
    "--queued-only",
    is_flag=True,
    help="Só cancela queued (não pede cancel aos running).",
)
@click.option("--json", "as_json", is_flag=True, help="Dump JSON da resposta.")
def flush_cmd(queued_only: bool, as_json: bool) -> None:
    """Limpa a fila UMS (alias de ``cancel --all``)."""
    resp = _send({"cmd": P.CMD_FLUSH, "queued_only": queued_only}, timeout=30.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo.[/yellow]")
        sys.exit(1)
    if as_json:
        _print_json(resp)
        if resp.get("status") != "ok":
            sys.exit(1)
        return
    if resp.get("status") == "ok":
        console.print(f"[bold green]✓[/bold green] {resp.get('message', 'fila limpa')} (count={resp.get('count', 0)})")
    else:
        _print_ums_error(resp)
        sys.exit(1)


@cli.command("queue")
@click.option("--json", "as_json", is_flag=True, help="Dump JSON completo (inclui debug).")
def queue_cmd(as_json: bool) -> None:
    """Lista jobs na fila e em execução."""
    resp = _send({"cmd": P.CMD_QUEUE}, timeout=5.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo.[/yellow]")
        sys.exit(1)

    if as_json:
        _print_json(resp)
        return

    dbg = resp.get("debug") or {}
    depth = int(resp.get("queue_depth") or 0)
    inflight = int(resp.get("inflight") or 0)
    console.print(
        Panel.fit(
            f"[bold]Fila UMS[/bold] — {depth} queued, "
            f"{inflight} inflight, max_depth={resp.get('max_depth', '?')}\n"
            f"[bold]{format_ums_holding_summary(resp)}[/bold]\n"
            f"[dim]loaded={dbg.get('loaded_backends', [])} "
            f"max_cuts={dbg.get('max_affinity_cuts', '?')} "
            f"max_inflight={dbg.get('max_inflight', '?')}[/dim]",
            border_style="blue",
        )
    )
    _print_do_not_kill_tip(inflight=inflight, depth=depth)

    def _print_jobs(title: str, jobs: list) -> None:
        if not jobs:
            console.print(f"[dim]{title}: (vazio)[/dim]")
            return
        jt = Table(title=f"[bold]{title}", box=box.SIMPLE)
        jt.add_column("job_id", style="cyan")
        jt.add_column("backend")
        jt.add_column("priority")
        jt.add_column("state")
        jt.add_column("cuts", justify="right")
        jt.add_column("wait_s", justify="right")
        jt.add_column("gen_s", justify="right")
        jt.add_column("progress")
        for j in jobs:
            pct = j.get("progress_pct")
            msg = j.get("progress_msg") or ""
            prog = f"{pct:.0%}" if isinstance(pct, (int, float)) else "—"
            if msg:
                prog = f"{prog} {msg}"[:28]
            jt.add_row(
                _short_job_id(j.get("job_id")),
                str(j.get("backend", "")),
                str(j.get("priority", "")),
                str(j.get("state", "")),
                str(j.get("affinity_cuts", 0)),
                str(j.get("queue_wait_sec") if j.get("queue_wait_sec") is not None else "—"),
                str(j.get("generate_sec") if j.get("generate_sec") is not None else "—"),
                prog,
            )
        console.print(jt)
        if jobs:
            console.print(
                "[dim]job_id truncado acima — `cancel` / `wait` aceitam prefixo ou UUID completo "
                f"(ex.: cancel {_short_job_id(jobs[0].get('job_id'), n=8)})[/dim]"
            )

    _print_jobs("Running", resp.get("running") or [])
    _print_jobs("Queued", resp.get("queued") or [])


@cli.command("wait")
@click.argument("job_id")
@click.option("--timeout", default=600.0, show_default=True, type=float, help="Segundos máximos.")
@click.option("--json", "as_json", is_flag=True, help="Dump JSON da resposta final.")
def wait_cmd(job_id: str, timeout: float, as_json: bool) -> None:
    """Bloqueia até o job UMS terminar (ou timeout)."""
    from gamedev_shared.model_server import wait_ums_job

    console.print(f"[dim]À espera do job {job_id}… ({UMS_DO_NOT_KILL_TIP})[/dim]")
    resp = wait_ums_job(job_id, timeout_sec=timeout)
    if resp is None:
        console.print("[yellow]UMS não está ativo ou job desconhecido.[/yellow]")
        sys.exit(1)
    if as_json:
        _print_json(resp)
        if resp.get("status") != "ok":
            sys.exit(1)
        return
    if resp.get("status") == "ok":
        console.print(f"[bold green]✓[/bold green] job {_short_job_id(job_id)} concluído")
        if resp.get("output"):
            console.print(f"[cyan]{resp['output']}[/cyan]")
    else:
        _print_ums_error(resp)
        sys.exit(1)


@cli.command("backends")
def backends_cmd() -> None:
    """Lista os backends registados (não precisa do UMS a correr)."""
    registry = Registry()
    t = Table(title="[bold blue]Backends registados", box=box.SIMPLE)

    # Estado loaded se o UMS estiver up.
    loaded_set: set[str] = set()
    resp = _send({"cmd": P.CMD_LIST_BACKENDS}, timeout=5.0)
    if resp and resp.get("status") == "ok":
        loaded_set = {b["name"] for b in resp.get("backends", []) if b.get("loaded")}

    t.add_column("Backend", style="cyan")
    t.add_column("Adapter")
    t.add_column("VRAM (MiB)", justify="right")
    t.add_column("Priority", justify="right")
    t.add_column("Estado")
    for desc in registry:
        estado = "[green]carregado[/green]" if desc.name in loaded_set else "[dim]—[/dim]"
        t.add_row(desc.name, desc.adapter, str(desc.vram_mib), str(desc.priority), estado)
    console.print(t)


@cli.command("preload")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Dump JSON da resposta.")
def preload_cmd(name: str, as_json: bool) -> None:
    """Pré-carrega um backend (ex: text2icon)."""
    resp = _send({"cmd": P.CMD_PRELOAD, "backend": name}, timeout=600.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo. Arranca com: gamedev-model-server start[/yellow]")
        sys.exit(1)
    if as_json:
        _print_json(resp)
        if resp.get("status") != "ok":
            sys.exit(1)
        return
    if resp.get("status") == "ok":
        console.print(f"[bold green]✓ {resp.get('message', 'pré-carregado')}[/bold green]")
        if resp.get("ums_debug"):
            console.print(f"[dim]ums_debug: {json.dumps(resp['ums_debug'], ensure_ascii=False)}[/dim]")
    else:
        _print_ums_error(resp)
        sys.exit(1)


@cli.command("evict")
@click.argument("name", required=False)
def evict_cmd(name: str | None) -> None:
    """Evicta um backend específico ou todos (sem argumento)."""
    request: dict = {"cmd": P.CMD_RELEASE}
    if name:
        request["backend"] = name
    resp = _send(request, timeout=60.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo.[/yellow]")
        sys.exit(0)
    if resp.get("status") == "ok":
        console.print(f"[bold green]✓ {resp.get('message', 'evicted')}[/bold green]")
    else:
        console.print(f"[bold red]✗ {resp.get('error', resp)}[/bold red]")
        sys.exit(1)


def _print_queue_metrics(qm: dict[str, Any], *, affinity_hits: object = None) -> None:
    """Tabela compacta de métricas de fila."""
    if not qm and affinity_hits is None:
        return
    t = Table(title="[bold]Fila (métricas)", box=box.SIMPLE)
    t.add_column("Métrica", style="cyan")
    t.add_column("Valor", justify="right")
    rows = [
        ("enqueued", qm.get("enqueued", 0)),
        ("completed", qm.get("completed", 0)),
        ("cancelled", qm.get("cancelled", 0)),
        ("queue_full", qm.get("queue_full_count", 0)),
        ("affinity_cutsΣ", qm.get("affinity_cuts_total", 0)),
        ("max_depth_seen", qm.get("max_depth_seen", 0)),
        ("wait_p50_s", qm.get("queue_wait_p50_sec", "—")),
        ("wait_p95_s", qm.get("queue_wait_p95_sec", "—")),
        ("wait_samples", qm.get("queue_wait_samples", 0)),
    ]
    if affinity_hits is not None:
        rows.append(("affinity_hits", affinity_hits))
    for k, v in rows:
        t.add_row(k, str(v))
    console.print(t)


def _budget_short(budget: dict[str, Any] | None) -> str:
    if not budget:
        return "—"
    parts: list[str] = []
    for key in ("num_chunks", "max_num_view", "tiles", "octree_resolution", "free_vram_mib"):
        if key in budget and budget[key] is not None:
            parts.append(f"{key}={budget[key]}")
    if not parts:
        # fallback: primeiras 2 keys
        for i, (k, v) in enumerate(budget.items()):
            if i >= 2:
                break
            parts.append(f"{k}={v}")
    return ", ".join(parts)[:48] or "—"


@cli.command("stats")
@click.option("--reset", is_flag=True, help="Limpa contadores in-process (NÃO para UMS / NÃO cancela jobs).")
@click.option("--json", "as_json", is_flag=True, help="Dump JSON completo.")
def stats_cmd(reset: bool, as_json: bool) -> None:
    """Estatísticas por backend + métricas de fila (loads/gens/timings/budget)."""
    resp = _send({"cmd": P.CMD_STATS, "reset": bool(reset)}, timeout=5.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo.[/yellow]")
        sys.exit(1)

    if reset:
        if resp.get("reset") or resp.get("message"):
            if as_json:
                _print_json(resp)
            else:
                console.print(
                    f"[bold green]✓[/bold green] {resp.get('message', 'stats reset')} "
                    f"(PID {resp.get('pid', '?')}) — jobs/backends intactos"
                )
            if not resp.get("reset"):
                console.print(
                    "[dim]Nota: UMS antigo pode ignorar reset — reinicia o supervisor "
                    "quando puderes (não agora se houver jobs).[/dim]"
                )
            return
        console.print("[yellow]Reset não confirmado pelo UMS (supervisor antigo?).[/yellow]")
        sys.exit(1)

    if as_json:
        _print_json(resp)
        return

    q = resp.get("queue") or {}
    console.print(
        Panel.fit(
            f"[bold]UMS Stats[/bold] — PID {resp.get('pid', '?')}, "
            f"{resp.get('requests_served', 0)} pedidos, "
            f"idle-evict {resp.get('idle_evict_timeout_sec', '?')}s\n"
            f"fila {q.get('queue_depth', 0)}q / {q.get('inflight', 0)}run · "
            f"inflight≤{resp.get('max_inflight', '?')} · cuts≤{resp.get('max_affinity_cuts', '?')}",
            border_style="blue",
        )
    )
    _print_do_not_kill_tip(
        inflight=int(q.get("inflight") or 0),
        depth=int(q.get("queue_depth") or 0),
    )

    _print_queue_metrics(
        dict(resp.get("queue_metrics") or {}),
        affinity_hits=resp.get("affinity_hits"),
    )

    backends = resp.get("backends", {})
    if not backends:
        console.print("[dim]Sem atividade registada (nenhum backend usado ainda).[/dim]")
        return

    t = Table(title="[bold blue]Backends", box=box.SIMPLE)
    t.add_column("Backend", style="cyan")
    t.add_column("Loads", justify="right")
    t.add_column("Gens", justify="right")
    t.add_column("Evicts", justify="right")
    t.add_column("Err", justify="right")
    t.add_column("AvgLoad", justify="right")
    t.add_column("AvgGen", justify="right")
    t.add_column("LastGen", justify="right")
    t.add_column("Idle", justify="right")
    t.add_column("Budget", style="dim")

    for name in sorted(backends):
        s = backends[name]
        idle = s.get("idle_sec")
        idle_s = f"{idle:.0f}s" if isinstance(idle, (int, float)) else "—"
        t.add_row(
            name,
            str(s.get("load_count", 0)),
            str(s.get("generate_count", 0)),
            str(s.get("evict_count", 0)),
            str(s.get("error_count", 0)),
            f"{float(s.get('avg_load_time_sec') or 0):.1f}s",
            f"{float(s.get('avg_generate_time_sec') or 0):.1f}s",
            f"{float(s.get('last_generate_time_sec') or 0):.1f}s",
            idle_s,
            _budget_short(s.get("last_runtime_budget")),
        )
    console.print(t)

    dbg = resp.get("debug") or {}
    last_errors = dbg.get("last_errors") or {n: s.get("last_error") for n, s in backends.items() if s.get("last_error")}
    if last_errors:
        et = Table(title="[bold yellow]last_error", box=box.SIMPLE)
        et.add_column("Backend", style="cyan")
        et.add_column("Erro", style="yellow")
        for name, err in last_errors.items():
            et.add_row(str(name), str(err)[:140])
        console.print(et)


@cli.command("debug")
@click.option("--json", "as_json", is_flag=True, help="Dump JSON agregado (status+queue+stats).")
@click.option(
    "--watch",
    "watch_sec",
    type=float,
    default=0.0,
    help="Re-imprimir a cada N segundos (0=uma vez). Só leitura — não para jobs.",
)
def debug_cmd(as_json: bool, watch_sec: float) -> None:
    """Snapshot debug read-only: HOLDING, fila, erros, budgets, GPU.

    Nunca faz stop/flush/cancel/evict. Ideal enquanto batch corre.
    """

    def _once() -> int:
        status = _send({"cmd": P.CMD_STATUS}, timeout=5.0)
        if status is None:
            console.print("[yellow]UMS não está ativo.[/yellow]")
            return 1
        queue = _send({"cmd": P.CMD_QUEUE}, timeout=5.0) or {}
        stats = _send({"cmd": P.CMD_STATS}, timeout=5.0) or {}

        if as_json:
            _print_json({"status": status, "queue": queue, "stats": stats})
            return 0

        q = status.get("queue") or {}
        depth = int(q.get("queue_depth") or queue.get("queue_depth") or 0)
        inflight = int(q.get("inflight") or queue.get("inflight") or 0)
        dbg = status.get("debug") or {}
        hold = format_ums_holding_summary(queue if queue else q)

        free_s = "?"
        with contextlib.suppress(Exception):
            from gamedev_shared.gpu import query_gpu_free_mib

            free = query_gpu_free_mib()
            if free is not None:
                free_s = f"{free} MiB"

        console.print(
            Panel.fit(
                f"[bold]UMS Debug[/bold] — PID {status.get('pid', '?')}\n"
                f"[bold]{hold}[/bold]\n"
                f"GPU free≈{free_s} · loaded={dbg.get('loaded_backends', status.get('loaded', []))}\n"
                f"affinity_hits={dbg.get('affinity_hits', stats.get('affinity_hits', '—'))} · "
                f"eta={status.get('eta_sec', queue.get('eta_sec', '—'))}s",
                border_style="magenta",
            )
        )
        _print_do_not_kill_tip(inflight=inflight, depth=depth)

        _print_queue_metrics(
            dict(status.get("queue_metrics") or stats.get("queue_metrics") or {}),
            affinity_hits=dbg.get("affinity_hits", stats.get("affinity_hits")),
        )

        last_errors = dbg.get("last_errors") or (stats.get("debug") or {}).get("last_errors") or {}
        if last_errors:
            et = Table(title="[bold yellow]last_errors", box=box.SIMPLE)
            et.add_column("Backend", style="cyan")
            et.add_column("Erro")
            for name, err in last_errors.items():
                et.add_row(str(name), str(err)[:140])
            console.print(et)

        budgets = (stats.get("debug") or {}).get("last_runtime_budgets") or {}
        if not budgets:
            for name, s in (stats.get("backends") or {}).items():
                if s.get("last_runtime_budget"):
                    budgets[name] = s["last_runtime_budget"]
        if budgets:
            bt = Table(title="[bold]last_runtime_budget", box=box.SIMPLE)
            bt.add_column("Backend", style="cyan")
            bt.add_column("Budget", style="dim")
            for name, b in sorted(budgets.items()):
                bt.add_row(str(name), _budget_short(b if isinstance(b, dict) else None))
            console.print(bt)

        running = list(queue.get("running") or [])
        queued = list(queue.get("queued") or [])
        if running or queued:
            jt = Table(title="[bold]Jobs", box=box.SIMPLE)
            jt.add_column("state")
            jt.add_column("job_id", style="cyan")
            jt.add_column("backend")
            jt.add_column("pri")
            jt.add_column("pct", justify="right")
            for j in running:
                pct = j.get("progress_pct")
                pct_s = f"{pct:.0%}" if isinstance(pct, (int, float)) else "—"
                jt.add_row(
                    "RUN",
                    _short_job_id(j.get("job_id")),
                    str(j.get("backend") or "?"),
                    str(j.get("priority") or "?"),
                    pct_s,
                )
            for j in queued[:12]:
                jt.add_row(
                    "Q",
                    _short_job_id(j.get("job_id")),
                    str(j.get("backend") or "?"),
                    str(j.get("priority") or "?"),
                    "—",
                )
            if len(queued) > 12:
                jt.add_row("…", f"+{len(queued) - 12} queued", "", "", "")
            console.print(jt)

        console.print("[dim]Só leitura. Para parar jobs: ums cancel / flush — nunca kill GPU enquanto HOLDING.[/dim]")
        return 0

    if watch_sec and watch_sec > 0:
        try:
            while True:
                console.clear()
                code = _once()
                if code != 0:
                    return
                time.sleep(watch_sec)
        except KeyboardInterrupt:
            console.print("\n[dim]debug watch parado (UMS intacto).[/dim]")
            return
    rc = _once()
    if rc:
        sys.exit(rc)


def _percentile(samples: list[float], p: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, round((p / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


@cli.command("bench")
@click.option("--rounds", default=20, show_default=True, type=int, help="Rounds por comando IPC.")
@click.option("--json", "as_json", is_flag=True, help="Dump JSON com amostras.")
@click.option(
    "--cmds",
    default="status,queue,stats",
    show_default=True,
    help="Comandos RPC a medir (vírgula). Só leitura — sem generate/submit.",
)
def bench_cmd(rounds: int, as_json: bool, cmds: str) -> None:
    """Benchmark RTT do socket UMS (IPC). Não submete jobs GPU.

    Seguro com batch a correr: só ``status`` / ``queue`` / ``stats``.
    """
    if rounds < 1:
        raise click.ClickException("--rounds deve ser ≥ 1")

    allowed = {"status": P.CMD_STATUS, "queue": P.CMD_QUEUE, "stats": P.CMD_STATS}
    wanted = [c.strip().lower() for c in cmds.split(",") if c.strip()]
    for c in wanted:
        if c not in allowed:
            raise click.ClickException(f"cmd não permitido no bench: {c} (só {sorted(allowed)})")

    # Probe busy (read-only) — aviso, não aborta.
    q0 = _send({"cmd": P.CMD_QUEUE}, timeout=5.0)
    if q0 is None:
        console.print("[yellow]UMS não está ativo.[/yellow]")
        sys.exit(1)
    depth = int(q0.get("queue_depth") or 0)
    inflight = int(q0.get("inflight") or 0)
    if depth or inflight:
        console.print(
            f"[yellow]UMS ocupado ({format_ums_holding_summary(q0)}) — bench IPC continua; NÃO submete GPU.[/yellow]"
        )
        _print_do_not_kill_tip(inflight=inflight, depth=depth)

    results: dict[str, Any] = {"rounds": rounds, "busy": bool(depth or inflight), "cmds": {}}

    console.print(
        Panel.fit(
            f"[bold]UMS Bench IPC[/bold] — {rounds} rounds · cmds={wanted}\n"
            f"[dim]Sem generate/submit/preload/evict — jobs a correr ficam intactos.[/dim]",
            border_style="cyan",
        )
    )

    t = Table(title="[bold blue]RTT (ms)", box=box.ROUNDED)
    t.add_column("cmd", style="cyan")
    t.add_column("n", justify="right")
    t.add_column("min", justify="right")
    t.add_column("p50", justify="right")
    t.add_column("avg", justify="right")
    t.add_column("p95", justify="right")
    t.add_column("max", justify="right")
    t.add_column("err", justify="right")

    for name in wanted:
        cmd = allowed[name]
        samples_ms: list[float] = []
        errors = 0
        for _ in range(rounds):
            t0 = time.perf_counter()
            resp = _send({"cmd": cmd}, timeout=5.0)
            dt = (time.perf_counter() - t0) * 1000.0
            if resp is None:
                errors += 1
            else:
                samples_ms.append(dt)
        if samples_ms:
            avg = sum(samples_ms) / len(samples_ms)
            p50 = _percentile(samples_ms, 50)
            p95 = _percentile(samples_ms, 95)
            row = {
                "n": len(samples_ms),
                "min_ms": round(min(samples_ms), 2),
                "p50_ms": round(p50, 2) if p50 is not None else None,
                "avg_ms": round(avg, 2),
                "p95_ms": round(p95, 2) if p95 is not None else None,
                "max_ms": round(max(samples_ms), 2),
                "errors": errors,
            }
            results["cmds"][name] = row
            t.add_row(
                name,
                str(row["n"]),
                f"{row['min_ms']:.2f}",
                f"{row['p50_ms']:.2f}" if row["p50_ms"] is not None else "—",
                f"{row['avg_ms']:.2f}",
                f"{row['p95_ms']:.2f}" if row["p95_ms"] is not None else "—",
                f"{row['max_ms']:.2f}",
                str(errors),
            )
        else:
            results["cmds"][name] = {"n": 0, "errors": errors}
            t.add_row(name, "0", "—", "—", "—", "—", "—", str(errors))

    if as_json:
        _print_json(results)
    else:
        console.print(t)
        console.print("[dim]Valores = round-trip Unix socket (não tempo de generate GPU).[/dim]")


@cli.command("doctor")
def doctor_cmd() -> None:
    """Diagnostica: deps de backends, GPU, socket, fila, peak VRAM, legacy."""
    import importlib
    import shutil

    from rich.panel import Panel

    console.print(Panel.fit("[bold]UMS Doctor[/bold] — diagnóstico de ambiente", border_style="blue"))

    checks: list[tuple[str, bool, str]] = []

    # 1. Socket do UMS ativo?
    from gamedev_shared.model_server import UMS_SOCKET, discover_active_sockets, is_ums_running

    ums_up = is_ums_running()
    checks.append(
        ("UMS ativo", ums_up, "Socket presente e respondendo" if ums_up else "Arrancar: gamedev-model-server start")
    )

    free_mib: int | None = None
    with contextlib.suppress(Exception):
        from gamedev_shared.gpu import query_gpu_free_mib

        free_mib = query_gpu_free_mib()

    qresp: dict | None = None
    # Fila / scheduler / peak (se UMS up).
    if ums_up:
        qresp = _send({"cmd": P.CMD_STATUS}, timeout=5.0)
        if qresp:
            q = qresp.get("queue") or {}
            depth = q.get("queue_depth", 0)
            inflight = q.get("inflight", 0)
            qm = qresp.get("queue_metrics") or q.get("metrics") or {}
            eta = qresp.get("eta_sec")
            dbg = qresp.get("debug") or {}
            affinity_hits = dbg.get("affinity_hits", qm.get("affinity_hits", "—"))
            detail = (
                f"depth={depth}/{q.get('max_depth', '?')}, inflight={inflight}, "
                f"affinity_cuts≤{qresp.get('max_affinity_cuts', '?')}, "
                f"affinity_hits={affinity_hits}, "
                f"eta={eta if eta is not None else '—'}s, "
                f"fulls={qm.get('queue_full_count', 0)}, "
                f"wait_p95={qm.get('queue_wait_p95_sec', '—')}"
            )
            max_d = int(q.get("max_depth") or 32)
            ok_q = depth < max_d
            checks.append(("Fila UMS", ok_q, detail if ok_q else f"SATURADA — {detail}"))

            # Peak vs free por backend carregado.
            backends = qresp.get("backends") or []
            loaded = [b for b in backends if b.get("loaded")]
            if loaded:
                parts = []
                for b in loaded:
                    peak = b.get("peak_mib") or b.get("vram_mib") or "?"
                    parts.append(f"{b.get('name')} peak={peak} MiB")
                free_s = f"{free_mib} MiB livres" if free_mib is not None else "free=?"
                peak_ok = True
                if free_mib is not None:
                    for b in loaded:
                        peak_v = b.get("peak_mib")
                        if isinstance(peak_v, (int, float)) and free_mib < int(peak_v) * 0.15:
                            # Só aviso informativo — free baixo com modelos já loaded é normal.
                            pass
                checks.append(
                    (
                        "Backends carregados",
                        peak_ok,
                        f"{free_s}; " + "; ".join(parts),
                    )
                )
            else:
                free_s = f"{free_mib} MiB livres" if free_mib is not None else "free=?"
                checks.append(("Backends carregados", True, f"nenhum — {free_s}"))

            if inflight or depth:
                checks.append(
                    (
                        "Não matar GPU",
                        True,
                        "UMS tem jobs na fila — usa `ums queue` / cancel / wait; NÃO kill processos GPU",
                    )
                )

    # Legacy per-tool sockets (conflito potencial com UMS).
    try:
        legacy = [s for s in discover_active_sockets() if Path(s).resolve() != Path(UMS_SOCKET).resolve()]
    except Exception:
        legacy = []
    if legacy:
        names = ", ".join(Path(s).name for s in legacy)
        checks.append(
            (
                "Sockets legacy",
                False,
                f"Activos: {names} — conflito com UMS; para ou GAMEDEV_ALLOW_LEGACY_SERVER=1 só se preciso",
            )
        )
    else:
        checks.append(("Sockets legacy", True, "nenhum per-tool activo"))

    # 2. GPU disponível? (NVML preferido; fallback nvidia-smi via Shared)
    from gamedev_shared.gpu import list_gpu_snapshots, nvml_available

    snaps = list_gpu_snapshots()
    if snaps:
        s0 = snaps[0]
        extra = f" (+{len(snaps) - 1} GPU)" if len(snaps) > 1 else ""
        gpu_detail = f"{s0.name} — {s0.total_mib} MiB total, {s0.free_mib} MiB livres via {s0.source}{extra}"
    elif nvml_available():
        gpu_detail = "NVML ok mas sem devices"
    elif shutil.which("nvidia-smi") is not None:
        gpu_detail = "nvidia-smi presente mas sem leitura de memória"
    else:
        gpu_detail = "NVML/nvidia-smi indisponível"
    checks.append(("GPU NVIDIA", bool(snaps), gpu_detail))

    # 3. hf_xet (downloads HF acelerados via hub >=1.5)
    try:
        importlib.import_module("hf_xet")
        checks.append(("hf_xet", True, "Downloads HF acelerados (Xet / hub >=1.5)"))
    except ImportError:
        checks.append(("hf_xet", False, "pip install 'hf-xet>=1.2' — downloads HF acelerados"))

    # 4. Deps de cada backend (verificar se o módulo da tool importa).
    from .registry import Registry

    registry = Registry()
    tool_modules = {
        "text2icon": "text2icon.generator",
        "texture2d": "texture2d.generator",
        "text2d": "text2d.generator",
        "skymap2d": "skymap2d.generator",
        "text3d": "text3d.generator",
        "paint3d": "paint3d.painter",
        "text2sound": "text2sound.generator",
        "terrain3d": "terrain3d.generator",
        "part3d": "part3d.pipeline",
    }
    for backend_name in sorted(registry.names):
        mod_path = tool_modules.get(backend_name)
        if mod_path is None:
            checks.append((f"Backend {backend_name}", False, "mapping em falta"))
            continue
        try:
            importlib.import_module(mod_path)
            checks.append((f"Backend {backend_name}", True, "deps OK"))
        except ImportError as e:
            checks.append((f"Backend {backend_name}", False, f"ImportError: {e.name or e}"))

    # Renderizar tabela.
    t = Table(title="[bold blue]Diagnóstico", box=box.ROUNDED)
    t.add_column("Check", style="cyan", no_wrap=True)
    t.add_column("Estado")
    t.add_column("Detalhe", style="dim")

    all_ok = True
    for name, passed, detail in checks:
        status = "[green]✓ OK[/green]" if passed else "[red]✗ FALHA[/red]"
        if not passed:
            all_ok = False
        t.add_row(name, status, detail)

    console.print(t)
    _print_do_not_kill_tip()
    if ums_up and qresp and (qresp.get("queue") or {}).get("queue_depth"):
        console.print(
            "[yellow]Hint:[/yellow] há jobs na fila UMS — [bold]não mates GPU[/bold]; "
            "usa [cyan]gamedev-model-server queue[/cyan] / cancel."
        )
    if all_ok:
        console.print("[bold green]✓ Todos os checks passaram.[/bold green]")
    else:
        console.print("[yellow]Alguns checks falharam — ver detalhes acima.[/yellow]")


def main() -> None:
    """Entry point para ``gamedev-model-server`` / ``ums``."""
    cli()


if __name__ == "__main__":
    main()
