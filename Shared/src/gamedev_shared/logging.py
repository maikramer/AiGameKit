"""Logger unificado Rich/ANSI para o monorepo GameDev."""

from __future__ import annotations

import contextlib
import sys


def _configure_stdio_utf8() -> None:
    """Evita UnicodeEncodeError no Windows (cp1252) com Rich e símbolos como ✓."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            with contextlib.suppress(OSError, ValueError):
                stream.reconfigure(encoding="utf-8")


_configure_stdio_utf8()

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    _RICH = True
except ImportError:
    _RICH = False


class Logger:
    """Saída com Rich quando disponível; fallback ANSI.

    Unifica o padrão duplicado em Text2D, Text3D e Materialize installers.
    Pode receber um ``Console`` Rich existente ou criar um internamente.
    """

    def __init__(self, console: Console | None = None) -> None:
        if _RICH:
            self._console: Console | None = console or Console()
        else:
            self._console = None

    @property
    def rich_available(self) -> bool:
        return _RICH and self._console is not None

    @property
    def console(self) -> Console | None:
        return self._console

    def info(self, msg: str) -> None:
        if self.rich_available:
            self._console.print(f"[bold green]INFO[/bold green] {msg}")  # type: ignore[union-attr]
        else:
            print(f"\033[0;32m[INFO]\033[0m {msg}")

    def warn(self, msg: str) -> None:
        if self.rich_available:
            self._console.print(f"[bold yellow]WARN[/bold yellow] {msg}")  # type: ignore[union-attr]
        else:
            print(f"\033[1;33m[WARN]\033[0m {msg}")

    def error(self, msg: str) -> None:
        if self.rich_available:
            self._console.print(f"[bold red]ERROR[/bold red] {msg}")  # type: ignore[union-attr]
        else:
            print(f"\033[0;31m[ERROR]\033[0m {msg}")

    def step(self, msg: str) -> None:
        if self.rich_available:
            self._console.print(f"[bold blue]STEP[/bold blue] {msg}")  # type: ignore[union-attr]
        else:
            print(f"\033[0;34m[STEP]\033[0m {msg}")

    def dim(self, msg: str) -> None:
        """Mensagem secundária/esmaecida (progresso fino, detalhes não-críticos)."""
        if self.rich_available:
            self._console.print(f"[dim]{msg}[/dim]")  # type: ignore[union-attr]
        else:
            print(f"\033[2m{msg}\033[0m")

    def success(self, msg: str) -> None:
        if self.rich_available:
            self._console.print(f"[bold green]✓[/bold green] {msg}")  # type: ignore[union-attr]
        else:
            print(f"\033[92m✓ {msg}\033[0m")

    def header(self, text: str) -> None:
        """Secção destacada com Panel Rich ou ANSI."""
        if self.rich_available:
            self._console.print()  # type: ignore[union-attr]
            self._console.print(  # type: ignore[union-attr]
                Panel(
                    f"[bold cyan]{text}[/bold cyan]",
                    border_style="cyan",
                    expand=False,
                )
            )
        else:
            print(f"\n\033[1m\033[96m{text}\033[0m")
            print("=" * len(text))

    def panel(self, content: str, *, title: str = "", border: str = "green") -> None:
        """Panel Rich com fallback para caixa ANSI."""
        if self.rich_available:
            self._console.print(  # type: ignore[union-attr]
                Panel(content, title=title or None, border_style=border)
            )
        else:
            if title:
                print(f"\n{'=' * 42}")
                print(f"  {title}")
                print(f"{'=' * 42}")
            print(content)

    def table(self, rows: list[tuple[str, str]], *, title: str = "") -> None:
        """Tabela simples (chave, valor) com Rich ou texto plano."""
        if self.rich_available:
            t = Table(show_header=False, box=box.SIMPLE, title=title or None)
            for k, v in rows:
                t.add_row(k, v)
            self._console.print(Panel(t, border_style="cyan"))  # type: ignore[union-attr]
        else:
            if title:
                print(f"\n{title}")
            for k, v in rows:
                print(f"  {k}: {v}")
