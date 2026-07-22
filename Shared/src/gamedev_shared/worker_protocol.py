"""Protocolo JSONL partilhado entre o UMS (supervisor) e os workers subprocesso.

O UMS escreve **comandos** no stdin do worker (uma linha JSON por comando); o
worker escreve **eventos** no stdout (uma linha JSON por evento). Logs vão para
stderr (capturados pelo UMS para um ficheiro por backend).

Este módulo é o contrato canónico usado por:
- ``ModelServer/src/modelserver/subprocess_pool.py`` (lado UMS)
- ``Shared/src/gamedev_shared/worker_serve.py`` (lado worker, em cada tool)

Mensagens:

Comandos (UMS → Worker, stdin)::

    {"cmd": "load", "kwargs": {...}}         # carrega modelo (worker persistente)
    {"cmd": "generate", "request": {...}}    # executa um job
    {"cmd": "unload"}                        # descarrega pesos (worker fica idle)
    {"cmd": "abort"}                         # cancela o generate em curso (cooperativo)
    {"cmd": "ping"}                          # health check (worker responde pong)
    {"cmd": "shutdown"}                      # termina o worker graciosamente

Eventos (Worker → UMS, stdout)::

    {"event": "ready", "vram_mib": 1300}             # após load (modelo carregado)
    {"event": "progress", "pct": 0.25, "msg": "..."}  # progresso do generate
    {"event": "vram_budget", {...}}                   # budget pós refresh_runtime_budget
    {"event": "done", "result": {...}}                # generate terminou (result = dict UMS)
    {"event": "unloaded"}                             # unload completo
    {"event": "pong"}                                 # resposta a ping
    {"event": "error", "error": "...", "error_code": "..."}  # erro (worker pode continuar)

Convenções:
- Uma linha JSON por mensagem (NDJSON); newline à direita obrigatório.
- ``cmd`` e ``event`` são strings curtas canónicas; o worker ignora comandos
  desconhecidos com ``{"event":"error","error":"unknown cmd"}``.
- ``error_code`` alinhado com ``modelserver.protocol`` (``ERR_VRAM_INSUFFICIENT``,
  ``ERR_CANCELLED``, ``ERR_GENERATE_FAILED``, ``ERR_BACKEND_VENV_MISSING``).
- O UMS nunca escreve no stdout do worker (stdout é unidireccional worker→UMS).
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO, TypedDict

# ---------------------------------------------------------------------------
# Comandos (UMS → Worker)
# ---------------------------------------------------------------------------

CMD_LOAD = "load"
CMD_GENERATE = "generate"
CMD_UNLOAD = "unload"
CMD_ABORT = "abort"
CMD_PING = "ping"
CMD_SHUTDOWN = "shutdown"

ALL_CMDS = frozenset({CMD_LOAD, CMD_GENERATE, CMD_UNLOAD, CMD_ABORT, CMD_PING, CMD_SHUTDOWN})


class LoadCmd(TypedDict, total=False):
    cmd: str  # "load"
    kwargs: dict[str, Any]


class GenerateCmd(TypedDict, total=False):
    cmd: str  # "generate"
    request: dict[str, Any]


# ---------------------------------------------------------------------------
# Eventos (Worker → UMS)
# ---------------------------------------------------------------------------

EVENT_READY = "ready"
EVENT_PROGRESS = "progress"
EVENT_VRAM_BUDGET = "vram_budget"
EVENT_DONE = "done"
EVENT_UNLOADED = "unloaded"
EVENT_PONG = "pong"
EVENT_ERROR = "error"


# ---------------------------------------------------------------------------
# Erros canónicos (alinhados com modelserver.protocol; duplicados aqui para o
# worker não depender do package modelserver)
# ---------------------------------------------------------------------------

ERR_VRAM_INSUFFICIENT = "VRAM_INSUFFICIENT"
ERR_CANCELLED = "CANCELLED"
ERR_GENERATE_FAILED = "GENERATE_FAILED"
ERR_BACKEND_VENV_MISSING = "BACKEND_VENV_MISSING"
ERR_LOAD_FAILED = "LOAD_FAILED"


# ---------------------------------------------------------------------------
# Serialização — uma linha JSON por mensagem
# ---------------------------------------------------------------------------


def encode(obj: dict[str, Any]) -> bytes:
    """Serializa uma mensagem para uma linha JSON + newline (utf-8)."""
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def decode(line: str | bytes) -> dict[str, Any]:
    """Desserializa uma linha JSON. Levanta ``ValueError`` se não for JSON válido."""
    if isinstance(line, bytes):
        line = line.decode("utf-8")
    line = line.strip()
    if not line:
        raise ValueError("linha vazia")
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError(f"mensagem não é um objecto JSON: {type(data).__name__}")
    return data


# ---------------------------------------------------------------------------
# Helpers para o lado UMS (escrever cmd, ler evento)
# ---------------------------------------------------------------------------


def send_cmd(stream: TextIO, cmd: str, **payload: Any) -> None:
    """Escreve um comando no stream (stdin do worker).

    Args:
        stream: stream writável (text mode) — tipicamente ``proc.stdin``.
        cmd: um de :data:`ALL_CMDS`.
        **payload: campos extra (ex.: ``kwargs={...}``, ``request={...}``).
    """
    msg: dict[str, Any] = {"cmd": cmd, **payload}
    stream.write(json.dumps(msg, ensure_ascii=False) + "\n")
    stream.flush()


def read_event(stream: TextIO) -> dict[str, Any] | None:
    """Lê uma linha do stream (stdout do worker) e desserializa.

    Returns:
        Dict do evento, ou ``None`` se EOF (worker fechou o stdout).
    """
    line = stream.readline()
    if not line:
        return None
    return decode(line)


# ---------------------------------------------------------------------------
# Helpers para o lado Worker (ler cmd, emitir evento) — usados por worker_serve
# ---------------------------------------------------------------------------


def emit_event(event: str, **fields: Any) -> None:
    """Emitir um evento no stdout (uma linha JSON + flush obrigatório).

    Usado pelo worker. ``out`` fixo em ``sys.stdout`` — o UMS liga o stdout do
    subprocesso a um pipe e lê linha-a-linha.
    """
    msg = {"event": event, **fields}
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read_cmd(stream: TextIO | None = None) -> dict[str, Any] | None:
    """Lê um comando do stdin (uma linha JSON).

    Returns:
        Dict do comando, ou ``None`` se EOF (UMS fechou stdin = shutdown).
    """
    src = stream or sys.stdin
    line = src.readline()
    if not line:
        return None
    return decode(line)
