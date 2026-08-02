"""IdleEvictor — thread de background que liberta VRAM de backends idle.

Três níveis, do mais leve ao mais agressivo:

1. ``unload`` de pesos após ``idle_timeout_sec`` (default 120s). Liberta a maior
   parte da VRAM mantendo o worker vivo — recarregar é mais rápido que respawn.
2. ``shutdown`` do subprocesso worker após ``worker_shutdown_sec`` (default
   300s). Necessário porque o ``unload`` deixa o contexto CUDA do processo
   (~0.3-1 GiB) na GPU; só a morte do processo o devolve.
3. Health-check ``ping``/``pong`` aos workers vivos (default a cada 60s): um
   worker wedged deixa de responder mas continua a segurar VRAM — mata-se e o
   próximo job faz respawn.

O ``idle_timeout`` do servidor inteiro (``DEFAULT_IDLE_TIMEOUT_MIN``) é
complementar: encerra o processo supervisor; este evictor é granular.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from aigamekit_shared.logging import Logger

_logger = Logger()


class IdleEvictor:
    """Thread que evicta backends idle do BackendManager.

    Args:
        manager: BackendManager cujos backends serão inspecionados.
        idle_timeout_sec: Segundos de idle antes de descarregar pesos.
        check_interval_sec: Intervalo entre verificações.
        worker_shutdown_sec: Segundos de idle antes de terminar o subprocesso
            worker (``0`` desliga). Deve ser ``>= idle_timeout_sec``.
        health_check_sec: Intervalo do ping aos workers vivos (``0`` desliga).
        daemon: Se True, a thread morre quando o processo principal sai.
    """

    def __init__(
        self,
        manager: Any,
        *,
        idle_timeout_sec: float = 120.0,
        check_interval_sec: float = 15.0,
        worker_shutdown_sec: float = 300.0,
        health_check_sec: float = 60.0,
        daemon: bool = True,
        queue: Any = None,
    ) -> None:
        self._manager = manager
        self._idle_timeout_sec = idle_timeout_sec
        self._check_interval_sec = check_interval_sec
        self._worker_shutdown_sec = worker_shutdown_sec
        self._health_check_sec = health_check_sec
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._daemon = daemon
        self._last_health_check = 0.0
        # Queue opcional: o health-check salta quando há jobs a correr, para
        # não matar um worker no gap take()→ensure_loaded (ref_count ainda 0)
        # — bug M5.
        self._queue = queue

    @property
    def idle_timeout_sec(self) -> float:
        return self._idle_timeout_sec

    @idle_timeout_sec.setter
    def idle_timeout_sec(self, value: float) -> None:
        self._idle_timeout_sec = value

    @property
    def worker_shutdown_sec(self) -> float:
        return self._worker_shutdown_sec

    def start(self) -> None:
        """Arranca a thread de background (idempotente)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=self._daemon, name="ums-idle-evictor")
        self._thread.start()
        _logger.info(
            f"[UMS] IdleEvictor ativo (unload={self._idle_timeout_sec:.0f}s, "
            f"worker_shutdown={self._worker_shutdown_sec:.0f}s, "
            f"health={self._health_check_sec:.0f}s, interval={self._check_interval_sec:.0f}s)"
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
            try:
                self._shutdown_idle_workers()
            except Exception as e:
                _logger.warn(f"[UMS] IdleEvictor shutdown de workers falhou: {e}")
            try:
                self._health_check()
            except Exception as e:
                _logger.warn(f"[UMS] IdleEvictor health-check falhou: {e}")

    def _check_and_evict(self) -> None:
        """Verifica backends carregados e evicta os idle há demasiado tempo."""
        now = time.monotonic()
        manager = self._manager
        # API pública do manager — mode-agnostic (in-process e subprocesso).
        candidates = manager.idle_candidates(self._idle_timeout_sec)
        for name, last_used in candidates:
            idle_sec = now - last_used
            _logger.info(f"[UMS] IdleEvictor: backend {name!r} idle há {idle_sec:.0f}s — a evictar.")
            # ``manager.evict`` já chama ``_clear_cache`` (e ``scrub_dead_vram``
            # quando nada fica loaded) — não duplicar o toque em CUDA/NVML.
            manager.evict(name)

    def _shutdown_idle_workers(self) -> None:
        """Termina subprocessos worker idle — devolve o contexto CUDA à GPU."""
        if self._worker_shutdown_sec <= 0:
            return
        manager = self._manager
        idle_workers = getattr(manager, "idle_worker_candidates", None)
        if idle_workers is None:
            return
        now = time.monotonic()
        for name, last_used in idle_workers(self._worker_shutdown_sec):
            idle_sec = now - last_used if last_used else self._worker_shutdown_sec
            _logger.info(f"[UMS] IdleEvictor: worker {name!r} idle há {idle_sec:.0f}s — a terminar subprocesso.")
            manager.shutdown_worker(name)

    def _health_check(self) -> None:
        """Ping aos workers vivos; mata os que não respondem."""
        if self._health_check_sec <= 0:
            return
        now = time.monotonic()
        if now - self._last_health_check < self._health_check_sec:
            return
        # Com jobs a correr, há um worker prestes a receber (ou a processar)
        # um job no gap take()→ensure_loaded onde ref_count ainda é 0 — skip
        # para não o matar (bug M5).
        if self._queue is not None:
            try:
                if self._queue.is_busy():
                    return
            except Exception:
                pass
        self._last_health_check = now
        check = getattr(self._manager, "health_check_workers", None)
        if check is None:
            return
        for entry in check() or []:
            if not entry.get("ok"):
                _logger.warn(
                    f"[UMS] health-check: worker {entry.get('backend')!r} sem pong — terminado "
                    f"(respawn no próximo job)."
                )
