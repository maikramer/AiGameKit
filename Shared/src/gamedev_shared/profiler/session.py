"""Sessão de profiling com spans aninháveis e exportação JSONL + SQLite."""

from __future__ import annotations

import contextvars
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from .cuda import CudaMemorySnapshot, cuda_memory_snapshot, cuda_synchronize
from .env import env_profile_log_path, env_profile_tool, is_profiling_enabled
from .report import append_jsonl, print_summary_table, utc_now_iso
from .snapshot import ResourceSnapshot, resource_snapshot, subtract_cpu

_active_session: contextvars.ContextVar[ProfilerSession | None] = contextvars.ContextVar(
    "_active_profiler_session", default=None
)


class ProfilerSession:
    """
    Agrupa spans com métricas de tempo, CPU, RAM e (opcionalmente) CUDA.

    Quando ``GAMEDEV_PERF_DB`` está definido ou o módulo
    :mod:`gamedev_shared.perfstore` está disponível, os spans também são
    gravados automaticamente num banco SQLite local.

    Uso::

        with ProfilerSession("part3d", log_path=Path("run.jsonl")) as s:
            with s.span("load"):
                ...
    """

    def __init__(
        self,
        tool_name: str,
        *,
        log_path: Path | str | None = None,
        enabled: bool | None = None,
        cli_profile: bool = False,
        quantization_mode: str = "",
        model_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> None:
        self.tool_name = tool_name
        if enabled is not None:
            self._enabled = enabled
        else:
            self._enabled = is_profiling_enabled(cli_profile)
        env_log = env_profile_log_path()
        self.log_path: Path | None = Path(log_path) if log_path else (Path(env_log) if env_log else None)
        self._events: list[dict[str, Any]] = []
        self._token: contextvars.Token[ProfilerSession | None] | None = None
        self._perf_recorder: Any = None
        self._quantization_mode = quantization_mode
        self._model_id = model_id
        self._params = params or {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def __enter__(self) -> ProfilerSession:
        if self._enabled:
            self._token = _active_session.set(self)
            self._init_perf_recorder()
        return self

    def __exit__(self, *args: object) -> None:
        if self._perf_recorder is not None:
            with suppress(Exception):
                self._perf_recorder.__exit__(*args)
            self._perf_recorder = None
        if self._token is not None:
            _active_session.reset(self._token)
            self._token = None
        if self._enabled and self._events:
            print_summary_table(self._events)

    def _init_perf_recorder(self) -> None:
        if os.environ.get("GAMEDEV_PERF_DB", "").strip() == "off":
            return
        try:
            from gamedev_shared.perfstore.recorder import PerfRecorder

            self._perf_recorder = PerfRecorder(
                self.tool_name,
                quantization_mode=self._quantization_mode,
                model_id=self._model_id,
                params=self._params,
            )
            self._perf_recorder.__enter__()
        except Exception:
            self._perf_recorder = None

    @contextmanager
    def span(self, name: str, *, sync_cuda: bool = False) -> Iterator[None]:
        if not self._enabled:
            yield
            return

        t_wall0 = time.perf_counter()
        r0 = resource_snapshot()
        c0 = cuda_memory_snapshot()

        if sync_cuda:
            cuda_synchronize()

        try:
            yield
        finally:
            if sync_cuda:
                cuda_synchronize()

            t_wall1 = time.perf_counter()
            r1 = resource_snapshot()
            c1 = cuda_memory_snapshot()

            duration_ms = (t_wall1 - t_wall0) * 1000.0
            du, ds = subtract_cpu(r0, r1)
            rss_delta = None
            if r0.rss_bytes is not None and r1.rss_bytes is not None:
                rss_delta = r1.rss_bytes - r0.rss_bytes

            ev = self._build_event(
                name=name,
                duration_ms=duration_ms,
                r0=r0,
                r1=r1,
                du=du,
                ds=ds,
                rss_delta_bytes=rss_delta,
                c0=c0,
                c1=c1,
            )
            self._events.append(ev)
            if self.log_path is not None:
                append_jsonl(self.log_path, ev)

            if self._perf_recorder is not None:
                try:
                    from gamedev_shared.perfstore.models import SpanRecord

                    span = SpanRecord(
                        run_id=self._perf_recorder.run_id or 0,
                        span_name=name,
                        duration_ms=round(duration_ms, 3),
                        cuda_allocated_before_mb=(
                            round(c0.allocated_bytes / (1024 * 1024), 1) if c0.allocated_bytes else None
                        ),
                        cuda_allocated_after_mb=(
                            round(c1.allocated_bytes / (1024 * 1024), 1) if c1.allocated_bytes else None
                        ),
                        cuda_allocated_delta_mb=(
                            round((c1.allocated_bytes - c0.allocated_bytes) / (1024 * 1024), 1)
                            if c0.allocated_bytes and c1.allocated_bytes
                            else None
                        ),
                        cuda_reserved_before_mb=(
                            round(c0.reserved_bytes / (1024 * 1024), 1) if c0.reserved_bytes else None
                        ),
                        cuda_reserved_after_mb=(
                            round(c1.reserved_bytes / (1024 * 1024), 1) if c1.reserved_bytes else None
                        ),
                        cuda_peak_after_mb=(
                            round(c1.peak_allocated_bytes / (1024 * 1024), 1) if c1.peak_allocated_bytes else None
                        ),
                        cuda_free_after_mb=round(c1.free_bytes / (1024 * 1024), 1) if c1.free_bytes else None,
                        cuda_total_mb=round(c1.total_bytes / (1024 * 1024), 1) if c1.total_bytes else None,
                        rss_before_mb=round(r0.rss_bytes / (1024 * 1024), 1) if r0.rss_bytes else None,
                        rss_after_mb=round(r1.rss_bytes / (1024 * 1024), 1) if r1.rss_bytes else None,
                        rss_delta_mb=round(rss_delta / (1024 * 1024), 1) if rss_delta else None,
                        cpu_user_delta_s=round(du, 6) if du else None,
                        cpu_system_delta_s=round(ds, 6) if ds else None,
                        parent_tool=env_profile_tool() or "",
                    )
                    self._perf_recorder._ensure_db().insert_span(span)
                except Exception:
                    pass

    def _build_event(
        self,
        *,
        name: str,
        duration_ms: float,
        r0: ResourceSnapshot,
        r1: ResourceSnapshot,
        du: float | None,
        ds: float | None,
        rss_delta_bytes: int | None,
        c0: CudaMemorySnapshot,
        c1: CudaMemorySnapshot,
    ) -> dict[str, Any]:
        ev: dict[str, Any] = {
            "ts": utc_now_iso(),
            "tool": self.tool_name,
            "span": name,
            "duration_ms": round(duration_ms, 3),
            "resource_before": r0.to_dict(),
            "resource_after": r1.to_dict(),
        }
        extra = env_profile_tool()
        if extra:
            ev["parent_tool"] = extra
        if du is not None:
            ev["cpu_user_delta_s"] = round(du, 6)
        if ds is not None:
            ev["cpu_system_delta_s"] = round(ds, 6)
        if rss_delta_bytes is not None:
            ev["rss_delta_bytes"] = rss_delta_bytes
            ev["rss_delta_mb"] = round(rss_delta_bytes / (1024 * 1024), 4)

        ev["cuda_before"] = c0.to_dict()
        ev["cuda_after"] = c1.to_dict()
        if c0.available and c1.available and c0.allocated_bytes is not None and c1.allocated_bytes is not None:
            dab = c1.allocated_bytes - c0.allocated_bytes
            ev["cuda_allocated_delta_mb"] = round(dab / (1024 * 1024), 4)
        return ev

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)


@contextmanager
def profile_span(name: str, *, sync_cuda: bool = False) -> Iterator[None]:
    """
    Usa a :class:`ProfilerSession` activa (definida no ``with ProfilerSession(...)``).

    Se não houver sessão activa, é um no-op.
    """
    sess = _active_session.get()
    if sess is None or not sess.enabled:
        yield
        return
    with sess.span(name, sync_cuda=sync_cuda):
        yield


def get_active_session() -> ProfilerSession | None:
    return _active_session.get()
