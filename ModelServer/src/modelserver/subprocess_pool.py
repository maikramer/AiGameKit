"""SubprocessWorkerPool — gestão de workers subprocesso persistentes por backend.

Cada backend com ``tool:`` definido no ``backends.yaml`` corre num subprocesso
próprio, no venv da tool (ex.: ``Text3D/.venv/bin/python -m text3d serve --ums-worker``).
O worker é **persistente**: carrega o modelo no arranque (``load``) e mantém-se
vivo entre jobs (``generate``); evict = ``unload`` (descarrega pesos); idle
timeout = ``shutdown``.

Comunicação via stdin/stdout JSONL (ver :mod:`gamedev_shared.worker_protocol`):

- UMS → Worker (stdin): ``{"cmd":"load","kwargs":{...}}``, ``{"cmd":"generate",...}``,
  ``{"cmd":"unload"}``, ``{"cmd":"abort"}``, ``{"cmd":"shutdown"}``.
- Worker → UMS (stdout): ``{"event":"ready","vram_mib":...}``, ``{"event":"progress",...}``,
  ``{"event":"done","result":{...}}``, etc.
- stderr → ficheiro de log por backend (``~/.cache/gamedev/ums-worker-<backend>.log``).

Abort cooperativo (``{"cmd":"abort"}`` no stdin); SIGTERM como fallback após
``abort_timeout_sec`` sem ``done``. Worker morto inesperadamente → re-spawn +
requeue do job (ver :meth:`SubprocessWorkerPool.send_generate`).
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gamedev_shared.logging import Logger
from gamedev_shared.worker_protocol import (
    CMD_ABORT,
    CMD_GENERATE,
    CMD_LOAD,
    CMD_PING,
    CMD_SHUTDOWN,
    CMD_UNLOAD,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_PONG,
    EVENT_READY,
    EVENT_UNLOADED,
    decode,
)

_logger = Logger()

# Tempo máximo de esperar por um evento específico no stdout do worker.
DEFAULT_EVENT_TIMEOUT_SEC = 600.0  # 10 min — generate pode ser longo
DEFAULT_ABORT_TIMEOUT_SEC = 30.0  # abort cooperativo → SIGTERM se sem done
DEFAULT_LOAD_TIMEOUT_SEC = 300.0  # 5 min para carregar modelo
DEFAULT_PING_TIMEOUT_SEC = 5.0


SpawnFn = Callable[[list[str], Any, Any, Any], subprocess.Popen]
LogPathFn = Callable[[str], Path]


def _default_log_path(backend: str) -> Path:
    cache = Path(os.environ.get("GAMEDEV_CACHE_DIR") or (Path.home() / ".cache" / "gamedev"))
    return cache / f"ums-worker-{backend}.log"


def _default_spawn(
    cmd: list[str],
    stdin: Any,
    stdout: Any,
    stderr: Any,
) -> subprocess.Popen:
    """Spawn por omissão: sessão própria, stdout=PIPE, stderr=ficheiro, stdin=PIPE.

    A sessão própria isola o worker do Ctrl+C do terminal do UMS (o abort é
    cooperativo, via ``{"cmd":"abort"}``). Para o worker não sobreviver à morte
    do supervisor há duas redes: EOF no stdin e o watchdog de PPID em
    :func:`gamedev_shared.worker_serve.start_parent_watchdog`. Não se usa
    ``PR_SET_PDEATHSIG`` porque no Linux dispara com a morte da *thread* que
    criou o processo — e o spawn acontece nas threads do ``WorkerPool``.
    """
    return subprocess.Popen(
        cmd,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        text=True,
        bufsize=1,  # line-buffered
        start_new_session=True,
        env=os.environ.copy(),
    )


@dataclass
class _WorkerState:
    """Estado de um worker persistente (um por backend)."""

    backend: str
    proc: subprocess.Popen | None = None
    load_shape: dict[str, Any] = field(default_factory=dict)
    loaded: bool = False  # modelo carregado (após ``ready``)
    vram_mib: int | None = None  # último reportado pelo worker
    lock: threading.RLock = field(default_factory=threading.RLock)
    log_path: Path | None = None


class SubprocessWorkerError(Exception):
    """Erro de comunicação com worker subprocesso."""


class SubprocessWorkerPool:
    """Gere workers subprocesso persistentes para backends com ``tool:`` definido.

    Threading: cada chamada bloqueante (``load``/``generate``/``unload``/
    ``shutdown``) obtém o lock do backend; o UMS chama-as a partir da
    ``WorkerPool`` que já tem ``MAX_INFLIGHT`` (default 1).
    """

    def __init__(
        self,
        *,
        spawn_fn: SpawnFn = _default_spawn,
        log_path_fn: LogPathFn = _default_log_path,
        load_timeout_sec: float = DEFAULT_LOAD_TIMEOUT_SEC,
        event_timeout_sec: float = DEFAULT_EVENT_TIMEOUT_SEC,
        abort_timeout_sec: float = DEFAULT_ABORT_TIMEOUT_SEC,
        ping_timeout_sec: float = DEFAULT_PING_TIMEOUT_SEC,
        python_override: dict[str, str] | None = None,
    ) -> None:
        self._spawn_fn = spawn_fn
        self._log_path_fn = log_path_fn
        self._load_timeout = float(load_timeout_sec)
        self._event_timeout = float(event_timeout_sec)
        self._abort_timeout = float(abort_timeout_sec)
        self._ping_timeout = float(ping_timeout_sec)
        # Override do interpretador python por backend (testes / ambientes exóticos).
        self._python_override: dict[str, str] = dict(python_override or {})
        # Estado por backend (criado on-demand).
        self._workers: dict[str, _WorkerState] = {}
        self._pool_lock = threading.RLock()

    # ------------------------------------------------------------------
    # API pública — chamada pelo BackendManager
    # ------------------------------------------------------------------

    def load(
        self,
        backend: str,
        tool: str,
        kwargs: dict[str, Any],
        *,
        on_progress: Callable[[float | None, str | None], None] | None = None,
    ) -> dict[str, Any]:
        """Arranca o worker e carrega o modelo; retorna o evento ``ready``.

        Reutiliza worker já vivo com a mesma ``load_shape``; recarrega (sem re-spawn)
        se o worker está vivo mas descarregado; re-spawn se o worker morreu.
        """
        with self._pool_lock:
            state = self._workers.get(backend)
            if state is None:
                state = _WorkerState(backend=backend, log_path=self._log_path_fn(backend))
                self._workers[backend] = state

        with state.lock:
            # Worker vivo e carregado com mesma shape → noop.
            if (
                state.proc
                and state.proc.poll() is None
                and state.loaded
                and not _shape_mismatch(state.load_shape, kwargs)
            ):
                return {"ready": True, "vram_mib": state.vram_mib, "reused": True}

            # Spawn se necessário.
            if state.proc is None or state.proc.poll() is not None:
                self._spawn(backend, tool, state)

            # Enviar load.
            from gamedev_shared.worker_protocol import send_cmd

            send_cmd(state.proc.stdin, CMD_LOAD, kwargs=kwargs)
            event = self._wait_event(
                state,
                expected={EVENT_READY, EVENT_ERROR},
                timeout=self._load_timeout,
                on_progress=on_progress,
            )
            if event is None:
                raise SubprocessWorkerError(f"{backend}: EOF no load (worker morreu)")
            if event["event"] == EVENT_ERROR:
                raise SubprocessWorkerError(f"{backend}: load falhou — {event.get('error')}")
            state.loaded = True
            state.load_shape = dict(kwargs)
            state.vram_mib = event.get("vram_mib")
            return event

    def generate(
        self,
        backend: str,
        request: dict[str, Any],
        *,
        on_progress: Callable[[float | None, str | None], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Envia ``generate`` e bloqueia até ``done``.

        ``on_progress``/``should_abort`` são chamados a partir dos eventos do
        worker (``progress``) e do poll a ``should_abort`` (que envia
        ``{"cmd":"abort"}`` quando True).
        """
        with self._pool_lock:
            state = self._workers.get(backend)
        if state is None or state.proc is None or state.proc.poll() is not None:
            raise SubprocessWorkerError(f"{backend}: worker não está vivo — faz load primeiro")

        with state.lock:
            from gamedev_shared.worker_protocol import send_cmd

            # Strip hooks in-process antes de serializar para JSONL — o worker
            # recebe-os via request e reconstrói os seus próprios a partir do
            # estado interno (state["abort"] + emissor de progress).
            serializable_request = {k: v for k, v in request.items() if not k.startswith("_") and not callable(v)}
            send_cmd(state.proc.stdin, CMD_GENERATE, request=serializable_request)
            deadline = time.monotonic() + self._event_timeout
            abort_sent = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Timeout: abort + SIGTERM se persistir.
                    self._force_abort(state, backend)
                    raise SubprocessWorkerError(f"{backend}: timeout no generate")
                # Poll cooperativo: se o caller pediu abort, enviar ao worker.
                if not abort_sent and should_abort and should_abort():
                    send_cmd(state.proc.stdin, CMD_ABORT)
                    abort_sent = True
                event = self._read_event_with_timeout(state, timeout=min(remaining, 1.0))
                if event is None:
                    # Worker morreu mid-job.
                    state.loaded = False
                    raise SubprocessWorkerError(f"{backend}: worker fechou stdout mid-generate")
                ev = event["event"]
                if ev == "progress" and on_progress:
                    with contextlib.suppress(Exception):
                        on_progress(event.get("pct"), event.get("msg"))
                    continue
                if ev == "vram_budget":
                    state.vram_mib = event.get("vram_mib", state.vram_mib)
                    continue
                if ev == EVENT_DONE:
                    result = event.get("result", {})
                    return result
                if ev == EVENT_ERROR:
                    raise SubprocessWorkerError(
                        f"{backend}: generate erro — {event.get('error')} ({event.get('error_code')})"
                    )
                # Evento inesperado (pong, ready, unloaded): ignorar.

    def unload(self, backend: str) -> bool:
        """Manda o worker descarregar o modelo (worker persiste vivo)."""
        with self._pool_lock:
            state = self._workers.get(backend)
        if state is None or state.proc is None or state.proc.poll() is not None:
            return False
        with state.lock:
            from gamedev_shared.worker_protocol import send_cmd

            send_cmd(state.proc.stdin, CMD_UNLOAD)
            event = self._wait_event(state, expected={EVENT_UNLOADED, EVENT_ERROR}, timeout=60.0)
            state.loaded = False
            return event is not None and event.get("event") == EVENT_UNLOADED

    def shutdown(self, backend: str) -> bool:
        """Manda o worker terminar (gracioso)."""
        with self._pool_lock:
            state = self._workers.pop(backend, None)
        if state is None or state.proc is None:
            return False
        with state.lock:
            try:
                from gamedev_shared.worker_protocol import send_cmd

                send_cmd(state.proc.stdin, CMD_SHUTDOWN)
            except Exception:
                pass
            # Espera graciosa 5s; SIGTERM depois.
            try:
                state.proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    state.proc.terminate()
                try:
                    state.proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(Exception):
                        state.proc.kill()
            finally:
                state.proc = None
                state.loaded = False
            return True

    def shutdown_all(self) -> None:
        """Termina todos os workers (shutdown do UMS)."""
        with self._pool_lock:
            backends = list(self._workers)
        for b in backends:
            with contextlib.suppress(Exception):
                self.shutdown(b)

    def is_loaded(self, backend: str) -> bool:
        """True se o worker existe, está vivo E tem modelo carregado."""
        with self._pool_lock:
            state = self._workers.get(backend)
        if state is None or state.proc is None:
            return False
        return state.loaded and state.proc.poll() is None

    def is_alive(self, backend: str) -> bool:
        """True se o subprocesso está vivo (mesmo sem modelo carregado)."""
        with self._pool_lock:
            state = self._workers.get(backend)
        return state is not None and state.proc is not None and state.proc.poll() is None

    def vram_mib(self, backend: str) -> int | None:
        """Última VRAM reportada pelo worker (None se desconhecida)."""
        with self._pool_lock:
            state = self._workers.get(backend)
        return state.vram_mib if state else None

    def loaded_backends(self) -> set[str]:
        """Backends com modelo carregado (para o planner do BackendManager)."""
        with self._pool_lock:
            return {b for b, s in self._workers.items() if s.loaded and s.proc and s.proc.poll() is None}

    def ping(self, backend: str) -> bool:
        """Health check: envia ``ping`` e espera ``pong``."""
        with self._pool_lock:
            state = self._workers.get(backend)
        if state is None or state.proc is None or state.proc.poll() is not None:
            return False
        with state.lock:
            from gamedev_shared.worker_protocol import send_cmd

            send_cmd(state.proc.stdin, CMD_PING)
            event = self._wait_event(state, expected={EVENT_PONG, EVENT_ERROR}, timeout=self._ping_timeout)
            return event is not None and event.get("event") == EVENT_PONG

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _spawn(self, backend: str, tool: str, state: _WorkerState) -> None:
        """Arranca o subprocesso worker. Fecha o anterior se morto."""
        if state.proc is not None and state.proc.poll() is None:
            return
        python = self._python_override.get(tool) or _resolve_tool_python(tool)
        if python is None:
            raise SubprocessWorkerError(f"{backend}: venv da tool {tool!r} não encontrado — corre ./install.sh {tool}")
        cmd = [python, "-m", tool, "serve", "--ums-worker"]
        # Log stderr para ficheiro (captura imports / warnings torch).
        state.log_path = state.log_path or self._log_path_fn(backend)
        state.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(state.log_path, "ab")  # noqa: SIM115 — fechado no shutdown
        _logger.info(f"[UMS] spawn worker {backend}: {' '.join(cmd)} (log: {state.log_path})")
        # ``spawn_fn`` recebe kwargs stdin/stdout/stderr explícitos — o fake
        # em testes ignora-os (já tem StringIO próprios); o default passa-os
        # ao subprocess.Popen como PIPE/log_fh.
        state.proc = self._spawn_fn(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log_fh,
        )
        state.loaded = False

    def _read_event_with_timeout(self, state: _WorkerState, *, timeout: float) -> dict[str, Any] | None:
        """Lê 1 linha do stdout do worker com timeout aproximado (poll 0.2s)."""
        if state.proc is None or state.proc.stdout is None:
            return None
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if state.proc.poll() is not None:
                return None
            line = state.proc.stdout.readline()
            if line:
                try:
                    return decode(line)
                except ValueError as e:
                    _logger.warn(f"[UMS] worker {state.backend}: linha inválida: {e}")
                    continue
            time.sleep(0.05)
        return None

    def _wait_event(
        self,
        state: _WorkerState,
        *,
        expected: set[str],
        timeout: float,
        on_progress: Callable[[float | None, str | None], None] | None = None,
    ) -> dict[str, Any] | None:
        """Lê eventos até um dos ``expected`` (ignorando progress/vram_budget)."""
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            event = self._read_event_with_timeout(state, timeout=min(remaining, 1.0))
            if event is None:
                return None
            ev = event["event"]
            if ev == "progress" and on_progress:
                with contextlib.suppress(Exception):
                    on_progress(event.get("pct"), event.get("msg"))
                continue
            if ev == "vram_budget":
                state.vram_mib = event.get("vram_mib", state.vram_mib)
                continue
            if ev in expected:
                return event
            # Evento inesperado: logar e continuar.
            _logger.info(f"[UMS] worker {state.backend}: evento inesperado {ev} (à espera de {expected})")
        return None

    def _force_abort(self, state: _WorkerState, backend: str) -> None:
        """Abort cooperativo já falhou: SIGTERM e re-spawn limpo."""
        _logger.warn(f"[UMS] worker {backend}: abort forçado (SIGTERM)")
        if state.proc and state.proc.poll() is None:
            with contextlib.suppress(Exception):
                state.proc.terminate()
            try:
                state.proc.wait(timeout=self._abort_timeout)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    state.proc.kill()
        state.loaded = False
        state.proc = None


# ---------------------------------------------------------------------------
# Helpers de resolução
# ---------------------------------------------------------------------------


def _resolve_tool_python(tool: str) -> str | None:
    """Descobre o ``python`` do venv da tool no monorepo."""
    from gamedev_shared.env import discover_monorepo_tool_python

    return discover_monorepo_tool_python(tool)


def _shape_mismatch(stored: dict[str, Any], new: dict[str, Any]) -> bool:
    """True se kwargs relevantes mudaram (ex.: max_num_view, sdnq_preset)."""
    relevant = {"max_num_view", "view_resolution", "sdnq_preset", "memory_efficient", "gpu_ids", "octree_resolution"}
    return any(stored.get(k) != new.get(k) for k in relevant)
