"""Testes de persistência WAL/JSONL da JobQueue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from modelserver import protocol as P
from modelserver.job_queue import JobQueue


@pytest.fixture
def wal_path(tmp_path: Path) -> Path:
    return tmp_path / P.WAL_FILENAME


def test_enqueue_persists_wal(wal_path: Path) -> None:
    q = JobQueue(wal_path=wal_path)
    job = q.enqueue("text2icon", {"prompt": "sword"}, priority=P.PRIORITY_BATCH)

    assert wal_path.exists()
    lines = wal_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["op"] == "enqueue"
    assert rec["job_id"] == job.job_id
    assert rec["backend"] == "text2icon"
    assert rec["priority"] == P.PRIORITY_BATCH
    assert rec["request"] == {"prompt": "sword"}
    assert "ts" in rec


def test_replay_restores_queued(wal_path: Path) -> None:
    wal_path.write_text(
        json.dumps(
            {
                "op": "enqueue",
                "job_id": "old-1",
                "backend": "text2d",
                "priority": "interactive",
                "request": {"prompt": "hero"},
                "ts": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    q = JobQueue(wal_path=wal_path)
    requeued = q.replay_from_wal()

    assert requeued == 1
    assert q.depth == 1
    jobs = q.queued_jobs()
    assert len(jobs) == 1
    assert jobs[0].backend == "text2d"
    assert jobs[0].request == {"prompt": "hero"}
    assert jobs[0].priority == P.PRIORITY_INTERACTIVE

    compact = wal_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(compact) == 1
    assert json.loads(compact[0])["op"] == "enqueue"
    assert json.loads(compact[0])["job_id"] == jobs[0].job_id


def test_replay_running_at_crash_requeues(wal_path: Path) -> None:
    wal_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "op": "enqueue",
                        "job_id": "crash-1",
                        "backend": "text3d",
                        "priority": "batch",
                        "request": {"prompt": "cube"},
                        "ts": 1.0,
                    }
                ),
                json.dumps({"op": "started", "job_id": "crash-1", "ts": 2.0}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    q = JobQueue(wal_path=wal_path)
    requeued = q.replay_from_wal()

    assert requeued == 1
    assert q.depth == 1
    job = q.queued_jobs()[0]
    assert job.backend == "text3d"
    assert job.request == {"prompt": "cube"}


def test_replay_skips_finished_jobs(wal_path: Path) -> None:
    wal_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "op": "enqueue",
                        "job_id": "done-1",
                        "backend": "text2icon",
                        "priority": "batch",
                        "request": {"prompt": "x"},
                        "ts": 1.0,
                    }
                ),
                json.dumps({"op": "started", "job_id": "done-1", "ts": 2.0}),
                json.dumps({"op": "finished", "job_id": "done-1", "state": "done", "ts": 3.0}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    q = JobQueue(wal_path=wal_path)
    assert q.replay_from_wal() == 0
    assert q.depth == 0


def test_take_and_finish_write_wal(wal_path: Path) -> None:
    q = JobQueue(wal_path=wal_path)
    job = q.enqueue("text2sound", {"prompt": "beep"})
    taken = q.take(job.job_id)
    assert taken is not None
    q.finish(taken, {"status": P.STATUS_OK, "output": "/tmp/x.wav"})

    ops = [json.loads(line)["op"] for line in wal_path.read_text(encoding="utf-8").strip().splitlines()]
    assert ops == ["enqueue", "started", "finished"]


def test_replay_tolerates_incomplete_record(wal_path: Path) -> None:
    """JSON válido mas sem ``backend`` não pode derrubar o startup (regressão)."""
    lines = [
        json.dumps({"op": "enqueue", "job_id": "bad-1", "priority": "batch"}),  # sem backend
        json.dumps({"op": "enqueue", "job_id": "ok-1", "backend": "text2d", "request": {}}),
    ]
    wal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    q = JobQueue(wal_path=wal_path)
    assert q.replay_from_wal() == 1
    assert q.depth == 1
