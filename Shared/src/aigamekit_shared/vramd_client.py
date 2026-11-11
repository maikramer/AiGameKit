"""Model server genérico partilhado — mantém modelos AI carregados entre invocações.

Um server long-lived (Unix domain socket) que carrega um pipeline uma vez e serve
pedidos subsequentes sem cold start. Cada ferramenta regista o seu próprio loader
e tem o seu próprio socket (ex: ``text2d-server.sock``).

Protocolo de coordenação de VRAM:
  - ``request_release(socket)`` — pede ao server para descarregar o modelo mas
    continuar a correr (para outra ferramenta poder usar a GPU).
  - ``ensure_vram_available(needed_mib)`` — verifica se há VRAM livre; se não,
    pede a todos os servers ativos para fazer release e espera.
  - ``discover_server_pids()`` — lista PIDs de todos os servers ativos (para o
    ``kill_gpu_compute_processes_aggressive`` os proteger).

Protocolo JSON sobre Unix socket:
  Request (client → server, 1 linha JSON):
    {"cmd": "generate", ...kwargs}  → gera (delegado ao generator da tool)
    {"cmd": "release"}              → descarrega modelo (continua a correr)
    {"cmd": "status"}               → estado do server
    {"cmd": "shutdown"}             → encerra gracioso
  Response (server → client, JSONL):
    {"status": "ok"|"error"|"status", ...}
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import socket
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

from . import vramd_endpoint as endpoint
from .logging import Logger

_logger = Logger()

# Paths canónicos (``%LOCALAPPDATA%`` no Windows, ``~/.cache`` no POSIX).
DEFAULT_SERVER_DIR = endpoint.aigamekit_cache_dir()

# Endpoint canónico do vramd — supervisor único de VRAM (substitui o ModelServer).
# Quando o vramd está ativo, todas as ferramentas delegam nele; este endpoint é o
# ponto de entrada para geração (cmd "generate" com "backend") e coordenação
# de VRAM (cmd "ensure-vram"). Ver a tool ``Vramd/`` no monorepo (PyPI vramd).
VRAMD_SOCKET = endpoint.default_vramd_socket()

# Um socket por tool — evita misturar modelos diferentes (legacy per-tool servers).
_SOCKET_FOR_TOOL: dict[str, str] = {
    "text2d": "text2d-server.sock",
    "texture2d": "texture2d-server.sock",
}

DEFAULT_IDLE_TIMEOUT_MIN = 30

# Tectos do protocolo JSON local: uma linha/resposta maior é sinal de servidor
# corrompido (ou de um endpoint que não é o vramd), não de um job válido.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
# Pedido de um cliente per-tool (JSON de uma linha). Evita que uma ligação
# sem newline consuma RAM sem limite no accept thread.
MAX_REQUEST_BYTES = 4 * 1024 * 1024

# Serializa o auto-start: duas threads da mesma tool não podem arrancar dois
# supervisores (o segundo perde o bind e suja o log; no pior caso fica um
# processo a competir pela GPU).
_START_LOCK = threading.Lock()


def server_socket_path(tool: str) -> Path:
    """Path do socket para uma tool (ex: ``text2d`` → ``text2d-server.sock``)."""
    override = os.environ.get("VRAMD_CLIENT_SOCKET", "").strip()
    if override:
        return Path(override)
    name = _SOCKET_FOR_TOOL.get(tool, f"{tool}-server.sock")
    return DEFAULT_SERVER_DIR / name


def _pid_path(socket_path: Path) -> Path:
    """PID file path derivado do socket path."""
    return socket_path.with_suffix(".pid")


def _ensure_server_dir() -> None:
    DEFAULT_SERVER_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Liveness e discovery
# ---------------------------------------------------------------------------


def is_server_running(socket_path: Path | str | None = None) -> bool:
    """Verifica se um server está vivo via PID file + socket connect.

    Um descritor TCP é auto-suficiente (host/porta/token): não exige PID file.
    Só os endpoints Unix (per-tool legacy) dependem do PID, porque um socket
    órfão no filesystem não distingue um server vivo de um morto.
    """
    spath = Path(socket_path) if socket_path else server_socket_path("text2d")
    ppath = _pid_path(spath)

    if endpoint.read_endpoint(spath) is None:
        if not ppath.exists():
            return False

        try:
            pid = int(ppath.read_text().strip())
        except (ValueError, OSError):
            return False

        if not endpoint.pid_exists(pid):
            return False

    if not spath.exists():
        return False

    try:
        sock, _token = endpoint.connect(spath, timeout=2.0)
        sock.close()
        return True
    except OSError:
        return False


def get_server_pid(socket_path: Path | str | None = None) -> int | None:
    """Lê o PID de um server ativo. Retorna ``None`` se não estiver a correr."""
    spath = Path(socket_path) if socket_path else server_socket_path("text2d")
    ppath = _pid_path(spath)
    if not ppath.exists():
        return None
    try:
        pid = int(ppath.read_text().strip())
        return pid if endpoint.pid_exists(pid) else None
    except (ValueError, OSError, ProcessLookupError):
        return None


def discover_server_pids() -> set[int]:
    """Descobre PIDs de todos os servers ativos em ``DEFAULT_SERVER_DIR``.

    Usado por ``kill_gpu_compute_processes_aggressive(protect_model_servers=True)``
    para evitar matar processes que estão a servir modelos.
    """
    pids: set[int] = set()
    if not DEFAULT_SERVER_DIR.exists():
        return pids
    for pid_file in DEFAULT_SERVER_DIR.glob("*.pid"):
        try:
            pid = int(pid_file.read_text().strip())
            if endpoint.pid_exists(pid):
                pids.add(pid)
        except (ValueError, OSError):
            pass
    return pids


def discover_active_sockets() -> list[Path]:
    """Lista sockets de servers ativos (para pedir release em lote)."""
    sockets: list[Path] = []
    if not DEFAULT_SERVER_DIR.exists():
        return sockets
    for sock_path in DEFAULT_SERVER_DIR.glob("*.sock"):
        if is_server_running(sock_path):
            sockets.append(sock_path)
    return sockets


# ---------------------------------------------------------------------------
# Cliente: enviar comandos ao server
# ---------------------------------------------------------------------------


def _follows_remote(socket_path: Path | str | None) -> bool:
    """True se o pedido deve seguir ``VRAMD_REMOTE`` (em vez de forçar local).

    O endpoint canónico do vramd (``None`` ou ``VRAMD_SOCKET``) segue a política
    global. Um path explícito diferente — ex.: o socket per-tool passado a
    ``request_release``/``get_server_status`` — é sempre local: quem o passa
    quer aquele servidor, não o daemon remoto.
    """
    if socket_path is None:
        return True
    try:
        return Path(socket_path) == VRAMD_SOCKET
    except TypeError:
        return False


def send_request(
    request: dict[str, Any],
    socket_path: Path | str | None = None,
    *,
    timeout_sec: float = 300.0,
    force_local: bool = False,
) -> dict[str, Any] | None:
    """Envia um pedido JSON ao server e lê a resposta.

    Se o servidor enviar várias linhas NDJSON (stream), devolve a **última**
    linha (resultado final).

    Args:
        force_local: Ignora ``VRAMD_REMOTE`` e usa o socket local (batch misto
            com assets ``gpu: local``).

    Returns:
        Dict de resposta, ou ``None`` se o server não estiver disponível.
    """
    from . import vramd_remote

    # O socket canónico do vramd segue VRAMD_REMOTE; um path per-tool fica
    # sempre local (ver :func:`_follows_remote`).
    if _follows_remote(socket_path) and vramd_remote.remote_enabled() and not force_local:
        try:
            return vramd_remote.RemoteClient().rpc(request, timeout=timeout_sec)
        except (vramd_remote.RemoteDown, vramd_remote.RemoteStagingError):
            return None
    spath = Path(socket_path) if socket_path else server_socket_path("text2d")
    if not spath.exists():
        return None
    try:
        sock, token = endpoint.connect(spath, timeout=timeout_sec)
    except OSError:
        return None
    try:
        with sock:
            sock.sendall((json.dumps(endpoint.with_auth(request, token)) + "\n").encode())
            data = b""
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                data += chunk
                if len(data) > MAX_RESPONSE_BYTES:
                    return None
            lines = data.decode("utf-8", errors="replace").strip().split("\n")
            if not lines:
                return None
            try:
                response = json.loads(lines[-1])
            except json.JSONDecodeError:
                return None
            # Uma resposta que não é um objecto não tem o contrato (``.get``);
            # devolvê-la rebentava em todos os callers.
            return cast(dict[str, Any], response) if isinstance(response, dict) else None
    except OSError:
        return None


def send_request_stream(
    request: dict[str, Any],
    socket_path: Path | str | None = None,
    *,
    timeout_sec: float = 600.0,
    force_local: bool = False,
) -> Iterator[dict[str, Any]]:
    """Envia um pedido e faz yield de cada linha NDJSON (eventos + resultado).

    No modo remoto não há stream: devolve-se uma única linha com o resultado
    (a transferência de ficheiros não combina com progresso incremental).

    Yields:
        Dict por linha. A última tipicamente tem ``status`` ok/error.
    """
    from . import vramd_remote

    # Ver a nota em send_request: o socket canónico segue VRAMD_REMOTE; um
    # path per-tool explícito é local.
    if _follows_remote(socket_path) and vramd_remote.remote_enabled() and not force_local:
        try:
            yield vramd_remote.RemoteClient().rpc(request, timeout=timeout_sec)
        except (vramd_remote.RemoteDown, vramd_remote.RemoteStagingError):
            return
        return
    spath = Path(socket_path) if socket_path else server_socket_path("text2d")
    if not spath.exists():
        return
    try:
        sock, token = endpoint.connect(spath, timeout=timeout_sec)
    except OSError:
        return
    try:
        with sock:
            sock.sendall((json.dumps(endpoint.with_auth(request, token)) + "\n").encode())
            buf = b""
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if len(line) > MAX_RESPONSE_BYTES:
                        yield {
                            "status": "error",
                            "error": f"linha NDJSON excedeu {MAX_RESPONSE_BYTES} bytes",
                            "error_code": "VRAMD_PROTOCOL",
                        }
                        return
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        event = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        yield event
                if len(buf) > MAX_RESPONSE_BYTES:
                    # Uma única linha sem newline: não deixar o buffer crescer sem
                    # limite (o servidor não vai terminar a linha).
                    yield {
                        "status": "error",
                        "error": f"linha NDJSON excedeu {MAX_RESPONSE_BYTES} bytes",
                        "error_code": "VRAMD_PROTOCOL",
                    }
                    return
            # O servidor fechou sem newline na última linha (ex.: um stream de
            # uma só resposta): não perder o evento final em silêncio.
            if buf:
                text = buf.decode("utf-8", errors="replace").strip()
                if text:
                    try:
                        event = json.loads(text)
                    except json.JSONDecodeError:
                        pass
                    else:
                        if isinstance(event, dict):
                            yield event
    except OSError:
        # Socket caiu a meio do stream: terminar o gerador em silêncio (o
        # caller trata a ausência do evento final), em vez de rebentar o CLI.
        return


def resolve_vramd_priority(explicit: str | None = None) -> str:
    """Resolve prioridade vramd: argumento → env ``VRAMD_PRIORITY`` → interactive."""
    if explicit:
        return str(explicit).strip().lower()
    env = os.environ.get("VRAMD_PRIORITY", "").strip().lower()
    if env in ("interactive", "batch"):
        return env
    return "interactive"


def request_release(socket_path: Path | str | None = None) -> bool:
    """Pede ao server para descarregar o modelo (mas continuar a correr).

    O server faz ``loader_obj.unload()`` e responde ``ok``. No próximo pedido de
    geração, o modelo é recarregado (cold start).
    """
    result = send_request({"cmd": "release"}, socket_path, timeout_sec=30.0)
    return result is not None and result.get("status") == "ok"


def get_server_status(socket_path: Path | str | None = None) -> dict[str, Any] | None:
    """Pede o estado ao server."""
    return send_request({"cmd": "status"}, socket_path, timeout_sec=5.0)


def stop_server(socket_path: Path | str | None = None) -> bool:
    """Envia comando de shutdown ao server."""
    result = send_request({"cmd": "shutdown"}, socket_path, timeout_sec=5.0)
    return result is not None and result.get("status") == "ok"


# ---------------------------------------------------------------------------
# Cliente do vramd (supervisor de VRAM)
# ---------------------------------------------------------------------------


# Tip estável para humanos e agentes (CLIs, kill path, docs).
VRAMD_DO_NOT_KILL_TIP = (
    "VRAM ocupada? Usa `vramd status` / `queue` — espera ou "
    "`vramd cancel <job_id>`. NÃO mates processos GPU "
    "(kill GPU) enquanto o vramd tiver jobs; isso corre contra a fila."
)


def is_vramd_running() -> bool:
    """Verifica se o vramd está ativo no socket canónico (ou no remoto)."""
    from . import vramd_remote

    if vramd_remote.remote_enabled():
        return vramd_remote.RemoteClient().is_up()
    return is_server_running(VRAMD_SOCKET)


def fetch_vramd_queue_snapshot(*, timeout_sec: float = 5.0) -> dict[str, Any] | None:
    """Snapshot da fila vramd (``cmd=queue``), ou ``None`` se o vramd estiver down."""
    if not is_vramd_running():
        return None
    return send_to_vramd({"cmd": "queue"}, timeout_sec=timeout_sec, auto_start=False)


def vramd_is_busy(snapshot: dict[str, Any] | None = None) -> bool:
    """True se há jobs inflight ou na fila (snapshot opcional para evitar 2 RPCs)."""
    snap = snapshot if snapshot is not None else fetch_vramd_queue_snapshot()
    if not snap:
        return False
    try:
        inflight = int(snap.get("inflight") or 0)
        depth = int(snap.get("queue_depth") or 0)
    except (TypeError, ValueError):
        return False
    return inflight > 0 or depth > 0


def format_vramd_holding_summary(snapshot: dict[str, Any]) -> str:
    """Uma linha humana: quem segura a GPU + profundidade da fila."""
    running = list(snapshot.get("running") or [])
    queued = list(snapshot.get("queued") or [])
    inflight = snapshot.get("inflight", len(running))
    depth = snapshot.get("queue_depth", len(queued))
    if running:
        heads = []
        for j in running[:3]:
            jid = str(j.get("job_id") or "")
            short = jid if len(jid) <= 12 else f"{jid[:12]}…"
            backend = j.get("backend") or "?"
            pct = j.get("progress_pct")
            pct_s = f" {pct:.0%}" if isinstance(pct, (int, float)) else ""
            heads.append(f"{backend} job={short}{pct_s}")
        hold = "; ".join(heads)
    else:
        hold = "(nenhum job running)"
    return f"HOLDING: {hold} | QUEUE: {depth} waiting / {inflight} inflight"


# ---------------------------------------------------------------------------
# Calibração atrelada à VRAM
# ---------------------------------------------------------------------------

# Catálogo de calibrações empacotadas: um ficheiro por capacidade de GPU
# (``backends-6g.yaml``, ``backends-16g.yaml``…), gerado por ``vramd calibrate``
# numa GPU com essa VRAM. O admit usa os números MEDIDOS quando existe
# calibração para o hardware do utilizador; sem calibração para a VRAM dele,
# ficam as estimativas + hw-auto (comportamento normal).
CALIBRATED_DIR = Path(__file__).resolve().parent / "data" / "calibrated"


def _calibration_gb(name: str) -> int | None:
    """``backends-6g.yaml`` → 6; ``None`` se o nome não for calibrado."""
    m = re.search(r"backends-(\d+(?:\.\d+)?)g\.ya?ml$", name)
    return int(float(m.group(1))) if m else None


def resolve_vramd_calibration(vram_total_mib: int | None = None) -> Path | None:
    """Calibração empacotada para a VRAM da GPU, ou ``None`` (hw-auto).

    Regra: o ficheiro com a maior etiqueta **≤ VRAM total** da GPU. Quem tem
    6 GB usa a calibração de 6 GB; quem tem 24 GB e o maior ficheiro é 16 GB
    usa o de 16 GB — por segurança, o cenário mais restritivo medido. Se a GPU
    é mais pequena que todas as calibrações (ou a VRAM não é legível), não há
    calibração "para o hardware dele" → ``None``, e o sistema segue com
    estimativas + hw-auto.

    Args:
        vram_total_mib: VRAM total em MiB (default: NVML do device 0).

    Returns:
        Path do ficheiro calibrado, ou ``None``.
    """
    if not CALIBRATED_DIR.is_dir():
        return None
    candidates = [(gb, p) for p in CALIBRATED_DIR.glob("backends-*.yaml") if (gb := _calibration_gb(p.name))]
    if not candidates:
        return None
    candidates.sort()
    if vram_total_mib is None:
        try:
            from .gpu import gpu_total_mib

            vram_total_mib = gpu_total_mib()
        except Exception:
            return None
    if vram_total_mib is None:
        return None
    # Comparação em GB arredondados: a etiqueta é a classe NOMINAL da placa
    # (backends-6g = "calibrado numa GPU de 6 GB") e a VRAM reportada varia
    # com o driver (NVML total 6141 MiB; utilizável ~5772 MiB numa RTX 4050).
    # round(5772/1024)=6 → a placa de 6 GB apanha a calibração 6g.
    user_gb = round(vram_total_mib / 1024)
    chosen: Path | None = None
    for gb, path in candidates:
        if gb <= user_gb:
            chosen = path
    return chosen


def calibrated_peak_signals(backend: str) -> dict[str, Any] | None:
    """Sinais de pico do hw-auto vindos da calibração selecionada.

    A calibração guarda o config que FOI medido (``peak_profile.quant_mode`` +
    ``load_kwargs``). Quando existe calibração para o hardware, a produção deve
    pedir o **mesmo** config — senão o admit descarta a medição (quant
    mismatch) e volta às estimativas (ex.: paint3d medido sem SDNQ mas produção
    a pedir sdnq-uint8 → admit com 4000 em vez de 5760 → risco OOM).

    Returns:
        ``{"sdnq_preset": ..., "memory_efficient": ...}`` ou ``None`` se não há
        calibração para o hardware/backend (o hw-auto cai no perfil da tool).
    """
    cal = resolve_vramd_calibration()
    if cal is None:
        return None
    try:
        import yaml

        data = yaml.safe_load(cal.read_text(encoding="utf-8"))
        for entry in data.get("backends", []):
            if entry.get("name") != backend:
                continue
            pp = entry.get("peak_profile") or {}
            lk = pp.get("load_kwargs") or {}
            signals: dict[str, Any] = {}
            preset = pp.get("quant_mode") or lk.get("sdnq_preset") or lk.get("quant_preset")
            if preset and str(preset).strip().lower() not in ("", "none", "null"):
                signals["sdnq_preset"] = str(preset)
            if lk.get("memory_efficient") is not None:
                signals["memory_efficient"] = bool(lk["memory_efficient"])
            return signals or None
    except Exception:
        return None
    return None


def _discover_vramd_python() -> Path | None:
    """Procura um venv dedicado ao supervisor (``VRAMD_VENV``) ou o canónico
    ``Vramd/.venv`` relativo ao monorepo / Shared.

    O venv canónico do supervisor é criado por ``./install.sh vramd`` (tool
    clified que instala o pacote ``vramd`` do PyPI). ``VRAMD_VENV`` permite
    separar o supervisor das tools (mesma semântica do vramd; no Windows o
    interpretador vive em ``Scripts\\python.exe``).
    """
    raw = os.environ.get("VRAMD_VENV", "").strip()
    if raw:
        venv = Path(os.path.expanduser(raw))
        for candidate in (venv / "bin" / "python", venv / "Scripts" / "python.exe", venv):
            if candidate.is_file():
                return candidate
    here = Path(__file__).resolve()
    # Shared/src/aigamekit_shared/vramd_client.py → monorepo root = parents[3]
    candidates: list[Path] = []
    for parent in here.parents:
        candidates.append(parent / "Vramd" / ".venv" / "bin" / "python")
        candidates.append(parent / "Vramd" / ".venv" / "Scripts" / "python.exe")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _can_import_vramd() -> bool:
    """True se ``vramd`` é importável no venv actual (fallback)."""
    try:
        import vramd  # noqa: F401
    except ImportError:
        return False
    return True


def _resolve_vramd_start_cmd(
    *,
    vramd_bin: str | None = None,
    canonical_python: Path | None = None,
    path_lookup: Callable[[str], str | None] | None = None,
    import_probe: Callable[[], bool] | None = None,
    sys_executable: str = "python",
) -> tuple[list[str] | None, str]:
    """Resolve o comando de arranque do vramd com precedência correcta.

    Prioridade (supervisor nunca deve herdar o venv da tool que o chama):
      1. ``VRAMD_BIN`` (override explícito do operador)
      2. ``Vramd/.venv`` canónico (criado por ``./install.sh vramd``)
      3. ``vramd`` no PATH (instalado pelo install.sh)
      4. ``sys.executable`` actual (último recurso; warning visível)

    Args:
        vramd_bin: Override do operador (``VRAMD_BIN``).
        canonical_python: Path para o python do Vramd/.venv (de
            :func:`_discover_vramd_python`).
        path_lookup: ``shutil.which``-like (testável).
        import_probe: ``lambda: can_import("vramd")`` (testável).
        sys_executable: ``sys.executable`` (testável).

    Returns:
        ``(cmd, warning)`` — ``cmd=None`` se nada serve (caller deve falhar
        com mensagem útil); ``warning`` não-vazia quando o fallback é o venv
        da tool actual (situação incorrecta mas recuperável).
    """
    if vramd_bin and vramd_bin.strip():
        p = vramd_bin.strip()
        if Path(p).exists():
            return [p, "start"], ""
    if canonical_python is not None:
        return [str(canonical_python), "-m", "vramd", "start"], ""
    lookup = path_lookup or (lambda _name: None)
    on_path = lookup("vramd")
    if on_path:
        return [on_path, "start"], ""
    probe = import_probe or (lambda: False)
    if probe():
        return (
            [sys_executable, "-m", "vramd", "start"],
            (
                "Vramd/.venv não encontrado; a usar o venv actual. Isto é INCORRECTO "
                "para um supervisor multi-tool — corre `./install.sh vramd`."
            ),
        )
    return None, ""


def ensure_vramd_running(*, timeout_sec: float = 30.0, auto_start: bool = True, force_local: bool = False) -> bool:
    """Garante que o vramd está ativo. Arranca-o automaticamente se necessário.

    Quando uma tool chama ``delegate_to_vramd``, esta função assegura que o vramd
    está a correr. Se não estiver, tenta arrancá-lo em background via
    ``vramd start`` e espera até o socket ficar pronto.

    O auto-start pode ser desativado com a env var ``VRAMD_AUTO_START=0``.

    Args:
        timeout_sec: Tempo máximo de espera pelo arranque do vramd.
        auto_start: Se ``False``, não arranca o vramd (só verifica se está ativo).

    Returns:
        ``True`` se o vramd está ativo e pronto a receber pedidos.
    """
    from . import vramd_remote

    if vramd_remote.remote_enabled() and not force_local:
        # Remoto: só se verifica; NUNCA se arranca um supervisor local (os jobs
        # iriam para a GPU errada sem o utilizador saber).
        return vramd_remote.RemoteClient().is_up(timeout=min(timeout_sec, 5.0))

    if is_vramd_running():
        return True

    if not auto_start:
        return False

    # Kill-switch via env var.
    if os.environ.get("VRAMD_AUTO_START", "1") == "0":
        return False

    with _START_LOCK:
        # Outra thread pode ter arrancado o supervisor enquanto esperávamos
        # pelo lock; re-provar antes de lançar um segundo processo.
        if is_vramd_running():
            return True
        return _start_vramd_process(timeout_sec=timeout_sec)


def _start_vramd_process(*, timeout_sec: float) -> bool:
    """Arranca o vramd e espera pelo endpoint (chamado sob ``_START_LOCK``)."""
    import shutil
    import subprocess
    import sys

    # Resolver o binário. Prioridade ao venv canónico do vramd (./install.sh
    # vramd): o supervisor deve arrancar NUNCA no venv da tool que o chama —
    # senão herda só os pacotes dessa tool e falha ao importar outras.
    cmd, warning = _resolve_vramd_start_cmd(
        vramd_bin=os.environ.get("VRAMD_BIN", "").strip() or None,
        canonical_python=_discover_vramd_python(),
        path_lookup=shutil.which,
        import_probe=_can_import_vramd,
        sys_executable=sys.executable,
    )
    if cmd is None:
        _logger.warn(
            "[vramd] Auto-start: vramd não instalado. "
            "Corre `./install.sh vramd` (cria Vramd/.venv canónico) "
            "ou define VRAMD_BIN."
        )
        return False
    if warning:
        _logger.warn(f"[vramd] Auto-start: {warning}")

    _logger.info(f"[vramd] Auto-start: {' '.join(cmd)}")
    log_path = endpoint.autostart_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        log_fh.write(f"\n--- auto-start {time.strftime('%Y-%m-%d %H:%M:%S')} cmd={' '.join(cmd)}\n")
        log_fh.flush()
    except OSError as e:
        _logger.warn(f"[vramd] Auto-start: não foi possível abrir log {log_path}: {e}")
        log_fh = subprocess.DEVNULL  # type: ignore[assignment]

    # O supervisor precisa de conhecer o registry e os venvs das tools do
    # monorepo — sem isto o vramd não descobre nenhum backend (tool:).
    env = os.environ.copy()
    try:
        from .monorepo import try_find_monorepo_root

        root = try_find_monorepo_root()
        if root is not None:
            env.setdefault("VRAMD_TOOLS_ROOT", str(root))
            base = root / "Shared" / "src" / "aigamekit_shared" / "data" / "backends.yaml"
            # Calibração atrelada à VRAM da GPU: se existe um ficheiro calibrado
            # para este hardware, entra como overlay (merge por chave — o vramd
            # aceita vários paths em VRAMD_BACKENDS_FILE, o último vence).
            # Sem calibração para a VRAM do utilizador → estimativas + hw-auto
            # (comportamento normal).
            calibrated = resolve_vramd_calibration()
            if calibrated is not None:
                env.setdefault("VRAMD_BACKENDS_FILE", f"{base}{os.pathsep}{calibrated}")
                _logger.info(f"[vramd] Auto-start: calibração {calibrated.name} para a VRAM da GPU")
            else:
                env.setdefault("VRAMD_BACKENDS_FILE", str(base))
    except Exception:
        pass

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT if log_fh is not subprocess.DEVNULL else subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=env,
            **endpoint.detached_popen_kwargs(),  # detach: sobrevive ao chamador
        )
        if log_fh is not subprocess.DEVNULL:
            _logger.info(f"[vramd] Auto-start log: {log_path} (pid={proc.pid})")
            # O filho ficou com o seu próprio dup do fd — o pai pode fechar a
            # sua cópia (sem isto, cada auto-start vaza um fd no processo chamador).
            with contextlib.suppress(Exception):
                log_fh.close()
    except OSError as e:
        _logger.warn(f"[vramd] Auto-start falhou: {e}")
        if log_fh is not subprocess.DEVNULL:
            with contextlib.suppress(Exception):
                log_fh.close()
        return False

    # Esperar que o socket fique pronto.
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if is_vramd_running():
            _logger.info("[vramd] Auto-start: vramd ativo e pronto.")
            return True
        # O supervisor saiu (falta de deps, lock de outro supervisor, etc.):
        # esperar o timeout inteiro só atrasava o fallback do caller.
        if proc.poll() is not None:
            _logger.warn(
                f"[vramd] Auto-start: processo terminou (exit={proc.returncode}) antes do "
                f"endpoint ficar pronto; vê {log_path}."
            )
            return False

    _logger.warn(f"[vramd] Auto-start: timeout após {timeout_sec:.0f}s à espera do socket.")
    return False


def send_to_vramd(
    request: dict[str, Any],
    *,
    timeout_sec: float = 300.0,
    auto_start: bool = True,
    force_local: bool = False,
) -> dict[str, Any] | None:
    """Envia um pedido ao vramd.

    Args:
        request: Payload JSON (deve incluir ``cmd``).
        timeout_sec: Timeout do socket.
        auto_start: Se ``True`` (default), arranca o vramd se estiver down.
            ``poll`` / ``wait`` / ``cancel`` / ``queue`` devem usar ``False`` —
            auto-start a meio de um job perde o ``job_id`` (vramd vazio novo).
    """
    if not ensure_vramd_running(auto_start=auto_start, force_local=force_local):
        return None
    return send_request(request, VRAMD_SOCKET, timeout_sec=timeout_sec, force_local=force_local)


def delegate_to_vramd(
    backend: str,
    request: dict[str, Any],
    *,
    timeout_sec: float = 600.0,
    priority: str | None = None,
    remote: bool | None = None,
) -> dict[str, Any] | None:
    """Delega um pedido de geração ao vramd, arrancando-o automaticamente se necessário.

    Helper para CLIs das tools: no início do comando ``generate``, chamar esta
    função. Se retornar um dict com ``status == "ok"``, a geração foi feita pelo
    vramd (usar ``result["output"]``). Se retornar ``None``, o vramd não está ativo —
    fazer fallback in-process.

    Se o vramd não estiver ativo, ``ensure_vramd_running`` arranca-o automaticamente
    em background (a menos que ``VRAMD_AUTO_START=0``).

    Prioridade: ``priority`` explícito, senão env ``VRAMD_PRIORITY``,
    senão ``interactive``. GameAssets batch deve exportar ``VRAMD_PRIORITY=batch``.

    Args:
        backend: Nome do backend (ex: ``text2d``).
        request: Parâmetros do pedido (prompt, output, steps, ...).
        timeout_sec: Timeout para o pedido de geração.
        priority: ``interactive`` | ``batch`` (opcional).

    Returns:
        Dict de resposta (``{"status": "ok", "output": ...}``) ou ``None`` se o
        vramd não está ativo (nem pôde ser arrancado).
    """
    from . import vramd_remote

    # ``remote`` explícito (manifesto ``gpu:``) vence o modo global. ``None``
    # mantém o comportamento antigo: remote se VRAMD_REMOTE estiver definido.
    resolved = _resolve_remote(remote)
    use_remote = resolved if resolved is not None else vramd_remote.remote_enabled()
    if use_remote:
        if not vramd_remote.remote_enabled():
            return _remote_error(
                "VRAMD_REMOTE_NOT_CONFIGURED",
                "asset pediu GPU remota mas VRAMD_REMOTE/VRAMD_REMOTE_TOKEN não estão definidos",
            )
        return _delegate_remote(backend, request, timeout_sec=timeout_sec, priority=priority)

    if not ensure_vramd_running(force_local=True):
        return None
    pri = resolve_vramd_priority(priority if priority is not None else request.get("priority"))
    # ``cmd``/``backend`` explícitos por último: o payload do pedido nunca deve
    # re-rotear a delegação (um dicionário vindo de kwargs do CLI pode ter essas chaves).
    req = {**request, "cmd": "generate", "backend": backend, "priority": pri}
    # send_request directo (não send_to_vramd): o ensure acima já garantiu o vramd —
    # evita repetir o probe pid-file + os.kill + connect a cada delegação.
    resp = send_request(req, VRAMD_SOCKET, timeout_sec=timeout_sec, force_local=True)
    if resp is None and is_vramd_running():
        # Socket morreu/timeout mas o supervisor está vivo — o job pode estar a
        # meio do generate na GPU. NÃO devolver None: o caller faria fallback
        # in-process e ficariam 2 jobs a competir pela mesma VRAM.
        return {
            "status": "error",
            "error": "vramd sem resposta (timeout/socket) com supervisor ainda ativo",
            "error_code": "VRAMD_NO_RESPONSE",
            "hint": (
                "Inspecciona `vramd queue` / `vramd stats` antes de repetir; "
                "sem fallback in-process enquanto o vramd puder estar a gerar."
            ),
        }
    return resp


def _resolve_remote(remote: bool | None) -> bool | None:
    """``remote`` explícito > ``VRAMD_GPU_MODE`` > ``None`` (política global)."""
    if remote is not None:
        return remote
    mode = os.environ.get("VRAMD_GPU_MODE", "").strip().lower()
    if mode == "remote":
        return True
    if mode == "local":
        return False
    return None


def _remote_error(code: str, message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error": message,
        "error_code": code,
        "hint": (
            "vramd remoto: verifica VRAMD_REMOTE/VRAMD_REMOTE_TOKEN e "
            "`vramd status`; o fallback in-process está desligado em modo remoto."
        ),
    }


def _restage_after_session_loss(
    backend: str,
    request: dict[str, Any],
    *,
    cmd: str,
    priority: str,
) -> tuple[Any, dict[str, Any]]:
    """Reabre a sessão (o servidor reiniciou) e reencaminha o job uma vez."""
    from . import vramd_bundle, vramd_remote

    vramd_bundle.reset()
    staged = vramd_remote.stage_request(backend, request)
    req = {**staged.request, "cmd": cmd, "backend": backend, "priority": priority}
    return staged, req


def _delegate_remote(
    backend: str,
    request: dict[str, Any],
    *,
    timeout_sec: float,
    priority: str | None,
) -> dict[str, Any] | None:
    """Staging + generate + download contra o daemon remoto."""
    from . import vramd_remote

    try:
        staged = vramd_remote.stage_request(backend, request)
    except vramd_remote.RemoteDown as exc:
        if not vramd_remote.strict():
            return None
        return _remote_error("VRAMD_REMOTE_DOWN", str(exc))
    except vramd_remote.RemoteStagingError as exc:
        return _remote_error("VRAMD_REMOTE_STAGE", str(exc))

    pri = resolve_vramd_priority(priority if priority is not None else request.get("priority"))
    req = {**staged.request, "cmd": "generate", "backend": backend, "priority": pri}
    try:
        response = staged.client.rpc(req, timeout=timeout_sec)
        if response.get("error_code") == "SESSION_UNKNOWN":
            # O supervisor reiniciou e perdeu as sessões em memória; o job não
            # chegou a correr. Reabre e tenta uma vez (o bundle está no CAS).
            staged.discard()
            staged, req = _restage_after_session_loss(backend, request, cmd="generate", priority=pri)
            response = staged.client.rpc(req, timeout=timeout_sec)
        return vramd_remote.finalize(staged, response)
    except (vramd_remote.RemoteDown, OSError) as exc:
        # O job pode estar a correr no servidor; não limpar o sandbox (TTL limpa).
        # Fechar a ligação é seguro e evita um socket persistente por job falhado.
        staged.close()
        return _remote_error("VRAMD_REMOTE_DOWN", str(exc))
    except vramd_remote.RemoteStagingError as exc:
        staged.close()
        return _remote_error("VRAMD_REMOTE_TRANSFER", str(exc))
    except vramd_remote.TransferError as exc:
        staged.close()
        return _remote_error("VRAMD_REMOTE_TRANSFER", str(exc))


def submit_to_vramd(
    backend: str,
    request: dict[str, Any],
    *,
    timeout_sec: float = 30.0,
    priority: str | None = None,
    remote: bool | None = None,
) -> dict[str, Any] | None:
    """Enfileira um job no vramd (async). Retorna ``{"status":"ok","job_id":...}``."""
    from . import vramd_remote

    resolved = _resolve_remote(remote)
    use_remote = resolved if resolved is not None else vramd_remote.remote_enabled()
    if use_remote:
        if not vramd_remote.remote_enabled():
            return _remote_error(
                "VRAMD_REMOTE_NOT_CONFIGURED",
                "asset pediu GPU remota mas VRAMD_REMOTE/VRAMD_REMOTE_TOKEN não estão definidos",
            )
        try:
            staged = vramd_remote.stage_request(backend, request)
        except vramd_remote.RemoteDown as exc:
            if not vramd_remote.strict():
                return None
            return _remote_error("VRAMD_REMOTE_DOWN", str(exc))
        except vramd_remote.RemoteStagingError as exc:
            return _remote_error("VRAMD_REMOTE_STAGE", str(exc))
        except vramd_remote.TransferError as exc:
            return _remote_error("VRAMD_REMOTE_STAGE", str(exc))
        pri = resolve_vramd_priority(priority if priority is not None else request.get("priority"))
        req = {**staged.request, "cmd": "submit", "backend": backend, "priority": pri}
        try:
            response = staged.client.rpc(req, timeout=timeout_sec)
            if response.get("error_code") == "SESSION_UNKNOWN":
                staged.discard()
                staged, req = _restage_after_session_loss(backend, request, cmd="submit", priority=pri)
                response = staged.client.rpc(req, timeout=timeout_sec)
        except (vramd_remote.RemoteDown, vramd_remote.RemoteStagingError, vramd_remote.TransferError, OSError) as exc:
            staged.close()
            return _remote_error("VRAMD_REMOTE_DOWN", str(exc))
        if response.get("status") == "ok" and response.get("job_id"):
            vramd_remote.remember(str(response["job_id"]), staged)
        else:
            # Sem job_id não há ``wait`` para recolher a ligação e o sandbox;
            # fechar já o socket evita um leak por submit recusado. O sandbox
            # fica para o TTL (o submit pode ter enfileirado antes do erro).
            staged.close()
        # Em erro não se limpa o sandbox: o submit pode ter enfileirado o job
        # antes de a resposta se perder (TTL recolhe).
        return response

    if not ensure_vramd_running(force_local=True):
        return None
    pri = resolve_vramd_priority(priority if priority is not None else request.get("priority"))
    # Mesma regra do delegate_to_vramd: cmd/backend explícitos vencem o payload.
    req = {**request, "cmd": "submit", "backend": backend, "priority": pri}
    # send_request directo: ensure já feito acima (ver delegate_to_vramd).
    return send_request(req, VRAMD_SOCKET, timeout_sec=timeout_sec, force_local=True)


def poll_vramd_job(job_id: str, *, timeout_sec: float = 5.0) -> dict[str, Any] | None:
    """Consulta o estado de um job vramd."""
    return send_to_vramd({"cmd": "poll", "job_id": job_id}, timeout_sec=timeout_sec, auto_start=False)


def wait_vramd_job(
    job_id: str,
    *,
    timeout_sec: float = 600.0,
    stream: bool = False,
    remote: bool | None = None,
) -> dict[str, Any] | None:
    """Espera um job vramd terminar. Com ``stream=True`` devolve só o resultado final.

    ``timeout_sec`` é enviado ao vramd (campo ``timeout_sec``); o socket do cliente
    recebe uma margem extra — se o job terminar mesmo no limite, a resposta do
    servidor ainda chega antes do deadline local (senão reportávamos "vramd down"
    para um job que completou).
    """
    req: dict[str, Any] = {"cmd": "wait", "job_id": job_id, "timeout_sec": timeout_sec}
    if stream:
        req["stream"] = True

    from . import vramd_remote

    resolved = _resolve_remote(remote)
    use_remote = resolved if resolved is not None else vramd_remote.remote_enabled()
    if use_remote:
        if not vramd_remote.remote_enabled():
            return _remote_error(
                "VRAMD_REMOTE_NOT_CONFIGURED",
                "asset pediu GPU remota mas VRAMD_REMOTE/VRAMD_REMOTE_TOKEN não estão definidos",
            )
        staged = vramd_remote.peek(job_id)
        client = staged.client if staged is not None else vramd_remote.RemoteClient()
        try:
            response = client.rpc(req, timeout=timeout_sec + 30.0)
        except (vramd_remote.RemoteDown, vramd_remote.TransferError, OSError) as exc:
            # A ligação persistente pode ter sido fechada pelo idle timeout do
            # servidor entre o submit e o wait. O job continua na fila: reabre
            # no mesmo endpoint e repete o wait (idempotente).
            if staged is None or not vramd_remote.reconnect(staged):
                return _remote_error("VRAMD_REMOTE_DOWN", str(exc))
            try:
                response = staged.client.rpc(req, timeout=timeout_sec + 30.0)
            except (vramd_remote.RemoteDown, vramd_remote.TransferError, OSError) as retry_exc:
                return _remote_error("VRAMD_REMOTE_DOWN", str(retry_exc))
        if staged is None:
            return response
        # ``take`` pode devolver None se outra thread já levou o staging;
        # usar o ``staged`` do peek mantém o caminho idempotente.
        target = vramd_remote.take(job_id) or staged
        try:
            return vramd_remote.finalize(target, response)
        except (vramd_remote.RemoteDown, vramd_remote.RemoteStagingError, vramd_remote.TransferError, OSError) as exc:
            # O download falhou a meio: não deixar o socket persistente
            # aberto (o sandbox fica para o TTL, não para um retry local).
            target.close()
            return _remote_error("VRAMD_REMOTE_TRANSFER", str(exc))

    return send_to_vramd(req, timeout_sec=timeout_sec + 30.0, auto_start=False, force_local=True)


def cancel_vramd_job(job_id: str, *, timeout_sec: float = 10.0, remote: bool | None = None) -> dict[str, Any] | None:
    """Pede cancelamento de um job vramd (queued imediato; running best-effort).

    ``remote=True`` explícito exige ``VRAMD_REMOTE`` configurado — sem isto o
    ``send_to_vramd`` cairia no supervisor local e cancelaria o job errado.
    """
    from . import vramd_remote

    resolved = _resolve_remote(remote)
    use_remote = resolved if resolved is not None else vramd_remote.remote_enabled()
    if resolved is True and not vramd_remote.remote_enabled():
        return _remote_error(
            "VRAMD_REMOTE_NOT_CONFIGURED",
            "cancel pediu GPU remota mas VRAMD_REMOTE/VRAMD_REMOTE_TOKEN não estão definidos",
        )
    return send_to_vramd(
        {"cmd": "cancel", "job_id": job_id},
        timeout_sec=timeout_sec,
        auto_start=False,
        force_local=not use_remote,
    )


def cancel_vramd_all(
    *,
    queued_only: bool = False,
    timeout_sec: float = 30.0,
) -> dict[str, Any] | None:
    """Cancela todos os jobs vramd (``flush`` / ``cancel --all``)."""
    from . import vramd_remote

    resolved = _resolve_remote(None)
    use_remote = resolved if resolved is not None else vramd_remote.remote_enabled()
    if resolved is True and not vramd_remote.remote_enabled():
        # ``VRAMD_GPU_MODE=remote`` sem VRAMD_REMOTE: não cancelar jobs no
        # supervisor errado (o local) em silêncio.
        return _remote_error(
            "VRAMD_REMOTE_NOT_CONFIGURED",
            "flush pediu GPU remota mas VRAMD_REMOTE/VRAMD_REMOTE_TOKEN não estão definidos",
        )
    return send_to_vramd(
        {"cmd": "flush", "queued_only": queued_only},
        timeout_sec=timeout_sec,
        auto_start=False,
        force_local=not use_remote,
    )


def respawn_vramd_backend(
    backend: str | None = None,
    *,
    lazy: bool = True,
    timeout_sec: float = 120.0,
) -> dict[str, Any] | None:
    """Reinicia o worker subprocesso de um backend vramd (sem reiniciar o supervisor).

    Caso de uso: depois de editar código de uma tool (ex.: ``Text3D/utils/export.py``
    onde mora o ``save_mesh`` do GLB), o worker persistente no venv da tool ainda
    tem o módulo antigo em memória — ``evict``/``release`` só descarrega pesos.
    Esta chamada mata o subprocesso do worker e arranca um novo, pelo que o
    próximo ``generate`` já corre o código atualizado.

    Args:
        backend: Nome do backend (ex.: ``text3d``). ``None`` reinicia todos os
            backends com worker subprocesso.
        lazy: Se ``True`` (default), só mata o worker; o reload fica pendente
            para o próximo ``generate``/``preload``. Se ``False``, recarrega já
            o modelo com o mesmo ``load_shape`` (fica quente).
        timeout_sec: Timeout da chamada (load pode demorar em modo ``hot``).

    Returns:
        Resposta do vramd (``{"status": "ok", "results": [...], ...}``) ou
        ``None`` se o vramd não estiver ativo (nada para respawnar).
    """
    req: dict[str, Any] = {"cmd": "respawn", "lazy": lazy}
    if backend:
        req["backend"] = backend
    return send_to_vramd(req, timeout_sec=timeout_sec, auto_start=False)


def zero_vramd_vram(*, timeout_sec: float = 120.0) -> dict[str, Any] | None:
    """Zera TODA a VRAM segurada pelo vramd sem parar o supervisor.

    ``evict``/``release`` só largam os pesos — os workers subprocesso ficam
    vivos a segurar o contexto CUDA (~0.3-1 GiB cada). ``zero`` termina todos
    os workers vivos (o próximo ``generate`` arranca-os frescos), evicta
    resíduos in-process e scrubba caches. Recusa com ``ZERO_BUSY`` se houver
    jobs na fila — nunca mata um worker a meio de um job.

    Args:
        timeout_sec: Timeout da chamada (shutdown gracioso de workers).

    Returns:
        Resposta do vramd (``{"status": "ok", "workers_killed": N,
        "free_mib_before/after": ..., "results": [...]}``) ou ``None`` se o
        vramd não estiver ativo (nada para zerar).
    """
    return send_to_vramd({"cmd": "zero"}, timeout_sec=timeout_sec, auto_start=False)


# ---------------------------------------------------------------------------
# Coordenação de VRAM
# ---------------------------------------------------------------------------


def ensure_vram_available(
    needed_mib: int,
    *,
    timeout_sec: float = 30.0,
    backend: str | None = None,
    quant_mode: str | None = None,
) -> bool:
    """Garante que há VRAM suficiente; se não, pede aos servers para descarregar.

    Chamaado por ferramentas pesadas (text3d, paint3d) antes de ocupar a GPU.
    Se houver servers ativos a segurar VRAM, pede-lhes ``release`` gracioso e
    espera até haver espaço (ou timeout).

    Preferência: se o **vramd** estiver ativo e **faltar** espaço (livre <
    ``needed_mib``), envia ``ensure-vram`` para evicção inteligente peso+LRU.
    Com espaço suficiente o caller prossegue sem consultar o daemon — o
    ``needed_mib`` é do caller, que conhece o seu modo (quantização, group
    offload com pesos em streaming); o pico calibrado do backend (``max(
    needed, peak)`` no vramd) entra apenas na evicção, como worst-case.

    Args:
        needed_mib: VRAM necessária em MiB (mínimo pedido pelo cliente).
        timeout_sec: Tempo máximo de espera pelo release.
        backend: Nome do backend vramd (ex: ``text3d``) para reservar pico de inferência.
        quant_mode: Quantização assumida no pico (ex: ``sdnq-int4`` / ``none``).

    Returns:
        ``True`` se há VRAM suficiente (ou se não foi possível verificar).
    """
    from . import vramd_remote
    from .gpu import query_gpu_free_mib

    # Em modo remoto a GPU que importa é a do servidor; a VRAM livre desta
    # máquina não diz nada sobre a admissão do job. Sondá-la aqui podia
    # devolver True (GPU local folgada) sem nunca pedir evicção ao daemon, ou
    # pedir evicção por uma GPU local apertada que não corre o job.
    remote_mode = _resolve_remote(None)
    if remote_mode is None:
        remote_mode = vramd_remote.remote_enabled()

    free: int | None = None
    if not remote_mode:
        free = query_gpu_free_mib()
        if free is not None and free >= needed_mib:
            # Já há espaço para a necessidade declarada pelo caller — que conhece o
            # seu modo real (quantização / group offload com pesos em streaming).
            # O pico calibrado do backend é um worst-case estático (fp16 residente)
            # que recusava jobs que cabem (ex.: paint3d SDNQ+streams, ~3 GiB de
            # pico vs 5+ GiB calibrados). O vramd só é consultado quando falta
            # espaço — para pedir evicção.
            return True

    # Preferir o vramd se ativo (evicção inteligente peso+LRU + peak por backend).
    if is_vramd_running():
        req: dict[str, Any] = {"cmd": "ensure-vram", "needed_mib": needed_mib}
        if backend:
            req["backend"] = backend
        if quant_mode:
            req["quant_mode"] = quant_mode
        if remote_mode:
            _logger.info(
                f"VRAM remota: a pedir ensure-vram ao vramd (preciso {needed_mib}"
                + (f", backend={backend}" if backend else "")
                + ")"
            )
        else:
            _logger.info(
                f"VRAM insuficiente ({free} MiB livres, preciso {needed_mib}"
                + (f", backend={backend}" if backend else "")
                + ") — a pedir evicção ao vramd"
            )
        resp = send_to_vramd(req, timeout_sec=timeout_sec, force_local=not remote_mode)
        if resp is not None:
            ok = resp.get("status") == "ok"
            if not ok:
                _logger.warn(f"vramd respondeu {resp.get('status')} ao ensure-vram")
            return ok
        # vramd estava running mas não respondeu — cair para legacy só se permitido.

    # Legacy per-tool servers: opt-in (preferir vramd). Sem override, não descobrir
    # sockets antigos nem esperar releases legados — evita corridas com o vramd.
    allow_legacy = os.environ.get("VRAMD_ALLOW_LEGACY_SERVER", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not allow_legacy:
        if free is None:
            return True
        if free >= needed_mib:
            return True
        _logger.warn(
            f"VRAM insuficiente ({free} MiB livres, preciso {needed_mib}) e legacy "
            "ensure_vram desligado — arranca vramd ou VRAMD_ALLOW_LEGACY_SERVER=1"
        )
        return False

    # Legacy: pedir a todos os per-tool servers ativos para fazer release.
    active = [s for s in discover_active_sockets() if Path(s).resolve() != VRAMD_SOCKET.resolve()]
    if active:
        msg = (
            f"VRAM insuficiente ({free} MiB livres, preciso {needed_mib}) — "
            f"a pedir release a {len(active)} server(s) legacy"
        )
        _logger.info(msg)
        for sock in active:
            with contextlib.suppress(Exception):
                request_release(sock)
    elif free is None:
        return True  # não dá para verificar (NVML/smi indisponível); deixar tentar

    # Esperar que libertem
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        time.sleep(1.0)
        free = query_gpu_free_mib()
        if free is not None and free >= needed_mib:
            return True

    _logger.warn(f"Timeout a esperar VRAM (livre={free} MiB, preciso {needed_mib})")
    return False


# ---------------------------------------------------------------------------
# Server genérico
# ---------------------------------------------------------------------------


class ModelServer:
    """Server genérico que mantém um modelo carregado e serve pedidos.

    Cada tool cria uma instância com o seu ``loader`` (função que devolve um
    objeto com ``warmup()`` / ``generate()`` / ``unload()``) e ``generator``
    (função que recebe o objeto carregado + kwargs e devolve um dict de resultado).
    """

    def __init__(
        self,
        socket_path: Path | str,
        loader: Callable[[], Any],
        generator: Callable[[Any, dict[str, Any]], dict[str, Any]],
        *,
        idle_timeout_min: int = DEFAULT_IDLE_TIMEOUT_MIN,
        verbose: bool = False,
        tool_name: str = "model",
    ) -> None:
        self.socket_path = Path(socket_path)
        self.ppid_path = _pid_path(self.socket_path)
        self.idle_timeout_sec = idle_timeout_min * 60
        self.verbose = verbose
        self.tool_name = tool_name
        self._loader = loader
        self._generator = generator

        self._obj: Any = None  # o modelo carregado (lazy)
        # RLock: ``_handle_generate`` segura-o durante todo o generate e chama
        # ``_ensure_loaded`` (que também o adquire) na mesma thread. Um Lock
        # simples obrigava a libertar entre load e generate — janela em que um
        # ``release`` concorrente descarregava o modelo que ia ser usado.
        self._obj_lock = threading.RLock()
        self._server_sock: socket.socket | None = None
        self._auth_token: str | None = None
        self._transport = endpoint.TRANSPORT_UNIX
        self._running = False
        self._last_activity = time.monotonic()
        self._requests_served = 0
        # Handlers em curso: o idle timeout não pode encerrar o server (e
        # descarregar o modelo) por baixo de um generate ativo.
        self._active_clients = 0
        self._clients_lock = threading.Lock()
        self._pid = os.getpid()

    def _log(self, msg: str) -> None:
        if self.verbose:
            _logger.info(f"[{self.tool_name}-server] {msg}")

    def _ensure_loaded(self) -> Any:
        """Carrega o modelo (idempotente). Thread-safe."""
        with self._obj_lock:
            if self._obj is not None:
                return self._obj
            self._log("A carregar modelo (cold start)...")
            obj = self._loader()
            self._obj = obj
            self._log("Modelo carregado e pronto.")
            return obj

    def _release_model(self) -> None:
        """Descarrega o modelo (liberta VRAM). O server continua a correr."""
        with self._obj_lock:
            if self._obj is not None:
                self._log("Release: a descarregar modelo...")
                unload = getattr(self._obj, "unload", None)
                if callable(unload):
                    with contextlib.suppress(Exception):
                        unload()
                self._obj = None
                self._log("Modelo descarregado.")

    def _handle_generate(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            # Load + generate sob a MESMA lock: sem isto, um ``release`` entre
            # os dois deixava-nos a gerar sobre um modelo já descarregado.
            with self._obj_lock:
                obj = self._ensure_loaded()
                return self._generator(obj, request)
        except Exception as e:
            self._log(f"Erro na geração: {e}")
            # OOM? Descarregar para próxima tentativa recarregar. Sob a MESMA
            # lock do load — sem isto, uma thread em _ensure_loaded podia acabar
            # de carregar um modelo novo e esta linha anulá-lo (VRAM órfã).
            with self._obj_lock, contextlib.suppress(Exception):
                unload = getattr(self._obj, "unload", None)
                if callable(unload):
                    unload()
                self._obj = None
            return {"status": "error", "error": str(e)}

    def _handle_client(self, conn: socket.socket) -> None:
        self._last_activity = time.monotonic()
        with self._clients_lock:
            self._active_clients += 1
        try:
            conn.settimeout(300.0)
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if len(data) > MAX_REQUEST_BYTES:
                    # Sem newline e acima do tecto: não continuar a acumular.
                    with contextlib.suppress(OSError):
                        conn.sendall(
                            (
                                json.dumps(
                                    {
                                        "status": "error",
                                        "error": f"pedido excede {MAX_REQUEST_BYTES} bytes",
                                        "error_code": "REQUEST_TOO_LARGE",
                                    }
                                )
                                + "\n"
                            ).encode()
                        )
                    return

            line = data.decode("utf-8", errors="replace").strip()
            if not line:
                return

            request = json.loads(line)
            if not isinstance(request, dict):
                conn.sendall((json.dumps({"status": "error", "error": "request não é objecto"}) + "\n").encode())
                return
            if not endpoint.is_authorized(request, self._auth_token):
                conn.sendall(
                    (
                        json.dumps(
                            {
                                "status": "error",
                                "error": "token do endpoint ausente ou inválido",
                                "error_code": "UNAUTHORIZED",
                            }
                        )
                        + "\n"
                    ).encode()
                )
                return
            request.pop("auth", None)
            cmd = request.get("cmd", "generate")
            self._log(f"Pedido: {cmd}")

            if cmd == "shutdown":
                conn.sendall((json.dumps({"status": "ok", "message": "shutting down"}) + "\n").encode())
                self._running = False
                return
            if cmd == "status":
                response = {
                    "status": "status",
                    "pid": self._pid,
                    "model_loaded": self._obj is not None,
                    "requests_served": self._requests_served,
                    "socket": str(self.socket_path),
                    "transport": self._transport,
                    "endpoint": endpoint.endpoint_label(self.socket_path),
                    "tool": self.tool_name,
                }
                conn.sendall((json.dumps(response) + "\n").encode())
                return
            if cmd == "release":
                self._release_model()
                conn.sendall((json.dumps({"status": "ok", "message": "model released"}) + "\n").encode())
                return

            # Geração
            response = self._handle_generate(request)
            if response.get("status") == "ok":
                self._requests_served += 1
            conn.sendall((json.dumps(response) + "\n").encode())

        except json.JSONDecodeError as e:
            with contextlib.suppress(OSError):
                conn.sendall((json.dumps({"status": "error", "error": f"JSON inválido: {e}"}) + "\n").encode())
        except Exception as e:
            self._log(f"Erro ao processar cliente: {e}")
            with contextlib.suppress(OSError):
                conn.sendall((json.dumps({"status": "error", "error": str(e)}) + "\n").encode())
        finally:
            with self._clients_lock:
                self._active_clients = max(0, self._active_clients - 1)
            # Um generate longo não pode deixar o relógio de idle no inicio da
            # ligação e disparar o shutdown mal o handler acabe.
            self._last_activity = time.monotonic()
            conn.close()

    def _cleanup(self) -> None:
        self._log("Cleanup...")
        # Sob a lock do modelo: não descarregar por baixo de um generate que
        # ainda está a correr num handler thread.
        with self._obj_lock, contextlib.suppress(Exception):
            if self._obj is not None:
                unload = getattr(self._obj, "unload", None)
                if callable(unload):
                    unload()
            self._obj = None
        endpoint.remove_endpoint(self.socket_path)
        with contextlib.suppress(OSError):
            self.ppid_path.unlink(missing_ok=True)
        if self._server_sock is not None:
            with contextlib.suppress(OSError):
                self._server_sock.close()
        self._auth_token = None

    def _signal_handler(self, signum: int, frame: Any) -> None:
        self._log(f"Sinal {signum} recebido — a encerrar.")
        self._running = False

    def serve_forever(self) -> None:
        """Arranca o server (bloqueante). Graceful shutdown via SIGTERM/SIGINT."""
        _ensure_server_dir()

        # Cleanup stale
        if self.socket_path.exists() and not is_server_running(self.socket_path):
            with contextlib.suppress(OSError):
                self.socket_path.unlink(missing_ok=True)
                self.ppid_path.unlink(missing_ok=True)

        self.ppid_path.write_text(str(self._pid))

        try:
            self._server_sock, info = endpoint.listen(self.socket_path)
        except OSError:
            # Sem bind não há server; o pid file não pode ficar a fingir que há.
            with contextlib.suppress(OSError):
                self.ppid_path.unlink(missing_ok=True)
            raise
        self._auth_token = info.token
        self._transport = info.transport

        # Registar signals só na main thread (signal.signal exige main thread).
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGINT):
                with contextlib.suppress(OSError, ValueError):
                    signal.signal(sig, self._signal_handler)
            if endpoint.IS_WINDOWS and hasattr(signal, "SIGBREAK"):
                with contextlib.suppress(OSError, ValueError):
                    signal.signal(signal.SIGBREAK, self._signal_handler)  # type: ignore[attr-defined]

        self._running = True
        _logger.info(
            f"{self.tool_name} model server ativo em {endpoint.endpoint_label(self.socket_path)} "
            f"({self._transport}, {self.socket_path}) (PID {self._pid})"
        )
        _logger.info(f"Idle timeout: {self.idle_timeout_sec / 60:.0f} min")

        try:
            while self._running:
                idle = time.monotonic() - self._last_activity
                if self._requests_served > 0 and self._active_clients == 0 and idle > self.idle_timeout_sec:
                    _logger.info(f"Idle {idle / 60:.0f}min > timeout — a encerrar.")
                    break
                try:
                    conn, _ = self._server_sock.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
                t.start()
        finally:
            self._cleanup()
            _logger.info("Server encerrado.")
