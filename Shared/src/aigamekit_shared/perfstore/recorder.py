"""High-level recorder that bridges ProfilerSession spans into PerfDB SQLite."""

from __future__ import annotations

import json
import platform
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .db import PerfDB
from .models import GPUMeta, RunRecord, SpanRecord


def _capture_gpu_meta() -> tuple[GPUMeta, str, str]:
    """Collect GPU info. Returns (GPUMeta, pytorch_version, cuda_version)."""
    pt_ver = ""
    cuda_ver = ""
    gpu = GPUMeta(device_name="", total_vram_mb=0.0, compute_capability="")

    try:
        import torch

        pt_ver = getattr(torch, "__version__", "")
        cuda_ver = getattr(torch.version, "cuda", "") or ""
        if torch.cuda.is_available():
            idx = 0
            props = torch.cuda.get_device_properties(idx)
            gpu.device_name = props.name
            gpu.total_vram_mb = props.total_memory / (1024 * 1024)
            major = getattr(props, "major", 0)
            minor = getattr(props, "minor", 0)
            gpu.compute_capability = f"{major}.{minor}"
    except ImportError:
        pass

    return gpu, pt_ver, cuda_ver


class PerfRecorder:
    """Creates a ``runs`` row and streams spans into SQLite.

    Usage::

        with PerfRecorder("text2d", db=db) as rec:
            with rec.span("warmup"):
                generator.warmup()
            with rec.span("generate", sync_cuda=True):
                image = generator.generate(prompt)
    """

    def __init__(
        self,
        tool: str,
        *,
        db: PerfDB | None = None,
        quantization_mode: str = "",
        model_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> None:
        self._tool = tool
        self._db = db
        self._quant = quantization_mode
        self._model_id = model_id
        self._params = params or {}
        self._run_id: int | None = None
        self._started_at: str = ""
        self._t0: float = 0.0
        self._success = True
        self._owns_db = False

    @property
    def run_id(self) -> int | None:
        return self._run_id

    def _ensure_db(self) -> PerfDB:
        if self._db is None:
            self._db = PerfDB()
            self._owns_db = True
        return self._db

    def __enter__(self) -> PerfRecorder:
        db = self._ensure_db()

        from aigamekit_shared.profiler.report import utc_now_iso

        self._started_at = utc_now_iso()
        self._t0 = time.perf_counter()

        gpu, pt_ver, cuda_ver = _capture_gpu_meta()

        params_copy = dict(self._params)
        if self._quant:
            params_copy["quantization_mode"] = self._quant
        if self._model_id:
            params_copy["model_id"] = self._model_id

        run = RunRecord(
            tool=self._tool,
            started_at=self._started_at,
            gpu_name=gpu.device_name,
            gpu_total_vram_mb=gpu.total_vram_mb,
            gpu_compute_cap=gpu.compute_capability,
            hostname=platform.node(),
            python_version=platform.python_version(),
            pytorch_version=pt_ver,
            cuda_version=cuda_ver,
            quantization_mode=self._quant,
            model_id=self._model_id,
            params_json=json.dumps(params_copy, ensure_ascii=False),
        )
        self._run_id = db.insert_run(run)
        return self

    def __exit__(self, *args: object) -> None:
        from aigamekit_shared.profiler.report import utc_now_iso

        db = self._ensure_db()
        elapsed = (time.perf_counter() - self._t0) * 1000.0
        self._success = args[0] is None
        if self._run_id is not None:
            db.update_run_finish(
                self._run_id,
                finished_at=utc_now_iso(),
                total_duration_ms=elapsed,
                success=self._success,
            )
        if self._owns_db:
            db.close()

    @contextmanager
    def span(self, name: str, *, sync_cuda: bool = False) -> Iterator[None]:
        """Record a timed span with CPU/RSS/CUDA metrics into SQLite."""
        from aigamekit_shared.profiler.cuda import cuda_memory_snapshot, cuda_synchronize
        from aigamekit_shared.profiler.snapshot import resource_snapshot, subtract_cpu

        t0 = time.perf_counter()
        r0 = resource_snapshot()
        c0 = cuda_memory_snapshot()

        if sync_cuda:
            cuda_synchronize()

        try:
            yield
        finally:
            if sync_cuda:
                cuda_synchronize()

            t1 = time.perf_counter()
            r1 = resource_snapshot()
            c1 = cuda_memory_snapshot()

            du, ds = subtract_cpu(r0, r1)
            rss_before = r0.rss_bytes / (1024 * 1024) if r0.rss_bytes else None
            rss_after = r1.rss_bytes / (1024 * 1024) if r1.rss_bytes else None
            rss_delta = (r1.rss_bytes - r0.rss_bytes) / (1024 * 1024) if (r0.rss_bytes and r1.rss_bytes) else None

            span = SpanRecord(
                run_id=self._run_id or 0,
                span_name=name,
                duration_ms=round((t1 - t0) * 1000.0, 3),
                cuda_allocated_before_mb=round(c0.allocated_bytes / (1024 * 1024), 1) if c0.allocated_bytes else None,
                cuda_allocated_after_mb=round(c1.allocated_bytes / (1024 * 1024), 1) if c1.allocated_bytes else None,
                cuda_allocated_delta_mb=(
                    round((c1.allocated_bytes - c0.allocated_bytes) / (1024 * 1024), 1)
                    if c0.allocated_bytes and c1.allocated_bytes
                    else None
                ),
                cuda_reserved_before_mb=round(c0.reserved_bytes / (1024 * 1024), 1) if c0.reserved_bytes else None,
                cuda_reserved_after_mb=round(c1.reserved_bytes / (1024 * 1024), 1) if c1.reserved_bytes else None,
                cuda_peak_after_mb=round(c1.peak_allocated_bytes / (1024 * 1024), 1)
                if c1.peak_allocated_bytes
                else None,
                cuda_free_after_mb=round(c1.free_bytes / (1024 * 1024), 1) if c1.free_bytes else None,
                cuda_total_mb=round(c1.total_bytes / (1024 * 1024), 1) if c1.total_bytes else None,
                rss_before_mb=round(rss_before, 1) if rss_before else None,
                rss_after_mb=round(rss_after, 1) if rss_after else None,
                rss_delta_mb=round(rss_delta, 1) if rss_delta else None,
                cpu_user_delta_s=round(du, 6) if du else None,
                cpu_system_delta_s=round(ds, 6) if ds else None,
            )

            db = self._ensure_db()
            db.insert_span(span)

    def update_params(self, **params: Any) -> None:
        if self._run_id is not None:
            db = self._ensure_db()
            db.update_run_params(self._run_id, **params)


@contextmanager
def record_span(name: str, *, sync_cuda: bool = False) -> Iterator[None]:
    """Record a span using the global ``PerfRecorder`` from ``ProfilerSession`` context.

    This is the bridge: when a ``PerfRecorder`` is active, it also
    feeds spans into the SQLite DB alongside the existing JSONL output.
    """
    from aigamekit_shared.profiler.session import get_active_session

    session = get_active_session()
    if session is not None and hasattr(session, "_perf_recorder"):
        recorder: PerfRecorder | None = getattr(session, "_perf_recorder", None)
        if recorder is not None:
            with recorder.span(name, sync_cuda=sync_cuda):
                yield
            return
    yield
