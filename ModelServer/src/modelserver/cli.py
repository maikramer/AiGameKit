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
    """Diagnostica: deps de backends, GPU, socket, cache HF."""
    import importlib
    import shutil

    from rich.panel import Panel

    console.print(Panel.fit("[bold]UMS Doctor[/bold] — diagnóstico de ambiente", border_style="blue"))

    checks: list[tuple[str, bool, str]] = []

    # 1. Socket do UMS ativo?
    from gamedev_shared.model_server import is_ums_running

    ums_up = is_ums_running()
    checks.append(
        ("UMS ativo", ums_up, "Socket presente e respondendo" if ums_up else "Arrancar: gamedev-model-server start")
    )

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

    # 3. hf_transfer instalado?
    try:
        importlib.import_module("hf_transfer")
        checks.append(("hf_transfer", True, "Downloads HF acelerados (5-10x)"))
    except ImportError:
        checks.append(("hf_transfer", False, "pip install hf_transfer — downloads 5-10x mais rápidos"))

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
    if all_ok:
        console.print("[bold green]✓ Todos os checks passaram.[/bold green]")
    else:
        console.print("[yellow]Alguns checks falharam — ver detalhes acima.[/yellow]")


def main() -> None:
    """Entry point para o console script ``gamedev-model-server``."""
    cli()


if __name__ == "__main__":
    main()
