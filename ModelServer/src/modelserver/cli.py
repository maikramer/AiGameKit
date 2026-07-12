#!/usr/bin/env python3
"""Unified Model Server — CLI principal.

Comandos:
  gamedev-model-server start [--verbose] [--idle-timeout N]   Arranca o UMS (foreground)
  gamedev-model-server stop                                    Graceful shutdown
  gamedev-model-server status                                  Estado do UMS
  gamedev-model-server backends                                Lista backends registados
  gamedev-model-server preload <name>                          Pré-carrega um backend
  gamedev-model-server evict [<name>]                          Evicta um (ou todos) os backends
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

try:
    import rich_click as click
except ImportError:  # pragma: no cover
    import click  # type: ignore[no-redef]

from gamedev_shared.model_server import is_server_running, send_request

from . import protocol as P
from .registry import Registry

console = Console()


def _send(request: dict, *, timeout: float = 30.0) -> dict | None:
    """Envia um pedido ao UMS no socket canónico. Retorna None se down."""
    if not is_server_running(P.DEFAULT_SOCKET_PATH):
        return None
    return send_request(request, P.DEFAULT_SOCKET_PATH, timeout_sec=timeout)


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
    from .server import UnifiedModelServer

    sock = Path(socket_path) if socket_path else P.DEFAULT_SOCKET_PATH
    if is_server_running(sock):
        console.print("[yellow]UMS já está ativo neste socket.[/yellow]")
        sys.exit(1)

    registry = Registry()
    console.print(
        Panel.fit(
            f"[bold]Unified Model Server[/bold]\n"
            f"Socket: [cyan]{sock}[/cyan]\n"
            f"Backends: [green]{', '.join(registry.names)}[/green]\n"
            f"Idle timeout: [green]{idle_timeout_min} min[/green]\n\n"
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
def status_cmd() -> None:
    """Mostra o estado do UMS e backends carregados."""
    resp = _send({"cmd": P.CMD_STATUS}, timeout=5.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo.[/yellow]")
        console.print("[dim]Arranca com: gamedev-model-server start[/dim]")
        sys.exit(1)

    t = Table(title="[bold blue]Unified Model Server", box=box.ROUNDED)
    t.add_column("Campo", style="cyan", no_wrap=True)
    t.add_column("Valor", style="green")
    t.add_row("PID", str(resp.get("pid", "?")))
    t.add_row("Socket", str(resp.get("socket", "?")))
    t.add_row("Backends carregados", f"{resp.get('loaded_count', 0)} ({resp.get('loaded_vram_mib', 0)} MiB)")
    t.add_row("Pedidos servidos", str(resp.get("requests_served", 0)))
    console.print(t)

    backends = resp.get("backends", [])
    if backends:
        bt = Table(title="[bold]Backends", box=box.SIMPLE)
        bt.add_column("Backend", style="cyan")
        bt.add_column("VRAM (MiB)", justify="right")
        bt.add_column("Priority", justify="right")
        bt.add_column("Carregado")
        bt.add_column("Refs", justify="right")
        for b in backends:
            loaded = "[green]✓[/green]" if b.get("loaded") else "[dim]✗[/dim]"
            bt.add_row(
                b["name"],
                str(b["vram_mib"]),
                str(b["priority"]),
                loaded,
                str(b.get("ref_count", 0)),
            )
        console.print(bt)


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
def preload_cmd(name: str) -> None:
    """Pré-carrega um backend (ex: text2icon)."""
    resp = _send({"cmd": P.CMD_PRELOAD, "backend": name}, timeout=600.0)
    if resp is None:
        console.print("[yellow]UMS não está ativo. Arranca com: gamedev-model-server start[/yellow]")
        sys.exit(1)
    if resp.get("status") == "ok":
        console.print(f"[bold green]✓ {resp.get('message', 'pré-carregado')}[/bold green]")
    else:
        console.print(f"[bold red]✗ {resp.get('error', resp)}[/bold red]")
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


def main() -> None:
    """Entry point para o console script ``gamedev-model-server``."""
    cli()


if __name__ == "__main__":
    main()
