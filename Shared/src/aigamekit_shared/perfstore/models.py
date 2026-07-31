"""Data models for performance records — pure dataclasses, no I/O."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GPUMeta:
    """GPU hardware info captured once per run."""

    device_name: str
    total_vram_mb: float
    compute_capability: str
    driver_version: str = ""
    cuda_version: str = ""


@dataclass
class RunRecord:
    """A single tool invocation (one ``--profile`` session)."""

    id: int | None = None
    tool: str = ""
    started_at: str = ""
    finished_at: str = ""
    total_duration_ms: float = 0.0
    success: bool = True
    gpu_name: str = ""
    gpu_total_vram_mb: float = 0.0
    gpu_compute_cap: str = ""
    hostname: str = ""
    python_version: str = ""
    pytorch_version: str = ""
    cuda_version: str = ""
    quantization_mode: str = ""
    model_id: str = ""
    params_json: str = ""


@dataclass
class SpanRecord:
    """A single measured span within a run."""

    id: int | None = None
    run_id: int = 0
    span_name: str = ""
    duration_ms: float = 0.0
    cuda_allocated_before_mb: float | None = None
    cuda_allocated_after_mb: float | None = None
    cuda_allocated_delta_mb: float | None = None
    cuda_reserved_before_mb: float | None = None
    cuda_reserved_after_mb: float | None = None
    cuda_peak_after_mb: float | None = None
    cuda_free_after_mb: float | None = None
    cuda_total_mb: float | None = None
    rss_before_mb: float | None = None
    rss_after_mb: float | None = None
    rss_delta_mb: float | None = None
    cpu_user_delta_s: float | None = None
    cpu_system_delta_s: float | None = None
    parent_tool: str = ""
    extra_json: str = ""
