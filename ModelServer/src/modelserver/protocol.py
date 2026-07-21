"""Protocolo do Unified Model Server.

JSON sobre Unix domain socket. Comandos curtos: 1 linha request → 1 linha response.
``generate`` / ``wait`` com ``stream: true``: várias linhas NDJSON (eventos + resultado).

O UMS escuta num único socket canónico (``~/.cache/gamedev/model-server.sock``)
e roteia pedidos para backends via fila inteligente (afinidade VRAM + prioridades).

Comandos suportados:

  Request:
    {"cmd": "generate", "backend": "text2icon", ...kwargs}
        Enfileira + espera resultado (sync). Opcional: priority, stream.
    {"cmd": "submit", "backend": "...", ...}
        Enfileira; devolve job_id de imediato.
    {"cmd": "poll", "job_id": "..."}
        Estado actual do job.
    {"cmd": "wait", "job_id": "..."}
        Bloqueia até o job terminar (opcional stream).
    {"cmd": "cancel", "job_id": "..."}
        Cancela se queued; se running, best-effort.
    {"cmd": "queue"}
        Snapshot da fila (jobs queued/running).
    {"cmd": "release"} / {"cmd": "release", "backend": "X"}
    {"cmd": "status"} / {"cmd": "stats"} / {"cmd": "list-backends"}
    {"cmd": "preload", "backend": "X"}
    {"cmd": "ensure-vram", "needed_mib": N, "backend"?: "..."}
        Evicta até N MiB livres; com backend usa max(N, peak=pesos+activação+safety).
    {"cmd": "shutdown"}

  Response:
    {"status": "ok"|"error"|"status"|"queue_full", ...}
"""

from __future__ import annotations

import os
from pathlib import Path

# Socket canónico do UMS (mesmo diretório dos per-tool legacy servers).
SOCKET_FILENAME = "model-server.sock"
WAL_FILENAME = "ums-jobs.jsonl"
DEFAULT_SOCKET_PATH = Path.home() / ".cache" / "gamedev" / SOCKET_FILENAME

# Comandos do protocolo.
CMD_GENERATE = "generate"
CMD_SUBMIT = "submit"
CMD_POLL = "poll"
CMD_WAIT = "wait"
CMD_CANCEL = "cancel"
CMD_FLUSH = "flush"
CMD_QUEUE = "queue"
CMD_RELEASE = "release"
CMD_STATUS = "status"
CMD_SHUTDOWN = "shutdown"
CMD_STATS = "stats"
CMD_LIST_BACKENDS = "list-backends"
CMD_PRELOAD = "preload"
CMD_ENSURE_VRAM = "ensure-vram"

# Comandos válidos (para validação no servidor).
KNOWN_COMMANDS = frozenset(
    {
        CMD_GENERATE,
        CMD_SUBMIT,
        CMD_POLL,
        CMD_WAIT,
        CMD_CANCEL,
        CMD_FLUSH,
        CMD_QUEUE,
        CMD_RELEASE,
        CMD_STATUS,
        CMD_SHUTDOWN,
        CMD_LIST_BACKENDS,
        CMD_PRELOAD,
        CMD_ENSURE_VRAM,
        CMD_STATS,
    }
)

# Valores de "status" nas respostas.
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_STATUS = "status"
STATUS_QUEUE_FULL = "queue_full"

# Códigos de erro estáveis (campo ``error_code`` nas respostas) — úteis para debug/CI.
ERR_BACKEND_UNKNOWN = "BACKEND_UNKNOWN"
ERR_BACKEND_AMBIGUOUS = "BACKEND_AMBIGUOUS"
ERR_QUEUE_FULL = "QUEUE_FULL"
ERR_GENERATE_FAILED = "GENERATE_FAILED"
ERR_CANCELLED = "CANCELLED"
ERR_TIMEOUT = "TIMEOUT"
ERR_JOB_UNKNOWN = "JOB_UNKNOWN"
ERR_INVALID_REQUEST = "INVALID_REQUEST"
ERR_PRELOAD_FAILED = "PRELOAD_FAILED"
ERR_VRAM_INSUFFICIENT = "VRAM_INSUFFICIENT"

# Prioridades de pedido (menor rank = atende primeiro).
PRIORITY_INTERACTIVE = "interactive"
PRIORITY_BATCH = "batch"
PRIORITY_RANK: dict[str, int] = {
    PRIORITY_INTERACTIVE: 0,
    PRIORITY_BATCH: 1,
}
DEFAULT_PRIORITY = PRIORITY_INTERACTIVE

# Estados de job.
JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"

# Eventos NDJSON (stream).
EVENT_QUEUED = "queued"
EVENT_STARTED = "started"
EVENT_PROGRESS = "progress"
EVENT_DONE = "done"
EVENT_ERROR = "error"
EVENT_CANCELLED = "cancelled"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Fila / scheduler (env overrideáveis).
MAX_AFFINITY_CUTS = _env_int("GAMEDEV_UMS_MAX_AFFINITY_CUTS", 3)
MAX_QUEUE_DEPTH = _env_int("GAMEDEV_UMS_MAX_QUEUE_DEPTH", 32)
MAX_INFLIGHT = _env_int("GAMEDEV_UMS_MAX_INFLIGHT", 1)
# 0 = desactivado. Se >0, job queued há mais de N segundos força pick (anti-starve).
STARVATION_TIMEOUT_SEC = float(_env_int("GAMEDEV_UMS_STARVATION_TIMEOUT_SEC", 0))

# VRAM transitória (processo externo / fragmentação CUDA): requeue em vez de
# falhar o batch inteiro. Pico > VRAM total da GPU → sem retry (impossível).
MAX_VRAM_RETRIES = _env_int("GAMEDEV_UMS_MAX_VRAM_RETRIES", 8)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


VRAM_RETRY_BASE_SEC = _env_float("GAMEDEV_UMS_VRAM_RETRY_BASE_SEC", 2.0)
VRAM_RETRY_MAX_SEC = _env_float("GAMEDEV_UMS_VRAM_RETRY_MAX_SEC", 30.0)
# Retries consecutivos SEM progresso (VRAM livre plana ±slack e nada evictável)
# antes de falhar rápido — evita o loop histórico de 8x30s sem saída possível.
VRAM_FLAT_RETRY_MAX = _env_int("GAMEDEV_UMS_VRAM_FLAT_RETRY_MAX", 2)
# Slack (MiB) para considerar a VRAM livre «plana» entre retries.
VRAM_FLAT_SLACK_MIB = _env_int("GAMEDEV_UMS_VRAM_FLAT_SLACK_MIB", 32)
# Espera curta dentro de ensure_loaded antes de recusar (evict+clear já feitos).
VRAM_ADMIT_WAIT_SEC = _env_float("GAMEDEV_UMS_VRAM_ADMIT_WAIT_SEC", 8.0)
VRAM_ADMIT_POLL_SEC = _env_float("GAMEDEV_UMS_VRAM_ADMIT_POLL_SEC", 0.5)
# Limiar para o aviso de residual no PID do UMS com ``loaded=[]`` (contexto
# CUDA / cache que sobrevive ao scrub). O residual é baseline do worker: o
# admit in-process credita o cache reutilizável (reserved-allocated) — sem
# acção destrutiva sobre o processo.
DEAD_VRAM_MIB = _env_int("GAMEDEV_UMS_DEAD_VRAM_MIB", 256)

# Default cmd quando ausente no request (retrocompat com per-tool: gerar).
DEFAULT_CMD = CMD_GENERATE

# Timeout default para pedidos de geração (segundos).
DEFAULT_GENERATE_TIMEOUT_SEC = 600.0

# Tamanho máximo de um request (1 linha JSON) — proteção contra reads sem newline.
MAX_REQUEST_BYTES = 1 * 1024 * 1024  # 1 MiB

# Minutos de idle antes de self-shutdown do UMS (0 = desativado; o IdleEvictor
# trata de libertar VRAM de backends individuais sem matar o servidor).
DEFAULT_IDLE_TIMEOUT_MIN = 0


def normalize_priority(value: object | None) -> str:
    """Normaliza priority do request; default ``interactive``."""
    if value is None:
        return DEFAULT_PRIORITY
    text = str(value).strip().lower()
    if text in PRIORITY_RANK:
        return text
    return DEFAULT_PRIORITY
