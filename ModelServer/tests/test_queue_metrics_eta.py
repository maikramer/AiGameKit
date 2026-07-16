"""P1: ETA + métricas de fila; P2: inflight condicional VRAM."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from modelserver import protocol as P
from modelserver.job_queue import JobQueue, QueueFullError
from modelserver.registry import BackendDescriptor, Registry
from modelserver.scheduler import AffinityScheduler
from modelserver.stats import StatsCollector
from modelserver.worker import WorkerPool

from .conftest_helpers import MockAdapter
from .test_server import _send_request, _start_ums, _stop_ums


class TestQueueStats:
    def test_enqueue_finish_and_queue_full_metrics(self) -> None:
        stats = StatsCollector()
        q = JobQueue(max_depth=1, stats=stats)
        job = q.enqueue("a", {})
        assert stats.queue.enqueued == 1
        with pytest.raises(QueueFullError):
            q.enqueue("b", {})
        assert stats.queue.queue_full_count == 1

        taken = q.take(job.job_id)
        assert taken is not None
        taken.mark_started()
        q.finish(taken, {"status": P.STATUS_OK, "output": "x"})
        assert stats.queue.completed == 1
        assert stats.queue_dict()["queue_wait_p50_sec"] is not None

        j2 = q.enqueue("b", {})
        q.cancel(j2.job_id)
        assert stats.queue.cancelled >= 1


class TestEtaStatus:
    def test_status_includes_eta_and_queue_metrics(self, tmp_path: Path) -> None:
        srv, sock, thread = _start_ums(tmp_path)
        try:
            # Seed avg generate time.
            srv.manager.stats.record_generate("alpha", 2.0)
            resp = _send_request(sock, {"cmd": P.CMD_STATUS})
            assert "queue_metrics" in resp
            assert "eta_sec" in resp
            # Sem jobs → eta None
            assert resp["eta_sec"] is None

            # Enfileirar com worker parado: ETA usa fallback 30s.
            srv.workers.stop()
            job = srv.queue.enqueue("alpha", {"prompt": "x"})
            resp2 = _send_request(sock, {"cmd": P.CMD_QUEUE})
            assert resp2["eta_sec"] == 2.0  # avg do alpha
            srv.queue.cancel(job.job_id)
        finally:
            _stop_ums(srv, sock, thread)


class TestStarvationTimeout:
    def test_starvation_forces_cold_head(self) -> None:
        sched = AffinityScheduler(max_cuts=10, starvation_timeout_sec=0.05)
        q = JobQueue(max_depth=8)
        cold = q.enqueue("cold", {})
        q.enqueue("hot", {})
        time.sleep(0.08)
        picked = sched.pick_next(q.queued_jobs(), loaded={"hot"})
        assert picked is not None
        assert picked.job_id == cold.job_id


class TestInflightVramGate:
    def test_second_job_skipped_when_free_low(self) -> None:
        specs = {"alpha": (1000, 10), "beta": (3000, 30)}
        descriptors = {
            n: BackendDescriptor(name=n, adapter=f"_mock_{n}", vram_mib=v, priority=p) for n, (v, p) in specs.items()
        }
        registry = Registry(descriptors=descriptors)
        for n in specs:
            registry._adapter_instances[n] = MockAdapter(name=n)

        class _Mgr:
            def __init__(self) -> None:
                self._registry = registry
                self._loaded = {"alpha"}
                self.calls: list[str] = []

            def loaded_names(self) -> list[str]:
                return sorted(self._loaded)

            def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
                self.calls.append(name)
                time.sleep(0.2)
                return {"status": P.STATUS_OK, "output": f"/tmp/{name}.png"}

        q = JobQueue(max_depth=8)
        mgr = _Mgr()
        pool = WorkerPool(
            q,
            mgr,  # type: ignore[arg-type]
            AffinityScheduler(),
            max_inflight=2,
            query_free_mib=lambda: 100,  # insuficiente para beta=3000
        )
        pool.start()
        try:
            j1 = q.enqueue("alpha", {})
            j2 = q.enqueue("beta", {})
            assert j1.done_event.wait(timeout=3.0)
            # beta não deve correr em paralelo (free baixo); eventualmente corre sozinho.
            assert j2.done_event.wait(timeout=3.0)
            assert mgr.calls[0] == "alpha"
        finally:
            pool.stop()


class TestDiffusionControl:
    def test_attach_step_hooks_aborts(self) -> None:
        from gamedev_shared.diffusion_control import GenerationAborted, attach_step_hooks

        kwargs: dict[str, Any] = {}
        attach_step_hooks(kwargs, num_inference_steps=4, should_abort=lambda: True)
        cb = kwargs["callback_on_step_end"]
        with pytest.raises(GenerationAborted):
            cb(type("P", (), {"_interrupt": False})(), 0, 0, {})
