"""Memória, GPU e gestão de processos — delegate para gamedev_shared.gpu."""

from gamedev_shared.gpu import (
    DEFAULT_EXCLUSIVE_GPU_MAX_USED_PCT,
    check_gpu_compatibility,
    clear_cuda_memory,
    enforce_exclusive_gpu,
    estimate_vram_requirement,
    format_bytes,
    get_gpu_info,
    get_system_info,
    gpu_bytes_in_use,
    gpu_total_mib,
    kill_gpu_compute_processes_aggressive,
    list_nvidia_compute_apps,
)

__all__ = [
    "DEFAULT_EXCLUSIVE_GPU_MAX_USED_PCT",
    "check_gpu_compatibility",
    "clear_cuda_memory",
    "enforce_exclusive_gpu",
    "estimate_vram_requirement",
    "format_bytes",
    "get_gpu_info",
    "get_system_info",
    "gpu_bytes_in_use",
    "gpu_total_mib",
    "kill_gpu_compute_processes_aggressive",
    "list_nvidia_compute_apps",
]
