"""Fila de jobs do UMS — enqueue, cancel, backpressure, wait.

Os jobs são despachados pelo ``AffinityScheduler`` + ``WorkerPool``; esta classe
só gere o inventário e a sincronização com os clientes.
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import protocol as P


class QueueFullError(Exception):
    """A fila atingiu ``MAX_QUEUE_DEPTH``."""


ProgressListener = Callable[[dict[str, Any]], None]


@dataclass
class Job:
    """Pedido de geração enfileirado."""

    job_id: str
    backend: str
    request: dict[str, Any]
    priority: str
    seq: int
    state: str = P.JOB_QUEUED
    affinity_cuts: int = 0
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    cancel_requested: bool = False
    progress_pct: float | None = None
    progress_msg: str | None = None
    counted_served: bool = False  # evita double-count em wait/generate
    done_event: threading.Event = field(default_factory=threading.Event)
    _listeners: list[ProgressListener] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_listener(self, listener: ProgressListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def _emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            with contextlib.suppress(Exception):
                listener(event)

    def report_progress(self, pct: float | None = None, msg: str | None = None) -> None:
        """Reporta progresso (usado pelo worker / adapters)."""
        if pct is not None:
            self.progress_pct = float(pct)
        if msg is not None:
            self.progress_msg = msg
        self._emit(
            {
                "event": P.EVENT_PROGRESS,
                "job_id": self.job_id,
                "backend": self.backend,
                "pct": self.progress_pct,
                "message": self.progress_msg,
                "state": self.state,
            }
        )

    def mark_started(self) -> None:
        self.state = P.JOB_RUNNING
        self.started_at = time.monotonic()
        self._emit(
            {
                "event": P.EVENT_STARTED,
                "job_id": self.job_id,
                "backend": self.backend,
                "state": self.state,
                "priority": self.priority,
                "affinity_cuts": self.affinity_cuts,
                "queue_wait_sec": round(self.started_at - self.created_at, 3),
            }
        )

    def mark_finished(self, result: dict[str, Any]) -> None:
        self.result = result
        self.finished_at = time.monotonic()
        status = result.get("status", P.STATUS_ERROR)
        if status == P.STATUS_OK:
            self.state = P.JOB_DONE
            event_name = P.EVENT_DONE
        elif self.cancel_requested or (status == P.STATUS_ERROR and "cancel" in str(result.get("error", "")).lower()):
            self.state = P.JOB_CANCELLED
            event_name = P.EVENT_CANCELLED
        else:
            self.state = P.JOB_FAILED
            event_name = P.EVENT_ERROR
        payload = {
            "event": event_name,
            "job_id": self.job_id,
            "backend": self.backend,
            "state": self.state,
            **{k: v for k, v in result.items() if k != "event"},
        }
        self._emit(payload)
        self.done_event.set()

    def mark_cancelled(self, reason: str = "cancelled") -> None:
        self.cancel_requested = True
        self.result = {
            "status": P.STATUS_ERROR,
            "error": reason,
            "error_code": P.ERR_CANCELLED,
        }
        self.state = P.JOB_CANCELLED
        self.finished_at = time.monotonic()
        self._emit(
            {
                "event": P.EVENT_CANCELLED,
                "job_id": self.job_id,
                "backend": self.backend,
                "state": self.state,
                "error": reason,
                "error_code": P.ERR_CANCELLED,
            }
        )
        self.done_event.set()

    def timing_dict(self) -> dict[str, float | None]:
        """Timings do job (segundos) para ``ums_debug`` / poll."""
        queue_wait: float | None = None
        generate_sec: float | None = None
        total_sec: float | None = None
        if self.started_at is not None:
            queue_wait = round(self.started_at - self.created_at, 3)
            end = self.finished_at if self.finished_at is not None else time.monotonic()
            generate_sec = round(end - self.started_at, 3)
        elif self.state == P.JOB_QUEUED:
            queue_wait = round(time.monotonic() - self.created_at, 3)
        if self.finished_at is not None:
            total_sec = round(self.finished_at - self.created_at, 3)
        return {
            "queue_wait_sec": queue_wait,
            "generate_sec": generate_sec,
            "total_sec": total_sec,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Snapshot serializável para status/queue/poll."""
        timing = self.timing_dict()
        return {
            "job_id": self.job_id,
            "backend": self.backend,
            "priority": self.priority,
            "state": self.state,
            "affinity_cuts": self.affinity_cuts,
            "seq": self.seq,
            "queue_wait_sec": timing["queue_wait_sec"],
            "generate_sec": timing["generate_sec"],
            "total_sec": timing["total_sec"],
            "progress_pct": self.progress_pct,
            "progress_msg": self.progress_msg,
            "cancel_requested": self.cancel_requested,
            "error": (self.result or {}).get("error") if self.state in (P.JOB_FAILED, P.JOB_CANCELLED) else None,
        }


class JobQueue:
    """Inventário thread-safe de jobs + backpressure."""

    def __init__(self, *, max_depth: int = P.MAX_QUEUE_DEPTH) -> None:
        self.max_depth = max_depth
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._jobs: dict[str, Job] = {}
        self._queued: list[str] = []  # job_ids in arrival order (scheduler reorders)
        self._seq = 0
        self._inflight = 0
        self._running_ids: list[str] = []

    def __len__(self) -> int:
        with self._lock:
            return len(self._queued)

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._queued)

    @property
    def inflight(self) -> int:
        with self._lock:
            return self._inflight

    def enqueue(
        self,
        backend: str,
        request: dict[str, Any],
        *,
        priority: str | None = None,
    ) -> Job:
        """Cria e enfileira um job. Levanta ``QueueFullError`` se saturado."""
        pri = P.normalize_priority(priority if priority is not None else request.get("priority"))
        with self._cond:
            if len(self._queued) >= self.max_depth:
                raise QueueFullError(f"fila cheia ({self.max_depth})")
            self._seq += 1
            job = Job(
                job_id=str(uuid.uuid4()),
                backend=backend,
                request=dict(request),
                priority=pri,
                seq=self._seq,
            )
            self._jobs[job.job_id] = job
            self._queued.append(job.job_id)
            job._emit(
                {
                    "event": P.EVENT_QUEUED,
                    "job_id": job.job_id,
                    "backend": job.backend,
                    "priority": job.priority,
                    "state": job.state,
                    "queue_position": len(self._queued),
                }
            )
            self._cond.notify_all()
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def queued_jobs(self) -> list[Job]:
        """Jobs ainda em ``queued`` (ordem de chegada; o scheduler reordena)."""
        with self._lock:
            return [self._jobs[jid] for jid in self._queued if jid in self._jobs]

    def take(self, job_id: str) -> Job | None:
        """Remove o job da fila queued e marca-o running (worker)."""
        with self._lock:
            if job_id not in self._queued:
                return None
            self._queued.remove(job_id)
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.cancel_requested or job.state == P.JOB_CANCELLED:
                return None
            self._inflight += 1
            self._running_ids.append(job_id)
            # Estado running aqui para cancel() distinguir de queued
            # (mark_started no worker emite o evento NDJSON).
            job.state = P.JOB_RUNNING
            return job

    def finish(self, job: Job, result: dict[str, Any]) -> None:
        with self._cond:
            if job.job_id in self._running_ids:
                self._running_ids.remove(job.job_id)
            self._inflight = max(0, self._inflight - 1)
            if job.cancel_requested and result.get("status") == P.STATUS_OK:
                # Generate terminou mas cancel foi pedido a meio — reportar cancel.
                job.mark_cancelled("cancelled during run")
            else:
                job.mark_finished(result)
            self._cond.notify_all()

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancela um job. Queued: remove já. Running: marca flag best-effort."""
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None:
                return {
                    "status": P.STATUS_ERROR,
                    "error": f"job desconhecido: {job_id}",
                    "error_code": P.ERR_JOB_UNKNOWN,
                    "hint": "Lista jobs com gamedev-model-server queue",
                }
            if job.state in (P.JOB_DONE, P.JOB_FAILED, P.JOB_CANCELLED):
                return {
                    "status": P.STATUS_OK,
                    "job_id": job_id,
                    "state": job.state,
                    "message": "job já terminado",
                    "ums_debug": {
                        "job_id": job.job_id,
                        "backend": job.backend,
                        "priority": job.priority,
                        "state": job.state,
                        **job.timing_dict(),
                    },
                }
            if job.state == P.JOB_QUEUED:
                if job_id in self._queued:
                    self._queued.remove(job_id)
                job.mark_cancelled("cancelled while queued")
                self._cond.notify_all()
                return {"status": P.STATUS_OK, "job_id": job_id, "state": P.JOB_CANCELLED}
            # running
            job.cancel_requested = True
            return {
                "status": P.STATUS_OK,
                "job_id": job_id,
                "state": P.JOB_RUNNING,
                "message": "cancel requested (best-effort; aguarda fim do generate)",
            }

    def wait(self, job_id: str, *, timeout_sec: float | None = None) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        job.done_event.wait(timeout=timeout_sec)
        return job

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            queued = [self._jobs[jid].to_public_dict() for jid in self._queued if jid in self._jobs]
            running = [self._jobs[jid].to_public_dict() for jid in self._running_ids if jid in self._jobs]
            return {
                "queue_depth": len(queued),
                "inflight": self._inflight,
                "max_depth": self.max_depth,
                "queued": queued,
                "running": running,
            }

    def wait_for_work(self, timeout: float = 0.5) -> bool:
        """Espera até haver jobs na fila ou timeout. Retorna True se há trabalho."""
        with self._cond:
            if self._queued:
                return True
            self._cond.wait(timeout=timeout)
            return bool(self._queued)

    def notify(self) -> None:
        with self._cond:
            self._cond.notify_all()
