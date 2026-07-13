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
import signal
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .logging import Logger

_logger = Logger()

# Paths canónicos.
DEFAULT_SERVER_DIR = Path.home() / ".cache" / "gamedev"

# Socket canónico do Unified Model Server (UMS) — supervisor único de VRAM.
# Quando o UMS está ativo, todas as ferramentas delegam nele; este socket é o
# ponto de entrada para geração (cmd "generate" com "backend") e coordenação
# de VRAM (cmd "ensure-vram"). Ver ModelServer/ no monorepo.
UMS_SOCKET = DEFAULT_SERVER_DIR / "model-server.sock"

# Um socket por tool — evita misturar modelos diferentes.
_SOCKET_FOR_TOOL: dict[str, str] = {
    "text2icon": "text2icon-server.sock",
    "text2d": "text2d-server.sock",
    "texture2d": "texture2d-server.sock",
    "modelserver": "model-server.sock",
}

DEFAULT_IDLE_TIMEOUT_MIN = 30


def server_socket_path(tool: str) -> Path:
    """Path do socket para uma tool (ex: ``text2icon`` → ``text2icon-server.sock``)."""
    override = os.environ.get("GAMEDEV_MODEL_SERVER_SOCKET", "").strip()
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
            return json.loads(lines[-1])
    except (OSError, json.JSONDecodeError):
        return None


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
# Unified Model Server (UMS)
# ---------------------------------------------------------------------------


def is_ums_running() -> bool:
    """Verifica se o Unified Model Server está ativo no socket canónico."""
    return is_server_running(UMS_SOCKET)


def ensure_ums_running(*, timeout_sec: float = 30.0, auto_start: bool = True) -> bool:
    """Garante que o UMS está ativo. Arranca-o automaticamente se necessário.

    Quando uma tool chama ``delegate_to_ums``, esta função assegura que o UMS
    está a correr. Se não estiver, tenta arrancá-lo em background via
    ``gamedev-model-server start`` e espera até o socket ficar pronto.

    O auto-start pode ser desativado com a env var ``GAMEDEV_UMS_AUTO_START=0``.

    Args:
        timeout_sec: Tempo máximo de espera pelo arranque do UMS.
        auto_start: Se ``False``, não arranca o UMS (só verifica se está ativo).

    Returns:
        ``True`` se o UMS está ativo e pronto a receber pedidos.
    """
    if is_ums_running():
        return True

    if not auto_start:
        return False

    # Kill-switch via env var.
    if os.environ.get("GAMEDEV_UMS_AUTO_START", "1") == "0":
        return False

    # Tentar arrancar o UMS em background.
    import shutil
    import subprocess
    import sys

    # Resolver o binário: env var MODELSERVER_BIN → PATH → python -m modelserver.
    bin_path = os.environ.get("MODELSERVER_BIN", "").strip()
    if bin_path and Path(bin_path).exists():
        cmd = [bin_path, "start"]
    elif shutil.which("gamedev-model-server"):
        cmd = ["gamedev-model-server", "start"]
    else:
        # Fallback: python -m modelserver (requer modelserver instalado no venv).
        cmd = [sys.executable, "-m", "modelserver", "start"]

    _logger.info(f"[UMS] Auto-start: {' '.join(cmd)}")
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach: sobrevive ao processo chamador
        )
    except OSError as e:
        _logger.warn(f"[UMS] Auto-start falhou: {e}")
        return False

    # Esperar que o socket fique pronto.
    import time

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        time.sleep(0.5)
        if is_ums_running():
            _logger.info("[UMS] Auto-start: UMS ativo e pronto.")
            return True

    _logger.warn(f"[UMS] Auto-start: timeout após {timeout_sec:.0f}s à espera do socket.")
    return False


def send_to_ums(request: dict[str, Any], *, timeout_sec: float = 300.0) -> dict[str, Any] | None:
    """Envia um pedido ao UMS. Arranca o UMS automaticamente se necessário."""
    if not ensure_ums_running():
        return None
    return send_request(request, UMS_SOCKET, timeout_sec=timeout_sec)


def delegate_to_ums(
    backend: str,
    request: dict[str, Any],
    *,
    timeout_sec: float = 600.0,
) -> dict[str, Any] | None:
    """Delega um pedido de geração ao UMS, arrancando-o automaticamente se necessário.

    Helper para CLIs das tools: no início do comando ``generate``, chamar esta
    função. Se retornar um dict com ``status == "ok"``, a geração foi feita pelo
    UMS (usar ``result["output"]``). Se retornar ``None``, o UMS não está ativo —
    fazer fallback in-process.

    Se o UMS não estiver ativo, ``ensure_ums_running`` arranca-o automaticamente
    em background (a menos que ``GAMEDEV_UMS_AUTO_START=0``).

    Args:
        backend: Nome do backend (ex: ``text2icon``).
        request: Parâmetros do pedido (prompt, output, steps, ...).
        timeout_sec: Timeout para o pedido de geração.

    Returns:
        Dict de resposta (``{"status": "ok", "output": ...}``) ou ``None`` se o
        UMS não estiver ativo (nem pôde ser arrancado).
    """
    if not ensure_ums_running():
        return None
    req = {"cmd": "generate", "backend": backend, **request}
    return send_to_ums(req, timeout_sec=timeout_sec)


# ---------------------------------------------------------------------------
# Coordenação de VRAM
# ---------------------------------------------------------------------------


def ensure_vram_available(needed_mib: int, *, timeout_sec: float = 30.0) -> bool:
    """Garante que há VRAM suficiente; se não, pede aos servers para descarregar.

    Chamaado por ferramentas pesadas (text3d, paint3d) antes de ocupar a GPU.
    Se houver servers ativos a segurar VRAM, pede-lhes ``release`` gracioso e
    espera até haver espaço (ou timeout).

    Preferência: se o **Unified Model Server** (UMS) estiver ativo, envia
    ``ensure-vram`` para evicção inteligente peso+LRU. Caso contrário, cai no
    comportamento legacy (descobrir per-tool sockets, ``release`` cego).

    Args:
        needed_mib: VRAM necessária em MiB.
        timeout_sec: Tempo máximo de espera pelo release.

    Returns:
        ``True`` se há VRAM suficiente (ou se não foi possível verificar).
    """
    from .gpu import query_gpu_free_mib

    free = query_gpu_free_mib()
    if free is not None and free >= needed_mib:
        return True  # já há espaço, não incomodar ninguém

    # Preferir o UMS se ativo (evicção inteligente peso+LRU).
    if is_ums_running():
        _logger.info(f"VRAM insuficiente ({free} MiB livres, preciso {needed_mib}) — a pedir evicção ao UMS")
        resp = send_to_ums({"cmd": "ensure-vram", "needed_mib": needed_mib}, timeout_sec=timeout_sec)
        if resp is not None:
            ok = resp.get("status") == "ok"
            if not ok:
                _logger.warn(f"UMS respondeu {resp.get('status')} ao ensure-vram")
            return ok
        # UMS estava running mas não respondeu — cair para legacy.

    # Legacy: pedir a todos os per-tool servers ativos para fazer release.
    active = discover_active_sockets()
    if active:
        msg = f"VRAM insuficiente ({free} MiB livres, preciso {needed_mib}) — a pedir release a {len(active)} server(s)"
        _logger.info(msg)
        for sock in active:
            with contextlib.suppress(Exception):
                request_release(sock)
    elif free is None:
        return True  # não dá para verificar (sem nvidia-smi); deixar tentar

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
