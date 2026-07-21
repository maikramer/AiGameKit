"""UnifiedModelServer — servidor único que roteia pedidos para backends.

Um único processo detém toda a VRAM e escuta num único Unix socket. Pedidos de
geração passam por uma **fila inteligente** (prioridade + afinidade VRAM) e um
worker pool com ``MAX_INFLIGHT`` (default 1).

Protocolo: ver ``protocol.py``. Ciclo de vida: ver ``serve_forever``.

Retrocompatibilidade: o socket único (``model-server.sock``) é descoberto por
``gamedev_shared.model_server.discover_active_sockets`` como qualquer per-tool
legacy server. ``ensure_vram_available`` envia ``ensure-vram`` ao UMS quando
disponível, caindo para o comportamento legacy caso contrário.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Any

from gamedev_shared.logging import Logger
from gamedev_shared.model_server import _ensure_server_dir, _pid_path, is_server_running

from . import protocol as P
from .backend_manager import BackendManager
from .job_queue import Job, JobQueue, QueueFullError
from .registry import Registry
from .scheduler import AffinityScheduler
from .worker import WorkerPool

_logger = Logger()


class UnifiedModelServer:
    """Servidor único de VRAM — fila inteligente + BackendManager.

    Args:
        registry: Registry de backends. Se ``None``, carrega do ``backends.yaml`` default.
        socket_path: Path do socket. Se ``None``, usa ``DEFAULT_SOCKET_PATH``.
        idle_timeout_min: Minutos de idle (sem pedidos) antes de self-shutdown.
        verbose: Logs detalhados.
    """

    def __init__(
        self,
        registry: Registry | None = None,
        *,
        socket_path: Path | str | None = None,
        idle_timeout_min: int = P.DEFAULT_IDLE_TIMEOUT_MIN,
        idle_evict_sec: float = 600.0,
        max_queue_depth: int = P.MAX_QUEUE_DEPTH,
        max_inflight: int = P.MAX_INFLIGHT,
        max_affinity_cuts: int = P.MAX_AFFINITY_CUTS,
        verbose: bool = False,
        query_free_mib: Any = None,
        clear_vram: Any = None,
    ) -> None:
        self.registry = registry if registry is not None else Registry()
        self.manager = BackendManager(self.registry, query_free_mib=query_free_mib, clear_vram=clear_vram)
        self.socket_path = Path(socket_path) if socket_path else P.DEFAULT_SOCKET_PATH
        self.ppid_path = _pid_path(self.socket_path)
        self.idle_timeout_sec = idle_timeout_min * 60
        self.verbose = verbose

        wal_path = self.socket_path.parent / P.WAL_FILENAME
        self.queue = JobQueue(max_depth=max_queue_depth, stats=self.manager.stats, wal_path=wal_path)
        self.scheduler = AffinityScheduler(
            max_cuts=max_affinity_cuts,
            starvation_timeout_sec=P.STARVATION_TIMEOUT_SEC,
        )
        self.workers = WorkerPool(
            self.queue,
            self.manager,
            self.scheduler,
            max_inflight=max_inflight,
            verbose=verbose,
        )

        from .idle_evictor import IdleEvictor

        self.idle_evictor = IdleEvictor(self.manager, idle_timeout_sec=idle_evict_sec)

        self._server_sock: socket.socket | None = None
        self._bound = False  # True só após bind+listen OK (protege cleanup em double-start)
        self._running = False
        self._last_activity = time.monotonic()
        self._requests_served = 0
        self._pid = os.getpid()

    def _log(self, msg: str) -> None:
        # Sempre ficheiro; consola só com --verbose.
        _logger.info(f"[UMS] {msg}", console=self.verbose)

    # ------------------------------------------------------------------
    # Despacho de comandos
    # ------------------------------------------------------------------

    def _resolve_backend(self, request: dict[str, Any]) -> str | None:
        """Resolve o nome do backend a partir do request.

        Ordem: ``backend`` explícito → ``tool`` field → único backend carregado.
        Retorna ``None`` se não for possível determinar.
        """
        backend = request.get("backend")
        if backend:
            return str(backend)
        tool = request.get("tool")
        if tool:
            return str(tool)
        loaded = self.manager.loaded_names()
        if len(loaded) == 1:
            return loaded[0]
        return None

    def _error(
        self,
        error: str,
        *,
        error_code: str,
        hint: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Resposta de erro estruturada para debug."""
        out: dict[str, Any] = {
            "status": P.STATUS_ERROR,
            "error": error,
            "error_code": error_code,
        }
        if hint:
            out["hint"] = hint
        out.update(extra)
        return out

    def _backend_error(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Valida backend; devolve dict de erro ou None se OK. Também define backend resolvido."""
        backend = self._resolve_backend(request)
        if backend is None:
            loaded = self.manager.loaded_names()
            known = list(self.registry.names)
            hint = (
                f'Backends carregados: {loaded or "nenhum"}. Registados: {known}. Passa "backend": "<nome>" no request.'
            )
            return self._error(
                "backend ambíguo — especifica 'backend' no request",
                error_code=P.ERR_BACKEND_AMBIGUOUS,
                hint=hint,
                loaded_backends=loaded,
                known_backends=known,
            )
        if not self.registry.has(backend):
            known = list(self.registry.names)
            return self._error(
                f"backend desconhecido: {backend}",
                error_code=P.ERR_BACKEND_UNKNOWN,
                hint=f"Backends válidos: {known}",
                known_backends=known,
            )
        request["_resolved_backend"] = backend
        return None

    def _enqueue_from_request(self, request: dict[str, Any]) -> Job | dict[str, Any]:
        """Enfileira a partir de generate/submit. Retorna Job ou dict de erro."""
        err = self._backend_error(request)
        if err is not None:
            return err
        backend = str(request["_resolved_backend"])
        # Não persistir campos internos no job request.
        job_req = {k: v for k, v in request.items() if not k.startswith("_") and k not in ("cmd", "stream")}
        try:
            job = self.queue.enqueue(backend, job_req, priority=request.get("priority"))
        except QueueFullError as e:
            snap = self.queue.snapshot()
            loaded = self.manager.loaded_names()
            return {
                "status": P.STATUS_QUEUE_FULL,
                "error": str(e),
                "error_code": P.ERR_QUEUE_FULL,
                "hint": (
                    "Espera jobs terminarem, cancela com gamedev-model-server cancel <job_id>, "
                    "ou aumenta GAMEDEV_UMS_MAX_QUEUE_DEPTH."
                ),
                "queue_depth": self.queue.depth,
                "max_depth": self.queue.max_depth,
                "inflight": self.queue.inflight,
                "queued": snap.get("queued", []),
                "running": snap.get("running", []),
                "ums_debug": {
                    "backend": backend,
                    "priority": P.normalize_priority(request.get("priority")),
                    "queue_depth": self.queue.depth,
                    "max_depth": self.queue.max_depth,
                    "inflight": self.queue.inflight,
                    "loaded_backends": loaded,
                    "queued_backends": [j.get("backend") for j in snap.get("queued", [])],
                    "running_backends": [j.get("backend") for j in snap.get("running", [])],
                },
            }
        self._log(f"Enfileirado job {job.job_id[:8]} backend={backend!r} pri={job.priority}")
        return job

    def _ums_debug_for_job(self, job: Job) -> dict[str, Any]:
        """Bloco de debug anexado a generate/wait/poll."""
        timing = job.timing_dict()
        return {
            "job_id": job.job_id,
            "backend": job.backend,
            "priority": job.priority,
            "state": job.state,
            "affinity_cuts": job.affinity_cuts,
            "seq": job.seq,
            "queue_wait_sec": timing["queue_wait_sec"],
            "generate_sec": timing["generate_sec"],
            "total_sec": timing["total_sec"],
            "queue_depth": self.queue.depth,
            "inflight": self.queue.inflight,
            "loaded_backends": self.manager.loaded_names(),
            "cancel_requested": job.cancel_requested,
        }

    def _estimate_eta_sec(self) -> float | None:
        """ETA aproximado: soma avg_generate dos jobs queued + remaining do running.

        Itera os Jobs directamente (sem ``snapshot()`` — serializar cada job com
        ``to_public_dict`` a cada status/queue/stats é desperdício).
        """
        total = 0.0
        any_est = False
        for j in self.queue.running_jobs():
            avg = self.manager.stats.avg_generate_sec(j.backend)
            if avg is None:
                continue
            # Heurística: metade do avg ainda a correr.
            total += avg * 0.5
            any_est = True
        for j in self.queue.queued_jobs():
            avg = self.manager.stats.avg_generate_sec(j.backend)
            if avg is None:
                avg = 30.0  # fallback conservador sem histórico
            total += avg
            any_est = True
        if not any_est:
            return None
        return round(total, 1)

    def _request_timeout_sec(self, request: dict[str, Any]) -> float:
        raw = request.get("timeout_sec")
        if raw is None:
            return P.DEFAULT_GENERATE_TIMEOUT_SEC
        try:
            return max(1.0, float(raw))
        except (TypeError, ValueError):
            return P.DEFAULT_GENERATE_TIMEOUT_SEC

    def _result_from_job(self, job: Job) -> dict[str, Any]:
        result = job.result or {"status": P.STATUS_ERROR, "error": "sem resultado"}
        out = dict(result)
        out.setdefault("job_id", job.job_id)
        out.setdefault("backend", job.backend)
        out["priority"] = job.priority
        out["ums_debug"] = self._ums_debug_for_job(job)

        # Normalizar error_code em falhas.
        if out.get("status") != P.STATUS_OK:
            err_txt = str(out.get("error", "")).lower()
            if "cancel" in err_txt:
                out.setdefault("error_code", P.ERR_CANCELLED)
                out.setdefault("hint", "Job cancelado (queued ou durante generate).")
            elif "timeout" in err_txt:
                out.setdefault("error_code", P.ERR_TIMEOUT)
                out.setdefault("hint", "Aumenta timeout do cliente ou inspecciona gamedev-model-server queue.")
            else:
                out.setdefault("error_code", P.ERR_GENERATE_FAILED)
                out.setdefault(
                    "hint",
                    "Vê ums_debug + gamedev-model-server stats (last_error do backend). "
                    "Se OOM, evict outros backends ou usa --no-ums.",
                )

        if out.get("status") == P.STATUS_OK:
            # Check-and-set sob o lock do job: um generate bloqueante + wait(s)
            # concorrentes para o mesmo job não podem contar 2x.
            with job._lock:
                if not job.counted_served:
                    job.counted_served = True
                    self._requests_served += 1
        return out

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        """Despacha um request já parseado. Retorna a resposta (dict).

        Nota: ``generate`` / ``wait`` com stream são tratados em ``_handle_client``
        (precisam do socket para NDJSON). Aqui ``generate`` sem stream bloqueia.
        """
        cmd = request.get("cmd", P.DEFAULT_CMD)
        self._log(f"Comando: {cmd}")

        if cmd == P.CMD_SHUTDOWN:
            self._running = False
            return {"status": P.STATUS_OK, "message": "shutting down"}

        if cmd == P.CMD_STATUS:
            mgr_status = self.manager.status()
            qsnap = self.queue.snapshot()
            backend_stats = self.manager.stats.get_all()
            last_errors = {name: s["last_error"] for name, s in backend_stats.items() if s.get("last_error")}
            eta = self._estimate_eta_sec()
            qsnap["eta_sec"] = eta
            return {
                "status": P.STATUS_STATUS,
                "pid": self._pid,
                "socket": str(self.socket_path),
                "tool": "modelserver",
                "requests_served": self._requests_served,
                "max_affinity_cuts": self.scheduler.max_cuts,
                "max_inflight": self.workers.max_inflight,
                "starvation_timeout_sec": self.scheduler.starvation_timeout_sec,
                "queue": qsnap,
                "queue_metrics": self.manager.stats.queue_dict(),
                "eta_sec": eta,
                "debug": {
                    "loaded_backends": self.manager.loaded_names(),
                    "last_errors": last_errors,
                    "affinity_cuts_max": self.scheduler.max_cuts,
                    "queue_depth": qsnap.get("queue_depth", 0),
                    "inflight": qsnap.get("inflight", 0),
                    "affinity_hits": getattr(self.workers, "_affinity_hits", 0),
                    "process_vram_mib": mgr_status.get("process_vram_mib"),
                },
                **mgr_status,
            }

        if cmd == P.CMD_QUEUE:
            snap = self.queue.snapshot()
            eta = self._estimate_eta_sec()
            snap["eta_sec"] = eta
            return {
                "status": P.STATUS_OK,
                "eta_sec": eta,
                "debug": {
                    "loaded_backends": self.manager.loaded_names(),
                    "max_affinity_cuts": self.scheduler.max_cuts,
                    "max_inflight": self.workers.max_inflight,
                    "starvation_timeout_sec": self.scheduler.starvation_timeout_sec,
                },
                **snap,
            }

        if cmd == P.CMD_LIST_BACKENDS:
            return {
                "status": P.STATUS_OK,
                "backends": [
                    {
                        "name": desc.name,
                        "vram_mib": desc.vram_mib,
                        "priority": desc.priority,
                        "loaded": self.manager.is_loaded(desc.name),
                    }
                    for desc in self.registry
                ],
            }

        if cmd == P.CMD_STATS:
            # Reset in-process — NÃO para o UMS nem cancela jobs.
            if request.get("reset"):
                self.manager.stats.reset()
                return {
                    "status": P.STATUS_OK,
                    "reset": True,
                    "message": "stats reset (jobs/backends intactos)",
                    "pid": self._pid,
                    "queue_metrics": self.manager.stats.queue_dict(),
                    "backends": {},
                }
            stats = self.manager.stats.get_all()
            qsnap = self.queue.snapshot()
            qsnap["eta_sec"] = self._estimate_eta_sec()
            return {
                "status": P.STATUS_OK,
                "pid": self._pid,
                "requests_served": self._requests_served,
                "idle_evict_timeout_sec": self.idle_evictor.idle_timeout_sec,
                "max_affinity_cuts": self.scheduler.max_cuts,
                "max_inflight": self.workers.max_inflight,
                "starvation_timeout_sec": self.scheduler.starvation_timeout_sec,
                "queue": qsnap,
                "queue_metrics": self.manager.stats.queue_dict(),
                "affinity_hits": getattr(self.workers, "_affinity_hits", 0),
                "backends": stats,
                "debug": {
                    "loaded_backends": self.manager.loaded_names(),
                    "last_errors": {name: s["last_error"] for name, s in stats.items() if s.get("last_error")},
                    "last_runtime_budgets": {
                        name: s["last_runtime_budget"] for name, s in stats.items() if s.get("last_runtime_budget")
                    },
                },
            }

        if cmd == P.CMD_RELEASE:
            backend = request.get("backend")
            if backend:
                evicted = self.manager.evict(str(backend))
                scrub = self.manager.scrub_dead_vram() if not self.manager.loaded_names() else None
                msg = f"backend {backend} {'evicted' if evicted else 'não estava carregado'}"
                if scrub and scrub.get("dead_vram"):
                    msg += f" (residual process={scrub.get('process_vram_mib_after')} MiB — contexto/cache)"
                return {
                    "status": P.STATUS_OK if evicted else P.STATUS_ERROR,
                    "message": msg,
                    "scrub": scrub,
                }
            count = self.manager.evict_all()
            proc = self.manager._process_vram_mib()
            return {
                "status": P.STATUS_OK,
                "message": f"{count} backend(s) evicted (cache scrubbed)",
                "scrub": {"process_vram_mib": proc},
            }

        if cmd == P.CMD_PRELOAD:
            backend = request.get("backend")
            if not backend:
                return self._error("preload requer 'backend'", error_code=P.ERR_INVALID_REQUEST)
            name = str(backend)
            if not self.registry.has(name):
                return self._error(
                    f"backend desconhecido: {name}",
                    error_code=P.ERR_BACKEND_UNKNOWN,
                    hint=f"Backends válidos: {list(self.registry.names)}",
                )
            try:
                from .backend_manager import _LOAD_KWARG_KEYS, InsufficientVramError

                load_kwargs = {k: v for k, v in request.items() if k in _LOAD_KWARG_KEYS}
                self.manager.ensure_loaded(name, **load_kwargs)
                quant, mem_eff, group_off, streams = self.manager.resolve_peak_params(name, load_kwargs)
                return {
                    "status": P.STATUS_OK,
                    "message": f"backend {name} pré-carregado",
                    "ums_debug": {
                        "backend": name,
                        "loaded_backends": self.manager.loaded_names(),
                        "peak_mib": self.manager.peak_vram_mib(
                            name,
                            quant_mode=quant,
                            memory_efficient=mem_eff,
                            group_offload=group_off or streams,
                            footprint_key=load_kwargs.get("footprint_key"),
                        ),
                        "quant_mode": quant,
                        "memory_efficient": mem_eff,
                        "group_offload": group_off,
                    },
                }
            except InsufficientVramError as e:
                return self._error(
                    str(e),
                    error_code=P.ERR_VRAM_INSUFFICIENT,
                    hint=("Pico = pesos + activação + safety. Em ~6 GB usa sdnq-int4; não mates GPU — `ums queue`."),
                    backend=name,
                    peak_mib=e.peak_mib,
                    free_mib=e.free_mib,
                )
            except Exception as e:
                return self._error(
                    f"falha ao pré-carregar {name}: {e}",
                    error_code=P.ERR_PRELOAD_FAILED,
                    hint="Verifica deps do backend (gamedev-model-server doctor) e VRAM livre.",
                    backend=name,
                )

        if cmd == P.CMD_ENSURE_VRAM:
            needed = request.get("needed_mib")
            if needed is None:
                return self._error("ensure-vram requer 'needed_mib'", error_code=P.ERR_INVALID_REQUEST)
            backend = request.get("backend")
            bname = str(backend) if backend else ""
            group_off = False
            allow_go = self.manager._as_bool(request.get("allow_group_offload"))
            if backend and self.registry.has(bname):
                quant, mem_eff, group_off, streams = self.manager.resolve_peak_params(bname, request)
            else:
                quant = self.manager.resolve_quant_mode(request)
                mem_eff = self.manager._as_bool(request.get("memory_efficient")) is True
            before = self.manager.loaded_names()
            # Admit/evict room: pesos completos no load frio — EXCEPTO backends
            # com load já streaming (diffusers offload → streams_on_load).
            target = int(needed)
            if backend and self.registry.has(bname):
                target = max(
                    target,
                    self.manager.peak_vram_mib(
                        bname,
                        quant_mode=quant,
                        memory_efficient=mem_eff,
                        group_offload=streams,
                        footprint_key=request.get("footprint_key"),
                    ),
                )
            ok = self.manager.ensure_vram(
                int(needed),
                backend=bname if backend else None,
                quant_mode=quant,
                memory_efficient=mem_eff,
                allow_group_offload=allow_go,
            )
            after = self.manager.loaded_names()
            return {
                "status": P.STATUS_OK if ok else P.STATUS_ERROR,
                "needed_mib": int(needed),
                "target_mib": target,
                "error_code": None if ok else P.ERR_VRAM_INSUFFICIENT,
                "ums_debug": {
                    "loaded_before": before,
                    "loaded_after": after,
                    "evicted": sorted(set(before) - set(after)),
                    "backend": backend,
                    "quant_mode": quant,
                    "memory_efficient": mem_eff,
                    "group_offload": group_off,
                    "peak_mib": (
                        self.manager.peak_vram_mib(
                            str(backend),
                            quant_mode=quant,
                            memory_efficient=mem_eff,
                            group_offload=group_off or streams,
                            footprint_key=request.get("footprint_key"),
                        )
                        if backend and self.registry.has(str(backend))
                        else None
                    ),
                },
            }

        if cmd == P.CMD_SUBMIT:
            outcome = self._enqueue_from_request(request)
            if isinstance(outcome, dict):
                return outcome
            return {
                "status": P.STATUS_OK,
                "job_id": outcome.job_id,
                "backend": outcome.backend,
                "priority": outcome.priority,
                "state": outcome.state,
                "queue_position": self.queue.depth,
            }

        if cmd == P.CMD_POLL:
            job_id = request.get("job_id")
            if not job_id:
                return self._error("poll requer 'job_id'", error_code=P.ERR_INVALID_REQUEST)
            job = self.queue.get(str(job_id))
            if job is None:
                return self._error(
                    f"job desconhecido: {job_id}",
                    error_code=P.ERR_JOB_UNKNOWN,
                    hint="Lista jobs com gamedev-model-server queue",
                )
            payload = job.to_public_dict()
            payload["ums_debug"] = self._ums_debug_for_job(job)
            if job.result is not None:
                payload["result"] = job.result
            return {"status": P.STATUS_OK, **payload}

        if cmd == P.CMD_CANCEL:
            if request.get("all") or str(request.get("job_id") or "").lower() in ("all", "*"):
                return self.queue.cancel_all(include_running=not bool(request.get("queued_only")))
            job_id = request.get("job_id")
            if not job_id:
                return self._error(
                    "cancel requer 'job_id' ou all=true",
                    error_code=P.ERR_INVALID_REQUEST,
                    hint="ums cancel <job_id|prefixo> | ums cancel --all | ums flush",
                )
            return self.queue.cancel(str(job_id))

        if cmd == P.CMD_FLUSH:
            return self.queue.cancel_all(include_running=not bool(request.get("queued_only")))

        if cmd == P.CMD_WAIT:
            job_id = request.get("job_id")
            if not job_id:
                return self._error("wait requer 'job_id'", error_code=P.ERR_INVALID_REQUEST)
            resolved, err = self.queue.resolve_job_id(str(job_id))
            if resolved is None:
                return self._error(
                    err or f"job desconhecido: {job_id}",
                    error_code=P.ERR_JOB_UNKNOWN,
                    hint="Lista jobs com gamedev-model-server queue",
                )
            job_id = resolved
            timeout = self._request_timeout_sec(request)
            job = self.queue.wait(str(job_id), timeout_sec=timeout)
            if job is None:
                return self._error(
                    f"job desconhecido: {job_id}",
                    error_code=P.ERR_JOB_UNKNOWN,
                    hint="Lista jobs com gamedev-model-server queue",
                )
            if not job.done_event.is_set():
                return self._error(
                    "timeout à espera do job",
                    error_code=P.ERR_TIMEOUT,
                    hint="Inspecciona gamedev-model-server queue / status",
                    job_id=job_id,
                    ums_debug=self._ums_debug_for_job(job),
                )
            return self._result_from_job(job)

        if cmd == P.CMD_GENERATE:
            outcome = self._enqueue_from_request(request)
            if isinstance(outcome, dict):
                return outcome
            job = outcome
            timeout = self._request_timeout_sec(request)
            job.done_event.wait(timeout=timeout)
            if not job.done_event.is_set():
                return self._error(
                    "timeout à espera do job na fila",
                    error_code=P.ERR_TIMEOUT,
                    hint="Inspecciona gamedev-model-server queue / status",
                    job_id=job.job_id,
                    ums_debug=self._ums_debug_for_job(job),
                )
            return self._result_from_job(job)

        return self._error(
            f"comando desconhecido: {cmd}",
            error_code=P.ERR_INVALID_REQUEST,
            hint=f"Comandos: {sorted(P.KNOWN_COMMANDS)}",
        )

    # ------------------------------------------------------------------
    # Handle de 1 ligação (com suporte a NDJSON stream)
    # ------------------------------------------------------------------

    def _send_json(self, conn: socket.socket, obj: dict[str, Any]) -> None:
        conn.sendall((json.dumps(obj) + "\n").encode())

    def _handle_streaming_job(
        self,
        conn: socket.socket,
        job: Job,
        *,
        timeout_sec: float | None = None,
    ) -> None:
        """Envia eventos NDJSON até o job terminar; última linha = resultado."""
        events: list[dict[str, Any]] = []
        ready = threading.Event()
        timeout = timeout_sec if timeout_sec is not None else P.DEFAULT_GENERATE_TIMEOUT_SEC

        def on_event(event: dict[str, Any]) -> None:
            events.append(event)
            ready.set()

        job.add_listener(on_event)
        try:
            pos0 = self.queue.queue_position(job.job_id) or max(1, self.queue.depth)
            self._send_json(
                conn,
                {
                    "event": P.EVENT_QUEUED,
                    "job_id": job.job_id,
                    "backend": job.backend,
                    "priority": job.priority,
                    "state": job.state,
                    "queue_position": pos0,
                },
            )

            deadline = time.monotonic() + timeout
            sent = 0
            last_pos: int | None = pos0
            last_pos_emit = time.monotonic()
            while time.monotonic() < deadline:
                if job.done_event.is_set() and sent >= len(events):
                    break
                ready.wait(timeout=0.25)
                ready.clear()
                while sent < len(events):
                    ev = events[sent]
                    sent += 1
                    if ev.get("event") in (P.EVENT_DONE, P.EVENT_ERROR, P.EVENT_CANCELLED):
                        continue
                    with contextlib.suppress(OSError):
                        self._send_json(conn, ev)

                # Actualizar queue_position enquanto ainda queued.
                if job.state == P.JOB_QUEUED and (time.monotonic() - last_pos_emit) >= 1.0:
                    pos = self.queue.queue_position(job.job_id)
                    if pos is not None and pos != last_pos:
                        last_pos = pos
                        with contextlib.suppress(OSError):
                            self._send_json(
                                conn,
                                {
                                    "event": P.EVENT_QUEUED,
                                    "job_id": job.job_id,
                                    "backend": job.backend,
                                    "priority": job.priority,
                                    "state": job.state,
                                    "queue_position": pos,
                                },
                            )
                    last_pos_emit = time.monotonic()

            if not job.done_event.is_set():
                self._send_json(
                    conn,
                    self._error(
                        "timeout à espera do job",
                        error_code=P.ERR_TIMEOUT,
                        hint="Inspecciona gamedev-model-server queue / status",
                        job_id=job.job_id,
                        ums_debug=self._ums_debug_for_job(job),
                    ),
                )
                return
            self._send_json(conn, self._result_from_job(job))
        finally:
            # Sem isto, o listener (e a lista ``events`` que retém) ficava preso
            # ao job para sempre — incluindo em timeout / disconnect do cliente.
            job.remove_listener(on_event)

    def _handle_client(self, conn: socket.socket) -> None:
        self._last_activity = time.monotonic()
        try:
            conn.settimeout(P.DEFAULT_GENERATE_TIMEOUT_SEC)
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(8192)
                if not chunk:
                    break
                data += chunk
                if len(data) > P.MAX_REQUEST_BYTES:
                    self._send_json(
                        conn,
                        self._error(
                            f"request excede {P.MAX_REQUEST_BYTES} bytes",
                            error_code=P.ERR_INVALID_REQUEST,
                        ),
                    )
                    return

            line = data.decode("utf-8", errors="replace").strip()
            if not line:
                return

            request = json.loads(line)
            cmd = request.get("cmd", P.DEFAULT_CMD)
            want_stream = bool(request.get("stream"))

            # generate/wait com stream: caminho NDJSON.
            if cmd == P.CMD_GENERATE and want_stream:
                outcome = self._enqueue_from_request(request)
                if isinstance(outcome, dict):
                    self._send_json(conn, outcome)
                    return
                self._handle_streaming_job(conn, outcome, timeout_sec=self._request_timeout_sec(request))
                return

            if cmd == P.CMD_WAIT and want_stream:
                job_id = request.get("job_id")
                if not job_id:
                    self._send_json(
                        conn,
                        self._error("wait requer 'job_id'", error_code=P.ERR_INVALID_REQUEST),
                    )
                    return
                resolved, err = self.queue.resolve_job_id(str(job_id))
                job = self.queue.get(resolved) if resolved is not None else None
                if job is None:
                    self._send_json(
                        conn,
                        self._error(
                            err or f"job desconhecido: {job_id}",
                            error_code=P.ERR_JOB_UNKNOWN,
                            hint="Lista jobs com gamedev-model-server queue",
                        ),
                    )
                    return
                self._handle_streaming_job(conn, job, timeout_sec=self._request_timeout_sec(request))
                return

            response = self._dispatch(request)
            self._send_json(conn, response)

        except json.JSONDecodeError as e:
            with contextlib.suppress(OSError):
                self._send_json(
                    conn,
                    self._error(f"JSON inválido: {e}", error_code=P.ERR_INVALID_REQUEST),
                )
        except Exception as e:
            self._log(f"Erro ao processar cliente: {e}")
            with contextlib.suppress(OSError):
                self._send_json(
                    conn,
                    self._error(str(e), error_code=P.ERR_GENERATE_FAILED),
                )
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _cleanup(self) -> None:
        self._log("Cleanup...")
        with contextlib.suppress(Exception):
            self.workers.stop()
        with contextlib.suppress(Exception):
            self.idle_evictor.stop()
        with contextlib.suppress(Exception):
            self.manager.evict_all()
        if self._bound:
            # Só remover socket/pid se fomos nós a criá-los (bind OK) — um
            # double-start que falhou o bind não pode apagar os do UMS vivo.
            with contextlib.suppress(OSError):
                self.socket_path.unlink(missing_ok=True)
            with contextlib.suppress(OSError):
                self.ppid_path.unlink(missing_ok=True)
            self._bound = False
        if self._server_sock is not None:
            with contextlib.suppress(OSError):
                self._server_sock.close()
            self._server_sock = None

    def _signal_handler(self, signum: int, frame: Any) -> None:
        self._log(f"Sinal {signum} recebido — a encerrar.")
        self._running = False

    def serve_forever(self) -> None:
        """Arranca o UMS (bloqueante). Graceful shutdown via SIGTERM/SIGINT."""
        _ensure_server_dir()

        if self.socket_path.exists() and not is_server_running(self.socket_path):
            with contextlib.suppress(OSError):
                self.socket_path.unlink(missing_ok=True)
                self.ppid_path.unlink(missing_ok=True)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.settimeout(1.0)
        try:
            self._server_sock.bind(str(self.socket_path))
            self._server_sock.listen(8)
        except OSError as e:
            _logger.error(f"Não foi possível bind ao socket {self.socket_path}: {e}")
            # NÃO unlink socket/pid aqui — podem pertencer a um UMS vivo (double-start).
            with contextlib.suppress(OSError):
                self._server_sock.close()
            self._server_sock = None
            raise
        self._bound = True
        # PID file só depois do bind: um double-start não pode clobber o pid do UMS vivo.
        self.ppid_path.write_text(str(self._pid))

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)

        self._running = True
        replayed = self.queue.replay_from_wal()
        if replayed:
            _logger.info(f"WAL replay: {replayed} job(s) re-enfileirados")
        self.workers.start()
        _logger.info(f"Unified Model Server ativo em {self.socket_path} (PID {self._pid})")
        _logger.info(f"Backends registados: {', '.join(self.registry.names)}")
        _logger.info(
            f"Fila: max_depth={self.queue.max_depth}, max_inflight={self.workers.max_inflight}, "
            f"affinity_cuts={self.scheduler.max_cuts}"
        )
        _logger.info(f"Idle timeout: {self.idle_timeout_sec / 60:.0f} min")
        _logger.info(f"Idle evictor: backends descarregados após {self.idle_evictor.idle_timeout_sec:.0f}s sem uso")

        self.idle_evictor.start()

        try:
            while self._running:
                idle = time.monotonic() - self._last_activity
                if self.idle_timeout_sec > 0 and self._requests_served > 0 and idle > self.idle_timeout_sec:
                    _logger.info(f"Idle {idle / 60:.0f}min > timeout — a encerrar.")
                    break
                try:
                    conn, _ = self._server_sock.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
                t.start()
        finally:
            self._cleanup()
            _logger.info("UMS encerrado.")
