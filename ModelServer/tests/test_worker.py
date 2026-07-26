"""Testes do WorkerPool — inflight, cancel, progresso, erros."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
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
        self.evict_calls: list[str] = []
        self._loaded: set[str] = set()

    def loaded_names(self) -> list[str]:
        return sorted(self._loaded)

    def evict(self, name: str) -> bool:
        self.evict_calls.append(name)
        self._loaded.discard(name)
        return True

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

    def test_vram_insufficient_requeues_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(P, "MAX_VRAM_RETRIES", 4)
        monkeypatch.setattr(P, "VRAM_RETRY_BASE_SEC", 0.05)
        monkeypatch.setattr(P, "VRAM_RETRY_MAX_SEC", 0.1)

        class _VramThenOk(_FakeManager):
            def __init__(self) -> None:
                super().__init__()
                self.evict_calls = 0

            def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
                self.generate_calls.append((name, request))
                if len(self.generate_calls) < 3:
                    return {
                        "status": P.STATUS_ERROR,
                        "error": "VRAM insuficiente para 'alpha'",
                        "error_code": P.ERR_VRAM_INSUFFICIENT,
                        "peak_mib": 5000,
                        "free_mib": 4000,
                    }
                return {"status": P.STATUS_OK, "output": f"/tmp/{name}.png"}

            def evict_all(self) -> int:
                self.evict_calls += 1
                return 0

            def _clear_cache(self) -> None:
                return None

        q = JobQueue(max_depth=8)
        mgr = _VramThenOk()
        # Free desconhecido (como CI sem NVML) → guarda de flat-retry conservadora.
        pool = WorkerPool(q, mgr, max_inflight=1, query_free_mib=lambda: None)
        # Pico cabe na GPU → retry allowed.
        pool._total_mib = lambda: 6141  # type: ignore[method-assign]
        pool.start()
        try:
            job = q.enqueue("alpha", {})
            assert job.done_event.wait(timeout=5.0)
            assert job.state == P.JOB_DONE
            assert len(mgr.generate_calls) == 3
            assert job.vram_retries == 2
            assert mgr.evict_calls >= 2
        finally:
            pool.stop()

    def test_vram_hard_refuse_no_retry_when_peak_exceeds_total(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(P, "MAX_VRAM_RETRIES", 4)
        monkeypatch.setattr(P, "VRAM_RETRY_BASE_SEC", 0.05)

        class _AlwaysVram(_FakeManager):
            def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
                self.generate_calls.append((name, request))
                return {
                    "status": P.STATUS_ERROR,
                    "error": "VRAM insuficiente",
                    "error_code": P.ERR_VRAM_INSUFFICIENT,
                    "peak_mib": 9000,
                    "free_mib": 5000,
                }

            def evict_all(self) -> int:
                return 0

        q = JobQueue(max_depth=8)
        mgr = _AlwaysVram()
        pool = WorkerPool(q, mgr, max_inflight=1)
        pool._total_mib = lambda: 6141  # type: ignore[method-assign]
        pool.start()
        try:
            job = q.enqueue("alpha", {})
            assert job.done_event.wait(timeout=3.0)
            assert job.state == P.JOB_FAILED
            assert len(mgr.generate_calls) == 1  # sem requeue
            assert job.vram_retries == 0
            assert job.result is not None
            assert job.result.get("error_code") == P.ERR_VRAM_INSUFFICIENT
        finally:
            pool.stop()

    def test_worker_dead_requeues_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regressão scorpion_nest: «worker não está vivo» → reload+retry."""
        monkeypatch.setattr(P, "MAX_WORKER_DEAD_RETRIES", 2)

        class _DeadThenOk(_FakeManager):
            def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
                self.generate_calls.append((name, request))
                if len(self.generate_calls) == 1:
                    return {
                        "status": P.STATUS_ERROR,
                        "error": "text3d: worker não está vivo — faz load primeiro",
                        "error_code": P.ERR_WORKER_DEAD,
                    }
                return {"status": P.STATUS_OK, "output": f"/tmp/{name}.glb"}

        q = JobQueue(max_depth=8)
        mgr = _DeadThenOk()
        pool = WorkerPool(q, mgr, max_inflight=1)
        pool.start()
        try:
            job = q.enqueue("text3d", {"output": "/tmp/nest.glb"})
            assert job.done_event.wait(timeout=5.0)
            assert job.state == P.JOB_DONE
            assert len(mgr.generate_calls) == 2
            assert job.worker_retries == 1
            assert mgr.evict_calls == ["text3d"]
        finally:
            pool.stop()

    def test_worker_dead_gives_up_after_max_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(P, "MAX_WORKER_DEAD_RETRIES", 2)

        class _AlwaysDead(_FakeManager):
            def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
                self.generate_calls.append((name, request))
                return {
                    "status": P.STATUS_ERROR,
                    "error": "text3d: worker não está vivo — faz load primeiro",
                    "error_code": P.ERR_WORKER_DEAD,
                }

        q = JobQueue(max_depth=8)
        mgr = _AlwaysDead()
        pool = WorkerPool(q, mgr, max_inflight=1)
        pool.start()
        try:
            job = q.enqueue("text3d", {})
            assert job.done_event.wait(timeout=5.0)
            assert job.state == P.JOB_FAILED
            # 1ª falha + 2 requeues = 3 generates
            assert len(mgr.generate_calls) == 3
            assert job.worker_retries == 2
            assert job.result is not None
            assert job.result.get("error_code") == P.ERR_WORKER_DEAD
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


class TestNoSpinAndAffinityHits:
    """Regressão: claim falho não pode busy-spinnar; affinity_hits conta dispatches."""

    def test_wait_for_slot_when_job_does_not_fit(self) -> None:
        q = JobQueue(max_depth=8)
        mgr = _FakeManager(delay=0.3)
        mgr._loaded = {"alpha"}
        j1_done = threading.Event()
        calls = {"slot": 0}

        def _free() -> int:
            # Enquanto j1 corre: -1 (nada cabe); depois: 0 (peak fake = 0 cabe).
            return 0 if j1_done.is_set() else -1

        pool = WorkerPool(q, mgr, max_inflight=2, query_free_mib=_free)
        orig_wait = q.wait_for_slot

        def _spy(timeout: float = 0.5) -> None:
            calls["slot"] += 1
            orig_wait(timeout=0.01)  # acelerar o teste

        q.wait_for_slot = _spy  # type: ignore[method-assign]
        pool.start()
        try:
            j1 = q.enqueue("alpha", {})
            j2 = q.enqueue("beta", {})  # cold; não cabe enquanto j1 corre
            assert j1.done_event.wait(timeout=5.0)
            j1_done.set()
            q.notify()
            assert j2.done_event.wait(timeout=5.0)
            assert j2.state == P.JOB_DONE
            # Esperou na condition em vez de spin a queimar CPU/NVML.
            assert calls["slot"] >= 1
        finally:
            pool.stop()

    def test_affinity_hits_counts_dispatch_not_evaluation(self) -> None:
        q = JobQueue(max_depth=8)
        mgr = _FakeManager()
        mgr._loaded = {"alpha"}
        pool = WorkerPool(q, mgr, max_inflight=1)

        hot = q.enqueue("alpha", {})
        assert pool._claim_next() is hot
        assert pool._affinity_hits == 1  # dispatch real para backend quente
        q.finish(hot, {"status": P.STATUS_OK})

        cold = q.enqueue("beta", {})
        assert pool._claim_next() is cold
        assert pool._affinity_hits == 1  # cold: sem incremento
        q.finish(cold, {"status": P.STATUS_OK})


class TestVramFlatRetryGuard:
    def test_flat_free_nothing_evictable_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Livre plana + 0 loaded N vezes → falha rápida (nunca mais o loop 8x30s)."""
        monkeypatch.setattr(P, "MAX_VRAM_RETRIES", 8)
        monkeypatch.setattr(P, "VRAM_RETRY_BASE_SEC", 0.02)
        monkeypatch.setattr(P, "VRAM_RETRY_MAX_SEC", 0.05)
        monkeypatch.setattr(P, "VRAM_FLAT_RETRY_MAX", 2)
        monkeypatch.setattr(P, "VRAM_FLAT_SLACK_MIB", 32)

        class _AlwaysVram(_FakeManager):
            def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
                self.generate_calls.append((name, request))
                return {
                    "status": P.STATUS_ERROR,
                    "error": "VRAM insuficiente",
                    "error_code": P.ERR_VRAM_INSUFFICIENT,
                    "peak_mib": 5000,
                    "free_mib": 4000,
                }

            def evict_all(self) -> int:
                return 0

            def _clear_cache(self) -> None:
                return None

        q = JobQueue(max_depth=8)
        mgr = _AlwaysVram()  # loaded_names() → sempre vazio
        pool = WorkerPool(q, mgr, max_inflight=1, query_free_mib=lambda: 4000)
        pool._total_mib = lambda: 6141  # type: ignore[method-assign]
        pool.start()
        try:
            job = q.enqueue("alpha", {})
            assert job.done_event.wait(timeout=5.0)
            assert job.state == P.JOB_FAILED
            # 1ª tentativa + 1 requeue; no 2º flat consecutivo falha sem mais requeues.
            assert len(mgr.generate_calls) == 2
            assert job.result is not None
            hint = job.result.get("hint") or ""
            assert "VRAM livre não subiu após retries" in hint
        finally:
            pool.stop()

    def test_evictable_backends_reset_flat_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Com backends evictáveis há progresso potencial → retry até MAX_VRAM_RETRIES."""
        monkeypatch.setattr(P, "MAX_VRAM_RETRIES", 3)
        monkeypatch.setattr(P, "VRAM_RETRY_BASE_SEC", 0.02)
        monkeypatch.setattr(P, "VRAM_RETRY_MAX_SEC", 0.05)
        monkeypatch.setattr(P, "VRAM_FLAT_RETRY_MAX", 2)
        monkeypatch.setattr(P, "VRAM_FLAT_SLACK_MIB", 32)

        class _VramWithLoaded(_FakeManager):
            def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
                self.generate_calls.append((name, request))
                return {
                    "status": P.STATUS_ERROR,
                    "error": "VRAM insuficiente",
                    "error_code": P.ERR_VRAM_INSUFFICIENT,
                    "peak_mib": 5000,
                    "free_mib": 4000,
                }

            def evict_all(self) -> int:
                return 1

            def _clear_cache(self) -> None:
                return None

        q = JobQueue(max_depth=8)
        mgr = _VramWithLoaded()
        mgr._loaded.add("beta")  # algo evictável → progresso potencial
        pool = WorkerPool(q, mgr, max_inflight=1, query_free_mib=lambda: 4000)
        pool._total_mib = lambda: 6141  # type: ignore[method-assign]
        pool.start()
        try:
            job = q.enqueue("alpha", {})
            assert job.done_event.wait(timeout=5.0)
            assert job.state == P.JOB_FAILED
            # Esgota os MAX_VRAM_RETRIES (não falha rápido — havia o que evictar).
            assert len(mgr.generate_calls) == 1 + 3
        finally:
            pool.stop()

    def test_free_moving_resets_flat_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Free a oscilar (processo externo activo) → continua a tentar."""
        monkeypatch.setattr(P, "MAX_VRAM_RETRIES", 4)
        monkeypatch.setattr(P, "VRAM_RETRY_BASE_SEC", 0.02)
        monkeypatch.setattr(P, "VRAM_RETRY_MAX_SEC", 0.05)
        monkeypatch.setattr(P, "VRAM_FLAT_RETRY_MAX", 2)
        monkeypatch.setattr(P, "VRAM_FLAT_SLACK_MIB", 32)

        frees = iter([3900, 4000, 4000, 4000, 4000, 4000, 4000])

        class _VramThenOk(_FakeManager):
            def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
                self.generate_calls.append((name, request))
                if len(self.generate_calls) < 3:
                    return {
                        "status": P.STATUS_ERROR,
                        "error": "VRAM insuficiente",
                        "error_code": P.ERR_VRAM_INSUFFICIENT,
                        "peak_mib": 5000,
                        "free_mib": 4000,
                    }
                return {"status": P.STATUS_OK, "output": "/tmp/x.png"}

            def evict_all(self) -> int:
                return 0

            def _clear_cache(self) -> None:
                return None

        q = JobQueue(max_depth=8)
        mgr = _VramThenOk()
        pool = WorkerPool(q, mgr, max_inflight=1, query_free_mib=lambda: next(frees, 4000))
        pool._total_mib = lambda: 6141  # type: ignore[method-assign]
        pool.start()
        try:
            job = q.enqueue("alpha", {})
            assert job.done_event.wait(timeout=5.0)
            assert job.state == P.JOB_DONE
            assert len(mgr.generate_calls) == 3
        finally:
            pool.stop()
