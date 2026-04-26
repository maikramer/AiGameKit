"""Execução de ferramentas via subprocess — resolve binários e executa comandos."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    """Resultado de um comando subprocess."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def merge_subprocess_output(
    r: RunResult,
    *,
    max_chars: int | None = None,
) -> str:
    """Junta stderr e stdout para diagnóstico (tracebacks podem ir só para stdout)."""
    err = (r.stderr or "").strip()
    out = (r.stdout or "").strip()
    text = f"{err}\n\n--- stdout ---\n{out}" if err and out else err or out
    if max_chars is not None and len(text) > max_chars:
        return "... (truncado no início)\n" + text[-max_chars:]
    return text


def resolve_binary(env_name: str, default_name: str) -> str:
    """Resolve executável: variável de ambiente → PATH → FileNotFoundError.

    Args:
        env_name: Nome da variável de ambiente (ex: ``TEXT2D_BIN``).
        default_name: Nome do comando no PATH (ex: ``text2d``).

    Returns:
        Caminho absoluto ou nome do executável encontrado.

    Raises:
        FileNotFoundError: Binário não encontrado.
    """
    override = os.environ.get(env_name, "").strip()
    if override:
        return override
    found = shutil.which(default_name)
    if not found:
        raise FileNotFoundError(
            f"Comando não encontrado: {default_name!r}. Instala o pacote ou define {env_name} com o caminho absoluto."
        )
    return found


def run_cmd(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    capture: bool = True,
    timeout: float | None = None,
    extra_env: dict[str, str] | None = None,
) -> RunResult:
    """Executa um comando e devolve ``RunResult``.

    Args:
        argv: Comando e argumentos.
        cwd: Directório de trabalho.
        capture: Capturar stdout/stderr.
        timeout: Timeout em segundos.
        extra_env: Variáveis extra a juntar ao ambiente.
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(
        list(argv),
        cwd=cwd,
        capture_output=capture,
        text=True,
        timeout=timeout,
        env=env,
    )
    return RunResult(
        returncode=r.returncode,
        stdout=r.stdout or "",
        stderr=r.stderr or "",
    )


def run_cmd_streaming(
    argv: Sequence[str],
    *,
    on_stdout_line: Callable[[str], None] | None = None,
    on_stderr_line: Callable[[str], None] | None = None,
    cwd: Path | None = None,
    timeout: float | None = None,
    extra_env: dict[str, str] | None = None,
) -> RunResult:
    """Run a subprocess, streaming stdout/stderr line-by-line to callbacks.

    Unlike :func:`run_cmd` (which blocks until completion and returns all output),
    this yields each stdout line as it arrives via ``on_stdout_line``.
    Stderr lines are also streamed if ``on_stderr_line`` is provided.

    Args:
        argv: Command and arguments.
        on_stdout_line: Called for each stdout line (including newline).
        on_stderr_line: Called for each stderr line (including newline).
        cwd: Working directory.
        timeout: Timeout in seconds (applied to the full run, not per-line).
        extra_env: Extra environment variables merged into ``os.environ``.

    Returns:
        :class:`RunResult` with full stdout/stderr accumulated during the run.
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(
        list(argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    import threading

    def _read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)
            if on_stderr_line:
                on_stderr_line(line)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    assert proc.stdout is not None
    for line in proc.stdout:
        stdout_lines.append(line)
        if on_stdout_line:
            on_stdout_line(line)

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    stderr_thread.join(timeout=5)

    return RunResult(
        returncode=proc.returncode,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
    )
