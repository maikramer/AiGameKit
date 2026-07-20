"""Testes da JobQueue — backpressure, cancel, wait."""

from __future__ import annotations

import threading
import time

import pytest
from modelserver import protocol as P
from modelserver.job_queue import JobQueue, QueueFullError


class TestJobQueue:
    def test_enqueue_and_depth(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {"prompt": "x"}, priority=P.PRIORITY_INTERACTIVE)
        assert j.state == P.JOB_QUEUED
        assert q.depth == 1
        assert j.priority == P.PRIORITY_INTERACTIVE

    def test_queue_full(self) -> None:
        q = JobQueue(max_depth=2)
        q.enqueue("a", {})
        q.enqueue("b", {})
        with pytest.raises(QueueFullError):
            q.enqueue("c", {})

    def test_cancel_queued(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})
        resp = q.cancel(j.job_id)
        assert resp["status"] == P.STATUS_OK
        assert j.state == P.JOB_CANCELLED
        assert q.depth == 0
        assert j.done_event.is_set()

    def test_cancel_running_best_effort(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})
        taken = q.take(j.job_id)
        assert taken is j
        assert q.inflight == 1
        resp = q.cancel(j.job_id)
        assert resp["status"] == P.STATUS_OK
        assert j.cancel_requested
        assert j.state == P.JOB_RUNNING

    def test_take_and_finish(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})
        assert q.take(j.job_id) is j
        q.finish(j, {"status": P.STATUS_OK, "output": "/tmp/x.png"})
        assert j.state == P.JOB_DONE
        assert q.inflight == 0

    def test_requeue_running_returns_to_front(self) -> None:
        q = JobQueue(max_depth=8)
        a = q.enqueue("alpha", {})
        b = q.enqueue("beta", {})
        taken = q.take(a.job_id)
        assert taken is a
        assert q.inflight == 1
        assert q.requeue_running(a, reason="livre=4000 peak=5000")
        assert a.state == P.JOB_QUEUED
        assert a.vram_retries == 1
        assert q.inflight == 0
        assert not a.done_event.is_set()
        # Frente: alpha antes de beta
        assert q.queued_jobs()[0] is a
        assert q.queued_jobs()[1] is b

    def test_requeue_cancelled_returns_false(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})
        q.take(j.job_id)
        j.cancel_requested = True
        assert q.requeue_running(j) is False
        assert q.inflight == 1  # ainda running até finish/cancel path

    def test_wait_unblocks_on_finish(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})

        def _finish() -> None:
            time.sleep(0.05)
            taken = q.take(j.job_id)
            assert taken is not None
            q.finish(taken, {"status": P.STATUS_OK, "output": "ok"})

        threading.Thread(target=_finish, daemon=True).start()
        done = q.wait(j.job_id, timeout_sec=2.0)
        assert done is not None
        assert done.state == P.JOB_DONE

    def test_priority_normalized(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("a", {"priority": "BATCH"})
        assert j.priority == P.PRIORITY_BATCH

    def test_snapshot(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})
        snap = q.snapshot()
        assert snap["queue_depth"] == 1
        assert snap["queued"][0]["job_id"] == j.job_id

    def test_finish_ok_after_cancel_becomes_cancelled(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})
        taken = q.take(j.job_id)
        assert taken is not None
        q.cancel(j.job_id)
        assert j.cancel_requested
        q.finish(j, {"status": P.STATUS_OK, "output": "/tmp/x.png"})
        assert j.state == P.JOB_CANCELLED
        assert j.done_event.is_set()
        assert q.inflight == 0

    def test_cancel_unknown_and_already_done(self) -> None:
        q = JobQueue(max_depth=8)
        assert q.cancel("nope")["status"] == P.STATUS_ERROR
        j = q.enqueue("alpha", {})
        taken = q.take(j.job_id)
        assert taken is not None
        q.finish(j, {"status": P.STATUS_OK, "output": "x"})
        resp = q.cancel(j.job_id)
        assert resp["status"] == P.STATUS_OK
        assert "terminado" in resp.get("message", "")

    def test_cancel_by_prefix(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})
        resp = q.cancel(j.job_id[:8])
        assert resp["status"] == P.STATUS_OK
        assert j.state == P.JOB_CANCELLED
        assert q.depth == 0

    def test_cancel_all_queued_and_running(self) -> None:
        q = JobQueue(max_depth=8)
        a = q.enqueue("alpha", {})
        b = q.enqueue("beta", {})
        taken = q.take(a.job_id)
        assert taken is a
        resp = q.cancel_all(include_running=True)
        assert resp["status"] == P.STATUS_OK
        assert a.job_id in resp["cancel_requested_running"]
        assert b.job_id in resp["cancelled_queued"]
        assert b.state == P.JOB_CANCELLED
        assert a.cancel_requested
        assert q.depth == 0

    def test_double_take_same_id(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})
        assert q.take(j.job_id) is j
        assert q.take(j.job_id) is None
        assert q.inflight == 1

    def test_listener_exception_does_not_break_emit(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})
        seen: list[str] = []

        def _bad(_e: dict) -> None:
            raise RuntimeError("listener boom")

        def _good(e: dict) -> None:
            seen.append(e.get("event", ""))

        j.add_listener(_bad)
        j.add_listener(_good)
        j.report_progress(0.5, "half")
        assert P.EVENT_PROGRESS in seen

    def test_concurrent_enqueue_respects_max_depth(self) -> None:
        q = JobQueue(max_depth=5)
        errors = 0
        ok = 0
        lock = threading.Lock()

        def _try() -> None:
            nonlocal errors, ok
            try:
                q.enqueue("alpha", {})
                with lock:
                    ok += 1
            except QueueFullError:
                with lock:
                    errors += 1

        threads = [threading.Thread(target=_try) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)
        assert ok == 5
        assert errors == 15
        assert q.depth == 5


class TestFinishedPurge:
    """Regressão: jobs terminais não podem acumular para sempre (daemon long-lived)."""

    def test_purge_by_ttl(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})
        q.take(j.job_id)
        q.finish(j, {"status": P.STATUS_OK})
        assert q.get(j.job_id) is not None
        # Envelhecer para lá do TTL; o finish seguinte dispara o purge.
        j.finished_at = time.monotonic() - 700.0
        j2 = q.enqueue("alpha", {})
        q.take(j2.job_id)
        q.finish(j2, {"status": P.STATUS_OK})
        assert q.get(j.job_id) is None
        assert q.get(j2.job_id) is not None

    def test_purge_by_cap(self) -> None:
        from modelserver.job_queue import _MAX_FINISHED_JOBS

        q = JobQueue(max_depth=4096)
        jobs = []
        for _ in range(_MAX_FINISHED_JOBS + 5):
            j = q.enqueue("a", {})
            q.take(j.job_id)
            q.finish(j, {"status": P.STATUS_OK})
            jobs.append(j)
        remaining = sum(1 for j in jobs if q.get(j.job_id) is not None)
        assert remaining <= _MAX_FINISHED_JOBS
        # Os mais recentes sobrevivem ao purge.
        assert q.get(jobs[-1].job_id) is not None

    def test_remove_listener(self) -> None:
        q = JobQueue(max_depth=8)
        j = q.enqueue("alpha", {})
        seen: list[dict] = []

        def _listener(ev: dict) -> None:
            seen.append(ev)

        j.add_listener(_listener)
        j.remove_listener(_listener)
        j.report_progress(0.5, "x")
        assert seen == []
