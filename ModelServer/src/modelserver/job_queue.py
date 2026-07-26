"""Fila de jobs do UMS — enqueue, cancel, backpressure, wait.

Os jobs são despachados pelo ``AffinityScheduler`` + ``WorkerPool``; esta classe
só gere o inventário e a sincronização com os clientes.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
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
    vram_retries: int = 0  # requeues por VRAM_INSUFFICIENT transitória
    worker_retries: int = 0  # requeues por worker subprocesso morto (transitório)
    # Tracking de progresso do retry: falhar rápido quando a VRAM livre fica
    # plana e não há nada evictável (loop improdutivo — nunca mais acontece).
    vram_flat_retries: int = 0
    last_vram_free_mib: int | None = None
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

    def remove_listener(self, listener: ProgressListener) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._listeners.remove(listener)

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
            "vram_retries": self.vram_retries,
            "seq": self.seq,
            "queue_wait_sec": timing["queue_wait_sec"],
            "generate_sec": timing["generate_sec"],
            "total_sec": timing["total_sec"],
            "progress_pct": self.progress_pct,
            "progress_msg": self.progress_msg,
            "cancel_requested": self.cancel_requested,
            "error": (self.result or {}).get("error") if self.state in (P.JOB_FAILED, P.JOB_CANCELLED) else None,
        }


_WAL_STATE_MAP = {
    P.JOB_DONE: "done",
    P.JOB_FAILED: "failed",
    P.JOB_CANCELLED: "cancelled",
}

# Retenção de jobs terminais em memória (poll/wait pós-fim continuam a funcionar
# dentro desta janela; depois são purgados para não crescer sem limite).
_FINISHED_TTL_SEC = 600.0
_MAX_FINISHED_JOBS = 256


class JobQueue:
    """Inventário thread-safe de jobs + backpressure."""

    def __init__(
        self,
        *,
        max_depth: int = P.MAX_QUEUE_DEPTH,
        stats: Any | None = None,
        wal_path: Path | str | None = None,
    ) -> None:
        self.max_depth = max_depth
        self.stats = stats  # StatsCollector opcional
        self._wal_path = Path(wal_path) if wal_path is not None else None
        self._wal_lock = threading.Lock()
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._jobs: dict[str, Job] = {}
        self._queued: list[str] = []  # job_ids in arrival order (scheduler reorders)
        self._seq = 0
        self._inflight = 0
        self._running_ids: list[str] = []

    def _append_wal(self, record: dict[str, Any]) -> None:
        if self._wal_path is None:
            return
        payload = {**record, "ts": time.time()}
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self._wal_lock:
            self._wal_path.parent.mkdir(parents=True, exist_ok=True)
            with self._wal_path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def _rewrite_wal_from_queue(self) -> None:
        if self._wal_path is None:
            return
        with self._wal_lock:
            with self._lock:
                lines = [
                    json.dumps(
                        {
                            "op": "enqueue",
                            "job_id": job.job_id,
                            "backend": job.backend,
                            "priority": job.priority,
                            "request": job.request,
                            "ts": time.time(),
                        },
                        ensure_ascii=False,
                    )
                    for jid in self._queued
                    if (job := self._jobs.get(jid)) is not None
                ]
            text = "\n".join(lines) + ("\n" if lines else "")
            tmp = self._wal_path.with_suffix(".jsonl.tmp")
            self._wal_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self._wal_path)

    def replay_from_wal(self) -> int:
        """Re-enfileira jobs pendentes/interrompidos a partir do WAL JSONL."""
        if self._wal_path is None or not self._wal_path.exists():
            return 0

        job_meta: dict[str, dict[str, Any]] = {}
        latest: dict[str, dict[str, Any]] = {}
        with self._wal_path.open(encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                op = rec.get("op")
                job_id = rec.get("job_id")
                if not op or not job_id:
                    continue
                if op == "enqueue":
                    backend = rec.get("backend")
                    if not backend:
                        continue  # registo válido mas incompleto (WAL corrupto/editado)
                    job_meta[str(job_id)] = {
                        "backend": backend,
                        "priority": rec.get("priority", P.DEFAULT_PRIORITY),
                        "request": rec.get("request", {}),
                    }
                    latest[str(job_id)] = {"phase": "queued"}
                elif op == "started":
                    latest[str(job_id)] = {"phase": "started"}
                elif op == "finished":
                    latest[str(job_id)] = {"phase": "finished", "state": rec.get("state")}

        requeued = 0
        for job_id, state in latest.items():
            phase = state.get("phase")
            if phase == "finished":
                continue
            meta = job_meta.get(job_id)
            if meta is None:
                continue
            self.enqueue(
                meta["backend"],
                meta["request"],
                priority=meta["priority"],
                _replay=True,
            )
            requeued += 1

        self._rewrite_wal_from_queue()
        return requeued

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
        _replay: bool = False,
    ) -> Job:
        """Cria e enfileira um job. Levanta ``QueueFullError`` se saturado."""
        pri = P.normalize_priority(priority if priority is not None else request.get("priority"))
        with self._cond:
            if len(self._queued) >= self.max_depth:
                if self.stats is not None:
                    self.stats.record_queue_full()
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
            depth = len(self._queued)
            if self.stats is not None:
                self.stats.record_enqueue(depth_after=depth)
            job._emit(
                {
                    "event": P.EVENT_QUEUED,
                    "job_id": job.job_id,
                    "backend": job.backend,
                    "priority": job.priority,
                    "state": job.state,
                    "queue_position": depth,
                }
            )
            if not _replay:
                self._append_wal(
                    {
                        "op": "enqueue",
                        "job_id": job.job_id,
                        "backend": job.backend,
                        "priority": job.priority,
                        "request": job.request,
                    }
                )
            self._cond.notify_all()
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def resolve_job_id(self, job_id_or_prefix: str) -> tuple[str | None, str | None]:
        """Resolve UUID completo ou prefixo único.

        Returns:
            ``(job_id, None)`` se ok; ``(None, erro)`` se desconhecido/ambíguo.
        """
        key = (job_id_or_prefix or "").strip()
        if not key:
            return None, "job_id vazio"
        with self._lock:
            if key in self._jobs:
                return key, None
            matches = [jid for jid in self._jobs if jid.startswith(key)]
            if not matches:
                return None, f"job desconhecido: {key}"
            active = [jid for jid in matches if self._jobs[jid].state in (P.JOB_QUEUED, P.JOB_RUNNING)]
            pool = active or matches
            if len(pool) == 1:
                return pool[0], None
            return None, f"prefixo ambíguo {key!r}: {len(pool)} jobs (usa mais caracteres)"

    def queued_jobs(self) -> list[Job]:
        """Jobs ainda em ``queued`` (ordem de chegada; o scheduler reordena)."""
        with self._lock:
            return [self._jobs[jid] for jid in self._queued if jid in self._jobs]

    def running_jobs(self) -> list[Job]:
        """Jobs actualmente running (para ETA/status sem serializar snapshot)."""
        with self._lock:
            return [self._jobs[jid] for jid in self._running_ids if jid in self._jobs]

    def _purge_finished_jobs(self) -> None:
        """Remove jobs terminais antigos/excedentes. Caller deve ter ``self._lock``.

        Sem isto ``_jobs`` crescia sem limite (request+result+listeners de cada
        job ficavam retidos para sempre num daemon long-lived).
        """
        now = time.monotonic()
        terminal = [
            j
            for j in self._jobs.values()
            if j.state in (P.JOB_DONE, P.JOB_FAILED, P.JOB_CANCELLED) and j.finished_at is not None
        ]
        victim_ids = {j.job_id for j in terminal if now - (j.finished_at or now) > _FINISHED_TTL_SEC}
        excess = len(terminal) - _MAX_FINISHED_JOBS
        if excess > 0:
            terminal.sort(key=lambda j: j.finished_at or 0.0)
            victim_ids.update(j.job_id for j in terminal[:excess])
        for jid in victim_ids:
            self._jobs.pop(jid, None)

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
            self._append_wal({"op": "started", "job_id": job_id})
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
            self._append_wal(
                {
                    "op": "finished",
                    "job_id": job.job_id,
                    "state": _WAL_STATE_MAP.get(job.state, "failed"),
                }
            )
            if self.stats is not None:
                timing = job.timing_dict()
                cancelled = job.state == P.JOB_CANCELLED
                self.stats.record_job_finished(
                    wait_sec=timing.get("queue_wait_sec"),
                    affinity_cuts=job.affinity_cuts,
                    cancelled=cancelled,
                )
            self._purge_finished_jobs()
            self._cond.notify_all()

    def requeue_running(
        self,
        job: Job,
        *,
        reason: str = "vram wait",
        kind: str = "vram",
    ) -> bool:
        """Devolve job ``running`` → ``queued`` (VRAM / worker-dead transitório).

        Decrementa inflight, NÃO termina o job (``done_event`` fica limpo).
        Coloca à frente da fila para retentar cedo após backoff no worker.

        Args:
            kind: ``\"vram\"`` incrementa ``vram_retries``; ``\"worker\"``
                incrementa ``worker_retries``.

        Returns:
            ``True`` se o job foi reenfileirado; ``False`` se já não estava running
            / foi cancelado.
        """
        with self._cond:
            if job.cancel_requested or job.state == P.JOB_CANCELLED:
                return False
            if job.job_id not in self._running_ids and job.state != P.JOB_RUNNING:
                return False
            if job.job_id in self._running_ids:
                self._running_ids.remove(job.job_id)
            self._inflight = max(0, self._inflight - 1)
            job.state = P.JOB_QUEUED
            job.started_at = None
            if kind == "worker":
                job.worker_retries += 1
                progress_msg = f"worker retry {job.worker_retries}/{P.MAX_WORKER_DEAD_RETRIES}: {reason}"
            else:
                job.vram_retries += 1
                progress_msg = f"vram retry {job.vram_retries}/{P.MAX_VRAM_RETRIES}: {reason}"
            job.result = None
            # Frente: retentar este job assim que o backoff no worker acabar.
            if job.job_id in self._queued:
                self._queued.remove(job.job_id)
            self._queued.insert(0, job.job_id)
            job.report_progress(None, progress_msg)
            self._append_wal(
                {
                    "op": "requeue",
                    "job_id": job.job_id,
                    "reason": reason,
                    "kind": kind,
                    "vram_retries": job.vram_retries,
                    "worker_retries": job.worker_retries,
                }
            )
            self._cond.notify_all()
            return True

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancela um job. Queued: remove já. Running: marca flag best-effort.

        Aceita UUID completo ou prefixo único (como no CLI ``queue``).
        """
        resolved, err = self.resolve_job_id(job_id)
        if resolved is None:
            return {
                "status": P.STATUS_ERROR,
                "error": err or f"job desconhecido: {job_id}",
                "error_code": P.ERR_JOB_UNKNOWN,
                "hint": "Lista jobs com gamedev-model-server queue; cancel --all limpa a fila",
            }
        job_id = resolved
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
                self._append_wal(
                    {
                        "op": "finished",
                        "job_id": job_id,
                        "state": "cancelled",
                    }
                )
                if self.stats is not None:
                    timing = job.timing_dict()
                    self.stats.record_job_finished(
                        wait_sec=timing.get("queue_wait_sec"),
                        affinity_cuts=job.affinity_cuts,
                        cancelled=True,
                    )
                self._purge_finished_jobs()
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

    def cancel_all(self, *, include_running: bool = True) -> dict[str, Any]:
        """Cancela todos os queued; opcionalmente pede cancel aos running."""
        cancelled_queued: list[str] = []
        cancel_requested_running: list[str] = []
        with self._cond:
            for jid in list(self._queued):
                job = self._jobs.get(jid)
                if job is None:
                    continue
                self._queued.remove(jid)
                job.mark_cancelled("cancelled by flush/cancel --all")
                self._append_wal({"op": "finished", "job_id": jid, "state": "cancelled"})
                if self.stats is not None:
                    timing = job.timing_dict()
                    self.stats.record_job_finished(
                        wait_sec=timing.get("queue_wait_sec"),
                        affinity_cuts=job.affinity_cuts,
                        cancelled=True,
                    )
                cancelled_queued.append(jid)
            if include_running:
                for jid in list(self._running_ids):
                    job = self._jobs.get(jid)
                    if job is None or job.state != P.JOB_RUNNING:
                        continue
                    job.cancel_requested = True
                    cancel_requested_running.append(jid)
            self._purge_finished_jobs()
            self._cond.notify_all()
        return {
            "status": P.STATUS_OK,
            "cancelled_queued": cancelled_queued,
            "cancel_requested_running": cancel_requested_running,
            "count": len(cancelled_queued) + len(cancel_requested_running),
            "message": (
                f"{len(cancelled_queued)} queued cancelados; {len(cancel_requested_running)} running com cancel pedido"
            ),
        }

    def wait(self, job_id: str, *, timeout_sec: float | None = None) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        job.done_event.wait(timeout=timeout_sec)
        return job

    def queue_position(self, job_id: str) -> int | None:
        """Posição 1-based na fila queued, ou None se não estiver queued."""
        with self._lock:
            try:
                return self._queued.index(job_id) + 1
            except ValueError:
                return None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            queued = [self._jobs[jid].to_public_dict() for jid in self._queued if jid in self._jobs]
            running = [self._jobs[jid].to_public_dict() for jid in self._running_ids if jid in self._jobs]
            out: dict[str, Any] = {
                "queue_depth": len(queued),
                "inflight": self._inflight,
                "max_depth": self.max_depth,
                "queued": queued,
                "running": running,
            }
            if self.stats is not None:
                out["metrics"] = self.stats.queue_dict()
            return out

    def wait_for_work(self, timeout: float = 0.5) -> bool:
        """Espera até haver jobs na fila ou timeout. Retorna True se há trabalho."""
        with self._cond:
            if self._queued:
                return True
            self._cond.wait(timeout=timeout)
            return bool(self._queued)

    def wait_for_slot(self, timeout: float = 0.5) -> None:
        """Bloqueia até notificação (enqueue/finish/cancel) ou timeout.

        Usado pelo worker quando há trabalho na fila mas nada despachável
        (slot cheio / VRAM não chega) — evita busy-spin a queimar CPU e a
        consultar NVML/nvidia-smi em loop apertado. O timeout limita a latência
        de re-poll de VRAM livre externa.
        """
        with self._cond:
            self._cond.wait(timeout=timeout)

    def notify(self) -> None:
        with self._cond:
            self._cond.notify_all()
