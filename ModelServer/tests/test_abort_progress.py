"""P0: cancel cooperativo (_abort) + progresso nos adapters."""

from __future__ import annotations

import time
from typing import Any

from modelserver import protocol as P
from modelserver.adapters.base import BackendAdapter
from modelserver.job_queue import JobQueue
from modelserver.scheduler import AffinityScheduler
from modelserver.worker import WorkerPool


class _AbortableFakeManager:
    def __init__(self, *, steps: int = 5, step_delay: float = 0.05) -> None:
        self.steps = steps
        self.step_delay = step_delay
        self.generate_calls: list[dict[str, Any]] = []
        self._loaded: set[str] = set()

    def loaded_names(self) -> list[str]:
        return sorted(self._loaded)

    def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
        self.generate_calls.append(request)
        self._loaded.add(name)
        abort = request.get("_abort")
        progress = request.get("_progress")
        for i in range(1, self.steps + 1):
            if callable(abort) and abort():
                return {
                    "status": P.STATUS_ERROR,
                    "error": "cancelled during diffusion",
                    "error_code": P.ERR_CANCELLED,
                }
            if callable(progress):
                progress(i / self.steps, f"step {i}/{self.steps}")
            time.sleep(self.step_delay)
        return {"status": P.STATUS_OK, "output": f"/tmp/{name}.png"}


class TestAbortHooks:
    def test_worker_injects_abort_callback(self) -> None:
        q = JobQueue(max_depth=8)
        mgr = _AbortableFakeManager(steps=2, step_delay=0.01)
        pool = WorkerPool(q, mgr, AffinityScheduler(), max_inflight=1)
        pool.start()
        try:
            job = q.enqueue("alpha", {"prompt": "x"})
            assert job.done_event.wait(timeout=3.0)
            assert mgr.generate_calls
            assert callable(mgr.generate_calls[0].get("_abort"))
            assert callable(mgr.generate_calls[0].get("_progress"))
        finally:
            pool.stop()

    def test_cancel_mid_generate_aborts_early(self) -> None:
        q = JobQueue(max_depth=8)
        mgr = _AbortableFakeManager(steps=20, step_delay=0.05)
        pool = WorkerPool(q, mgr, AffinityScheduler(), max_inflight=1)
        pool.start()
        try:
            job = q.enqueue("alpha", {"prompt": "x"})
            time.sleep(0.08)
            q.cancel(job.job_id)
            assert job.done_event.wait(timeout=5.0)
            assert job.state == P.JOB_CANCELLED
            assert job.result is not None
            assert job.result.get("error_code") == P.ERR_CANCELLED
        finally:
            pool.stop()


class TestAdapterHelpers:
    def test_should_abort_and_cancelled_response(self) -> None:
        flag = {"v": False}
        req = {"_abort": lambda: flag["v"]}
        assert BackendAdapter.should_abort(req) is False
        flag["v"] = True
        assert BackendAdapter.should_abort(req) is True
        resp = BackendAdapter.cancelled_response("cancelled mid")
        assert resp["error_code"] == P.ERR_CANCELLED
        assert resp["status"] == P.STATUS_ERROR

    def test_report_progress_invokes_callback(self) -> None:
        seen: list[tuple[float | None, str | None]] = []
        req = {"_progress": lambda pct, msg: seen.append((pct, msg))}
        BackendAdapter.report_progress(req, 0.5, "halfway")
        assert seen == [(0.5, "halfway")]
