"""
Profiling partilhado: tempos de parede, CPU, RSS, VRAM CUDA (spans JSONL).

Ativar por CLI ``--profile`` ou ambiente ``GAMEDEV_PROFILE=1``;
opcionalmente ``GAMEDEV_PROFILE_LOG=/caminho/events.jsonl``.
"""

from __future__ import annotations

from .cuda import CudaMemorySnapshot, cuda_memory_snapshot, cuda_memory_snapshot_all, cuda_synchronize
from .env import (
    ENV_LOG,
    ENV_PROFILE,
    ENV_TOOL,
    env_profile_enabled,
    env_profile_log_path,
    env_profile_tool,
    is_profiling_enabled,
)
from .session import ProfilerSession, get_active_session, profile_span

__all__ = [
    "CudaMemorySnapshot",
    "ENV_LOG",
    "ENV_PROFILE",
    "ENV_TOOL",
    "ProfilerSession",
    "cuda_memory_snapshot",
    "cuda_memory_snapshot_all",
    "cuda_synchronize",
    "env_profile_enabled",
    "env_profile_log_path",
    "env_profile_tool",
    "get_active_session",
    "is_profiling_enabled",
    "profile_span",
]
