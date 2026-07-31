"""
Centralized performance storage for the AiGameKit monorepo.

SQLite database at ``~/.cache/aigamekit/perf.db`` (overridable via
``AIGAMEKIT_PERF_DB``).  Every tool run with ``--profile`` (or
``AIGAMEKIT_PROFILE=1``) automatically records spans, GPU info, and
tool parameters here.

AiGameKitLab analytics commands query this DB to recommend optimal
quantization / VAE / parameter configs for your GPU.
"""

from __future__ import annotations

from .db import PerfDB, default_db_path
from .models import GPUMeta, RunRecord, SpanRecord
from .recorder import PerfRecorder, record_span

__all__ = [
    "GPUMeta",
    "PerfDB",
    "PerfRecorder",
    "RunRecord",
    "SpanRecord",
    "default_db_path",
    "record_span",
]
