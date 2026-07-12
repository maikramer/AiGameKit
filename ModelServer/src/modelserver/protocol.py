"""Protocolo do Unified Model Server.

JSON sobre Unix domain socket (1 linha de pedido, 1 linha de resposta por ligação).

O UMS escuta num único socket canónico (``~/.cache/gamedev/model-server.sock``)
e roteia pedidos para backends (ferramentas GPU) carregados sob procura.

Comandos suportados:

  Request:
    {"cmd": "generate", "backend": "text2icon", ...kwargs}
        Gera; carrega o backend se preciso (evicta LRU se VRAM apertada).
        "backend" é opcional: se omitido, usa-se "tool" ou o único backend carregado.
    {"cmd": "release"}
        Evicta TODOS os backends carregados (release global).
    {"cmd": "release", "backend": "X"}
        Evicta só o backend X.
    {"cmd": "status"}
        Estado do UMS + lista de backends carregados (vram_mib de cada um).
    {"cmd": "shutdown"}
        Graceful: descarrega tudo e encerra.
    {"cmd": "list-backends"}
        Lista o registry (name, vram_mib, priority, loaded?).
    {"cmd": "preload", "backend": "X"}
        Pré-carrega o backend X (carrega + evicta LRU se VRAM não chegar).
    {"cmd": "ensure-vram", "needed_mib": N}
        Evicta peso+LRU até ter N MiB livres; responde ok/timeout.

  Response (sempre 1 linha JSON):
    {"status": "ok", ...}
    {"status": "error", "error": "..."}
    {"status": "status", ...}   (resposta a "status")
"""

from __future__ import annotations

from pathlib import Path

# Socket canónico do UMS (mesmo diretório dos per-tool legacy servers).
SOCKET_FILENAME = "model-server.sock"
DEFAULT_SOCKET_PATH = Path.home() / ".cache" / "gamedev" / SOCKET_FILENAME

# Comandos do protocolo.
CMD_GENERATE = "generate"
CMD_RELEASE = "release"
CMD_STATUS = "status"
CMD_SHUTDOWN = "shutdown"
CMD_LIST_BACKENDS = "list-backends"
CMD_PRELOAD = "preload"
CMD_ENSURE_VRAM = "ensure-vram"

# Comandos válidos (para validação no servidor).
KNOWN_COMMANDS = frozenset(
    {CMD_GENERATE, CMD_RELEASE, CMD_STATUS, CMD_SHUTDOWN, CMD_LIST_BACKENDS, CMD_PRELOAD, CMD_ENSURE_VRAM}
)

# Valores de "status" nas respostas.
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_STATUS = "status"

# Default cmd quando ausente no request (retrocompat com per-tool: gerar).
DEFAULT_CMD = CMD_GENERATE

# Timeout default para pedidos de geração (segundos).
DEFAULT_GENERATE_TIMEOUT_SEC = 600.0

# Minutos de idle antes de self-shutdown do UMS.
DEFAULT_IDLE_TIMEOUT_MIN = 30
