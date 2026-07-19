"""Estatísticas por backend + métricas globais de fila UMS."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class BackendStats:
    """Estatísticas runtime de um backend."""

    load_count: int = 0
    generate_count: int = 0
    evict_count: int = 0
    error_count: int = 0
    total_load_time_sec: float = 0.0
    total_generate_time_sec: float = 0.0
    last_load_time_sec: float = 0.0
    last_generate_time_sec: float = 0.0
    last_error: str | None = None
    first_loaded_at: float = 0.0
    last_used_at: float = 0.0
    # Último runtime VRAM budget reportado pelo adapter (chunks/views/tiles).
    last_runtime_budget: dict | None = None

    @property
    def avg_load_time_sec(self) -> float:
        return self.total_load_time_sec / self.load_count if self.load_count > 0 else 0.0

    @property
    def avg_generate_time_sec(self) -> float:
        return self.total_generate_time_sec / self.generate_count if self.generate_count > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "load_count": self.load_count,
            "generate_count": self.generate_count,
            "evict_count": self.evict_count,
            "error_count": self.error_count,
            "avg_load_time_sec": round(self.avg_load_time_sec, 2),
            "avg_generate_time_sec": round(self.avg_generate_time_sec, 2),
            "last_load_time_sec": round(self.last_load_time_sec, 2),
            "last_generate_time_sec": round(self.last_generate_time_sec, 2),
            "last_error": self.last_error,
            "idle_sec": round(time.monotonic() - self.last_used_at, 1) if self.last_used_at > 0 else None,
            "last_runtime_budget": self.last_runtime_budget,
        }


@dataclass
class QueueStats:
    """Métricas agregadas da fila UMS."""

    enqueued: int = 0
    completed: int = 0
    cancelled: int = 0
    queue_full_count: int = 0
    affinity_cuts_total: int = 0
    wait_samples: list[float] = field(default_factory=list)
    max_depth_seen: int = 0

    def record_wait(self, wait_sec: float) -> None:
        self.wait_samples.append(float(wait_sec))
        # Cap memória: manter últimas 500 amostras.
        if len(self.wait_samples) > 500:
            self.wait_samples = self.wait_samples[-500:]

    @staticmethod
    def _percentile(samples: list[float], p: float) -> float | None:
        if not samples:
            return None
        ordered = sorted(samples)
        idx = min(len(ordered) - 1, max(0, round((p / 100.0) * (len(ordered) - 1))))
        return round(ordered[idx], 3)

    def to_dict(self) -> dict:
        return {
            "enqueued": self.enqueued,
            "completed": self.completed,
            "cancelled": self.cancelled,
            "queue_full_count": self.queue_full_count,
            "affinity_cuts_total": self.affinity_cuts_total,
            "max_depth_seen": self.max_depth_seen,
            "queue_wait_p50_sec": self._percentile(self.wait_samples, 50),
            "queue_wait_p95_sec": self._percentile(self.wait_samples, 95),
            "queue_wait_samples": len(self.wait_samples),
        }


class StatsCollector:
    """Coletor thread-safe de estatísticas por backend + fila."""

    def __init__(self) -> None:
        self._stats: dict[str, BackendStats] = {}
        self.queue = QueueStats()
        self._lock = threading.Lock()

    def _get_or_create(self, name: str) -> BackendStats:
        if name not in self._stats:
            self._stats[name] = BackendStats()
        return self._stats[name]

    def record_load(self, name: str, duration_sec: float) -> None:
        with self._lock:
            s = self._get_or_create(name)
            s.load_count += 1
            s.total_load_time_sec += duration_sec
            s.last_load_time_sec = duration_sec
            now = time.monotonic()
            if s.first_loaded_at == 0.0:
                s.first_loaded_at = now
            s.last_used_at = now

    def record_generate(self, name: str, duration_sec: float) -> None:
        with self._lock:
            s = self._get_or_create(name)
            s.generate_count += 1
            s.total_generate_time_sec += duration_sec
            s.last_generate_time_sec = duration_sec
            s.last_used_at = time.monotonic()

    def record_evict(self, name: str) -> None:
        with self._lock:
            s = self._get_or_create(name)
            s.evict_count += 1

    def record_error(self, name: str, error: str) -> None:
        with self._lock:
            s = self._get_or_create(name)
            s.error_count += 1
            s.last_error = error

    def record_runtime_budget(self, name: str, budget: dict | None) -> None:
        """Guarda o último runtime VRAM budget (chunks/views/tiles) do backend."""
        if not budget:
            return
        with self._lock:
            s = self._get_or_create(name)
            s.last_runtime_budget = dict(budget)

    def record_enqueue(self, *, depth_after: int) -> None:
        with self._lock:
            self.queue.enqueued += 1
            self.queue.max_depth_seen = max(self.queue.max_depth_seen, depth_after)

    def record_queue_full(self) -> None:
        with self._lock:
            self.queue.queue_full_count += 1

    def record_job_finished(self, *, wait_sec: float | None, affinity_cuts: int, cancelled: bool) -> None:
        with self._lock:
            if cancelled:
                self.queue.cancelled += 1
            else:
                self.queue.completed += 1
            self.queue.affinity_cuts_total += max(0, int(affinity_cuts))
            if wait_sec is not None:
                self.queue.record_wait(wait_sec)

    def avg_generate_sec(self, backend: str) -> float | None:
        with self._lock:
            s = self._stats.get(backend)
            if s is None or s.generate_count <= 0:
                return None
            return s.avg_generate_time_sec

    def get(self, name: str) -> BackendStats | None:
        with self._lock:
            return self._stats.get(name)

    def get_all(self) -> dict[str, dict]:
        with self._lock:
            return {name: s.to_dict() for name, s in self._stats.items()}

    def queue_dict(self) -> dict:
        with self._lock:
            return self.queue.to_dict()

    def reset(self) -> None:
        with self._lock:
            self._stats.clear()
            self.queue = QueueStats()
