#!/usr/bin/env python3
"""Unified Model Server — CLI principal.

Comandos (alias ``ums`` = ``aigamekit-model-server``):
  start|stop|status|submit|cancel|flush|queue|wait|backends|preload|evict|reap|
  respawn|zero|stats|debug|bench|doctor|calibrate

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

from aigamekit_shared.model_server import (
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


def _describe_stray(proc: dict[str, Any]) -> str:
    """Uma linha por órfão: ``PID 123 worker/text3d 3470 MiB``."""
    who = str(proc.get("kind") or "?")
    if proc.get("backend"):
        who += f"/{proc['backend']}"
    vram = f" {proc['vram_mib']} MiB" if proc.get("vram_mib") else ""
    return f"PID {proc.get('pid')} {who}{vram}"


def _print_strays(strays: dict[str, Any]) -> None:
    """Avisa sobre supervisores/workers UMS órfãos e a VRAM que seguram."""
    count = int(strays.get("count") or 0)
    if not count:
        return
    vram = strays.get("vram_mib") or 0
    st = Table(title="[bold red]Processos UMS órfãos", box=box.SIMPLE)
    st.add_column("PID", justify="right", style="red")
    st.add_column("Tipo", style="cyan")
    st.add_column("Backend")
    st.add_column("VRAM", justify="right")
    for proc in strays.get("processes") or []:
        st.add_row(
            str(proc.get("pid")),
            str(proc.get("kind")),
            str(proc.get("backend") or "—"),
            f"{proc.get('vram_mib')} MiB" if proc.get("vram_mib") else "—",
        )
    console.print(st)
    console.print(f"[yellow]{count} processo(s) órfão(s) a segurar ~{vram} MiB — limpa com `ums reap`.[/yellow]")


def _short_job_id(job_id: object, *, n: int = 12) -> str:
    jid = str(job_id or "")
    if not jid:
        return "?"
    return jid if len(jid) <= n else f"{jid[:n]}…"


@click.group()
@click.version_option(version="0.1.0", prog_name="aigamekit-model-server")
def cli() -> None:
    """Unified Model Server — supervisor único de VRAM para o monorepo AiGameKit."""


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
@click.option(
    "--idle-evict-sec",
    default=P.IDLE_EVICT_SEC,
    show_default=True,
    type=float,
    help="Segundos sem uso antes de descarregar os pesos de um backend.",
)
@click.option(
    "--worker-shutdown-sec",
    default=P.WORKER_IDLE_SHUTDOWN_SEC,
    show_default=True,
    type=float,
    help="Segundos sem uso antes de terminar o subprocesso worker (0 desliga).",
)
@click.option("--verbose", "-v", is_flag=True, help="Logs detalhados")
def start_cmd(
    socket_path: str | None,
    idle_timeout_min: int,
    idle_evict_sec: float,
    worker_shutdown_sec: float,
    verbose: bool,
) -> None:
    """Arranca o Unified Model Server (foreground)."""
    from aigamekit_shared.logging import configure_logging

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
            f"Idle evict: [green]{idle_evict_sec:.0f}s[/green] (unload) / "
            f"[green]{worker_shutdown_sec:.0f}s[/green] (worker)\n"
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
        idle_evict_sec=idle_evict_sec,
        worker_shutdown_sec=worker_shutdown_sec,
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
        console.print("[dim]Arranca com: aigamekit-model-server start[/dim]")
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
    worker_vram = resp.get("worker_vram_mib")
    if worker_vram:
        t.add_row("VRAM nos workers", f"{worker_vram} MiB")
    t.add_row("Pedidos servidos", str(resp.get("requests_served", 0)))
    t.add_row(
        "Idle evict",
        f"{float(resp.get('idle_evict_timeout_sec') or 0):.0f}s unload / "
        f"{float(resp.get('worker_shutdown_sec') or 0):.0f}s worker",
    )
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

    _print_strays(resp.get("strays") or {})

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
    from aigamekit_shared.model_server import wait_ums_job

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
        console.print("[yellow]UMS não está ativo. Arranca com: aigamekit-model-server start[/yellow]")
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
    """Evicta um backend específico ou todos (sem argumento).

    Descarrega os pesos do modelo mas MANTÉM o worker vivo — NÃO apanha código
    novo da tool. Para recarregar código editado, usa ``ums respawn``.
    """
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


@cli.command("reap")
@click.option("--dry-run", is_flag=True, help="Só lista o que seria terminado.")
@click.option("--json", "as_json", is_flag=True, help="Dump JSON da resposta.")
def reap_cmd(dry_run: bool, as_json: bool) -> None:
    """Termina supervisores/workers UMS órfãos que seguram VRAM.

    Com o UMS ativo, o pedido é delegado nele (protege-se a si e aos seus
    workers). Sem UMS, o reap corre localmente e limpa toda a família — é o caso
    típico de um supervisor zombie que já não responde no socket.
    """
    resp = _send({"cmd": P.CMD_REAP, "dry_run": dry_run}, timeout=30.0)
    if resp is None:
        from .process_guard import reap_strays as _reap

        resp = {"status": P.STATUS_OK, "local": True, **_reap(dry_run=dry_run)}
    if as_json:
        _print_json(resp)
        return
    count = int(resp.get("count") or 0)
    if not count:
        console.print("[green]✓ Nenhum processo UMS órfão.[/green]")
        return
    freed = resp.get("vram_mib_freed")
    if dry_run:
        for line in resp.get("would_reap") or []:
            console.print(f"[yellow]• {line}[/yellow]")
        console.print(f"[yellow]{count} órfão(s), ~{freed} MiB — corre sem --dry-run para terminar.[/yellow]")
        return
    for entry in resp.get("reaped") or []:
        console.print(f"[green]✓[/green] PID {entry.get('pid')} ({entry.get('kind')}) — {entry.get('signal')}")
    console.print(f"[bold green]✓ {count} processo(s) terminado(s); ~{freed} MiB de VRAM recuperados.[/bold green]")


@cli.command("respawn")
@click.argument("name", required=False)
@click.option(
    "--lazy/--hot",
    "lazy",
    default=True,
    help="lazy (default): mata o worker vivo; o próximo generate arranca-o com código novo. "
    "hot: mata e recarrega já o modelo (fica quente).",
)
def respawn_cmd(name: str | None, lazy: bool) -> None:
    """Reinicia SÓ o worker de um backend para apanhar código novo da tool.

    Depois de editar código de uma tool (ex.: Text3D ``utils/export.py``), o
    worker persistente em ``<Tool>/.venv`` ainda tem o módulo antigo em memória
    — ``evict`` só larga os pesos. ``respawn`` mata o subprocesso e arranca um
    novo no venv da tool, pelo que o próximo ``generate`` já corre o código
    atualizado, SEM reiniciar o supervisor UMS.

    Sem argumento: reinicia todos os backends com worker subprocesso.
    """
    request: dict = {"cmd": P.CMD_RESPAWN, "lazy": lazy}
    if name:
        request["backend"] = name
    resp = _send(request, timeout=120.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo — nada para respawnar.[/yellow]")
        sys.exit(0)
    if resp.get("status") != "ok":
        _print_ums_error(resp)
        _print_do_not_kill_tip()
        sys.exit(1)

    results = resp.get("results", []) or []
    mode = "lazy" if resp.get("lazy", lazy) else "hot"
    killed = sum(1 for r in results if r.get("respawned"))
    total = len(results)
    console.print(f"[bold green]✓ {killed}/{total} worker(s) reiniciado(s) ({mode}) — supervisor intacto.[/bold green]")
    for r in results:
        rname = r.get("name", "?")
        if r.get("respawned"):
            tag = f"reiniciado ({mode})"
        elif r.get("mode") in ("in-process", "no-pool"):
            tag = f"[dim]no-op: {r.get('reason', r.get('mode'))}[/dim]"
        else:
            tag = "[dim]não estava vivo[/dim]"
        shape = r.get("load_shape") or {}
        shape_str = f" shape={shape}" if shape else ""
        console.print(f"  • {rname}: {tag}{shape_str}")
    # Recomendar preload se foi hot e a shape não chegou (worker novo precisa de load).
    if mode == "hot":
        console.print(
            "[dim]Próximo generate está quente se o load_shape foi preservado; "
            "caso contrário usa `ums preload <backend>`.[/dim]"
        )
    else:
        console.print("[dim]Worker arranca no próximo generate/preload — já com código atualizado.[/dim]")


@cli.command("zero")
def zero_cmd() -> None:
    """Zera TODA a VRAM segurada pelo UMS SEM parar o supervisor.

    ``evict`` só larga os pesos — os workers ficam vivos a segurar o contexto
    CUDA (~0.3-1 GiB cada). ``zero`` termina todos os workers (o próximo
    generate arranca-os frescos), evicta resíduos e scrubba caches. Recusa com
    fila ocupada — nunca mata um worker a meio de um job.
    """
    resp = _send({"cmd": P.CMD_ZERO}, timeout=120.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo — nada para zerar.[/yellow]")
        sys.exit(0)
    if resp.get("status") != "ok":
        _print_ums_error(resp)
        _print_do_not_kill_tip()
        sys.exit(1)

    for r in resp.get("results", []) or []:
        rname = r.get("name", "?")
        if r.get("killed"):
            tag = "terminado (contexto CUDA libertado)"
        elif r.get("was_alive"):
            tag = "[dim]skip (load/generate em curso)[/dim]"
        else:
            tag = "[dim]não estava vivo[/dim]"
        model_str = " (tinha modelo)" if r.get("had_model") else ""
        console.print(f"  • {rname}: {tag}{model_str}")
    fb, fa = resp.get("free_mib_before"), resp.get("free_mib_after")
    free_str = f" — VRAM livre: {fb} → {fa} MiB" if isinstance(fa, int) else ""
    console.print(f"[bold green]✓ {resp.get('message', 'VRAM zerada')}{free_str}[/bold green]")


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
            from aigamekit_shared.gpu import query_gpu_free_mib

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
@click.option("--fix", is_flag=True, help="Corrige o que é seguro (reap de processos UMS órfãos).")
def doctor_cmd(fix: bool) -> None:
    """Diagnostica: deps de backends, GPU, socket, fila, peak VRAM, órfãos, legacy."""
    import importlib
    import shutil

    from rich.panel import Panel

    console.print(Panel.fit("[bold]UMS Doctor[/bold] — diagnóstico de ambiente", border_style="blue"))

    checks: list[tuple[str, bool, str]] = []

    # 1. Socket do UMS ativo?
    from aigamekit_shared.model_server import UMS_SOCKET, discover_active_sockets, is_ums_running

    ums_up = is_ums_running()
    checks.append(
        ("UMS ativo", ums_up, "Socket presente e respondendo" if ums_up else "Arrancar: aigamekit-model-server start")
    )

    free_mib: int | None = None
    with contextlib.suppress(Exception):
        from aigamekit_shared.gpu import query_gpu_free_mib

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

    # Órfãos: supervisores/workers de runs anteriores a segurar VRAM.
    from .process_guard import reap_strays as _reap
    from .process_guard import stray_report as _stray_report

    strays: dict[str, Any] = {}
    if ums_up and qresp:
        strays = qresp.get("strays") or {}
    else:
        with contextlib.suppress(Exception):
            strays = _stray_report()
    stray_count = int(strays.get("count") or 0)
    if stray_count:
        detail = ", ".join(_describe_stray(p) for p in strays.get("processes") or [])
        if fix:
            report = _reap()
            checks.append(
                (
                    "Processos UMS órfãos",
                    True,
                    f"{report.get('count')} terminado(s) (~{report.get('vram_mib_freed')} MiB) — {detail}",
                )
            )
        else:
            checks.append(
                (
                    "Processos UMS órfãos",
                    False,
                    f"{stray_count} a segurar ~{strays.get('vram_mib')} MiB ({detail}) — corre `ums reap`",
                )
            )
    else:
        checks.append(("Processos UMS órfãos", True, "nenhum"))

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
                f"Activos: {names} — conflito com UMS; para ou AIGAMEKIT_ALLOW_LEGACY_SERVER=1 só se preciso",
            )
        )
    else:
        checks.append(("Sockets legacy", True, "nenhum per-tool activo"))

    # 2. GPU disponível? (NVML preferido; fallback nvidia-smi via Shared)
    from aigamekit_shared.gpu import check_nvidia_driver_match, list_gpu_snapshots, nvml_available

    driver_ok, driver_detail = check_nvidia_driver_match()
    checks.append(("NVIDIA driver match", driver_ok, driver_detail))

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
            "usa [cyan]aigamekit-model-server queue[/cyan] / cancel."
        )
    if all_ok:
        console.print("[bold green]✓ Todos os checks passaram.[/bold green]")
    else:
        console.print("[yellow]Alguns checks falharam — ver detalhes acima.[/yellow]")


def _coerce_scalar(raw: str) -> Any:
    """``"4"`` → 4, ``"0.7"`` → 0.7, ``"true"`` → True, ``"none"`` → None, resto string."""
    text = raw.strip()
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("none", "null", ""):
        return None
    with contextlib.suppress(ValueError):
        return int(text)
    with contextlib.suppress(ValueError):
        return float(text)
    return text


def _parse_kv(pairs: tuple[str, ...]) -> dict[str, Any]:
    """``("a=1", "b=x")`` → ``{"a": 1, "b": "x"}``.

    Raises:
        click.BadParameter: par sem ``=``.
    """
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise click.BadParameter(f"esperado K=V, recebido {pair!r}")
        key, _, value = pair.partition("=")
        out[key.strip()] = _coerce_scalar(value)
    return out


def _render_calibration(cal: Any) -> None:
    """Tabela com a decomposição medida + avisos."""
    colour = {"high": "green", "medium": "yellow", "low": "red"}.get(cal.confidence, "white")
    console.print(
        Panel.fit(
            f"[bold]{cal.backend}[/bold] — pico medido [bold]{cal.peak_mib} MiB[/bold] "
            f"(admit {cal.admit_peak_mib} MiB) · confiança [{colour}]{cal.confidence}[/{colour}]",
            border_style=colour,
        )
    )
    t = Table(box=box.SIMPLE, show_header=True)
    t.add_column("Componente")
    t.add_column("MiB", justify="right")
    t.add_column("GiB", justify="right")
    t.add_column("Origem", style="dim")
    t.add_row("contexto CUDA", str(cal.context_mib), f"{cal.context_gib:.2f}", "residual pós-unload")
    t.add_row("pesos", str(cal.weights_mib), f"{cal.weights_gib:.2f}", "residente pós-load - contexto")
    t.add_row("activação", str(cal.activation_mib), f"{cal.activation_gib:.2f}", "pico generate - residente")
    t.add_row("pico do load", str(cal.load_peak_mib), "", "transiente de carregamento")
    t.add_row("pico do generate", str(cal.generate_peak_mib), "", f"máx de {cal.repeats} repetições")
    t.add_row("[bold]pico[/bold]", f"[bold]{cal.peak_mib}[/bold]", f"{cal.peak_gib:.2f}", "max(load, generate)")
    t.add_row("safety recomendado", str(cal.recommended_safety_mib), "", "dispersão + fragmentação")
    console.print(t)

    q = Table(box=box.SIMPLE, show_header=True, title="Qualidade da medição")
    q.add_column("Métrica")
    q.add_column("Valor", justify="right")
    q.add_row("amostras", str(cal.samples_n))
    q.add_row("maior gap", f"{cal.max_gap_sec:.3f}s (alvo {cal.interval_sec:.3f}s)")
    q.add_row("amostras sem dados", f"{cal.missed_ratio:.1%}")
    q.add_row("VRAM de terceiros", f"{cal.foreign_baseline_mib} → {cal.foreign_max_mib} MiB")
    q.add_row("load / generate", f"{cal.load_sec:.1f}s / {cal.generate_sec_median:.1f}s (mediana)")
    console.print(q)

    for warning in cal.warnings:
        console.print(f"[yellow]![/yellow] {warning}")


def _render_comparison(rows: list[Any]) -> int:
    """Tabela medido vs declarado; devolve o número de métricas subdimensionadas."""
    from .calibrate.compare import VERDICT_OK, VERDICT_OVER, VERDICT_UNDER

    t = Table(box=box.SIMPLE, show_header=True, title="Declarado vs medido")
    t.add_column("Métrica")
    t.add_column("Declarado", justify="right")
    t.add_column("Medido", justify="right")
    t.add_column("Δ", justify="right")
    t.add_column("Rácio", justify="right")
    t.add_column("Veredicto")
    styles = {
        VERDICT_OK: "[green]ok[/green]",
        VERDICT_UNDER: "[red]subdimensionado[/red]",
        VERDICT_OVER: "[yellow]sobredimensionado[/yellow]",
    }
    under = 0
    for row in rows:
        if row.verdict == VERDICT_UNDER:
            under += 1
        delta = "—" if row.delta_mib is None else f"{row.delta_mib:+d}"
        ratio = "—" if row.ratio is None else f"{row.ratio:.2f}x"
        t.add_row(
            row.metric,
            "—" if row.declared_mib is None else str(row.declared_mib),
            str(row.measured_mib),
            delta,
            ratio,
            styles.get(row.verdict, row.verdict),
        )
    console.print(t)
    return under


@cli.command("calibrate")
@click.argument("backend")
@click.option("--prompt", default=None, help="Prompt do job de calibração (atalho para --request-json).")
@click.option("--output", "output_path", default=None, help="Path de output do job.")
@click.option(
    "--request-json",
    "request_json",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Ficheiro JSON com o request completo (backends sem prompt: paint3d, part3d…).",
)
@click.option("--load-kwarg", "load_kwargs_raw", multiple=True, metavar="K=V", help="Kwarg de load (repetível).")
@click.option("--quant", default=None, help="Atalho para --load-kwarg sdnq_preset=… (etiqueta no relatório).")
@click.option("--repeats", default=3, show_default=True, help="Repetições do generate (≥2 separa warmup).")
@click.option("--cycles", default=1, show_default=True, help="Pares load/unload extra (isola contexto CUDA).")
@click.option("--interval", default=0.05, show_default=True, help="Intervalo de amostragem (s).")
@click.option("--settle", default=1.5, show_default=True, help="Espera para o driver assentar (s).")
@click.option("--baseline", default=1.0, show_default=True, help="Janela de silêncio antes do spawn (s).")
@click.option("--out", "out_path", type=click.Path(dir_okay=False), default=None, help="Escreve o YAML calibrado.")
@click.option("--report", "report_path", type=click.Path(dir_okay=False), default=None, help="Escreve o JSON.")
@click.option("--compare/--no-compare", default=True, show_default=True, help="Comparar com o declarado.")
@click.option("--zero/--no-zero", default=True, show_default=True, help="`ums zero` antes de medir (GPU limpa).")
@click.option("--force", is_flag=True, help="Ignora o preflight (medição pode ficar contaminada).")
@click.option("--json", "as_json", is_flag=True, help="Dump JSON do relatório.")
def calibrate_cmd(
    backend: str,
    prompt: str | None,
    output_path: str | None,
    request_json: str | None,
    load_kwargs_raw: tuple[str, ...],
    quant: str | None,
    repeats: int,
    cycles: int,
    interval: float,
    settle: float,
    baseline: float,
    out_path: str | None,
    report_path: str | None,
    compare: bool,
    zero: bool,
    force: bool,
    as_json: bool,
) -> None:
    """Mede o footprint VRAM real de um backend e emite o descriptor YAML.

    Corre um job real com amostragem por processo a ~20 Hz, separa contexto
    CUDA / pesos / activação, e compara com o que está declarado.

    A GPU tem de estar livre: com jobs UMS em curso a medição mede-os a eles.
    """
    from .calibrate import CalibrationRunner, CalibrationSpec
    from .calibrate.compare import compare_to_declared, declared_parts_from_registry
    from .calibrate.emit import calibration_to_report, calibration_to_yaml
    from .subprocess_pool import SubprocessWorkerPool

    registry = Registry()
    try:
        desc = registry.descriptor(backend)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(2)

    load_kwargs = _parse_kv(load_kwargs_raw)
    if quant:
        load_kwargs.setdefault("sdnq_preset", quant)
    quant_mode = str(load_kwargs.get("sdnq_preset") or load_kwargs.get("quant_mode") or "none")

    if request_json:
        request = json.loads(Path(request_json).read_text(encoding="utf-8"))
    else:
        request = {
            "prompt": prompt or "calibration job",
            "output": output_path or f"/tmp/ums-calibrate-{backend}.bin",
        }

    pool = SubprocessWorkerPool()
    runner = CalibrationRunner(pool)

    blockers = runner.preflight()
    if blockers:
        for reason in blockers:
            console.print(f"[red]✗[/red] {reason}")
        if not force:
            console.print("[dim]Usa --force para medir na mesma (resultado marcado como pouco fiável).[/dim]")
            sys.exit(2)

    if zero:
        with contextlib.suppress(Exception):
            from aigamekit_shared.model_server import zero_ums_vram

            if zero_ums_vram() is not None:
                console.print("[dim]UMS zero: workers idle terminados antes de medir.[/dim]")

    spec = CalibrationSpec(
        backend=backend,
        tool=desc.tool,
        request=request,
        load_kwargs=load_kwargs,
        repeats=repeats,
        cycles=cycles,
        baseline_sec=baseline,
        settle_sec=settle,
        interval_sec=interval,
        quant_mode=quant_mode,
    )

    console.print(f"[dim]A calibrar {backend} ({repeats}x generate, amostragem {interval:.3f}s)…[/dim]")
    try:
        cal = runner.run(spec)
    except Exception as exc:
        console.print(f"[red]Calibração falhou:[/red] {exc}")
        sys.exit(1)
    finally:
        with contextlib.suppress(Exception):
            pool.shutdown_all()

    report = calibration_to_report(cal)
    if as_json:
        _print_json(report)
    else:
        _render_calibration(cal)

    exit_code = 0
    if compare:
        weights, activation, vram_mib = declared_parts_from_registry(backend, registry=registry, quant_mode=quant_mode)
        rows = compare_to_declared(
            cal,
            declared_weights_mib=weights,
            declared_activation_mib=activation,
            declared_vram_mib=vram_mib,
        )
        report["comparison"] = [row.as_dict() for row in rows]
        if not as_json:
            under = _render_comparison(rows)
            if under:
                console.print(f"[red]{under} métrica(s) subdimensionada(s)[/red] — risco de OOM em admissão.")
                exit_code = 1

    if out_path:
        meta = {
            backend: {
                "adapter": desc.adapter,
                "priority": desc.priority,
                "footprint_key": desc.footprint_key,
            }
        }
        Path(out_path).write_text(calibration_to_yaml(cal, descriptors=meta), encoding="utf-8")
        console.print(f"[green]YAML escrito:[/green] {out_path}")
    if report_path:
        Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(f"[green]Relatório escrito:[/green] {report_path}")

    sys.exit(exit_code)


def main() -> None:
    """Entry point para ``aigamekit-model-server`` / ``ums``."""
    cli()


if __name__ == "__main__":
    main()
