"""WorkerPool — despacha jobs da fila com ``MAX_INFLIGHT`` gerações paralelas.

Default ``MAX_INFLIGHT=1``: uma geração de cada vez na GPU (evita dois backends
pesados em paralelo). O ``AffinityScheduler`` escolhe *qual* job tirar da fila.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

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
    ) -> None:
        self.queue = queue
        self.manager = manager
        self.scheduler = scheduler if scheduler is not None else AffinityScheduler()
        self.max_inflight = max(1, max_inflight)
        self.verbose = verbose
        self._threads: list[threading.Thread] = []
        self._running = False
        self._stop = threading.Event()

    def _log(self, msg: str) -> None:
        if self.verbose:
            _logger.info(f"[UMS-worker] {msg}")

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

    def _claim_next(self) -> Job | None:
        jobs = self.queue.queued_jobs()
        if not jobs:
            return None
        picked = self.scheduler.pick_next(jobs, self.manager.loaded_names())
        if picked is None:
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
        self._log(
            f"A correr job {job.job_id[:8]} backend={job.backend!r} "
            f"cuts={job.affinity_cuts} pri={job.priority}"
        )

        def on_progress(pct: float | None = None, msg: str | None = None) -> None:
            if job.cancel_requested:
                return
            job.report_progress(pct, msg)

        try:
            req = dict(job.request)
            req["_progress"] = on_progress
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
