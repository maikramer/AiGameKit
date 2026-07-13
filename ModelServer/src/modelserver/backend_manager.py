"""BackendManager — gere o ciclo de vida dos backends carregados.

Responsabilidades:
  - **Carregar** um backend (lazy, na 1.ª procura) via adapter.
  - **Evictar** backends (unload) quando a VRAM escasseia, usando o VRAMPlanner.
  - **Ref-counting**: durante um ``generate``, o backend tem ref_count=1 e nunca
    é evicted (evita matar um modelo a meio de uma geração).
  - **Thread-safe**: todas as operações de carga/evicção são serializadas por um
    lock global; gerações usam um lock por-backend para permitirem paralelismo
    entre backends diferentes.

O manager conhece os ``BackendDescriptor`` (via Registry) e o estado runtime de
cada backend carregado (model object, ref_count, last_used).
"""

from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from gamedev_shared.logging import Logger

from .registry import Registry
from .stats import StatsCollector
from .vram_planner import LoadedBackend, plan_eviction

_logger = Logger()


@dataclass
class _LoadedState:
    """Estado runtime de um backend carregado (não no Registry — mutável)."""

    model: Any = None
    ref_count: int = 0
    last_used: float = 0.0
    gen_lock: threading.Lock = field(default_factory=threading.Lock)


class BackendManager:
    """Gere backends carregados: carga lazy, evicção peso+LRU, ref-counting.

    Args:
        registry: Registry de backends (descriptors + resolução lazy de adapters).
        query_free_mib: Callable que devolve MiB livres na GPU (injetado para
            testabilidade; default usa ``gamedev_shared.gpu.query_gpu_free_mib``).
        clear_vram: Callable que limpa cache CUDA após evicção (injetado; default
            ``gamedev_shared.gpu.clear_cuda_memory``).
    """

    def __init__(
        self,
        registry: Registry,
        *,
        query_free_mib: Any = None,
        clear_vram: Any = None,
    ) -> None:
        self._registry = registry
        self._states: dict[str, _LoadedState] = {}
        self._struct_lock = threading.RLock()  # reentrant: callbacks injetados (query_free_mib) podem chamar is_loaded
        self._query_free_mib = query_free_mib
        self._clear_vram = clear_vram
        self.stats = StatsCollector()

    # ------------------------------------------------------------------
    # Helpers de injeção de GPU (lazy para evitar import torch no arranque)
    # ------------------------------------------------------------------

    def _free_mib(self) -> int | None:
        if self._query_free_mib is not None:
            return self._query_free_mib()
        from gamedev_shared.gpu import query_gpu_free_mib

        return query_gpu_free_mib()

    def _clear_cache(self) -> None:
        if self._clear_vram is not None:
            self._clear_vram()
            return
        try:
            from gamedev_shared.gpu import clear_cuda_memory

            clear_cuda_memory()
        except Exception as e:
            _logger.warn(f"Falha ao limpar cache CUDA após evicção: {e}")

    # ------------------------------------------------------------------
    # Inventário (snapshots para o VRAMPlanner e para ``status``)
    # ------------------------------------------------------------------

    def loaded_names(self) -> list[str]:
        """Nomes dos backends atualmente carregados (com modelo em VRAM)."""
        with self._struct_lock:
            return [n for n, s in self._states.items() if s.model is not None]

    def is_loaded(self, name: str) -> bool:
        """True se o backend ``name`` tem modelo carregado."""
        with self._struct_lock:
            state = self._states.get(name)
            return state is not None and state.model is not None

    def _snapshot(self, name: str) -> LoadedBackend | None:
        state = self._states.get(name)
        if state is None or state.model is None:
            return None
        desc = self._registry.descriptor(name)
        # Se o backend tem footprint_key, derivar vram_mib do footprint (mais preciso
        # que o valor estático do YAML — reflecte quantização/offload real).
        vram_mib = desc.vram_mib
        if desc.footprint_key:
            vram_mib = self._footprint_vram_mib(desc.footprint_key, desc.vram_mib)
        return LoadedBackend(
            name=name,
            vram_mib=vram_mib,
            priority=desc.priority,
            ref_count=state.ref_count,
            last_used=state.last_used,
        )

    @staticmethod
    def _footprint_vram_mib(footprint_key: str, fallback_mib: int) -> int:
        """Deriva o VRAM (MiB) do footprint registry; fallback se indisponível."""
        try:
            from gamedev_shared.lowvram import get_footprint

            fp = get_footprint(footprint_key)
            # Pico ≈ pesos fp16 + ativação (sem quantização/offload — caso worst-case
            # para eviction: liberta o máximo possível). Converte GiB → MiB.
            return int((fp.fp16_weights_gib + fp.activation_gib) * 1024)
        except Exception:
            return fallback_mib

    def _all_snapshots(self) -> list[LoadedBackend]:
        snaps: list[LoadedBackend] = []
        for name in list(self._states):
            snap = self._snapshot(name)
            if snap is not None:
                snaps.append(snap)
        return snaps

    # ------------------------------------------------------------------
    # Carga
    # ------------------------------------------------------------------

    def ensure_loaded(self, name: str, **load_kwargs: Any) -> Any:
        """Garante que ``name`` está carregado e devolve o model object.

        Se já carregado, atualiza ``last_used`` e devolve. Caso contrário, evicta
        backends (peso+LRU) se a VRAM não chegar, depois carrega via adapter.

        Levanta ``KeyError`` se o backend não estiver registado, ou propaga
        exceções do adapter (ImportError se deps em falta, erros de carga).
        """
        desc = self._registry.descriptor(name)  # KeyError se desconhecido
        adapter = self._registry.adapter(name)

        with self._struct_lock:
            state = self._states.get(name)
            if state is not None and state.model is not None:
                state.last_used = time.monotonic()
                return state.model

            # Precisa carregar — planear evicção ANTES de largar o struct_lock
            # para não haver race com outro thread a carregar o mesmo backend.
            free = self._free_mib()
            if free is not None and free < desc.vram_mib:
                deficit = desc.vram_mib - free
                names_to_evict = plan_eviction(self._all_snapshots(), deficit, free)
                # Evictar dentro do lock (operações unload podem demorar, mas
                # a alternativa — largar o lock — abre race conditions).
                for victim in names_to_evict:
                    self._evict_unlocked(victim)
                self._clear_cache()

            if state is None:
                state = _LoadedState()
                self._states[name] = state

        # Carga fora do struct_lock (demora segundos; outros backends podem
        # servir pedidos entretanto). Mas guardamos o gen_lock do backend.
        with state.gen_lock:
            # Re-verificar (outro thread pode ter carregado enquanto esperávamos).
            if state.model is not None:
                state.last_used = time.monotonic()
                return state.model
            _logger.info(f"[UMS] A carregar backend {name!r} ({desc.vram_mib} MiB)...")
            t0 = time.perf_counter()
            model = adapter.load(**load_kwargs)
            load_time = time.perf_counter() - t0
            state.model = model
            state.last_used = time.monotonic()
            self.stats.record_load(name, load_time)
            _logger.info(f"[UMS] Backend {name!r} carregado em {load_time:.1f}s.")
            return model

    # ------------------------------------------------------------------
    # Geração (ref-counted)
    # ------------------------------------------------------------------

    def generate(self, name: str, request: dict[str, Any]) -> dict[str, Any]:
        """Carrega o backend (se preciso), executa ``generate``, devolve resposta.

        Durante a geração, o backend tem ref_count=1 (não evictável). Em caso de
        erro, o modelo é descarregado para a próxima tentativa recarregar limpo.
        """
        load_kwargs = {k: v for k, v in request.items() if k in ("verbose",)}
        model = self.ensure_loaded(name, **load_kwargs)
        state = self._states[name]

        with self._struct_lock:
            state.ref_count += 1
        try:
            with state.gen_lock:
                t0 = time.perf_counter()
                response = self._registry.adapter(name).generate(model, request)
                gen_time = time.perf_counter() - t0
                state.last_used = time.monotonic()
                self.stats.record_generate(name, gen_time)
                return response
        except Exception as e:
            # OOM / erro — descarregar para próxima tentativa recarregar.
            _logger.warn(f"[UMS] Erro no backend {name!r}: {e} — a descarregar para recovery.")
            self.stats.record_error(name, str(e))
            # Libertar a ref ANTES de evictar (senão _evict_unlocked recusa por ref>0).
            with self._struct_lock:
                state.ref_count = max(0, state.ref_count - 1)
                with contextlib.suppress(Exception):
                    self._evict_unlocked(name)
            self._clear_cache()
            return {"status": "error", "error": str(e)}
        finally:
            # Garantir que o ref_count volta a 0 (no-op se já decrementado no except).
            with self._struct_lock:
                state.ref_count = max(0, state.ref_count - 1)

    # ------------------------------------------------------------------
    # Evicção
    # ------------------------------------------------------------------

    def evict(self, name: str) -> bool:
        """Evicta (unload) um backend específico. Retorna ``True`` se estava carregado."""
        with self._struct_lock:
            return self._evict_unlocked(name)

    def evict_all(self) -> int:
        """Evicta TODOS os backends carregados (release global). Retorna o nº evicted."""
        with self._struct_lock:
            names = [n for n, s in self._states.items() if s.model is not None]
            count = 0
            for n in names:
                if self._evict_unlocked(n):
                    count += 1
        if count:
            self._clear_cache()
        return count

    def ensure_vram(self, needed_mib: int) -> bool:
        """Evicta peso+LRU até haver ``needed_mib`` livres. Retorna ``True`` se OK."""
        with self._struct_lock:
            free = self._free_mib()
            if free is not None and free >= needed_mib:
                return True
            if free is None:
                # Sem nvidia-smi — não dá para verificar; não evictar cegamente.
                return True
            names_to_evict = plan_eviction(self._all_snapshots(), needed_mib, free)
            for victim in names_to_evict:
                self._evict_unlocked(victim)
        if names_to_evict:
            self._clear_cache()
            free = self._free_mib()
            return free is None or free >= needed_mib
        return free is not None and free >= needed_mib

    def _evict_unlocked(self, name: str) -> bool:
        """Evicta SEM adquirir struct_lock (caller deve ter o lock). Retorna True se evicted."""
        state = self._states.get(name)
        if state is None or state.model is None:
            return False
        if state.ref_count > 0:
            _logger.warn(f"[UMS] Recusa evictar {name!r}: {state.ref_count} ref(s) ativa(s).")
            return False
        adapter = self._registry.adapter(name)
        _logger.info(f"[UMS] A evictar backend {name!r}...")
        with contextlib.suppress(Exception):
            adapter.unload(state.model)
        state.model = None
        state.last_used = 0.0
        self.stats.record_evict(name)
        _logger.info(f"[UMS] Backend {name!r} evicted (VRAM liberta).")
        return True

    # ------------------------------------------------------------------
    # Status (para o comando ``status`` do protocolo)
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Snapshot do estado para resposta a ``{"cmd": "status"}``."""
        with self._struct_lock:
            backends = []
            for name in sorted(self._registry.names):
                desc = self._registry.descriptor(name)
                state = self._states.get(name)
                loaded = state is not None and state.model is not None
                backends.append(
                    {
                        "name": name,
                        "loaded": loaded,
                        "vram_mib": desc.vram_mib,
                        "priority": desc.priority,
                        "ref_count": state.ref_count if state else 0,
                        "last_used": state.last_used if state else 0.0,
                    }
                )
            return {
                "loaded_count": sum(1 for b in backends if b["loaded"]),
                "loaded_vram_mib": sum(b["vram_mib"] for b in backends if b["loaded"]),
                "backends": backends,
            }
