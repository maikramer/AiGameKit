#!/usr/bin/env python3
"""Unified Model Server — CLI principal.

Comandos (alias ``ums`` = ``gamedev-model-server``):
  start|stop|status|queue|wait|cancel|backends|preload|evict|stats|doctor

Agentes / humanos: se a GPU estiver ocupada, usa ``status`` / ``queue`` —
**não** mates processos GPU enquanto houver jobs UMS.
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
@click.argument("job_id")
@click.option("--json", "as_json", is_flag=True, help="Dump JSON da resposta.")
def cancel_cmd(job_id: str, as_json: bool) -> None:
    """Cancela um job UMS (queued imediato; running best-effort)."""
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
        console.print(
            f"[bold green]✓[/bold green] job {job_id[:8]}… → {resp.get('state', '?')} {resp.get('message', '')}"
        )
        if resp.get("ums_debug"):
            console.print(f"[dim]ums_debug: {json.dumps(resp['ums_debug'], ensure_ascii=False)}[/dim]")
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


@cli.command("stats")
@click.option("--reset", is_flag=True, help="Limpa todas as estatísticas.")
def stats_cmd(reset: bool) -> None:
    """Mostra estatísticas de performance por backend (cargas, gerações, timings)."""
    if reset:
        resp = _send({"cmd": P.CMD_STATS}, timeout=5.0)
        if resp is not None:
            # Reset local das stats pedindo ao manager via protocolo extendido.
            # Como o stats collector vive no processo do UMS, o reset faz-se
            # parando e arrancando o servidor. Por agora, mostramos o atual.
            console.print("[yellow]Reset das stats requer restart do UMS.[/yellow]")
        return

    resp = _send({"cmd": P.CMD_STATS}, timeout=5.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo.[/yellow]")
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold]UMS Stats[/bold] — PID {resp.get('pid', '?')}, "
            f"{resp.get('requests_served', 0)} pedidos servidos, "
            f"idle evict após {resp.get('idle_evict_timeout_sec', '?')}s",
            border_style="blue",
        )
    )

    backends = resp.get("backends", {})
    if not backends:
        console.print("[dim]Sem atividade registada (nenhum backend usado ainda).[/dim]")
        return

    t = Table(title="[bold blue]Estatísticas por Backend", box=box.SIMPLE)
    t.add_column("Backend", style="cyan")
    t.add_column("Loads", justify="right")
    t.add_column("Gens", justify="right")
    t.add_column("Evicts", justify="right")
    t.add_column("Errors", justify="right")
    t.add_column("Avg Load", justify="right")
    t.add_column("Avg Gen", justify="right")
    t.add_column("Idle (s)", justify="right")

    for name in sorted(backends):
        s = backends[name]
        t.add_row(
            name,
            str(s.get("load_count", 0)),
            str(s.get("generate_count", 0)),
            str(s.get("evict_count", 0)),
            str(s.get("error_count", 0)),
            f"{s.get('avg_load_time_sec', 0):.1f}s",
            f"{s.get('avg_generate_time_sec', 0):.1f}s",
            str(s.get("idle_sec", "—")),
        )
    console.print(t)


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

    # 2. GPU disponível?
    gpu_ok = shutil.which("nvidia-smi") is not None
    gpu_detail = ""
    if gpu_ok:
        import subprocess

        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                parts = r.stdout.strip().split(", ")
                gpu_detail = f"{parts[0]} — {parts[1]} MiB total, {parts[2]} MiB livres"
        except Exception:
            gpu_detail = "nvidia-smi presente mas erro a ler"
    checks.append(("GPU NVIDIA", gpu_ok, gpu_detail or "nvidia-smi não encontrado"))

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
