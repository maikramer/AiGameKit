"""Testes do WorkerPool — inflight, cancel, progresso, erros."""

from __future__ import annotations

import threading
import time
from typing import Any

from modelserver import protocol as P
from modelserver.job_queue import JobQueue
from modelserver.scheduler import AffinityScheduler
from modelserver.worker import WorkerPool


class _FakeManager:
    """BackendManager mínimo para o worker (sem GPU)."""

    def __init__(self, *, delay: float = 0.0, raise_exc: bool = False, track_progress: bool = False) -> None:
        self.delay = delay
        self.raise_exc = raise_exc
        self.track_progress = track_progress
        self.generate_calls: list[tuple[str, dict[str, Any]]] = []
        self._loaded: set[str] = set()

    def loaded_names(self) -> list[str]:
        return sorted(self._loaded)

    def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
        self.generate_calls.append((name, request))
        self._loaded.add(name)
        progress = request.get("_progress")
        if callable(progress) and self.track_progress:
            progress(0.25, "quarter")
        if self.delay:
            time.sleep(self.delay)
        if self.raise_exc:
            raise RuntimeError("boom")
        if callable(progress) and self.track_progress:
            progress(1.0, "done")
        return {"status": P.STATUS_OK, "output": f"/tmp/{name}.png"}


class TestWorkerPoolLifecycle:
    def test_max_inflight_clamped_to_one(self) -> None:
        q = JobQueue(max_depth=8)
        pool = WorkerPool(q, _FakeManager(), max_inflight=0)
        assert pool.max_inflight == 1

    def test_start_spawns_max_inflight_threads(self) -> None:
        q = JobQueue(max_depth=8)
        pool = WorkerPool(q, _FakeManager(), max_inflight=2)
        pool.start()
        try:
            assert len(pool._threads) == 2
            assert all(t.is_alive() for t in pool._threads)
        finally:
            pool.stop()

    def test_start_idempotent(self) -> None:
        q = JobQueue(max_depth=8)
        pool = WorkerPool(q, _FakeManager(), max_inflight=1)
        pool.start()
        pool.start()
        try:
            assert len(pool._threads) == 1
        finally:
            pool.stop()


class TestWorkerPoolRun:
    def test_processes_job_to_done(self) -> None:
        q = JobQueue(max_depth=8)
        mgr = _FakeManager()
        pool = WorkerPool(q, mgr, AffinityScheduler(), max_inflight=1)
        pool.start()
        try:
            job = q.enqueue("alpha", {"prompt": "x"})
            assert job.done_event.wait(timeout=3.0)
            assert job.state == P.JOB_DONE
            assert job.result is not None
            assert job.result["status"] == P.STATUS_OK
            assert len(mgr.generate_calls) == 1
        finally:
            pool.stop()

    def test_cancel_before_start_finishes_cancelled(self) -> None:
        q = JobQueue(max_depth=8)
        pool = WorkerPool(q, _FakeManager(delay=0.5), max_inflight=1)
        # Não arrancar o pool — chamar _run_job directamente após take.
        job = q.enqueue("alpha", {})
        taken = q.take(job.job_id)
        assert taken is not None
        taken.cancel_requested = True
        pool._run_job(taken)
        assert taken.state == P.JOB_CANCELLED
        assert q.inflight == 0

    def test_cancel_during_run_overrides_ok(self) -> None:
        q = JobQueue(max_depth=8)
        mgr = _FakeManager(delay=0.35)
        pool = WorkerPool(q, mgr, max_inflight=1)
        pool.start()
        try:
            job = q.enqueue("alpha", {})
            # Esperar que entre em running.
            deadline = time.monotonic() + 2.0
            while job.state != P.JOB_RUNNING and time.monotonic() < deadline:
                time.sleep(0.02)
            assert job.state == P.JOB_RUNNING
            q.cancel(job.job_id)
            assert job.done_event.wait(timeout=3.0)
            assert job.state == P.JOB_CANCELLED
            assert job.result is not None
            assert "cancel" in job.result.get("error", "").lower()
        finally:
            pool.stop()

    def test_generate_exception_marks_failed(self) -> None:
        q = JobQueue(max_depth=8)
        pool = WorkerPool(q, _FakeManager(raise_exc=True), max_inflight=1)
        pool.start()
        try:
            job = q.enqueue("alpha", {})
            assert job.done_event.wait(timeout=3.0)
            assert job.state == P.JOB_FAILED
            assert job.result is not None
            assert "boom" in job.result["error"]
            assert q.inflight == 0
        finally:
            pool.stop()

    def test_progress_callback_injected(self) -> None:
        q = JobQueue(max_depth=8)
        mgr = _FakeManager(track_progress=True)
        pool = WorkerPool(q, mgr, max_inflight=1)
        events: list[dict[str, Any]] = []
        pool.start()
        try:
            job = q.enqueue("alpha", {})
            job.add_listener(events.append)
            assert job.done_event.wait(timeout=3.0)
            assert any(e.get("event") == P.EVENT_PROGRESS for e in events)
            assert job.progress_pct == 1.0
            # Request passado ao manager tinha _progress.
            assert "_progress" in mgr.generate_calls[0][1]
        finally:
            pool.stop()

    def test_two_workers_claim_distinct_jobs(self) -> None:
        q = JobQueue(max_depth=8)
        barrier = threading.Barrier(2)
        calls_lock = threading.Lock()
        concurrent: list[str] = []

        class _BarrierManager(_FakeManager):
            def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
                with calls_lock:
                    concurrent.append(name)
                barrier.wait(timeout=3.0)
                return super().generate(name, request)

        mgr = _BarrierManager(delay=0.05)
        # Marcar ambos "loaded" para o scheduler não reordenar por afinidade.
        mgr._loaded = {"alpha", "beta"}
        pool = WorkerPool(q, mgr, max_inflight=2)
        pool.start()
        try:
            j1 = q.enqueue("alpha", {})
            j2 = q.enqueue("beta", {})
            assert j1.done_event.wait(timeout=5.0)
            assert j2.done_event.wait(timeout=5.0)
            assert set(concurrent) == {"alpha", "beta"}
            assert j1.state == P.JOB_DONE
            assert j2.state == P.JOB_DONE
        finally:
            pool.stop()
