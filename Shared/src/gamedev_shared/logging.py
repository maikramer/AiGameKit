"""Logger unificado Rich/ANSI + ficheiro para o monorepo GameDev.

Por omissão grava em ``~/.cache/gamedev/logs/<tool>-YYYY-MM-DD.log``
(ou ``$GAMEDEV_LOG_DIR`` / ``$GAMEDEV_LOG_FILE``). Desligar com
``GAMEDEV_FILE_LOG=0`` ou ``GAMEDEV_NO_FILE_LOG=1``.

Em pytest o ficheiro fica desligado salvo ``GAMEDEV_FILE_LOG=1``.
"""

from __future__ import annotations

import atexit
import contextlib
import logging as _stdlib_logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

# ---------------------------------------------------------------------------
# Rich / ANSI console
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# File logging (process-wide singleton)
# ---------------------------------------------------------------------------

_LEVEL_RANK: dict[str, int] = {
    "DEBUG": 10,
    "DIM": 10,
    "INFO": 20,
    "STEP": 20,
    "SUCCESS": 20,
    "HEADER": 20,
    "WARN": 30,
    "WARNING": 30,
    "ERROR": 40,
}

_TOOL_ALIASES: dict[str, str] = {
    "gamedev-model-server": "ums",
    "ums": "ums",
    "gamedev-lab": "gamedev-lab",
    "gamedev-install": "gamedev-install",
    "__main__.py": "gamedev",
}

_lock = threading.RLock()
_file_fp: TextIO[str] | None = None
_file_path: Path | None = None
_tool_name: str | None = None
_min_rank: int = 20
_stdlib_bridged: bool = False
_atexit_registered: bool = False


def _cache_dir() -> Path:
    """Mesma precedência que ``quaternius_fetch.cache_dir`` (sem import circular)."""
    env = os.environ.get("GAMEDEV_CACHE_DIR", "").strip()
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "gamedev"
    return Path.home() / ".cache" / "gamedev"


def detect_tool_name() -> str:
    """Nome da tool para o ficheiro de log (env → argv → ``gamedev``)."""
    env = os.environ.get("GAMEDEV_LOG_TOOL", "").strip()
    if env:
        return env
    prog = Path(sys.argv[0]).name if sys.argv else "gamedev"
    return _TOOL_ALIASES.get(prog, prog or "gamedev")


def file_logging_enabled() -> bool:
    """True se logging para ficheiro está ativo."""
    explicit = os.environ.get("GAMEDEV_FILE_LOG", "").strip().lower()
    if explicit in ("0", "false", "no", "off"):
        return False
    no = os.environ.get("GAMEDEV_NO_FILE_LOG", "").strip().lower()
    if no in ("1", "true", "yes", "on"):
        return False
    # Pytest: off por omissão (evita poluir ~/.cache); on com GAMEDEV_FILE_LOG=1.
    under_pytest = "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    if under_pytest:
        return explicit in ("1", "true", "yes", "on")
    return True


def default_log_dir() -> Path:
    """Diretório de logs: ``GAMEDEV_LOG_DIR`` ou ``<cache>/logs``."""
    env = os.environ.get("GAMEDEV_LOG_DIR", "").strip()
    if env:
        return Path(env)
    return _cache_dir() / "logs"


def resolve_log_path(tool: str | None = None) -> Path:
    """Path do ficheiro de log para a tool (ou ``GAMEDEV_LOG_FILE``)."""
    override = os.environ.get("GAMEDEV_LOG_FILE", "").strip()
    if override:
        return Path(override)
    name = tool or detect_tool_name()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return default_log_dir() / f"{name}-{day}.log"


def current_log_path() -> Path | None:
    """Path do ficheiro aberto (None se ainda não configurado / desligado)."""
    return _file_path


def _parse_min_level() -> int:
    raw = os.environ.get("GAMEDEV_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    return _LEVEL_RANK.get(raw, 20)


def _close_file() -> None:
    global _file_fp, _file_path
    with _lock:
        if _file_fp is not None:
            with contextlib.suppress(OSError):
                _file_fp.flush()
                _file_fp.close()
        _file_fp = None


def _ensure_file(tool: str | None = None, *, force: bool = False) -> tuple[Path | None, bool]:
    """Abre (lazy) o ficheiro de log.

    Returns:
        ``(path, newly_opened)`` — path ``None`` se file logging desligado
        (salvo ``force=True``, usado quando ``Logger(file_logging=True)``).
    """
    global _file_fp, _file_path, _tool_name, _min_rank, _atexit_registered

    if not force and not file_logging_enabled():
        return None, False

    with _lock:
        wanted_tool = tool or _tool_name or detect_tool_name()
        wanted_path = resolve_log_path(wanted_tool)
        if _file_fp is not None and _file_path == wanted_path:
            return _file_path, False

        _close_file()
        _tool_name = wanted_tool
        _min_rank = _parse_min_level()
        wanted_path.parent.mkdir(parents=True, exist_ok=True)
        _file_fp = wanted_path.open("a", encoding="utf-8")
        _file_path = wanted_path
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        with contextlib.suppress(OSError):
            _file_fp.write(f"{ts} [INFO   ] === log start tool={wanted_tool} pid={os.getpid()} ===\n")
            _file_fp.flush()

        if not _atexit_registered:
            atexit.register(_close_file)
            _atexit_registered = True

        return _file_path, True


def _write_file(level: str, msg: str, *, tool: str | None = None, force: bool = False) -> None:
    """Escreve uma linha plain-text no ficheiro (thread-safe)."""
    rank = _LEVEL_RANK.get(level.upper(), 20)
    if rank < _parse_min_level():
        return
    path, _newly = _ensure_file(tool, force=force)
    if path is None or _file_fp is None:
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    line = f"{ts} [{level.upper():<7}] {msg}\n"
    with _lock:
        if _file_fp is None:
            return
        with contextlib.suppress(OSError):
            _file_fp.write(line)
            _file_fp.flush()


def configure_logging(
    tool: str | None = None,
    *,
    log_dir: Path | str | None = None,
    log_file: Path | str | None = None,
    level: str | None = None,
    bridge_stdlib: bool = True,
) -> Path | None:
    """Configura logging para ficheiro (idempotente).

    Args:
        tool: Nome da tool (ex: ``text2d``, ``ums``). Define o basename do log.
        log_dir: Override de ``GAMEDEV_LOG_DIR`` para este processo.
        log_file: Override de ``GAMEDEV_LOG_FILE`` (path exacto).
        level: Nível mínimo (``DEBUG``/``INFO``/``WARN``/``ERROR``).
        bridge_stdlib: Se True, encaminha ``logging.getLogger`` para o mesmo ficheiro.

    Returns:
        Path do ficheiro aberto, ou ``None`` se file logging desligado.
    """
    global _tool_name, _min_rank

    if tool:
        os.environ["GAMEDEV_LOG_TOOL"] = tool
        _tool_name = tool
    if log_dir is not None:
        os.environ["GAMEDEV_LOG_DIR"] = str(log_dir)
    if log_file is not None:
        os.environ["GAMEDEV_LOG_FILE"] = str(log_file)
    if level is not None:
        os.environ["GAMEDEV_LOG_LEVEL"] = level.upper()
        _min_rank = _parse_min_level()

    path, _newly = _ensure_file(tool)
    if bridge_stdlib and path is not None:
        bridge_stdlib_logging()
    return path


class _StdlibFileHandler(_stdlib_logging.Handler):
    """Handler stdlib que reutiliza o sink de ficheiro do GameDev."""

    def emit(self, record: _stdlib_logging.LogRecord) -> None:
        try:
            level = record.levelname
            if level == "WARNING":
                level = "WARN"
            msg = self.format(record)
            _write_file(level, msg)
        except Exception:
            self.handleError(record)


def bridge_stdlib_logging(*, level: int | None = None) -> None:
    """Liga o root logger stdlib ao ficheiro GameDev (uma vez por processo)."""
    global _stdlib_bridged
    with _lock:
        if _stdlib_bridged:
            return
        handler = _StdlibFileHandler()
        handler.setFormatter(_stdlib_logging.Formatter("%(name)s: %(message)s"))
        root = _stdlib_logging.getLogger()
        if level is not None:
            root.setLevel(level)
        elif root.level == _stdlib_logging.NOTSET:
            root.setLevel(_stdlib_logging.INFO)
        root.addHandler(handler)
        _stdlib_bridged = True


# ---------------------------------------------------------------------------
# Public Logger (console + file)
# ---------------------------------------------------------------------------


class Logger:
    """Saída com Rich quando disponível; fallback ANSI; espelho em ficheiro.

    Unifica o padrão duplicado em Text2D, Text3D e Materialize installers.
    Pode receber um ``Console`` Rich existente ou criar um internamente.
    """

    def __init__(
        self,
        console: Console | None = None,
        *,
        tool: str | None = None,
        file_logging: bool | None = None,
    ) -> None:
        if _RICH:
            self._console: Console | None = console or Console()
        else:
            self._console = None
        self._tool = tool
        self._file_logging = file_logging
        if tool:
            with contextlib.suppress(OSError):
                configure_logging(tool, bridge_stdlib=True)
        elif file_logging is not False and file_logging_enabled():
            # Lazy: abre no primeiro emit; bridge stdlib cedo se possível.
            with contextlib.suppress(OSError):
                bridge_stdlib_logging()

    @property
    def rich_available(self) -> bool:
        return _RICH and self._console is not None

    @property
    def console(self) -> Console | None:
        return self._console

    @property
    def log_path(self) -> Path | None:
        """Path do ficheiro de log actual (se activo)."""
        return current_log_path() or (resolve_log_path(self._tool) if self._file_enabled() else None)

    def _file_enabled(self) -> bool:
        flag = getattr(self, "_file_logging", None)
        if flag is False:
            return False
        if flag is True:
            return True
        return file_logging_enabled()

    def _emit_file(self, level: str, msg: str) -> None:
        if self._file_enabled():
            force = getattr(self, "_file_logging", None) is True
            _write_file(level, msg, tool=getattr(self, "_tool", None), force=force)

    def info(self, msg: str, *, console: bool = True) -> None:
        self._emit_file("INFO", msg)
        if not console:
            return
        if self.rich_available:
            self._console.print(f"[bold green]INFO[/bold green] {msg}")  # type: ignore[union-attr]
        else:
            print(f"\033[0;32m[INFO]\033[0m {msg}")

    def warn(self, msg: str, *, console: bool = True) -> None:
        self._emit_file("WARN", msg)
        if not console:
            return
        if self.rich_available:
            self._console.print(f"[bold yellow]WARN[/bold yellow] {msg}")  # type: ignore[union-attr]
        else:
            print(f"\033[1;33m[WARN]\033[0m {msg}")

    def error(self, msg: str, *, console: bool = True) -> None:
        self._emit_file("ERROR", msg)
        if not console:
            return
        if self.rich_available:
            self._console.print(f"[bold red]ERROR[/bold red] {msg}")  # type: ignore[union-attr]
        else:
            print(f"\033[0;31m[ERROR]\033[0m {msg}")

    def step(self, msg: str, *, console: bool = True) -> None:
        self._emit_file("STEP", msg)
        if not console:
            return
        if self.rich_available:
            self._console.print(f"[bold blue]STEP[/bold blue] {msg}")  # type: ignore[union-attr]
        else:
            print(f"\033[0;34m[STEP]\033[0m {msg}")

    def dim(self, msg: str, *, console: bool = True) -> None:
        """Mensagem secundária/esmaecida (progresso fino, detalhes não-críticos)."""
        self._emit_file("DIM", msg)
        if not console:
            return
        if self.rich_available:
            self._console.print(f"[dim]{msg}[/dim]")  # type: ignore[union-attr]
        else:
            print(f"\033[2m{msg}\033[0m")

    def success(self, msg: str, *, console: bool = True) -> None:
        self._emit_file("SUCCESS", msg)
        if not console:
            return
        if self.rich_available:
            self._console.print(f"[bold green]✓[/bold green] {msg}")  # type: ignore[union-attr]
        else:
            print(f"\033[92m✓ {msg}\033[0m")

    def header(self, text: str, *, console: bool = True) -> None:
        """Secção destacada com Panel Rich ou ANSI."""
        self._emit_file("HEADER", text)
        if not console:
            return
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

    def panel(self, content: str, *, title: str = "", border: str = "green", console: bool = True) -> None:
        """Panel Rich com fallback para caixa ANSI."""
        file_msg = f"{title}: {content}" if title else content
        self._emit_file("INFO", file_msg)
        if not console:
            return
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

    def table(self, rows: list[tuple[str, str]], *, title: str = "", console: bool = True) -> None:
        """Tabela simples (chave, valor) com Rich ou texto plano."""
        flat = "; ".join(f"{k}={v}" for k, v in rows)
        self._emit_file("INFO", f"{title}: {flat}" if title else flat)
        if not console:
            return
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


def reset_file_logging_for_tests() -> None:
    """Fecha o sink e limpa estado (só para testes)."""
    global _file_path, _tool_name, _min_rank
    _close_file()
    with _lock:
        _file_path = None
        _tool_name = None
        _min_rank = 20
