"""Estatísticas por backend — tracking de cargas, gerações, timings e erros.

Thread-safe (todas as operações usam um lock). Integrado no BackendManager para
registar automaticamente cada operação de load/generate/evict.

Usado pelo comando ``gamedev-model-server stats`` para diagnóstico de performance
e afinação de footprints de VRAM.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class BackendStats:
    """Estatísticas runtime de um backend.

    Attributes:
        load_count: Vezes que o backend foi carregado (cold starts).
        generate_count: Pedidos de geração servidos com sucesso.
        evict_count: Vezes que o backend foi evicted (unload forçado).
        error_count: Erros durante geração (OOM, etc.).
        total_load_time_sec: Tempo total gasto em carga (soma de todos os cold starts).
        total_generate_time_sec: Tempo total gasto em geração.
        last_load_time_sec: Duração do último cold start.
        last_generate_time_sec: Duração da última geração.
        last_error: Mensagem do último erro (ou None).
        first_loaded_at: Timestamp monotónico do primeiro carregamento.
        last_used_at: Timestamp monotónico do último uso (load ou generate).
    """

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

    @property
    def avg_load_time_sec(self) -> float:
        """Tempo médio de cold start (0 se nunca carregado)."""
        return self.total_load_time_sec / self.load_count if self.load_count > 0 else 0.0

    @property
    def avg_generate_time_sec(self) -> float:
        """Tempo médio de geração (0 se nunca gerado)."""
        return self.total_generate_time_sec / self.generate_count if self.generate_count > 0 else 0.0

    def to_dict(self) -> dict:
        """Serialização para resposta JSON do comando ``stats``."""
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
        }


class StatsCollector:
    """Coletor thread-safe de estatísticas por backend.

    O BackendManager chama os métodos ``record_load``, ``record_generate``,
    ``record_evict``, ``record_error`` após cada operação.
    """

    def __init__(self) -> None:
        self._stats: dict[str, BackendStats] = {}
        self._lock = threading.Lock()

    def _get_or_create(self, name: str) -> BackendStats:
        if name not in self._stats:
            self._stats[name] = BackendStats()
        return self._stats[name]

    def record_load(self, name: str, duration_sec: float) -> None:
        """Regista um cold start bem-sucedido."""
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
        """Regista uma geração bem-sucedida."""
        with self._lock:
            s = self._get_or_create(name)
            s.generate_count += 1
            s.total_generate_time_sec += duration_sec
            s.last_generate_time_sec = duration_sec
            s.last_used_at = time.monotonic()

    def record_evict(self, name: str) -> None:
        """Regista uma evicção (unload)."""
        with self._lock:
            s = self._get_or_create(name)
            s.evict_count += 1

    def record_error(self, name: str, error: str) -> None:
        """Regista um erro durante geração."""
        with self._lock:
            s = self._get_or_create(name)
            s.error_count += 1
            s.last_error = error

    def get(self, name: str) -> BackendStats | None:
        """Retorna as stats de um backend (None se nunca usado)."""
        with self._lock:
            return self._stats.get(name)

    def get_all(self) -> dict[str, dict]:
        """Retorna todas as stats serializadas (para comando ``stats``)."""
        with self._lock:
            return {name: s.to_dict() for name, s in self._stats.items()}

    def reset(self) -> None:
        """Limpa todas as stats (comando ``stats --reset``)."""
        with self._lock:
            self._stats.clear()
