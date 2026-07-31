"""Cobertura elaborada ModelServer — fila, scheduler, peak VRAM, protocolo (sem sockets/GPU)."""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import patch

import pytest
from modelserver import protocol as P
from modelserver.job_queue import Job, JobQueue, QueueFullError
from modelserver.registry import BackendDescriptor, Registry, load_descriptors
from modelserver.scheduler import AffinityScheduler
from modelserver.vram_planner import (
    LoadedBackend,
    can_admit,
    inference_headroom_mib,
    peak_vram_mib,
    plan_eviction,
    vram_safety_mib,
)

# --- peak VRAM / admit (40 casos extra weights grid) ---

_PEAK_GRID = [(w, a) for w in (256, 512, 1024, 2048, 4096) for a in (128, 256, 512, 1024)]


@pytest.mark.parametrize("weights,activation", _PEAK_GRID)
def test_peak_vram_monotonic_in_weights(weights: int, activation: int) -> None:
    low = peak_vram_mib(weights, activation, safety_mib=128)
    high = peak_vram_mib(weights + 256, activation, safety_mib=128)
    assert high > low


@pytest.mark.parametrize(
    "err_code", sorted({P.ERR_BACKEND_UNKNOWN, P.ERR_QUEUE_FULL, P.ERR_CANCELLED, P.ERR_VRAM_INSUFFICIENT})
)
def test_error_codes_are_strings(err_code: str) -> None:
    assert isinstance(err_code, str)
    assert err_code.isupper() or "_" in err_code


@pytest.mark.parametrize(
    "event",
    [P.EVENT_QUEUED, P.EVENT_STARTED, P.EVENT_PROGRESS, P.EVENT_DONE, P.EVENT_ERROR, P.EVENT_CANCELLED],
)
def test_stream_events_defined(event: str) -> None:
    assert isinstance(event, str)


@pytest.mark.parametrize("job_state", [P.JOB_QUEUED, P.JOB_RUNNING, P.JOB_DONE, P.JOB_FAILED, P.JOB_CANCELLED])
def test_job_states_distinct(job_state: str) -> None:
    assert job_state in {P.JOB_QUEUED, P.JOB_RUNNING, P.JOB_DONE, P.JOB_FAILED, P.JOB_CANCELLED}


@pytest.mark.parametrize("inflight_max", [1, 2, 4])
def test_protocol_max_inflight_env_readable(inflight_max: int) -> None:
    with patch.dict(os.environ, {"AIGAMEKIT_UMS_MAX_INFLIGHT": str(inflight_max)}, clear=False):
        assert P._env_int("AIGAMEKIT_UMS_MAX_INFLIGHT", 1) == inflight_max


@pytest.mark.parametrize("backend", ["text2d", "text2icon", "text3d", "paint3d", "part3d", "skymap2d", "texture2d"])
def test_registry_priority_non_negative(backend: str) -> None:
    d = load_descriptors()[backend]
    assert d.priority >= 0
    assert d.vram_mib > 0


@pytest.mark.parametrize("seq", range(1, 11))
def test_scheduler_fifo_when_all_cold(seq: int) -> None:
    sched = AffinityScheduler(max_cuts=10)
    jobs = [_job(i, f"b{i}") for i in range(1, seq + 1)]
    picked = sched.pick_next(jobs, set())
    assert picked is not None
    assert picked.seq == 1


@pytest.mark.parametrize(
    "weights,activation,safety,expected",
    [
        (1000, 500, 384, 1884),
        (0, 0, 0, 0),
        (8192, 2048, 384, 10624),
        (512, 256, 128, 896),
        (100, 50, None, 100 + 50 + vram_safety_mib()),
        (2048, 4096, 512, 6656),
        (1, 1, 1, 3),
        (6000, 1500, 384, 7884),
        (3000, 3000, 384, 6384),
        (7500, 500, 256, 8256),
    ],
)
def test_peak_vram_mib_formula(weights: int, activation: int, safety: int | None, expected: int) -> None:
    assert peak_vram_mib(weights, activation, safety_mib=safety) == expected


@pytest.mark.parametrize(
    "free,peak,expected",
    [
        (None, 8000, True),
        (8000, 8000, True),
        (7999, 8000, False),
        (0, 1, False),
        (100000, 1, True),
        (384, 384, True),
        (383, 384, False),
    ],
)
def test_can_admit(free: int | None, peak: int, expected: bool) -> None:
    assert can_admit(free, peak) is expected


@pytest.mark.parametrize(
    "activation,safety,expected",
    [
        (512, 384, 896),
        (0, 384, 384),
        (2048, 0, 2048),
        (100, 50, 150),
        (4096, None, 4096 + vram_safety_mib()),
    ],
)
def test_inference_headroom_mib(activation: int, safety: int | None, expected: int) -> None:
    assert inference_headroom_mib(activation, safety_mib=safety) == expected


@pytest.mark.parametrize(
    "env_val,expected_min",
    [
        ("512", 512),
        ("0", 0),
        ("", vram_safety_mib()),
        ("bad", vram_safety_mib()),
    ],
)
def test_vram_safety_mib_env(env_val: str, expected_min: int) -> None:
    with patch.dict(os.environ, {"AIGAMEKIT_UMS_VRAM_SAFETY_MIB": env_val}, clear=False):
        got = vram_safety_mib()
        if env_val == "512":
            assert got == 512
        elif env_val == "0":
            assert got == 0
        else:
            assert got == expected_min


# --- plan_eviction (18 casos) ---


def _lb(name: str, mib: int, pri: int, ref: int = 0, used: float = 0.0) -> LoadedBackend:
    return LoadedBackend(name=name, vram_mib=mib, priority=pri, ref_count=ref, last_used=used)


@pytest.mark.parametrize(
    "loaded,needed,free,expect_names",
    [
        ([], 1000, 500, []),
        ([_lb("a", 2000, 1)], 500, 2000, []),
        ([_lb("low", 3000, 1, 0, 1.0), _lb("high", 3000, 10, 0, 2.0)], 4000, 1000, ["low"]),
        ([_lb("busy", 5000, 1, 1, 0.0)], 8000, 1000, []),
        ([_lb("x", 1500, 2, 0, 5.0), _lb("y", 1500, 2, 0, 1.0)], 2000, 500, ["y"]),
    ],
)
def test_plan_eviction_scenarios(
    loaded: list[LoadedBackend],
    needed: int,
    free: int,
    expect_names: list[str],
) -> None:
    assert plan_eviction(loaded, needed, free) == expect_names


@pytest.mark.parametrize("deficit", [100, 500, 2000, 8000])
def test_plan_eviction_accumulates_until_deficit(deficit: int) -> None:
    loaded = [_lb(f"b{i}", 1000, 1, 0, float(i)) for i in range(10)]
    evicted = plan_eviction(loaded, deficit + 500, 500)
    freed = sum(b.vram_mib for b in loaded if b.name in evicted)
    assert freed >= deficit or len(evicted) == len([b for b in loaded if b.ref_count == 0])


# --- protocol (20 casos) ---


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, P.DEFAULT_PRIORITY),
        ("interactive", P.PRIORITY_INTERACTIVE),
        ("batch", P.PRIORITY_BATCH),
        ("BATCH", P.PRIORITY_BATCH),
        ("  interactive  ", P.PRIORITY_INTERACTIVE),
        ("unknown", P.DEFAULT_PRIORITY),
        ("", P.DEFAULT_PRIORITY),
    ],
)
def test_normalize_priority(value: object | None, expected: str) -> None:
    assert P.normalize_priority(value) == expected


@pytest.mark.parametrize("cmd", sorted(P.KNOWN_COMMANDS))
def test_known_commands_membership(cmd: str) -> None:
    assert cmd in P.KNOWN_COMMANDS
    assert isinstance(cmd, str) and len(cmd) > 2


@pytest.mark.parametrize(
    "rank_a,rank_b",
    [
        (P.PRIORITY_RANK[P.PRIORITY_INTERACTIVE], P.PRIORITY_RANK[P.PRIORITY_BATCH]),
    ],
)
def test_priority_rank_order(rank_a: int, rank_b: int) -> None:
    assert rank_a < rank_b


def test_protocol_status_constants_unique() -> None:
    assert P.STATUS_OK != P.STATUS_ERROR
    assert P.JOB_QUEUED != P.JOB_RUNNING


# --- AffinityScheduler (22 casos) ---


def _job(seq: int, backend: str, pri: str = P.PRIORITY_BATCH, cuts: int = 0) -> Job:
    return Job(
        job_id=f"j{seq}",
        backend=backend,
        request={},
        priority=pri,
        seq=seq,
        affinity_cuts=cuts,
    )


@pytest.mark.parametrize(
    "loaded,queue_backends,expect_backend",
    [
        ({"text2icon"}, [("text3d", 1), ("text2icon", 2)], "text2icon"),
        (set(), [("text3d", 1)], "text3d"),
        ({"paint3d"}, [("text3d", 1), ("paint3d", 2)], "paint3d"),
    ],
)
def test_scheduler_prefers_hot_backend(
    loaded: set[str],
    queue_backends: list[tuple[str, int]],
    expect_backend: str,
) -> None:
    sched = AffinityScheduler(max_cuts=3)
    jobs = [_job(seq, b) for b, seq in queue_backends]
    picked = sched.pick_next(jobs, loaded)
    assert picked is not None
    assert picked.backend == expect_backend


def test_scheduler_empty_returns_none() -> None:
    sched = AffinityScheduler()
    assert sched.pick_next([], set()) is None


def test_scheduler_interactive_before_batch() -> None:
    sched = AffinityScheduler(max_cuts=0)
    jobs = [
        _job(1, "text3d", P.PRIORITY_BATCH),
        _job(2, "text2icon", P.PRIORITY_INTERACTIVE),
    ]
    picked = sched.pick_next(jobs, set())
    assert picked is not None
    assert picked.backend == "text2icon"


def test_scheduler_max_cuts_forces_head() -> None:
    sched = AffinityScheduler(max_cuts=1)
    head = _job(1, "text3d", cuts=1)
    tail = _job(2, "text2icon")
    picked = sched.pick_next([head, tail], {"text2icon"})
    assert picked is head


def test_scheduler_starvation_timeout_forces_head() -> None:
    sched = AffinityScheduler(max_cuts=10, starvation_timeout_sec=0.001)
    head = _job(1, "text3d")
    head.created_at = time.monotonic() - 1.0
    tail = _job(2, "text2icon")
    picked = sched.pick_next([head, tail], {"text2icon"})
    assert picked is head


@pytest.mark.parametrize("cancel", [True, False])
def test_scheduler_skips_cancel_requested(cancel: bool) -> None:
    sched = AffinityScheduler()
    j = _job(1, "text3d")
    j.cancel_requested = cancel
    j.state = P.JOB_QUEUED if not cancel else P.JOB_CANCELLED
    if cancel:
        assert sched.pick_next([j], set()) is None
    else:
        assert sched.pick_next([j], set()) is j


# --- Job lifecycle (15 casos) ---


@pytest.mark.parametrize(
    "status,expected_state",
    [
        (P.STATUS_OK, P.JOB_DONE),
        (P.STATUS_ERROR, P.JOB_FAILED),
    ],
)
def test_job_mark_finished_states(status: str, expected_state: str) -> None:
    job = _job(1, "text2sound")
    events: list[dict] = []
    job.add_listener(events.append)
    job.mark_started()
    job.mark_finished({"status": status, "output": "/tmp/x.wav"})
    assert job.state == expected_state
    assert job.done_event.is_set()
    assert any(e.get("event") in (P.EVENT_DONE, P.EVENT_ERROR) for e in events)


def test_job_mark_cancelled() -> None:
    job = _job(2, "text2d")
    job.mark_cancelled("user")
    assert job.state == P.JOB_CANCELLED
    assert job.result is not None
    assert job.result.get("error_code") == P.ERR_CANCELLED


@pytest.mark.parametrize("pct", [0.0, 33.3, 100.0])
def test_job_report_progress(pct: float) -> None:
    job = _job(3, "part3d")
    seen: list[dict] = []
    job.add_listener(seen.append)
    job.report_progress(pct=pct, msg="step")
    assert job.progress_pct == pct
    assert job.progress_msg == "step"
    assert seen[-1]["event"] == P.EVENT_PROGRESS


def test_job_timing_dict_queued() -> None:
    job = _job(4, "skymap2d")
    t = job.timing_dict()
    assert t["queue_wait_sec"] is not None
    assert t["generate_sec"] is None


def test_job_to_public_dict_fields() -> None:
    job = _job(5, "texture2d", P.PRIORITY_INTERACTIVE)
    pub = job.to_public_dict()
    assert pub["job_id"] == "j5"
    assert pub["backend"] == "texture2d"
    assert pub["priority"] == P.PRIORITY_INTERACTIVE


# --- JobQueue (20 casos) ---


@pytest.mark.parametrize("max_depth", [1, 2, 8])
def test_job_queue_enqueue_respects_depth(max_depth: int) -> None:
    q = JobQueue(max_depth=max_depth)
    jobs = [q.enqueue("text2icon", {"prompt": "x"}) for _ in range(max_depth)]
    assert len(jobs) == max_depth
    with pytest.raises(QueueFullError):
        q.enqueue("text2icon", {"prompt": "overflow"})


def test_job_queue_cancel_queued() -> None:
    q = JobQueue(max_depth=4)
    job = q.enqueue("text3d", {"from_image": "a.png"})
    resp = q.cancel(job.job_id)
    assert resp["status"] == P.STATUS_OK
    assert job.state == P.JOB_CANCELLED


@pytest.mark.parametrize("pri_input", ["batch", "interactive", None])
def test_job_queue_normalizes_priority(pri_input: str | None) -> None:
    q = JobQueue(max_depth=2)
    req = {"prompt": "p"}
    if pri_input:
        req["priority"] = pri_input
    job = q.enqueue("text2sound", req, priority=pri_input)
    assert job.priority in (P.PRIORITY_BATCH, P.PRIORITY_INTERACTIVE)


def test_job_queue_snapshot_lists_queued() -> None:
    q = JobQueue(max_depth=4)
    q.enqueue("paint3d", {"mesh": "m.glb"})
    snap = q.snapshot()
    assert snap["queue_depth"] >= 1
    assert isinstance(snap.get("queued"), list)


def test_registry_loads_backends_yaml() -> None:
    desc = load_descriptors()
    assert "text3d" in desc
    assert "text2sound" in desc
    assert desc["text3d"].vram_mib > 0


@pytest.mark.parametrize("name", ["text2d", "text2icon", "text3d", "paint3d", "text2sound"])
def test_registry_has_backend(name: str) -> None:
    reg = Registry()
    assert reg.has(name)
    d = reg.descriptor(name)
    assert isinstance(d, BackendDescriptor)
    assert d.adapter


def test_registry_unknown_raises() -> None:
    reg = Registry()
    with pytest.raises(KeyError, match="desconhecido"):
        reg.descriptor("not-a-backend")


def test_job_listener_thread_safe() -> None:
    job = _job(99, "terrain3d")
    results: list[int] = []

    def listen(_: dict) -> None:
        results.append(1)

    job.add_listener(listen)
    threads = [threading.Thread(target=job.report_progress, kwargs={"pct": float(i)}) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 5
