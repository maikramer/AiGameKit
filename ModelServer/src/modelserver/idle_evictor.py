"""IdleEvictor — thread de background que descarrega backends idle.

Corre periodicamente (default a cada 60s) e evicta backends que estão carregados
mas sem atividade há mais de ``idle_timeout_sec`` (default 600s = 10 min).

Isto liberta VRAM para outros processos GPU mesmo quando o UMS não está sob
pressão de VRAM. O BackendManager continua a poder recarregar o backend a pedido
(cold start) — a diferença é que a VRAM fica livre para outros usos entre pedidos.

Compatível com o idle_timeout do UMS: o idle_timeout do servidor inteiro (default
30 min) encerra o processo; o idle_evictor é mais granular — evicta backends
individuais sem parar o servidor.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from gamedev_shared.logging import Logger

_logger = Logger()


class IdleEvictor:
    """Thread que evicta backends idle do BackendManager.

    Args:
        manager: BackendManager cujos backends serão inspecionados.
        idle_timeout_sec: Segundos de idle antes de evictar (default 600 = 10 min).
        check_interval_sec: Intervalo entre verificações (default 60s).
        daemon: Se True, a thread morre quando o processo principal sai.
    """

    def __init__(
        self,
        manager: Any,
        *,
        idle_timeout_sec: float = 600.0,
        check_interval_sec: float = 60.0,
        daemon: bool = True,
    ) -> None:
        self._manager = manager
        self._idle_timeout_sec = idle_timeout_sec
        self._check_interval_sec = check_interval_sec
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._daemon = daemon

    @property
    def idle_timeout_sec(self) -> float:
        return self._idle_timeout_sec

    @idle_timeout_sec.setter
    def idle_timeout_sec(self, value: float) -> None:
        self._idle_timeout_sec = value

    def start(self) -> None:
        """Arranca a thread de background (idempotente)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=self._daemon, name="ums-idle-evictor")
        self._thread.start()
        _logger.info(
            f"[UMS] IdleEvictor ativo (timeout={self._idle_timeout_sec:.0f}s, interval={self._check_interval_sec:.0f}s)"
        )

    def stop(self) -> None:
        """Pára a thread (graceful)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _run(self) -> None:
        """Loop principal — corre até stop() ser chamado."""
        while not self._stop_event.is_set():
            # Esperar pelo intervalo (interrompível por stop()).
            if self._stop_event.wait(self._check_interval_sec):
                break
            try:
                self._check_and_evict()
            except Exception as e:
                _logger.warn(f"[UMS] IdleEvictor erro: {e}")

    def _check_and_evict(self) -> None:
        """Verifica backends carregados e evicta os idle há demasiado tempo."""
        now = time.monotonic()
        manager = self._manager

        # Snapshot dos backends carregados com last_used (thread-safe).
        with manager._struct_lock:
            candidates: list[tuple[str, float, int]] = []  # (name, last_used, ref_count)
            for name, state in manager._states.items():
                if state.model is not None and state.ref_count == 0 and state.last_used > 0:
                    idle_sec = now - state.last_used
                    if idle_sec >= self._idle_timeout_sec:
                        candidates.append((name, state.last_used, state.ref_count))

        for name, last_used, _ref_count in candidates:
            idle_sec = now - last_used
            _logger.info(f"[UMS] IdleEvictor: backend {name!r} idle há {idle_sec:.0f}s — a evictar.")
            evicted = manager.evict(name)
            if evicted:
                # clear_cache após evict para libertar VRAM imediatamente.
                manager._clear_cache()
