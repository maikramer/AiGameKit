"""Worker JSONL do vramd — implementação local do protocolo de subprocesso.

O venv do Hi3D é **Python 3.10** (exigência do sparc3d) e o
``aigamekit_shared`` declara ``>=3.13``, por isso esta tool não pode importar
``aigamekit_shared.worker_serve``. O contrato é o mesmo e está documentado em
``Shared/src/aigamekit_shared/worker_protocol.py``; os testes
(``tests/test_ums_protocol.py``) comparam as constantes com a Shared no venv
3.13 para detetar deriva.

Uma linha JSON por mensagem: comandos em stdin, eventos em stdout, logs em
stderr.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import sys
import threading
import traceback
from typing import Any, TextIO

CMD_LOAD = "load"
CMD_GENERATE = "generate"
CMD_UNLOAD = "unload"
CMD_ABORT = "abort"
CMD_PING = "ping"
CMD_SHUTDOWN = "shutdown"

EVENT_READY = "ready"
EVENT_PROGRESS = "progress"
EVENT_DONE = "done"
EVENT_UNLOADED = "unloaded"
EVENT_PONG = "pong"
EVENT_ERROR = "error"

ERR_CANCELLED = "CANCELLED"
ERR_GENERATE_FAILED = "GENERATE_FAILED"
ERR_LOAD_FAILED = "LOAD_FAILED"


class _JsonlStream:
    """Stream dedicado para eventos (stdout real fica em stderr após arranque)."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def emit(self, event: str, **fields: Any) -> None:
        payload = {"event": event, **fields}
        self._stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._stream.flush()


def _install_jsonl_stdout() -> _JsonlStream:
    """Duplica stdout para eventos e redireciona prints acidentais para stderr."""
    try:
        fd = os.dup(1)
    except OSError:
        return _JsonlStream(sys.stdout)
    jsonl = os.fdopen(fd, "w", buffering=1, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.dup2(2, 1)
    sys.stdout = sys.stderr
    return _JsonlStream(jsonl)


def _read_cmd() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return {}
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return {"cmd": "__bad_cmd__"}
    return data if isinstance(data, dict) else {"cmd": "__bad_cmd__"}


def _start_reader(cmd_q: queue.Queue[dict[str, Any] | None], abort: dict[str, bool]) -> None:
    """Thread que drena stdin — o loop principal bloqueia dentro do generate."""

    def _reader() -> None:
        while True:
            try:
                msg = _read_cmd()
            except Exception:
                cmd_q.put(None)
                return
            if msg is None:
                cmd_q.put(None)
                return
            if msg.get("cmd") == CMD_ABORT:
                abort["abort"] = True
                continue
            cmd_q.put(msg)

    threading.Thread(target=_reader, name="hi3d-ums-reader", daemon=True).start()


def run_worker(adapter: Any | None = None, *, backend_name: str = "hi3d") -> int:
    """Loop canónico: load → (generate)* → unload/shutdown.

    Args:
        adapter: instância com ``load/generate/unload``. Omitido usa o adapter
            do package (``hi3d.worker_serve_adapter.Adapter``).
        backend_name: nome lógico do backend (vai no evento ``ready``).

    Returns:
        Código de saída do processo (0 normal, 1 erro fatal de load).
    """
    if adapter is None:
        from .worker_serve_adapter import Adapter

        adapter = Adapter()

    sink = _install_jsonl_stdout()
    abort = {"abort": False}
    cmd_q: queue.Queue[dict[str, Any] | None] = queue.Queue()
    _start_reader(cmd_q, abort)

    model: Any = None
    loaded = False

    while True:
        cmd = cmd_q.get()
        if cmd is None:
            break
        name = cmd.get("cmd")
        if name == CMD_PING:
            sink.emit(EVENT_PONG)
            continue
        if name == CMD_LOAD:
            try:
                model = adapter.load(**dict(cmd.get("kwargs") or {}))
                loaded = True
                sink.emit(
                    EVENT_READY,
                    backend=backend_name,
                    vram_mib=int(getattr(adapter, "vram_mib", lambda: 0)()),
                )
            except Exception as exc:
                sink.emit(
                    EVENT_ERROR,
                    error=f"{type(exc).__name__}: {exc}",
                    error_code=ERR_LOAD_FAILED,
                )
                traceback.print_exc(file=sys.stderr)
                return 1
            continue
        if name == CMD_GENERATE:
            if not loaded:
                sink.emit(
                    EVENT_ERROR,
                    error="worker sem modelo carregado (envie load primeiro)",
                    error_code=ERR_GENERATE_FAILED,
                )
                continue
            abort["abort"] = False
            request = dict(cmd.get("request") or {})

            def _progress(pct: float | None, msg: str | None) -> None:
                sink.emit(EVENT_PROGRESS, pct=pct, msg=msg)

            request["_progress"] = _progress
            request["_abort"] = lambda: abort["abort"]
            try:
                result = adapter.generate(model, request)
            except Exception as exc:
                code = ERR_CANCELLED if abort["abort"] else ERR_GENERATE_FAILED
                sink.emit(EVENT_ERROR, error=f"{type(exc).__name__}: {exc}", error_code=code)
                traceback.print_exc(file=sys.stderr)
                continue
            sink.emit(EVENT_DONE, result=result)
            continue
        if name == CMD_UNLOAD:
            try:
                if model is not None:
                    adapter.unload(model)
            finally:
                model = None
                loaded = False
                sink.emit(EVENT_UNLOADED)
            continue
        if name == CMD_SHUTDOWN:
            break
        sink.emit(EVENT_ERROR, error=f"unknown cmd: {name!r}", error_code=ERR_GENERATE_FAILED)

    if model is not None:
        with contextlib.suppress(Exception):  # shutdown não falha por unload
            adapter.unload(model)
    return 0
