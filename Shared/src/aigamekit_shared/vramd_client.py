"""Model server genérico partilhado — mantém modelos AI carregados entre invocações.

Um server long-lived (Unix domain socket) que carrega um pipeline uma vez e serve
pedidos subsequentes sem cold start. Cada ferramenta regista o seu próprio loader
e tem o seu próprio socket (ex: ``text2icon-server.sock``).

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

from .logging import Logger

_logger = Logger()

# Paths canónicos.
DEFAULT_SERVER_DIR = Path.home() / ".cache" / "aigamekit"

# Socket canónico do vramd — supervisor único de VRAM (substitui o ModelServer).
# Quando o vramd está ativo, todas as ferramentas delegam nele; este socket é o
# ponto de entrada para geração (cmd "generate" com "backend") e coordenação
# de VRAM (cmd "ensure-vram"). Ver a tool ``Vramd/`` no monorepo (PyPI vramd).
VRAMD_SOCKET = Path.home() / ".cache" / "vramd" / "vramd.sock"

# Um socket por tool — evita misturar modelos diferentes (legacy per-tool servers).
_SOCKET_FOR_TOOL: dict[str, str] = {
    "text2icon": "text2icon-server.sock",
    "text2d": "text2d-server.sock",
    "texture2d": "texture2d-server.sock",
}

DEFAULT_IDLE_TIMEOUT_MIN = 30


def server_socket_path(tool: str) -> Path:
    """Path do socket para uma tool (ex: ``text2icon`` → ``text2icon-server.sock``)."""
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
    """Verifica se um server está vivo via PID file + socket connect."""
    spath = Path(socket_path) if socket_path else server_socket_path("text2icon")
    ppath = _pid_path(spath)

    if not ppath.exists():
        return False

    try:
        pid = int(ppath.read_text().strip())
    except (ValueError, OSError):
        return False

    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False

    if not spath.exists():
        return False

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect(str(spath))
            return True
    except OSError:
        return False


def get_server_pid(socket_path: Path | str | None = None) -> int | None:
    """Lê o PID de um server ativo. Retorna ``None`` se não estiver a correr."""
    spath = Path(socket_path) if socket_path else server_socket_path("text2icon")
    ppath = _pid_path(spath)
    if not ppath.exists():
        return None
    try:
        pid = int(ppath.read_text().strip())
        os.kill(pid, 0)
        return pid
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
            os.kill(pid, 0)
            pids.add(pid)
        except (ValueError, OSError, ProcessLookupError):
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


def send_request(
    request: dict[str, Any],
    socket_path: Path | str | None = None,
    *,
    timeout_sec: float = 300.0,
) -> dict[str, Any] | None:
    """Envia um pedido JSON ao server e lê a resposta.

    Se o servidor enviar várias linhas NDJSON (stream), devolve a **última**
    linha (resultado final).

    Returns:
        Dict de resposta, ou ``None`` se o server não estiver disponível.
    """
    spath = Path(socket_path) if socket_path else server_socket_path("text2icon")
    if not spath.exists():
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_sec)
            s.connect(str(spath))
            s.sendall((json.dumps(request) + "\n").encode())
            data = b""
            while True:
                chunk = s.recv(8192)
                if not chunk:
                    break
                data += chunk
            lines = data.decode("utf-8", errors="replace").strip().split("\n")
            if not lines:
                return None
            return cast(dict[str, Any] | None, json.loads(lines[-1]))
    except (OSError, json.JSONDecodeError):
        return None


def send_request_stream(
    request: dict[str, Any],
    socket_path: Path | str | None = None,
    *,
    timeout_sec: float = 600.0,
) -> Iterator[dict[str, Any]]:
    """Envia um pedido e faz yield de cada linha NDJSON (eventos + resultado).

    Yields:
        Dict por linha. A última tipicamente tem ``status`` ok/error.
    """
    spath = Path(socket_path) if socket_path else server_socket_path("text2icon")
    if not spath.exists():
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout_sec)
            s.connect(str(spath))
            s.sendall((json.dumps(request) + "\n").encode())
            buf = b""
            while True:
                chunk = s.recv(8192)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        yield json.loads(text)
                    except json.JSONDecodeError:
                        continue
    except OSError:
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
    """Verifica se o vramd está ativo no socket canónico."""
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


def _discover_vramd_python() -> Path | None:
    """Procura ``Vramd/.venv/bin/python`` relativo ao monorepo / Shared.

    O venv canónico do supervisor é criado por ``./install.sh vramd`` (tool
    clified que instala o pacote ``vramd`` do PyPI).
    """
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


def ensure_vramd_running(*, timeout_sec: float = 30.0, auto_start: bool = True) -> bool:
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
    if is_vramd_running():
        return True

    if not auto_start:
        return False

    # Kill-switch via env var.
    if os.environ.get("VRAMD_AUTO_START", "1") == "0":
        return False

    # Tentar arrancar o vramd em background.
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
    log_path = Path(
        os.environ.get("VRAMD_AUTO_START_LOG", "").strip()
        or (Path.home() / ".cache" / "vramd" / "vramd-autostart.log")
    )
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
        subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT if log_fh is not subprocess.DEVNULL else subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,  # detach: sobrevive ao processo chamador
        )
        if log_fh is not subprocess.DEVNULL:
            _logger.info(f"[vramd] Auto-start log: {log_path}")
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

    _logger.warn(f"[vramd] Auto-start: timeout após {timeout_sec:.0f}s à espera do socket.")
    return False


def send_to_vramd(
    request: dict[str, Any],
    *,
    timeout_sec: float = 300.0,
    auto_start: bool = True,
) -> dict[str, Any] | None:
    """Envia um pedido ao vramd.

    Args:
        request: Payload JSON (deve incluir ``cmd``).
        timeout_sec: Timeout do socket.
        auto_start: Se ``True`` (default), arranca o vramd se estiver down.
            ``poll`` / ``wait`` / ``cancel`` / ``queue`` devem usar ``False`` —
            auto-start a meio de um job perde o ``job_id`` (vramd vazio novo).
    """
    if not ensure_vramd_running(auto_start=auto_start):
        return None
    return send_request(request, VRAMD_SOCKET, timeout_sec=timeout_sec)


def delegate_to_vramd(
    backend: str,
    request: dict[str, Any],
    *,
    timeout_sec: float = 600.0,
    priority: str | None = None,
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
        backend: Nome do backend (ex: ``text2icon``).
        request: Parâmetros do pedido (prompt, output, steps, ...).
        timeout_sec: Timeout para o pedido de geração.
        priority: ``interactive`` | ``batch`` (opcional).

    Returns:
        Dict de resposta (``{"status": "ok", "output": ...}``) ou ``None`` se o
        vramd não estiver ativo (nem pôde ser arrancado).
    """
    if not ensure_vramd_running():
        return None
    pri = resolve_vramd_priority(priority if priority is not None else request.get("priority"))
    req = {"cmd": "generate", "backend": backend, **request}
    req["priority"] = pri
    # send_request directo (não send_to_vramd): o ensure acima já garantiu o vramd —
    # evita repetir o probe pid-file + os.kill + connect a cada delegação.
    resp = send_request(req, VRAMD_SOCKET, timeout_sec=timeout_sec)
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


def submit_to_vramd(
    backend: str,
    request: dict[str, Any],
    *,
    timeout_sec: float = 30.0,
    priority: str | None = None,
) -> dict[str, Any] | None:
    """Enfileira um job no vramd (async). Retorna ``{"status":"ok","job_id":...}``."""
    if not ensure_vramd_running():
        return None
    pri = resolve_vramd_priority(priority if priority is not None else request.get("priority"))
    req = {"cmd": "submit", "backend": backend, "priority": pri, **request}
    req["priority"] = pri
    # send_request directo: ensure já feito acima (ver delegate_to_vramd).
    return send_request(req, VRAMD_SOCKET, timeout_sec=timeout_sec)


def poll_vramd_job(job_id: str, *, timeout_sec: float = 5.0) -> dict[str, Any] | None:
    """Consulta o estado de um job vramd."""
    return send_to_vramd({"cmd": "poll", "job_id": job_id}, timeout_sec=timeout_sec, auto_start=False)


def wait_vramd_job(
    job_id: str,
    *,
    timeout_sec: float = 600.0,
    stream: bool = False,
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
    return send_to_vramd(req, timeout_sec=timeout_sec + 30.0, auto_start=False)


def cancel_vramd_job(job_id: str, *, timeout_sec: float = 10.0) -> dict[str, Any] | None:
    """Pede cancelamento de um job vramd (queued imediato; running best-effort)."""
    return send_to_vramd({"cmd": "cancel", "job_id": job_id}, timeout_sec=timeout_sec, auto_start=False)


def cancel_vramd_all(
    *,
    queued_only: bool = False,
    timeout_sec: float = 30.0,
) -> dict[str, Any] | None:
    """Cancela todos os jobs vramd (``flush`` / ``cancel --all``)."""
    return send_to_vramd(
        {"cmd": "flush", "queued_only": queued_only},
        timeout_sec=timeout_sec,
        auto_start=False,
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

    Preferência: se o **vramd** estiver ativo, envia
    ``ensure-vram`` para evicção inteligente peso+LRU. Com ``backend``, o vramd
    usa ``max(needed_mib, peak=pesos+activação+safety)`` — não só o pedido cru.

    Args:
        needed_mib: VRAM necessária em MiB (mínimo pedido pelo cliente).
        timeout_sec: Tempo máximo de espera pelo release.
        backend: Nome do backend vramd (ex: ``text3d``) para reservar pico de inferência.
        quant_mode: Quantização assumida no pico (ex: ``sdnq-int4`` / ``none``).

    Returns:
        ``True`` se há VRAM suficiente (ou se não foi possível verificar).
    """
    from .gpu import query_gpu_free_mib

    free = query_gpu_free_mib()
    if free is not None and free >= needed_mib and backend is None:
        return True  # já há espaço, não incomodar ninguém

    # Preferir o vramd se ativo (evicção inteligente peso+LRU + peak por backend).
    if is_vramd_running():
        req: dict[str, Any] = {"cmd": "ensure-vram", "needed_mib": needed_mib}
        if backend:
            req["backend"] = backend
        if quant_mode:
            req["quant_mode"] = quant_mode
        _logger.info(
            f"VRAM insuficiente ({free} MiB livres, preciso {needed_mib}"
            + (f", backend={backend}" if backend else "")
            + ") — a pedir evicção ao vramd"
        )
        resp = send_to_vramd(req, timeout_sec=timeout_sec)
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
        self._obj_lock = threading.Lock()
        self._server_sock: socket.socket | None = None
        self._running = False
        self._last_activity = time.monotonic()
        self._requests_served = 0
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
        obj = self._ensure_loaded()
        try:
            with self._obj_lock:
                return self._generator(obj, request)
        except Exception as e:
            self._log(f"Erro na geração: {e}")
            # OOM? Descarregar para próxima tentativa recarregar
            with contextlib.suppress(Exception):
                unload = getattr(self._obj, "unload", None)
                if callable(unload):
                    unload()
                self._obj = None
            return {"status": "error", "error": str(e)}

    def _handle_client(self, conn: socket.socket) -> None:
        self._last_activity = time.monotonic()
        try:
            conn.settimeout(300.0)
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk

            line = data.decode("utf-8", errors="replace").strip()
            if not line:
                return

            request = json.loads(line)
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
            conn.close()

    def _cleanup(self) -> None:
        self._log("Cleanup...")
        with contextlib.suppress(Exception):
            if self._obj is not None:
                unload = getattr(self._obj, "unload", None)
                if callable(unload):
                    unload()
            self._obj = None
        with contextlib.suppress(OSError):
            self.socket_path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            self.ppid_path.unlink(missing_ok=True)
        if self._server_sock is not None:
            with contextlib.suppress(OSError):
                self._server_sock.close()

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

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.settimeout(1.0)
        try:
            self._server_sock.bind(str(self.socket_path))
        except OSError as e:
            _logger.error(f"Não foi possível bind ao socket {self.socket_path}: {e}")
            self._cleanup()
            raise
        self._server_sock.listen(8)

        # Registar signals só na main thread (signal.signal exige main thread).
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)

        self._running = True
        _logger.info(f"{self.tool_name} model server ativo em {self.socket_path} (PID {self._pid})")
        _logger.info(f"Idle timeout: {self.idle_timeout_sec / 60:.0f} min")

        try:
            while self._running:
                idle = time.monotonic() - self._last_activity
                if self._requests_served > 0 and idle > self.idle_timeout_sec:
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
