"""Snapshots de memória CUDA (import lazy de torch)."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any


@dataclass
class CudaMemorySnapshot:
    """VRAM e metadados quando CUDA está disponível."""

    available: bool
    device_index: int | None
    device_name: str | None
    allocated_bytes: int | None
    reserved_bytes: int | None
    peak_allocated_bytes: int | None
    free_bytes: int | None
    total_bytes: int | None

    def to_dict(self) -> dict[str, Any]:
        if not self.available:
            return {"cuda_available": False}
        d: dict[str, Any] = {"cuda_available": True}
        if self.device_index is not None:
            d["cuda_device"] = self.device_index
        if self.device_name:
            d["cuda_device_name"] = self.device_name
        if self.allocated_bytes is not None:
            d["cuda_allocated_mb"] = round(self.allocated_bytes / (1024 * 1024), 3)
        if self.reserved_bytes is not None:
            d["cuda_reserved_mb"] = round(self.reserved_bytes / (1024 * 1024), 3)
        if self.peak_allocated_bytes is not None:
            d["cuda_peak_allocated_mb"] = round(self.peak_allocated_bytes / (1024 * 1024), 3)
        if self.free_bytes is not None and self.total_bytes is not None:
            d["cuda_free_mb"] = round(self.free_bytes / (1024 * 1024), 3)
            d["cuda_total_mb"] = round(self.total_bytes / (1024 * 1024), 3)
        return d


def cuda_memory_snapshot(device_index: int = 0) -> CudaMemorySnapshot:
    """Snapshot de VRAM; devolve ``available=False`` se torch/CUDA indisponível."""
    try:
        import torch
    except ImportError:
        return CudaMemorySnapshot(
            available=False,
            device_index=None,
            device_name=None,
            allocated_bytes=None,
            reserved_bytes=None,
            peak_allocated_bytes=None,
            free_bytes=None,
            total_bytes=None,
        )

    if not torch.cuda.is_available():
        return CudaMemorySnapshot(
            available=False,
            device_index=None,
            device_name=None,
            allocated_bytes=None,
            reserved_bytes=None,
            peak_allocated_bytes=None,
            free_bytes=None,
            total_bytes=None,
        )

    idx = min(device_index, torch.cuda.device_count() - 1)
    with contextlib.suppress(Exception):
        torch.cuda.set_device(idx)

    name = None
    with contextlib.suppress(Exception):
        name = torch.cuda.get_device_name(idx)

    allocated = reserved = peak = free_b = total_b = None
    try:
        allocated = int(torch.cuda.memory_allocated(idx))
        reserved = int(torch.cuda.memory_reserved(idx))
        peak = int(torch.cuda.max_memory_allocated(idx))
    except Exception:
        pass

    try:
        if hasattr(torch.cuda, "mem_get_info"):
            free_b, total_b = torch.cuda.mem_get_info(idx)
            free_b = int(free_b)
            total_b = int(total_b)
    except Exception:
        pass

    return CudaMemorySnapshot(
        available=True,
        device_index=idx,
        device_name=name,
        allocated_bytes=allocated,
        reserved_bytes=reserved,
        peak_allocated_bytes=peak,
        free_bytes=free_b,
        total_bytes=total_b,
    )


def cuda_synchronize(device_index: int = 0) -> None:
    """``torch.cuda.synchronize`` se CUDA disponível; caso contrário no-op."""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize(device_index)
    except Exception:
        pass
