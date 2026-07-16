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
from .vram_planner import (
    LoadedBackend,
    can_admit,
    inference_headroom_mib,
    plan_eviction,
)
from .vram_planner import (
    peak_vram_mib as compute_peak_mib,
)

_logger = Logger()

# Kwargs do request que influenciam carga / pico VRAM (passar a ``ensure_loaded``).
_LOAD_KWARG_KEYS = frozenset(
    {
        "verbose",
        "sdnq_preset",
        "quant_mode",
        "gpu_ids",
        "offload",
        "memory_efficient",
        "torch_compile",
        "torch_compile_mode",
        "channels_last",
    }
)

# CFG chunking / vistas menores → menos activação que o footprint fp16 full.
_MEMORY_EFFICIENT_ACTIVATION_FACTOR = 0.65


class InsufficientVramError(RuntimeError):
    """GPU não tem VRAM livre para pesos + activação de inferência (+ safety)."""

    def __init__(
        self,
        backend: str,
        *,
        peak_mib: int,
        free_mib: int | None,
        weights_mib: int,
        activation_mib: int,
        quant_mode: str,
    ) -> None:
        self.backend = backend
        self.peak_mib = peak_mib
        self.free_mib = free_mib
        self.weights_mib = weights_mib
        self.activation_mib = activation_mib
        self.quant_mode = quant_mode
        free_s = "?" if free_mib is None else str(free_mib)
        super().__init__(
            f"VRAM insuficiente para {backend!r} (quant={quant_mode}): "
            f"preciso peak={peak_mib} MiB "
            f"(pesos={weights_mib} + activação={activation_mib} + safety), "
            f"livre={free_s} MiB. Usa sdnq-int4 / --quality fast, ou GPU maior — "
            f"não mates processos; vê `ums queue`."
        )


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
        # Eviction: liberta o pico worst-case (pesos fp16 + activação).
        vram_mib = self.peak_vram_mib(name, quant_mode="none")
        return LoadedBackend(
            name=name,
            vram_mib=vram_mib if vram_mib > 0 else desc.vram_mib,
            priority=desc.priority,
            ref_count=state.ref_count,
            last_used=state.last_used,
        )

    @staticmethod
    def _as_bool(value: Any) -> bool | None:
        """Normaliza bool / string env-like; ``None`` se ausente/ambíguo."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        s = str(value).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
        return None

    @staticmethod
    def resolve_quant_mode(source: dict[str, Any] | None = None, **kwargs: Any) -> str:
        """Extrai modo de quant do request/kwargs.

        Ordem:
          1. ``sdnq_preset`` / ``quant_mode`` explícitos (incl. ``none``)
          2. ``memory_efficient=True`` → ``sdnq-uint8`` (paint3d/part3d/…)
          3. ``none``
        """
        src = dict(source or {})
        src.update(kwargs)
        if "sdnq_preset" in src:
            raw = src.get("sdnq_preset")
        elif "quant_mode" in src:
            raw = src.get("quant_mode")
        else:
            raw = None
        if raw is not None and str(raw).strip() != "" and str(raw).strip().lower() not in ("none", "null"):
            return str(raw).strip().lower()
        if raw is not None and str(raw).strip().lower() in ("none", "null", ""):
            return "none"
        if BackendManager._as_bool(src.get("memory_efficient")) is True:
            return "sdnq-uint8"
        return "none"

    def resolve_peak_params(self, name: str, source: dict[str, Any] | None = None, **kwargs: Any) -> tuple[str, bool]:
        """``(quant_mode, memory_efficient)`` para cálculo de pico VRAM."""
        src = dict(source or {})
        src.update(kwargs)
        quant = self.resolve_quant_mode(src)
        mem = self._as_bool(src.get("memory_efficient"))
        if mem is None:
            # paint3d com SDNQ ⇒ caminho memory-efficient (CFG chunk / vistas menores).
            mem = name == "paint3d" and quant.startswith("sdnq")
        return quant, bool(mem)

    def footprint_parts_mib(
        self,
        name: str,
        *,
        quant_mode: str = "none",
        memory_efficient: bool = False,
    ) -> tuple[int, int]:
        """(weights_mib, activation_mib) a partir do footprint ou YAML."""
        desc = self._registry.descriptor(name)
        weights: int | None = None
        activation: int | None = None
        if desc.footprint_key:
            try:
                from gamedev_shared.lowvram import get_footprint

                fp = get_footprint(desc.footprint_key)
                weights = int(fp.weights_gib(quant_mode) * 1024)
                activation = int(fp.activation_gib * 1024)
            except Exception:
                weights = None
                activation = None
        if weights is None or activation is None:
            # YAML vram_mib ≈ pico estático; parte ~20% como activação se sem footprint.
            peak = int(desc.vram_mib)
            activation = max(512, int(peak * 0.2))
            weights = max(0, peak - activation)
            if quant_mode != "none":
                try:
                    from gamedev_shared.lowvram import QUANT_WEIGHT_FACTOR

                    weights = int(weights * QUANT_WEIGHT_FACTOR.get(quant_mode, 1.0))
                except Exception:
                    pass
        if memory_efficient:
            activation = max(512, int(activation * _MEMORY_EFFICIENT_ACTIVATION_FACTOR))
        return weights, activation

    def peak_vram_mib(
        self,
        name: str,
        *,
        quant_mode: str = "none",
        memory_efficient: bool = False,
    ) -> int:
        """Pico = pesos(quant) + activação de inferência + safety."""
        weights, activation = self.footprint_parts_mib(name, quant_mode=quant_mode, memory_efficient=memory_efficient)
        return compute_peak_mib(weights, activation)

    def activation_headroom_mib(self, name: str, *, memory_efficient: bool = False) -> int:
        """Livre necessário se os pesos já estão em VRAM."""
        _weights, activation = self.footprint_parts_mib(name, quant_mode="none", memory_efficient=memory_efficient)
        return inference_headroom_mib(activation)

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

        Levanta ``InsufficientVramError`` se, após evicção, a VRAM livre ainda
        for inferior ao **pico** (pesos + activação + safety) — evita OOM a meio
        do load/inferência.

        Levanta ``KeyError`` se o backend não estiver registado, ou propaga
        exceções do adapter (ImportError se deps em falta, erros de carga).
        """
        desc = self._registry.descriptor(name)  # KeyError se desconhecido
        adapter = self._registry.adapter(name)
        quant, mem_eff = self.resolve_peak_params(name, load_kwargs)
        weights_mib, activation_mib = self.footprint_parts_mib(name, quant_mode=quant, memory_efficient=mem_eff)
        peak = compute_peak_mib(weights_mib, activation_mib)

        with self._struct_lock:
            state = self._states.get(name)
            if state is not None and state.model is not None:
                state.last_used = time.monotonic()
                return state.model

            # Precisa carregar — evictar até caber o PICO (não só pesos YAML).
            free = self._free_mib()
            if free is not None and free < peak:
                names_to_evict = plan_eviction(self._all_snapshots(), peak, free)
                for victim in names_to_evict:
                    self._evict_unlocked(victim)
                self._clear_cache()
                free = self._free_mib()

            if not can_admit(free, peak):
                raise InsufficientVramError(
                    name,
                    peak_mib=peak,
                    free_mib=free,
                    weights_mib=weights_mib,
                    activation_mib=activation_mib,
                    quant_mode=quant,
                )

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
            # Re-check VRAM (outra carga pode ter corrido entretanto).
            free = self._free_mib()
            if not can_admit(free, peak):
                raise InsufficientVramError(
                    name,
                    peak_mib=peak,
                    free_mib=free,
                    weights_mib=weights_mib,
                    activation_mib=activation_mib,
                    quant_mode=quant,
                )
            _logger.info(
                f"[UMS] A carregar backend {name!r} "
                f"(peak={peak} MiB = pesos={weights_mib}+act={activation_mib}+safety, "
                f"quant={quant}, yaml={desc.vram_mib})..."
            )
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

        O request pode incluir ``_progress`` (callable) injectado pelo WorkerPool;
        é removido antes de passar ao adapter (adapters podem lê-lo se quiserem).
        """
        # Copiar para não mutar o dict do Job; manter _progress para o adapter.
        req = dict(request)
        progress_cb = req.get("_progress")
        load_kwargs = {k: v for k, v in req.items() if k in _LOAD_KWARG_KEYS}
        try:
            model = self.ensure_loaded(name, **load_kwargs)
        except InsufficientVramError as e:
            self.stats.record_error(name, str(e))
            return {
                "status": "error",
                "error": str(e),
                "error_code": "VRAM_INSUFFICIENT",
                "hint": (
                    "Pico = pesos + activação de inferência + safety. "
                    "Em ~6 GB usa sdnq-int4 / quality fast / memory_efficient; "
                    "não mates GPU — `ums queue`."
                ),
                "peak_mib": e.peak_mib,
                "free_mib": e.free_mib,
                "weights_mib": e.weights_mib,
                "activation_mib": e.activation_mib,
                "quant_mode": e.quant_mode,
            }
        state = self._states[name]

        # Pesos já em VRAM: ainda precisamos de headroom livre para activações.
        _quant, mem_eff = self.resolve_peak_params(name, req)
        headroom = self.activation_headroom_mib(name, memory_efficient=mem_eff)
        free = self._free_mib()
        if free is not None and free < headroom:
            # Tentar evictar idle irmãos para abrir activação.
            with self._struct_lock:
                names_to_evict = plan_eviction(self._all_snapshots(), headroom, free)
                # Não evictar o próprio backend (está loaded; snapshot tem ref 0 ainda).
                names_to_evict = [n for n in names_to_evict if n != name]
                for victim in names_to_evict:
                    self._evict_unlocked(victim)
                if names_to_evict:
                    self._clear_cache()
                free = self._free_mib()
            if free is not None and free < headroom:
                _w, act = self.footprint_parts_mib(name, quant_mode=_quant, memory_efficient=mem_eff)
                err = InsufficientVramError(
                    name,
                    peak_mib=headroom,
                    free_mib=free,
                    weights_mib=_w,
                    activation_mib=act,
                    quant_mode=_quant,
                )
                self.stats.record_error(name, str(err))
                return {
                    "status": "error",
                    "error": str(err),
                    "error_code": "VRAM_INSUFFICIENT",
                    "hint": (
                        "Modelo carregado mas sem VRAM livre para activação de inferência. "
                        "Evicta outros backends (`ums evict`) ou espera a fila."
                    ),
                    "peak_mib": headroom,
                    "free_mib": free,
                    "activation_mib": act,
                }

        with self._struct_lock:
            state.ref_count += 1
        try:
            with state.gen_lock:
                t0 = time.perf_counter()
                if callable(progress_cb):
                    with contextlib.suppress(Exception):
                        progress_cb(None, f"generating via {name}")
                response = self._registry.adapter(name).generate(model, req)
                gen_time = time.perf_counter() - t0
                state.last_used = time.monotonic()
                self.stats.record_generate(name, gen_time)
                return response
        except InsufficientVramError as e:
            self.stats.record_error(name, str(e))
            with self._struct_lock:
                state.ref_count = max(0, state.ref_count - 1)
            return {
                "status": "error",
                "error": str(e),
                "error_code": "VRAM_INSUFFICIENT",
                "peak_mib": e.peak_mib,
                "free_mib": e.free_mib,
            }
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
            err_txt = str(e)
            code = "VRAM_INSUFFICIENT" if "out of memory" in err_txt.lower() else None
            out: dict[str, Any] = {"status": "error", "error": err_txt}
            if code:
                out["error_code"] = code
                out["hint"] = "OOM na inferência — peak VRAM subestimado ou GPU partilhada. `ums queue`."
            return out
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

    def ensure_vram(
        self,
        needed_mib: int,
        *,
        backend: str | None = None,
        quant_mode: str = "none",
        memory_efficient: bool = False,
    ) -> bool:
        """Evicta peso+LRU até haver ``needed_mib`` livres. Retorna ``True`` se OK.

        Se ``backend`` for dado, o alvo é ``max(needed_mib, peak_vram_mib(backend))``
        — assim clientes que pedem 5000 MiB ainda reservam activação de inferência.
        """
        target = int(needed_mib)
        if backend:
            with contextlib.suppress(KeyError):
                target = max(
                    target,
                    self.peak_vram_mib(backend, quant_mode=quant_mode, memory_efficient=memory_efficient),
                )
        names_to_evict: list[str] = []
        with self._struct_lock:
            free = self._free_mib()
            if free is not None and free >= target:
                return True
            if free is None:
                # Sem nvidia-smi — não dá para verificar; não evictar cegamente.
                return True
            names_to_evict = plan_eviction(self._all_snapshots(), target, free)
            for victim in names_to_evict:
                self._evict_unlocked(victim)
        if names_to_evict:
            self._clear_cache()
            free = self._free_mib()
            return free is None or free >= target
        return free is not None and free >= target

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
                peak = self.peak_vram_mib(name, quant_mode="none")
                backends.append(
                    {
                        "name": name,
                        "loaded": loaded,
                        "vram_mib": desc.vram_mib,
                        "peak_mib": peak,
                        "activation_headroom_mib": self.activation_headroom_mib(name),
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
