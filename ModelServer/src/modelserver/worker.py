"""WorkerPool — despacha jobs da fila com ``MAX_INFLIGHT`` gerações paralelas.

Default ``MAX_INFLIGHT=1``: uma geração de cada vez na GPU. Com ``MAX_INFLIGHT>1``,
só arranca jobs em paralelo se a VRAM livre couber o footprint do candidato.

``VRAM_INSUFFICIENT`` transitória (livre < pico mas pico ≤ VRAM total): evict +
backoff + requeue até ``MAX_VRAM_RETRIES``. Pico > total GPU → falha imediata.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any

from aigamekit_shared.diffusion_control import GenerationAborted
from aigamekit_shared.logging import Logger

from . import protocol as P
from .backend_manager import BackendManager
from .job_queue import Job, JobQueue
from .scheduler import AffinityScheduler

_logger = Logger()


def _is_vram_insufficient(result: dict[str, Any]) -> bool:
    if result.get("error_code") == P.ERR_VRAM_INSUFFICIENT:
        return True
    err = str(result.get("error") or "").lower()
    return "vram insuficiente" in err or "out of memory" in err


def _is_worker_dead(result: dict[str, Any]) -> bool:
    """Worker subprocesso morto / load incompleto — requeue transitório."""
    if result.get("error_code") == P.ERR_WORKER_DEAD:
        return True
    err = str(result.get("error") or "").lower()
    return "não está vivo" in err or "nao esta vivo" in err or "worker fechou stdout" in err or "eof no load" in err


def _vram_retry_worthwhile(result: dict[str, Any], total_mib: int | None) -> bool:
    """False se o pico nunca cabe na GPU (sem retry)."""
    peak = result.get("peak_mib")
    if peak is None or total_mib is None:
        return True
    try:
        return int(peak) <= int(total_mib)
    except (TypeError, ValueError):
        return True


def _vram_backoff_sec(retries_done: int) -> float:
    """Backoff exponencial após N retries já consumidos (0-based next attempt)."""
    base = max(0.05, float(P.VRAM_RETRY_BASE_SEC))
    cap = max(base, float(P.VRAM_RETRY_MAX_SEC))
    return min(cap, base * (2 ** max(0, retries_done)))


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
        query_free_mib: Callable[[], int | None] | None = None,
    ) -> None:
        self.queue = queue
        self.manager = manager
        self.scheduler = scheduler if scheduler is not None else AffinityScheduler()
        self.max_inflight = max(1, max_inflight)
        self.verbose = verbose
        self._query_free_mib = query_free_mib
        self._threads: list[threading.Thread] = []
        self._running = False
        self._stop = threading.Event()
        self._affinity_hits = 0

    def _log(self, msg: str) -> None:
        if self.verbose:
            _logger.info(f"[UMS-worker] {msg}")

    def _free_mib(self) -> int | None:
        if self._query_free_mib is not None:
            return self._query_free_mib()
        try:
            from aigamekit_shared.gpu import query_gpu_free_mib

            return query_gpu_free_mib()
        except Exception:
            return None

    def _total_mib(self) -> int | None:
        try:
            from aigamekit_shared.gpu import query_gpu_snapshot

            snap = query_gpu_snapshot()
            if snap is None:
                return None
            total = getattr(snap, "total_mib", None)
            if total is None:
                total = getattr(snap, "memory_total_mib", None)
            return int(total) if total is not None else None
        except Exception:
            return None

    def _evict_and_clear(self) -> None:
        with contextlib.suppress(Exception):
            self.manager.evict_all()
        clear = getattr(self.manager, "_clear_cache", None)
        if callable(clear):
            with contextlib.suppress(Exception):
                clear()

    def _sleep_interruptible(self, job: Job, seconds: float) -> bool:
        """Sleep em fatias; ``False`` se cancel pedido."""
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if job.cancel_requested or self._stop.is_set():
                return False
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        return not job.cancel_requested

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
                # Há trabalho mas nada despachável (slot cheio ou VRAM não
                # chega): esperar por mudança de estado em vez de spin — cada
                # iteração de spin consultava NVML/nvidia-smi e inflacionava
                # affinity_hits/affinity_cuts sem dispatch real.
                self.queue.wait_for_slot(timeout=0.5)
                continue
            self._run_job(job)

    def _backend_peak_mib(self, name: str, request: dict[str, Any] | None = None) -> int:
        """Pico pesos+activação+safety (não o YAML estático)."""
        try:
            quant, mem_eff, group_off, streams = self.manager.resolve_peak_params(name, request or {})
            return int(
                self.manager.peak_vram_mib(
                    name,
                    quant_mode=quant,
                    memory_efficient=mem_eff,
                    group_offload=group_off or streams,
                    footprint_key=(request or {}).get("footprint_key"),
                )
            )
        except Exception:
            try:
                return int(self.manager._registry.descriptor(name).vram_mib)
            except Exception:
                return 0

    def _fits_parallel(self, job: Job) -> bool:
        """Com already-inflight>0, só permite se VRAM livre couber o pico/headroom."""
        if self.queue.inflight <= 0:
            return True
        if self.max_inflight <= 1:
            return False

        loaded = set(self.manager.loaded_names())
        free = self._free_mib()

        # Hot = backend carregado **e** load_shape compatível (senão = cold reload).
        if job.backend in loaded and self._job_is_hot(job):
            if free is None:
                return True
            try:
                _q, mem_eff, group_off, streams = self.manager.resolve_peak_params(job.backend, job.request)
                needed = self.manager.activation_headroom_mib(
                    job.backend,
                    quant_mode=_q,
                    memory_efficient=mem_eff,
                    group_offload=group_off or streams,
                    footprint_key=job.request.get("footprint_key"),
                )
            except Exception:
                needed = 512
            return free >= needed

        if free is None:
            # Sem leitura VRAM (NVML/smi): não arriscar segundo cold load.
            return False
        return free >= self._backend_peak_mib(job.backend, job.request)

    def _job_is_hot(self, job: Job) -> bool:
        """Backend loaded + load_shape match (duck-typed para mocks de teste)."""
        fn = getattr(self.manager, "shape_matches_loaded", None)
        if callable(fn):
            return bool(fn(job.backend, job.request))
        return job.backend in set(self.manager.loaded_names())

    def _claim_next(self) -> Job | None:
        jobs = self.queue.queued_jobs()
        if not jobs:
            return None
        # Já no máximo efectivo de threads; cada thread só pega se cabe.
        if self.queue.inflight >= self.max_inflight:
            return None
        picked = self.scheduler.pick_next(
            jobs,
            self.manager.loaded_names(),
            is_hot=self._job_is_hot,
        )
        if picked is None:
            return None
        if not self._fits_parallel(picked):
            self._log(
                f"Skip parallel job {picked.job_id[:8]} backend={picked.backend!r} "
                f"(VRAM insuficiente ou free desconhecido; inflight={self.queue.inflight})"
            )
            return None
        job = self.queue.take(picked.job_id)
        if job is not None and self._job_is_hot(job):
            # Métrica: dispatches reais para backend já quente (não avaliações).
            self._affinity_hits += 1
        return job

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
        self._log(f"A correr job {job.job_id[:8]} backend={job.backend!r} cuts={job.affinity_cuts} pri={job.priority}")

        def on_progress(pct: float | None = None, msg: str | None = None) -> None:
            if job.cancel_requested:
                return
            job.report_progress(pct, msg)

        def should_abort() -> bool:
            return bool(job.cancel_requested)

        try:
            req = dict(job.request)
            req["_progress"] = on_progress
            req["_abort"] = should_abort
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
        except GenerationAborted:
            result = {
                "status": P.STATUS_ERROR,
                "error": "cancelled during diffusion",
                "error_code": P.ERR_CANCELLED,
            }
        except Exception as e:
            _logger.warn(f"[UMS-worker] job {job.job_id[:8]} falhou: {e}")
            result = {
                "status": P.STATUS_ERROR,
                "error": str(e),
                "error_code": P.ERR_GENERATE_FAILED,
            }

        # Worker morto transitório: limpar + requeue (IdleEvictor race / crash).
        if (
            result.get("status") != P.STATUS_OK
            and _is_worker_dead(result)
            and not job.cancel_requested
            and job.worker_retries < P.MAX_WORKER_DEAD_RETRIES
        ):
            msg = f"worker morto — reload+retry ({job.worker_retries + 1}/{P.MAX_WORKER_DEAD_RETRIES})"
            _logger.warn(f"[UMS-worker] job {job.job_id[:8]} {msg}: {result.get('error')}")
            on_progress(None, msg)
            with contextlib.suppress(Exception):
                self.manager.evict(job.backend)
            if self.queue.requeue_running(job, reason="worker dead", kind="worker"):
                return
            if job.cancel_requested:
                result = {
                    "status": P.STATUS_ERROR,
                    "error": "cancelled during worker retry",
                    "error_code": P.ERR_CANCELLED,
                }

        # VRAM transitória: evict + backoff + requeue (não matar o batch).
        if (
            result.get("status") != P.STATUS_OK
            and _is_vram_insufficient(result)
            and not job.cancel_requested
            and _vram_retry_worthwhile(result, self._total_mib())
            and job.vram_retries < P.MAX_VRAM_RETRIES
            and job.vram_flat_retries < P.VRAM_FLAT_RETRY_MAX
        ):
            wait_sec = _vram_backoff_sec(job.vram_retries)
            free = self._free_mib()
            peak = result.get("peak_mib")
            msg = (
                f"VRAM insuficiente (livre={free} peak={peak}) — "
                f"evict+espera {wait_sec:.1f}s "
                f"(retry {job.vram_retries + 1}/{P.MAX_VRAM_RETRIES})"
            )
            _logger.warn(f"[UMS-worker] job {job.job_id[:8]} {msg}")
            on_progress(None, msg)
            # Progress-guard: retry só compensa se (a) havia backends evictáveis
            # (libertámos pesos agora) ou (b) a VRAM livre se mexeu (processo
            # externo activo — pode libertar). Livre plana + 0 loaded N vezes
            # seguidas = loop improdutivo (ex.: memória presa pelo próprio
            # servidor) → falhar rápido com hint em vez de 8x30s às cegas.
            had_evictable = bool(self.manager.loaded_names())
            self._evict_and_clear()
            free_after = self._free_mib()
            moved = free is not None and free_after is not None and abs(free_after - free) > P.VRAM_FLAT_SLACK_MIB
            # Leitura de free desconhecida (sem NVML) → conservador: retry normal.
            if had_evictable or moved or free is None or free_after is None:
                job.vram_flat_retries = 0
            else:
                job.vram_flat_retries += 1
                if job.vram_flat_retries >= P.VRAM_FLAT_RETRY_MAX:
                    _logger.warn(
                        f"[UMS-worker] job {job.job_id[:8]} VRAM plana "
                        f"{job.vram_flat_retries}x seguidas sem nada evictável "
                        f"(livre={free_after} peak={peak}) — falha rápida (loop improdutivo)."
                    )
                else:
                    job.last_vram_free_mib = free_after
            if (
                job.vram_flat_retries < P.VRAM_FLAT_RETRY_MAX
                and self._sleep_interruptible(job, wait_sec)
                and self.queue.requeue_running(job, reason=f"livre={free} peak={peak}")
            ):
                return
            if job.cancel_requested:
                result = {
                    "status": P.STATUS_ERROR,
                    "error": "cancelled during vram wait",
                    "error_code": P.ERR_CANCELLED,
                }

        if _is_vram_insufficient(result) and result.get("status") != P.STATUS_OK:
            result.setdefault("error_code", P.ERR_VRAM_INSUFFICIENT)
            if job.vram_flat_retries >= P.VRAM_FLAT_RETRY_MAX:
                result.setdefault(
                    "hint",
                    (
                        "VRAM livre não subiu após retries e não há backends evictáveis "
                        "(memória pode estar presa por processo externo — ver `nvidia-smi` "
                        "e `ums status`/`debug` para o breakdown do worker). Não mates PIDs GPU."
                    ),
                )
            else:
                result.setdefault(
                    "hint",
                    (
                        "VRAM livre < pico após retries. Espera `ums queue` libertar, "
                        "fecha processos GPU externos, ou usa sdnq-int4 / quality fast. "
                        "Não mates PIDs — vê Agents/anti-patterns no README UMS."
                    ),
                )
            result["vram_retries"] = job.vram_retries

        if _is_worker_dead(result) and result.get("status") != P.STATUS_OK:
            result.setdefault("error_code", P.ERR_WORKER_DEAD)
            result.setdefault(
                "hint",
                (
                    "Worker subprocesso morreu durante o job. UMS já retentou; "
                    "se persistir: `ums respawn <backend>` / `ums doctor`."
                ),
            )
            result["worker_retries"] = job.worker_retries

        self.queue.finish(job, result)


# Tipo auxiliar para testes / DI.
GenerateFn = Callable[[str, dict[str, Any]], dict[str, Any]]
