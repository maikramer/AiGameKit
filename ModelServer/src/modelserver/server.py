"""UnifiedModelServer — servidor único que roteia pedidos para backends.

Um único processo detém toda a VRAM e escuta num único Unix socket. Pedidos de
geração incluem ``backend`` (nome da tool) e são roteados para o BackendManager,
que carrega/evicta modelos sob procura com evicção inteligente peso+LRU.

Protocolo: ver ``protocol.py``. Ciclo de vida: ver ``serve_forever``.

Retrocompatibilidade: o socket único (``model-server.sock``) é descoberto por
``gamedev_shared.model_server.discover_active_sockets`` como qualquer per-tool
legacy server. ``ensure_vram_available`` envia ``ensure-vram`` ao UMS quando
disponível, caindo para o comportamento legacy caso contrário.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Any

from gamedev_shared.logging import Logger
from gamedev_shared.model_server import _ensure_server_dir, _pid_path, is_server_running

from . import protocol as P
from .backend_manager import BackendManager
from .registry import Registry

_logger = Logger()


class UnifiedModelServer:
    """Servidor único de VRAM — roteia pedidos para backends via BackendManager.

    Args:
        registry: Registry de backends. Se ``None``, carrega do ``backends.yaml`` default.
        socket_path: Path do socket. Se ``None``, usa ``DEFAULT_SOCKET_PATH``.
        idle_timeout_min: Minutos de idle (sem pedidos) antes de self-shutdown.
        verbose: Logs detalhados.
    """

    def __init__(
        self,
        registry: Registry | None = None,
        *,
        socket_path: Path | str | None = None,
        idle_timeout_min: int = P.DEFAULT_IDLE_TIMEOUT_MIN,
        idle_evict_sec: float = 600.0,
        verbose: bool = False,
    ) -> None:
        self.registry = registry if registry is not None else Registry()
        self.manager = BackendManager(self.registry)
        self.socket_path = Path(socket_path) if socket_path else P.DEFAULT_SOCKET_PATH
        self.ppid_path = _pid_path(self.socket_path)
        self.idle_timeout_sec = idle_timeout_min * 60
        self.verbose = verbose

        # IdleEvictor: descarrega backends individuais idle há > idle_evict_sec.
        # Mais granular que o idle_timeout do servidor inteiro (que encerra o processo).
        from .idle_evictor import IdleEvictor

        self.idle_evictor = IdleEvictor(self.manager, idle_timeout_sec=idle_evict_sec)

        self._server_sock: socket.socket | None = None
        self._running = False
        self._last_activity = time.monotonic()
        self._requests_served = 0
        self._pid = os.getpid()

    def _log(self, msg: str) -> None:
        if self.verbose:
            _logger.info(f"[UMS] {msg}")

    # ------------------------------------------------------------------
    # Despacho de comandos
    # ------------------------------------------------------------------

    def _resolve_backend(self, request: dict[str, Any]) -> str | None:
        """Resolve o nome do backend a partir do request.

        Ordem: ``backend`` explícito → ``tool`` field → único backend carregado.
        Retorna ``None`` se não for possível determinar.
        """
        backend = request.get("backend")
        if backend:
            return str(backend)
        tool = request.get("tool")
        if tool:
            return str(tool)
        # Fallback: se só há 1 backend carregado, usar esse.
        loaded = self.manager.loaded_names()
        if len(loaded) == 1:
            return loaded[0]
        return None

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Despacha um request já parseado. Retorna a resposta (dict)."""
        cmd = request.get("cmd", P.DEFAULT_CMD)
        self._log(f"Comando: {cmd}")

        if cmd == P.CMD_SHUTDOWN:
            self._running = False
            return {"status": P.STATUS_OK, "message": "shutting down"}

        if cmd == P.CMD_STATUS:
            mgr_status = self.manager.status()
            return {
                "status": P.STATUS_STATUS,
                "pid": self._pid,
                "socket": str(self.socket_path),
                "tool": "modelserver",
                "requests_served": self._requests_served,
                **mgr_status,
            }

        if cmd == P.CMD_LIST_BACKENDS:
            return {
                "status": P.STATUS_OK,
                "backends": [
                    {
                        "name": desc.name,
                        "vram_mib": desc.vram_mib,
                        "priority": desc.priority,
                        "loaded": self.manager.is_loaded(desc.name),
                    }
                    for desc in self.registry
                ],
            }

        if cmd == P.CMD_STATS:
            stats = self.manager.stats.get_all()
            return {
                "status": P.STATUS_OK,
                "pid": self._pid,
                "requests_served": self._requests_served,
                "idle_evict_timeout_sec": self.idle_evictor.idle_timeout_sec,
                "backends": stats,
            }

        if cmd == P.CMD_RELEASE:
            backend = request.get("backend")
            if backend:
                evicted = self.manager.evict(str(backend))
                return {
                    "status": P.STATUS_OK if evicted else P.STATUS_ERROR,
                    "message": f"backend {backend} {'evicted' if evicted else 'não estava carregado'}",
                }
            count = self.manager.evict_all()
            return {"status": P.STATUS_OK, "message": f"{count} backend(s) evicted"}

        if cmd == P.CMD_PRELOAD:
            backend = request.get("backend")
            if not backend:
                return {"status": P.STATUS_ERROR, "error": "preload requer 'backend'"}
            name = str(backend)
            if not self.registry.has(name):
                return {"status": P.STATUS_ERROR, "error": f"backend desconhecido: {name}"}
            try:
                load_kwargs = {k: v for k, v in request.items() if k in ("verbose",)}
                self.manager.ensure_loaded(name, **load_kwargs)
                return {"status": P.STATUS_OK, "message": f"backend {name} pré-carregado"}
            except Exception as e:
                return {"status": P.STATUS_ERROR, "error": f"falha ao pré-carregar {name}: {e}"}

        if cmd == P.CMD_ENSURE_VRAM:
            needed = request.get("needed_mib")
            if needed is None:
                return {"status": P.STATUS_ERROR, "error": "ensure-vram requer 'needed_mib'"}
            ok = self.manager.ensure_vram(int(needed))
            return {"status": P.STATUS_OK if ok else P.STATUS_ERROR, "needed_mib": int(needed)}

        if cmd == P.CMD_GENERATE:
            backend = self._resolve_backend(request)
            if backend is None:
                loaded = self.manager.loaded_names()
                hint = (
                    f"backends carregados: {loaded}. Especifica 'backend' no request ou pré-carrega exatamente um."
                    if loaded
                    else "Nenhum backend carregado. Especifica 'backend' no request."
                )
                return {"status": P.STATUS_ERROR, "error": f"backend ambíguo. {hint}"}
            if not self.registry.has(backend):
                return {"status": P.STATUS_ERROR, "error": f"backend desconhecido: {backend}"}
            self._log(f"Gerar via backend {backend!r}")
            return self.manager.generate(backend, request)

        return {"status": P.STATUS_ERROR, "error": f"comando desconhecido: {cmd}"}

    # ------------------------------------------------------------------
    # Handle de 1 ligação
    # ------------------------------------------------------------------

    def _handle_client(self, conn: socket.socket) -> None:
        self._last_activity = time.monotonic()
        try:
            conn.settimeout(P.DEFAULT_GENERATE_TIMEOUT_SEC)
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(8192)
                if not chunk:
                    break
                data += chunk

            line = data.decode("utf-8", errors="replace").strip()
            if not line:
                return

            request = json.loads(line)
            response = self._dispatch(request)

            if response.get("status") == P.STATUS_OK and request.get("cmd", P.DEFAULT_CMD) == P.CMD_GENERATE:
                self._requests_served += 1

            conn.sendall((json.dumps(response) + "\n").encode())

        except json.JSONDecodeError as e:
            with contextlib.suppress(OSError):
                conn.sendall((json.dumps({"status": P.STATUS_ERROR, "error": f"JSON inválido: {e}"}) + "\n").encode())
        except Exception as e:
            self._log(f"Erro ao processar cliente: {e}")
            with contextlib.suppress(OSError):
                conn.sendall((json.dumps({"status": P.STATUS_ERROR, "error": str(e)}) + "\n").encode())
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _cleanup(self) -> None:
        self._log("Cleanup...")
        with contextlib.suppress(Exception):
            self.idle_evictor.stop()
        with contextlib.suppress(Exception):
            self.manager.evict_all()
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
        """Arranca o UMS (bloqueante). Graceful shutdown via SIGTERM/SIGINT."""
        _ensure_server_dir()

        # Cleanup de socket stale.
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

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)

        self._running = True
        _logger.info(f"Unified Model Server ativo em {self.socket_path} (PID {self._pid})")
        _logger.info(f"Backends registados: {', '.join(self.registry.names)}")
        _logger.info(f"Idle timeout: {self.idle_timeout_sec / 60:.0f} min")
        _logger.info(f"Idle evictor: backends descarregados após {self.idle_evictor.idle_timeout_sec:.0f}s sem uso")

        # Arrancar IdleEvictor (thread de background para evicção proativa).
        self.idle_evictor.start()

        try:
            while self._running:
                idle = time.monotonic() - self._last_activity
                if self.idle_timeout_sec > 0 and self._requests_served > 0 and idle > self.idle_timeout_sec:
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
            _logger.info("UMS encerrado.")
