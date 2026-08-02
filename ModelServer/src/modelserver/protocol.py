"""Protocolo do Unified Model Server.

JSON sobre Unix domain socket. Comandos curtos: 1 linha request → 1 linha response.
``generate`` / ``wait`` com ``stream: true``: várias linhas NDJSON (eventos + resultado).

O UMS escuta num único socket canónico (``~/.cache/aigamekit/model-server.sock``)
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
    {"cmd": "respawn", "backend": "X"} / {"cmd": "respawn"}
        Reinicia SÓ o worker subprocesso de um backend (código novo da tool,
        sem reiniciar o supervisor UMS). Sem backend: todos os backends.
        Com ``lazy=true`` (default): mata o worker vivo mas NÃO recarrega — o
        próximo generate arranca-o já com o código atualizado.
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
DEFAULT_SOCKET_PATH = Path.home() / ".cache" / "aigamekit" / SOCKET_FILENAME

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
CMD_RESPAWN = "respawn"
CMD_REAP = "reap"
CMD_ZERO = "zero"

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
        CMD_RESPAWN,
        CMD_REAP,
        CMD_ZERO,
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
ERR_WORKER_DEAD = "WORKER_DEAD"
ERR_CANCELLED = "CANCELLED"
ERR_TIMEOUT = "TIMEOUT"
ERR_JOB_UNKNOWN = "JOB_UNKNOWN"
ERR_INVALID_REQUEST = "INVALID_REQUEST"
ERR_PRELOAD_FAILED = "PRELOAD_FAILED"
ERR_VRAM_INSUFFICIENT = "VRAM_INSUFFICIENT"
ERR_RESPAWN_FAILED = "RESPAWN_FAILED"
ERR_RESPAWN_BUSY = "RESPAWN_BUSY"
ERR_ZERO_BUSY = "ZERO_BUSY"
ERR_ALREADY_RUNNING = "ALREADY_RUNNING"
ERR_SHAPE_BUSY = "SHAPE_BUSY"

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
MAX_AFFINITY_CUTS = _env_int("AIGAMEKIT_UMS_MAX_AFFINITY_CUTS", 3)
MAX_QUEUE_DEPTH = _env_int("AIGAMEKIT_UMS_MAX_QUEUE_DEPTH", 32)
MAX_INFLIGHT = _env_int("AIGAMEKIT_UMS_MAX_INFLIGHT", 1)
# 0 = desactivado. Se >0, job queued há mais de N segundos força pick (anti-starve).
STARVATION_TIMEOUT_SEC = float(_env_int("AIGAMEKIT_UMS_STARVATION_TIMEOUT_SEC", 0))

# VRAM transitória (processo externo / fragmentação CUDA): requeue em vez de
# falhar o batch inteiro. Pico > VRAM total da GPU → sem retry (impossível).
MAX_VRAM_RETRIES = _env_int("AIGAMEKIT_UMS_MAX_VRAM_RETRIES", 8)
# Worker subprocesso morreu entre load e generate (IdleEvictor race, OOM kill,
# crash) — requeue curto em vez de falhar o asset no batch (ex. scorpion_nest).
MAX_WORKER_DEAD_RETRIES = _env_int("AIGAMEKIT_UMS_MAX_WORKER_DEAD_RETRIES", 2)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


VRAM_RETRY_BASE_SEC = _env_float("AIGAMEKIT_UMS_VRAM_RETRY_BASE_SEC", 2.0)
VRAM_RETRY_MAX_SEC = _env_float("AIGAMEKIT_UMS_VRAM_RETRY_MAX_SEC", 30.0)
# Retries consecutivos SEM progresso (VRAM livre plana ±slack e nada evictável)
# antes de falhar rápido — evita o loop histórico de 8x30s sem saída possível.
VRAM_FLAT_RETRY_MAX = _env_int("AIGAMEKIT_UMS_VRAM_FLAT_RETRY_MAX", 2)
# Slack (MiB) para considerar a VRAM livre «plana» entre retries.
VRAM_FLAT_SLACK_MIB = _env_int("AIGAMEKIT_UMS_VRAM_FLAT_SLACK_MIB", 32)
# Espera curta dentro de ensure_loaded antes de recusar (evict+clear já feitos).
VRAM_ADMIT_WAIT_SEC = _env_float("AIGAMEKIT_UMS_VRAM_ADMIT_WAIT_SEC", 8.0)
VRAM_ADMIT_POLL_SEC = _env_float("AIGAMEKIT_UMS_VRAM_ADMIT_POLL_SEC", 0.5)
# Limiar para o aviso de residual no PID do UMS com ``loaded=[]`` (contexto
# CUDA / cache que sobrevive ao scrub). O residual é baseline do worker: o
# admit in-process credita o cache reutilizável (reserved-allocated) — sem
# acção destrutiva sobre o processo.
DEAD_VRAM_MIB = _env_int("AIGAMEKIT_UMS_DEAD_VRAM_MIB", 256)

# Descarregar pesos de um backend após este tempo sem uso. 120s equilibra
# "VRAM livre para o resto do sistema" com o custo do cold start (text3d/paint3d
# levam dezenas de segundos); num batch contínuo o last_used renova-se e o
# modelo fica quente.
IDLE_EVICT_SEC = _env_float("AIGAMEKIT_UMS_IDLE_EVICT_SEC", 120.0)
IDLE_EVICT_CHECK_SEC = _env_float("AIGAMEKIT_UMS_IDLE_EVICT_CHECK_SEC", 15.0)
# Terminar o subprocesso worker após este tempo sem uso. ``unload`` só liberta
# pesos — o contexto CUDA do processo (~0.3-1 GiB) só sai com o processo.
WORKER_IDLE_SHUTDOWN_SEC = _env_float("AIGAMEKIT_UMS_WORKER_IDLE_SHUTDOWN_SEC", 300.0)
# Health-check (ping/pong) aos workers vivos; sem pong ⇒ mata e marca para respawn.
WORKER_HEALTH_CHECK_SEC = _env_float("AIGAMEKIT_UMS_WORKER_HEALTH_CHECK_SEC", 60.0)
# Reap de supervisores/workers órfãos no arranque (0 desliga).
REAP_ON_START = _env_int("AIGAMEKIT_UMS_REAP_ON_START", 1)

# Default cmd quando ausente no request (retrocompat com per-tool: gerar).
DEFAULT_CMD = CMD_GENERATE

# Timeout default para pedidos de geração (segundos).
DEFAULT_GENERATE_TIMEOUT_SEC = 600.0

# Tamanho máximo de um request (1 linha JSON) — proteção contra reads sem newline.
MAX_REQUEST_BYTES = 1 * 1024 * 1024  # 1 MiB

# Minutos de idle antes de self-shutdown do UMS (0 = desativado). 30 min: os
# clientes fazem auto-start quando precisam, logo um supervisor parado só está
# a arriscar ficar zombie. (Contexto CUDA do supervisor: não existe em modo
# subprocesso — `clear_cuda_memory` salta torch sem `is_initialized()`; para
# VRAM presa em workers idle vivos, `ums zero` liberta sem parar o supervisor.)
DEFAULT_IDLE_TIMEOUT_MIN = _env_int("AIGAMEKIT_UMS_IDLE_TIMEOUT_MIN", 30)


def normalize_priority(value: object | None) -> str:
    """Normaliza priority do request; default ``interactive``."""
    if value is None:
        return DEFAULT_PRIORITY
    text = str(value).strip().lower()
    if text in PRIORITY_RANK:
        return text
    return DEFAULT_PRIORITY
