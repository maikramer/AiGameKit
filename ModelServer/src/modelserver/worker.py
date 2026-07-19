"""WorkerPool — despacha jobs da fila com ``MAX_INFLIGHT`` gerações paralelas.

Default ``MAX_INFLIGHT=1``: uma geração de cada vez na GPU. Com ``MAX_INFLIGHT>1``,
só arranca jobs em paralelo se a VRAM livre couber o footprint do candidato.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from gamedev_shared.diffusion_control import GenerationAborted
from gamedev_shared.logging import Logger

from . import protocol as P
from .backend_manager import BackendManager
from .job_queue import Job, JobQueue
from .scheduler import AffinityScheduler

_logger = Logger()


class WorkerPool:
    """Pool de workers que puxam jobs da ``JobQueue`` via ``AffinityScheduler``."""

    def __init__(
        self,
        queue: JobQueue,
        manager: BackendManager,
        scheduler: AffinityScheduler | None = None,
        *,
        max_inflight: int = P.MAX_INFLIGHT,
        verbose: bool = False,
        query_free_mib: Callable[[], int | None] | None = None,
    ) -> None:
        self.queue = queue
        self.manager = manager
        self.scheduler = scheduler if scheduler is not None else AffinityScheduler()
        self.max_inflight = max(1, max_inflight)
        self.verbose = verbose
        self._query_free_mib = query_free_mib
        self._threads: list[threading.Thread] = []
        self._running = False
        self._stop = threading.Event()
        self._affinity_hits = 0

    def _log(self, msg: str) -> None:
        if self.verbose:
            _logger.info(f"[UMS-worker] {msg}")

    def _free_mib(self) -> int | None:
        if self._query_free_mib is not None:
            return self._query_free_mib()
        try:
            from gamedev_shared.gpu import query_gpu_free_mib

            return query_gpu_free_mib()
        except Exception:
            return None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop.clear()
        for i in range(self.max_inflight):
            t = threading.Thread(target=self._loop, name=f"ums-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        self._log(f"Arrancados {self.max_inflight} worker(s)")

    def stop(self) -> None:
        self._running = False
        self._stop.set()
        self.queue.notify()
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads.clear()

    def _loop(self) -> None:
        while self._running and not self._stop.is_set():
            if not self.queue.wait_for_work(timeout=0.5):
                continue
            job = self._claim_next()
            if job is None:
                continue
            self._run_job(job)

    def _backend_peak_mib(self, name: str, request: dict[str, Any] | None = None) -> int:
        """Pico pesos+activação+safety (não o YAML estático)."""
        try:
            quant, mem_eff, group_off = self.manager.resolve_peak_params(name, request or {})
            return int(
                self.manager.peak_vram_mib(name, quant_mode=quant, memory_efficient=mem_eff, group_offload=group_off)
            )
        except Exception:
            try:
                return int(self.manager._registry.descriptor(name).vram_mib)
            except Exception:
                return 0

    def _fits_parallel(self, job: Job) -> bool:
        """Com already-inflight>0, só permite se VRAM livre couber o pico/headroom."""
        if self.queue.inflight <= 0:
            return True
        if self.max_inflight <= 1:
            return False

        loaded = set(self.manager.loaded_names())
        free = self._free_mib()

        if job.backend in loaded:
            # Backend quente — headroom de activação de inferência (+ safety).
            self._affinity_hits += 1
            if free is None:
                return True
            try:
                needed = self.manager.activation_headroom_mib(job.backend)
            except Exception:
                needed = 512
            return free >= needed

        if free is None:
            # Sem nvidia-smi: não arriscar segundo cold load.
            return False
        return free >= self._backend_peak_mib(job.backend, job.request)

    def _claim_next(self) -> Job | None:
        jobs = self.queue.queued_jobs()
        if not jobs:
            return None
        # Já no máximo efectivo de threads; cada thread só pega se cabe.
        if self.queue.inflight >= self.max_inflight:
            return None
        picked = self.scheduler.pick_next(jobs, self.manager.loaded_names())
        if picked is None:
            return None
        if not self._fits_parallel(picked):
            self._log(
                f"Skip parallel job {picked.job_id[:8]} backend={picked.backend!r} "
                f"(VRAM insuficiente ou free desconhecido; inflight={self.queue.inflight})"
            )
            return None
        return self.queue.take(picked.job_id)

    def _run_job(self, job: Job) -> None:
        if job.cancel_requested:
            self.queue.finish(
                job,
                {
                    "status": P.STATUS_ERROR,
                    "error": "cancelled before start",
                    "error_code": P.ERR_CANCELLED,
                },
            )
            return

        job.mark_started()
        self._log(f"A correr job {job.job_id[:8]} backend={job.backend!r} cuts={job.affinity_cuts} pri={job.priority}")

        def on_progress(pct: float | None = None, msg: str | None = None) -> None:
            if job.cancel_requested:
                return
            job.report_progress(pct, msg)

        def should_abort() -> bool:
            return bool(job.cancel_requested)

        try:
            req = dict(job.request)
            req["_progress"] = on_progress
            req["_abort"] = should_abort
            if job.cancel_requested:
                result: dict[str, Any] = {
                    "status": P.STATUS_ERROR,
                    "error": "cancelled before generate",
                    "error_code": P.ERR_CANCELLED,
                }
            else:
                on_progress(0.0, "started")
                result = self.manager.generate(job.backend, req)
                if job.cancel_requested and result.get("status") == P.STATUS_OK:
                    result = {
                        "status": P.STATUS_ERROR,
                        "error": "cancelled during run",
                        "error_code": P.ERR_CANCELLED,
                    }
                elif result.get("status") != P.STATUS_OK:
                    result.setdefault("error_code", P.ERR_GENERATE_FAILED)
        except GenerationAborted:
            result = {
                "status": P.STATUS_ERROR,
                "error": "cancelled during diffusion",
                "error_code": P.ERR_CANCELLED,
            }
        except Exception as e:
            _logger.warn(f"[UMS-worker] job {job.job_id[:8]} falhou: {e}")
            result = {
                "status": P.STATUS_ERROR,
                "error": str(e),
                "error_code": P.ERR_GENERATE_FAILED,
            }

        self.queue.finish(job, result)


# Tipo auxiliar para testes / DI.
GenerateFn = Callable[[str, dict[str, Any]], dict[str, Any]]
