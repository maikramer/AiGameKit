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
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from gamedev_shared.logging import Logger

from . import protocol as P
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


def _is_worker_dead_message(err: str) -> bool:
    """True se a mensagem indica worker subprocesso morto / load incompleto."""
    low = err.lower()
    return "não está vivo" in low or "nao esta vivo" in low or "worker fechou stdout" in low or "eof no load" in low


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
        # Text3D / Omni (accel + placement)
        "volume_decoder",
        "mc_algo",
        "compile_models",
        "compile_mode",
        "allow_group_offload",
        "fp8_layerwise",
        "sdnq_quantized_matmul",
        "sage_attention",
        "use_ema",
        # Paint3D (shape do pipeline no load: vistas/res/atlas afetam activação)
        "max_num_view",
        "view_resolution",
        "render_size",
        "texture_size",
        "bake_exp",
        # Text2D (modelo / quant / kernel opts — fingerprint + load)
        "model_id",
        "quant_preset",
        "step_cache",
        # Override de pegada para o peak (4B vs 9B) — só matemática de admit.
        "footprint_key",
    }
)

# CFG chunking / vistas menores → menos activação que o footprint fp16 full.
_MEMORY_EFFICIENT_ACTIVATION_FACTOR = 0.65

# Keys que moldam o shape do pipeline no load — mismatch ⇒ reload (não reusar).
_SHAPE_LOAD_KEYS = frozenset(
    {
        "max_num_view",
        "view_resolution",
        "render_size",
        "texture_size",
        "bake_exp",
        "memory_efficient",
        "sdnq_preset",
        "quant_mode",
        "allow_group_offload",
        "gpu_ids",
        "offload",
        "volume_decoder",
        "fp8_layerwise",
        "channels_last",
        "torch_compile",
        "compile_models",
        "compile_mode",
        "torch_compile_mode",
        "model_id",
        "quant_preset",
        "step_cache",
    }
)


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


class ShapeBusyError(RuntimeError):
    """Backend em uso (ref>0) com load shape diferente do pedido — não dá reload."""

    def __init__(self, backend: str, *, stored: dict[str, Any], requested: dict[str, Any]) -> None:
        self.backend = backend
        self.stored = stored
        self.requested = requested
        super().__init__(
            f"Backend {backend!r} ocupado com outro load shape "
            f"(loaded={stored!r}, requested={requested!r}). Espera o job atual."
        )


@dataclass
class _LoadedState:
    """Estado runtime de um backend carregado (não no Registry — mutável).

    Dois modos:
      - **in-process** (legacy): ``model`` guarda o objecto torch vivo no
        processo UMS; ``is_loaded()`` ≡ ``model is not None``.
      - **subprocess** (Fase 3+): ``model`` fica a ``None`` e usa-se o
        marcador ``subprocess_loaded`` para indicar que o worker persistente
        tem o modelo carregado; o objecto torch vive noutro processo, no
        ``SubprocessWorkerPool``.
    """

    model: Any = None
    ref_count: int = 0
    last_used: float = 0.0
    # Última atividade preservada através do unload: ``last_used`` volta a 0 ao
    # descarregar, mas o worker subprocesso continua vivo e o timer de shutdown
    # tem de contar desde o último job real (ver ``idle_worker_candidates``).
    last_activity: float = 0.0
    gen_lock: threading.Lock = field(default_factory=threading.Lock)
    # Shape do load (views/res/quant/…) — mismatch com novo request ⇒ reload.
    load_shape: dict[str, Any] = field(default_factory=dict)
    # Modo subprocesso: True quando o worker persistente tem o modelo carregado.
    # Em modo in-process fica sempre False — ``is_loaded()`` usa ``model``.
    subprocess_loaded: bool = False

    def is_loaded(self) -> bool:
        """True se o backend tem um modelo carregado (in-process ou subprocesso)."""
        return self.model is not None or self.subprocess_loaded

    def mark_unloaded(self) -> None:
        """Marca o backend como descarregado em qualquer modo."""
        self.model = None
        self.subprocess_loaded = False
        self.load_shape = {}
        self.last_activity = self.last_used or time.monotonic()
        self.last_used = 0.0


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
        query_process_vram_mib: Any = None,
        subprocess_pool: Any = None,
        reap_strays: Any = None,
    ) -> None:
        self._registry = registry
        self._states: dict[str, _LoadedState] = {}
        self._struct_lock = threading.RLock()  # reentrant: callbacks injetados (query_free_mib) podem chamar is_loaded
        self._query_free_mib = query_free_mib
        self._clear_vram = clear_vram
        self._query_process_vram_mib = query_process_vram_mib
        # Recuperação de VRAM presa por supervisores/workers UMS órfãos —
        # injectado pelo servidor (process_guard.reap_strays) e usado como
        # último recurso no ensure_vram, antes de recusar o job.
        self._reap_strays = reap_strays
        self.stats = StatsCollector()
        # Pool de subprocessos (modo subprocess-per-backend). ``None`` desliga o
        # modo (todos os backends correm in-process, legado). Quando definido,
        # backends com ``desc.tool`` no registry despacham para o pool.
        self._subprocess_pool = subprocess_pool

    def _use_subprocess(self, name: str) -> bool:
        """True se ``name`` deve correr em subprocesso (desc.tool definido e pool activo).

        Env ``GAMEDEV_UMS_SUBPROCESS=0`` desliga globalmente (rollback rápido).
        """
        if self._subprocess_pool is None:
            return False
        if os.environ.get("GAMEDEV_UMS_SUBPROCESS", "1") == "0":
            return False
        try:
            desc = self._registry.descriptor(name)
        except KeyError:
            return False
        return bool(desc.tool)

    def _clear_stale_subprocess_unlocked(self, name: str, state: _LoadedState) -> bool:
        """Se ``subprocess_loaded`` mas o processo worker morreu, limpa o estado.

        Caller deve segurar ``_struct_lock``. Devolve True se limpou (precisa reload).
        """
        if not state.is_loaded() or not self._use_subprocess(name):
            return False
        pool = self._subprocess_pool
        if pool is None or pool.is_alive(name):
            return False
        _logger.warn(
            f"[UMS] Backend {name!r}: marcado loaded mas worker morto — limpar p/ reload "
            f"(evita «worker não está vivo» no generate)."
        )
        state.mark_unloaded()
        return True

    # ------------------------------------------------------------------
    # Helpers de injeção de GPU (lazy para evitar import torch no arranque)
    # ------------------------------------------------------------------

    def _free_mib(self) -> int | None:
        if self._query_free_mib is not None:
            return self._query_free_mib()
        from gamedev_shared.gpu import query_gpu_free_mib

        return query_gpu_free_mib()

    def _admit_free_mib(self) -> int | None:
        """VRAM livre para decisão de admit de um job **in-process**.

        ``NVML free + max(0, reserved - allocated)`` do allocator torch: o
        próximo job corre NESTE processo e aloca primeiro do cache — o free
        cru do driver subestima o utilizável (ex.: ~1.5 GiB de segmentos em
        cache pós-generate que o driver conta como «usados» mas que o
        allocator reutiliza sem nova alocação ao SO). O contexto CUDA
        (~100-300 MiB) é pago uma vez por processo e os peaks são calibrados
        in-process — não se cobra de novo por job.

        Sem torch inicializado (ou sem CUDA) equivale ao free cru.
        """
        free = self._free_mib()
        if free is None:
            return None
        stats = self._torch_alloc_stats()
        reusable = stats.get("reusable_mib")
        if not reusable:
            return free
        return free + int(reusable)

    def _wait_for_admit(self, peak_mib: int, free_mib: int | None) -> int | None:
        """Poll VRAM livre até caber ``peak`` ou timeout (processo externo a sair).

        Também tenta ``evict_all`` idle + clear cache a cada poll. Timeout via
        ``GAMEDEV_UMS_VRAM_ADMIT_WAIT_SEC`` (0 = sem espera).
        """
        wait_budget = max(0.0, float(P.VRAM_ADMIT_WAIT_SEC))
        if wait_budget <= 0:
            return free_mib
        poll = max(0.05, float(P.VRAM_ADMIT_POLL_SEC))
        deadline = time.monotonic() + wait_budget
        free = free_mib
        while time.monotonic() < deadline:
            with self._struct_lock:
                # Evict tudo idle — se nada loaded, não há o que libertar no UMS.
                for victim in list(self._states):
                    snap = self._snapshot(victim)
                    if snap is not None and snap.ref_count <= 0:
                        self._evict_unlocked(victim)
            self._clear_cache()
            free = self._admit_free_mib()
            if can_admit(free, peak_mib):
                _logger.info(f"[UMS] VRAM admit OK após espera (livre={free} peak={peak_mib}).")
                return free
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(poll, remaining))
        return free

    def _clear_cache(self) -> None:
        if self._clear_vram is not None:
            self._clear_vram()
            return
        try:
            from gamedev_shared.gpu import clear_cuda_memory

            clear_cuda_memory()
        except Exception as e:
            _logger.warn(f"Falha ao limpar cache CUDA após evicção: {e}")

    def _process_vram_mib(self) -> int | None:
        """VRAM do PID UMS (NVML/smi), com fallback ao allocator torch."""
        if self._query_process_vram_mib is not None:
            return self._query_process_vram_mib()
        try:
            from gamedev_shared.gpu import process_vram_mib, torch_reserved_mib

            proc = process_vram_mib()
            if proc is not None:
                return proc
            return torch_reserved_mib()
        except Exception:
            return None

    def scrub_dead_vram(self, *, min_mib: int | None = None) -> dict[str, Any]:
        """Limpa cache CUDA com ``loaded=[]`` e reporta residual restante.

        O residual que sobrevive ao scrub é contexto CUDA do processo + cache
        não libertável — baseline do worker, **sem acção destrutiva** (o admit
        de jobs in-process credita o cache reutilizável via
        :meth:`_admit_free_mib`; clientes externos recebem resposta honesta).

        Returns:
            Dict com ``scrubbed``, ``process_vram_mib_before/after``, ``loaded``.
        """
        threshold = int(P.DEAD_VRAM_MIB if min_mib is None else min_mib)
        loaded = self.loaded_names()
        before = self._process_vram_mib()
        # Sempre clear após unload / evict vazio — allocator pode segurar activação.
        self._clear_cache()
        after = self._process_vram_mib()
        residual = after if after is not None else before
        dead = bool(not loaded and residual is not None and residual >= threshold)
        if dead:
            _logger.warn(
                f"[UMS] VRAM residual: process={residual} MiB com loaded=[] "
                f"(threshold={threshold}) — contexto/cache do worker (baseline)."
            )
        return {
            "scrubbed": True,
            "loaded": list(loaded),
            "process_vram_mib_before": before,
            "process_vram_mib_after": after,
            "dead_vram": dead,
            "threshold_mib": threshold,
        }

    # ------------------------------------------------------------------
    # Inventário (snapshots para o VRAMPlanner e para ``status``)
    # ------------------------------------------------------------------

    def loaded_names(self) -> list[str]:
        """Nomes dos backends atualmente carregados (com modelo em VRAM)."""
        with self._struct_lock:
            return [n for n, s in self._states.items() if s.is_loaded()]

    def is_loaded(self, name: str) -> bool:
        """True se o backend ``name`` tem modelo carregado."""
        with self._struct_lock:
            state = self._states.get(name)
            return state is not None and state.is_loaded()

    def shape_matches_loaded(self, name: str, request: dict[str, Any] | None = None) -> bool:
        """True se ``name`` está carregado e o request não pede outro load_shape.

        Usado pelo AffinityScheduler: hot ≠ só nome do backend — quant/views/
        offload diferentes forçam cold reload.
        """
        with self._struct_lock:
            state = self._states.get(name)
            if state is None or not state.is_loaded():
                return False
            load_kwargs = {k: v for k, v in (request or {}).items() if k in _LOAD_KWARG_KEYS}
            if not load_kwargs:
                return True
            return not self._shape_mismatch(state.load_shape, load_kwargs)

    def _snapshot(self, name: str) -> LoadedBackend | None:
        state = self._states.get(name)
        if state is None or not state.is_loaded():
            return None
        desc = self._registry.descriptor(name)
        # Eviction: liberta o pico worst-case (pesos fp16 + activação).
        vram_mib = self.peak_vram_mib(name, quant_mode="none")
        # Modo subprocesso: preferir a VRAM reportada pelo worker quando houver.
        if state.subprocess_loaded and self._subprocess_pool is not None:
            pool_vram = self._subprocess_pool.vram_mib(name)
            if pool_vram and pool_vram > 0:
                vram_mib = pool_vram
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
          1. ``sdnq_preset`` / ``quant_mode`` / ``quant_preset`` (text2d) explícitos
          2. ``memory_efficient=True`` → ``sdnq-uint8`` (paint3d/part3d/text2d/…)
          3. ``none``
        """
        src = dict(source or {})
        src.update(kwargs)
        if "sdnq_preset" in src:
            raw = src.get("sdnq_preset")
        elif "quant_mode" in src:
            raw = src.get("quant_mode")
        elif "quant_preset" in src:
            raw = src.get("quant_preset")
        else:
            raw = None
        if raw is not None and str(raw).strip() != "" and str(raw).strip().lower() not in ("none", "null"):
            return str(raw).strip().lower()
        if raw is not None and str(raw).strip().lower() in ("none", "null", ""):
            return "none"
        if BackendManager._as_bool(src.get("memory_efficient")) is True:
            return "sdnq-uint8"
        return "none"

    def resolve_peak_params(
        self, name: str, source: dict[str, Any] | None = None, **kwargs: Any
    ) -> tuple[str, bool, bool, bool]:
        """``(quant_mode, memory_efficient, group_offload, streams_on_load)`` para pico VRAM.

        ``streams_on_load``: True quando o LOAD já é streaming módulo-a-módulo
        (diffusers model_cpu offload — o pico de warmup é largest-module +
        activação, NÃO pesos completos). Default por backend: text2d com
        memory_efficient (o seu load é diffusers offload — validado ~4.1 GiB).
        text3d carrega pesos completos e só depois aplica leaf-offload → False
        (admit exige pesos completos). Override explícito: ``streams_on_load``
        no request.
        """
        src = dict(source or {})
        src.update(kwargs)
        quant = self.resolve_quant_mode(src)
        mem = self._as_bool(src.get("memory_efficient"))
        if mem is None:
            # SDNQ ⇒ caminho memory-efficient:
            #   paint3d — CFG chunk / vistas menores
            #   text3d  — group offload + streams; act orçada em runtime
            #   text2d  — CPU offload / 4B path (activation x0.65)
            mem = name in ("paint3d", "text3d", "text2d") and quant.startswith("sdnq")
        mem = bool(mem)
        go = self._as_bool(src.get("allow_group_offload"))
        if go is None:
            # text3d mem_eff ⇒ group+stream por defeito (ums_load / generator).
            go = name == "text3d" and mem
        streams = self._as_bool(src.get("streams_on_load"))
        if streams is None:
            streams = name == "text2d" and mem
        return quant, mem, bool(go), bool(streams)

    @staticmethod
    def _normalize_shape_value(value: Any) -> Any:
        if isinstance(value, list):
            return tuple(value)
        if isinstance(value, dict):
            return tuple(sorted((k, BackendManager._normalize_shape_value(v)) for k, v in value.items()))
        return value

    @classmethod
    def _extract_load_shape(cls, load_kwargs: dict[str, Any]) -> dict[str, Any]:
        return {k: cls._normalize_shape_value(load_kwargs[k]) for k in _SHAPE_LOAD_KEYS if k in load_kwargs}

    @classmethod
    def _shape_mismatch(cls, stored: dict[str, Any], load_kwargs: dict[str, Any]) -> bool:
        """True se o novo request pede shape diferente do model já carregado."""
        for key in _SHAPE_LOAD_KEYS:
            if key not in load_kwargs:
                continue
            if stored.get(key) != cls._normalize_shape_value(load_kwargs[key]):
                return True
        return False

    def footprint_parts_mib(
        self,
        name: str,
        *,
        quant_mode: str = "none",
        memory_efficient: bool = False,
        group_offload: bool = False,
        footprint_key: str | None = None,
    ) -> tuple[int, int]:
        """(weights_mib, activation_mib) a partir do footprint ou YAML.

        ``footprint_key``: override por request (ex.: text2d 4B vs 9B — a chave
        do descriptor é estática e não sabe qual modelo o hw_auto escolheu).
        """
        desc = self._registry.descriptor(name)
        fp_key = footprint_key or desc.footprint_key
        weights: int | None = None
        activation: int | None = None
        if fp_key:
            try:
                from gamedev_shared.lowvram import get_footprint

                fp = get_footprint(fp_key)
                if group_offload:
                    # group+stream: pico ≈ maior leaf/block onloaded + activação
                    # completa (chunks dinâmicos usam a VRAM livre pós-offload).
                    weights = int(fp.largest_gib(quant_mode) * 1024)
                    activation = int(fp.activation_gib * 1024)
                    return max(256, weights), max(512, activation)
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
            if group_offload:
                # Sem footprint: aproximar onloaded ≈ 40% dos pesos (como largest default).
                weights = max(256, int(weights * 0.4))
                return weights, max(512, activation)
        if memory_efficient and not group_offload:
            activation = max(512, int(activation * _MEMORY_EFFICIENT_ACTIVATION_FACTOR))
        return weights, activation

    def peak_vram_mib(
        self,
        name: str,
        *,
        quant_mode: str = "none",
        memory_efficient: bool = False,
        group_offload: bool = False,
        footprint_key: str | None = None,
    ) -> int:
        """Pico = pesos(quant) + activação de inferência + safety."""
        weights, activation = self.footprint_parts_mib(
            name,
            quant_mode=quant_mode,
            memory_efficient=memory_efficient,
            group_offload=group_offload,
            footprint_key=footprint_key,
        )
        return compute_peak_mib(weights, activation)

    def activation_headroom_mib(
        self,
        name: str,
        *,
        quant_mode: str = "none",
        memory_efficient: bool = False,
        group_offload: bool = False,
        footprint_key: str | None = None,
    ) -> int:
        """Livre necessário se os pesos já estão em VRAM.

        Com ``group_offload`` (text3d SDNQ) os pesos já ocupam o maior leaf;
        a activação streama no free restante — exigir ``activation_gib`` completo
        (~2 GiB) em cima de pesos hot faz o 2º job falhar em GPUs ~6 GB.
        """
        if group_offload:
            # Stream/offload: basta margem para um bloco de activação, não o pico frio.
            base = 768 if memory_efficient or str(quant_mode).startswith("sdnq") else 1024
            return inference_headroom_mib(base)
        _weights, activation = self.footprint_parts_mib(
            name,
            quant_mode=quant_mode,
            memory_efficient=memory_efficient,
            group_offload=group_offload,
            footprint_key=footprint_key,
        )
        if memory_efficient:
            activation = max(512, int(activation * _MEMORY_EFFICIENT_ACTIVATION_FACTOR))
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

    def ensure_loaded(self, name: str, _pin: bool = False, **load_kwargs: Any) -> Any:
        """Garante que ``name`` está carregado e devolve o model object.

        Se já carregado, atualiza ``last_used`` e devolve. Caso contrário, evicta
        backends (peso+LRU) se a VRAM não chegar, depois carrega via adapter.

        Com ``_pin=True``, ``ref_count`` é incrementado **atomicamente** com a
        verificação/carga (sob ``_struct_lock``) — sem janela em que outro actor
        (ensure-vram, evict, preload concorrente) possa evictar o modelo entre
        o return desta função e o pin do caller. O caller fica obrigado a
        decrementar (ver ``generate``).

        Levanta ``InsufficientVramError`` se, após evicção, a VRAM livre ainda
        for inferior ao **pico** (pesos + activação + safety) — evita OOM a meio
        do load/inferência.

        Levanta ``KeyError`` se o backend não estiver registado, ou propaga
        exceções do adapter (ImportError se deps em falta, erros de carga).
        """
        desc = self._registry.descriptor(name)  # KeyError se desconhecido
        # Em modo subprocesso, o adapter real nunca é importado neste processo
        # — vive no venv da tool. Só é resolvido no caminho in-process abaixo.
        adapter: Any = None
        if not self._use_subprocess(name):
            adapter = self._registry.adapter(name)
        quant, mem_eff, group_off, streams = self.resolve_peak_params(name, load_kwargs)
        # Admit no load: pesos completos — EXCEPTO backends cujo load já é
        # streaming (diffusers model_cpu offload, text2d): aí o pico de warmup é
        # largest-module + activação. ``group_offload`` só reduz o residente
        # *depois* do load (headroom) — usar largest no admit de um backend que
        # carrega tudo subestima o warmup → OOM a meio do adapter.load.
        weights_mib, activation_mib = self.footprint_parts_mib(
            name,
            quant_mode=quant,
            memory_efficient=mem_eff,
            group_offload=streams,
            footprint_key=load_kwargs.get("footprint_key"),
        )
        peak = compute_peak_mib(weights_mib, activation_mib)
        new_shape = self._extract_load_shape(load_kwargs)

        with self._struct_lock:
            state = self._states.get(name)
            if state is not None:
                # Worker morto mas ``subprocess_loaded`` ainda True → falharia
                # generate com «não está vivo». Limpar e forçar reload.
                self._clear_stale_subprocess_unlocked(name, state)
            if state is not None and state.is_loaded():
                if not self._shape_mismatch(state.load_shape, load_kwargs):
                    state.last_used = time.monotonic()
                    if _pin:
                        state.ref_count += 1
                    return state.model
                # Shape diverge (ex. max_num_view 6→4 no load) — reload.
                if state.ref_count > 0:
                    raise ShapeBusyError(
                        name,
                        stored=dict(state.load_shape),
                        requested=new_shape,
                    )
                _logger.info(f"[UMS] Shape mismatch em {name!r} — a recarregar (views/quant/offload).")
                self._evict_unlocked(name)

            # Precisa carregar — evictar até caber o PICO (não só pesos YAML).
            free = self._admit_free_mib()
            if free is not None and free < peak:
                names_to_evict = plan_eviction(self._all_snapshots(), peak, free)
                for victim in names_to_evict:
                    self._evict_unlocked(victim)
                self._clear_cache()
                free = self._admit_free_mib()

            if state is None:
                state = _LoadedState()
                self._states[name] = state

        # Espera VRAM transitória FORA do struct_lock (não bloquear evict/status).
        free = self._admit_free_mib()
        if not can_admit(free, peak):
            free = self._wait_for_admit(peak, free)
            if not can_admit(free, peak):
                raise InsufficientVramError(
                    name,
                    peak_mib=peak,
                    free_mib=free,
                    weights_mib=weights_mib,
                    activation_mib=activation_mib,
                    quant_mode=quant,
                )

        # Carga fora do struct_lock (demora segundos; outros backends podem
        # servir pedidos entretanto). Mas guardamos o gen_lock do backend.
        with state.gen_lock:
            # Re-verificar (outro thread pode ter carregado enquanto esperávamos).
            with self._struct_lock:
                self._clear_stale_subprocess_unlocked(name, state)
            if state.is_loaded() and not self._shape_mismatch(state.load_shape, load_kwargs):
                state.last_used = time.monotonic()
                if _pin:
                    with self._struct_lock:
                        state.ref_count += 1
                return state.model
            if state.is_loaded() and self._shape_mismatch(state.load_shape, load_kwargs):
                if state.ref_count > 0:
                    raise ShapeBusyError(
                        name,
                        stored=dict(state.load_shape),
                        requested=new_shape,
                    )
                with self._struct_lock:
                    self._evict_unlocked(name)
            # Re-check VRAM (outra carga pode ter corrido entretanto).
            free = self._admit_free_mib()
            if not can_admit(free, peak):
                free = self._wait_for_admit(peak, free)
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
                f"quant={quant}, group_offload_after_load={group_off}, yaml={desc.vram_mib})..."
            )
            # footprint_key é sinal de peak/admit — nunca chega ao ctor do adapter.
            load_kwargs.pop("footprint_key", None)
            # Dentro de ``gen_lock``: refrescar activity para o IdleEvictor não
            # usar o timer do ciclo anterior enquanto o spawn/load corre.
            with self._struct_lock:
                state.last_activity = time.monotonic()
            t0 = time.perf_counter()
            use_subprocess = self._use_subprocess(name)
            if use_subprocess:
                # Modo subprocesso: o model object vive noutro processo.
                # SubprocessWorkerPool carrega o worker persistente.
                tool = desc.tool
                self._subprocess_pool.load(name, tool, load_kwargs)
                load_time = time.perf_counter() - t0
                with self._struct_lock:
                    # model fica None; o marcador is_loaded() vem de subprocess_loaded.
                    state.model = None
                    state.subprocess_loaded = True
                    state.load_shape = new_shape
                    state.last_used = time.monotonic()
                    if _pin:
                        state.ref_count += 1
                self.stats.record_load(name, load_time)
                _logger.info(f"[UMS] Backend {name!r} carregado em {load_time:.1f}s (subprocesso {tool}).")
                # Retornar um marcador não-None (callers usam como handle opaco).
                return state
            model = adapter.load(**load_kwargs)
            load_time = time.perf_counter() - t0
            with self._struct_lock:
                state.model = model
                state.load_shape = new_shape
                state.last_used = time.monotonic()
                if _pin:
                    # Pin atómico com a carga — sem janela ref=0 para eviction.
                    state.ref_count += 1
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
        abort_cb = req.get("_abort")
        load_kwargs = {k: v for k, v in req.items() if k in _LOAD_KWARG_KEYS}
        try:
            if callable(abort_cb) and abort_cb():
                return {
                    "status": "error",
                    "error": "cancelled before load",
                    "error_code": P.ERR_CANCELLED,
                }
            # _pin=True: ref_count sobe atomicamente com o ensure — fecha a
            # janela onde outro actor via ref=0 e evictava o model recém-obtido.
            model = self.ensure_loaded(name, _pin=True, **load_kwargs)
            # Load pode demorar minutos — cancel durante load só aplica aqui.
            if callable(abort_cb) and abort_cb():
                return {
                    "status": "error",
                    "error": "cancelled after load",
                    "error_code": P.ERR_CANCELLED,
                }
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
        except ShapeBusyError as e:
            self.stats.record_error(name, str(e))
            return {
                "status": "error",
                "error": str(e),
                "error_code": "SHAPE_BUSY",
                "hint": "Espera o job atual (`ums queue` / `ums wait`) antes de mudar shape.",
            }
        state = self._states[name]
        should_evict = False
        try:
            # Pesos já em VRAM: ainda precisamos de headroom livre para activações.
            _quant, mem_eff, group_off, streams = self.resolve_peak_params(name, req)
            headroom = self.activation_headroom_mib(
                name,
                quant_mode=_quant,
                memory_efficient=mem_eff,
                group_offload=group_off or streams,
                footprint_key=req.get("footprint_key"),
            )
            free = self._free_mib()
            if free is not None and free < headroom:
                # Activação residual do job anterior costuma ficar em cache CUDA —
                # limpar antes de falhar (senão 2º job em ~6 GB morre com pesos hot).
                self._clear_cache()
                free = self._free_mib()
            if free is not None and free < headroom:
                # Tentar evictar idle irmãos para abrir activação.
                with self._struct_lock:
                    names_to_evict = plan_eviction(self._all_snapshots(), headroom, free)
                    names_to_evict = [n for n in names_to_evict if n != name]
                    for victim in names_to_evict:
                        self._evict_unlocked(victim)
                    if names_to_evict:
                        self._clear_cache()
                    free = self._free_mib()
                if free is not None and free < headroom:
                    _w, act = self.footprint_parts_mib(
                        name,
                        quant_mode=_quant,
                        memory_efficient=mem_eff,
                        group_offload=group_off or streams,
                        footprint_key=req.get("footprint_key"),
                    )
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

            with state.gen_lock:
                t0 = time.perf_counter()
                if callable(progress_cb):
                    with contextlib.suppress(Exception):
                        progress_cb(None, f"generating via {name}")
                if self._use_subprocess(name):
                    # Modo subprocesso: o adapter.generate in-process é substituído
                    # pela chamada ao worker persistente via JSONL.
                    abort_cb = req.get("_abort")
                    response = self._subprocess_pool.generate(
                        name,
                        req,
                        on_progress=progress_cb,
                        should_abort=abort_cb if callable(abort_cb) else None,
                    )
                else:
                    response = self._registry.adapter(name).generate(model, req)
                gen_time = time.perf_counter() - t0
                state.last_used = time.monotonic()
                self.stats.record_generate(name, gen_time)
                # Runtime VRAM budget (chunks/views/tiles) reportado pelo adapter —
                # visível em `ums stats` para diagnosticar OOM/eficiência.
                if isinstance(response, dict):
                    self.stats.record_runtime_budget(name, response.get("runtime_budget"))
                # Libertar activação residual para o próximo job hot no mesmo backend.
                self._clear_cache()
                return response
        except InsufficientVramError as e:
            self.stats.record_error(name, str(e))
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
            should_evict = True
            err_txt = str(e)
            err_l = err_txt.lower()
            code: str | None = None
            if "out of memory" in err_l:
                code = P.ERR_VRAM_INSUFFICIENT
            elif _is_worker_dead_message(err_txt):
                code = P.ERR_WORKER_DEAD
            out: dict[str, Any] = {"status": "error", "error": err_txt}
            if code:
                out["error_code"] = code
                if code == "VRAM_INSUFFICIENT":
                    out["hint"] = "OOM na inferência — peak VRAM subestimado ou GPU partilhada. `ums queue`."
                else:
                    out["hint"] = (
                        "Worker subprocesso morto (idle shutdown / crash). "
                        "UMS requeue automático; se persistir: `ums respawn <backend>`."
                    )
            return out
        finally:
            # Um único decrement; evict só após ref=0 (senão _evict_unlocked recusa).
            with self._struct_lock:
                state.ref_count = max(0, state.ref_count - 1)
                if should_evict:
                    with contextlib.suppress(Exception):
                        self._evict_unlocked(name)
            if should_evict:
                self._clear_cache()

    # ------------------------------------------------------------------
    # Evicção
    # ------------------------------------------------------------------

    def evict(self, name: str) -> bool:
        """Evicta (unload) um backend específico. Retorna ``True`` se estava carregado."""
        with self._struct_lock:
            evicted = self._evict_unlocked(name)
        if evicted:
            self._clear_cache()
            # Último backend fora → residual costuma ficar no contexto CUDA.
            if not self.loaded_names():
                self.scrub_dead_vram()
        return evicted

    def evict_all(self) -> int:
        """Evicta TODOS os backends carregados (release global). Retorna o nº evicted."""
        with self._struct_lock:
            names = [n for n, s in self._states.items() if s.is_loaded()]
            count = 0
            for n in names:
                if self._evict_unlocked(n):
                    count += 1
        # Sempre scrub — mesmo com count=0 (loaded=[] mas contexto CUDA vivo).
        self.scrub_dead_vram()
        return count

    # ------------------------------------------------------------------
    # Respawn (reiniciar SÓ o worker subprocesso de um backend)
    # ------------------------------------------------------------------

    def respawn(self, name: str, *, lazy: bool = True) -> dict[str, Any]:
        """Reinicia o worker subprocesso do backend ``name`` sem tocar no supervisor.

        O caso de uso é desenvolvimento: depois de editar código da tool
        (ex.: ``Text3D/src/text3d/utils/export.py`` onde mora o ``save_mesh`` do
        GLB), o worker persistente em ``Text3D/.venv`` ainda tem o módulo
        antigo em memória — ``evict`` só descarrega os pesos, não apanha o
        código novo. Este método mata o subprocesso e arranca um novo no venv
        da tool, pelo que o próximo ``generate`` já corre o código atualizado.

        Com ``lazy=True`` (default): só mata o worker vivo (se existir); o
        reload fica pendente para o próximo ``generate``/``preload`` — não há
        aquecimento de VRAM desnecessário se a tool não for usada de seguida.

        Com ``lazy=False``: mata o worker E recarrega-o imediatamente com o
        mesmo ``load_shape`` guardado (quente na próxima chamada).

        Retorna um sumário ``{name, respawned, mode, was_alive, had_model}``.

        Levanta ``KeyError`` se o backend for desconhecido. Backends que não
        usam subprocesso (``desc.tool`` vazio ou modo in-process) são no-op:
        não há worker separado para reiniciar — o supervisor já não importa o
        código da tool em modo subprocesso, e em modo in-process o reload só é
        possível reiniciando o próprio supervisor.
        """
        desc = self._registry.descriptor(name)  # KeyError se desconhecido
        with self._struct_lock:
            state = self._states.get(name)
            was_loaded = bool(state and state.is_loaded())
            had_model = was_loaded
            saved_shape: dict[str, Any] = dict(state.load_shape) if state else {}
            was_alive = self._subprocess_pool.is_alive(name) if self._subprocess_pool else False

            if not self._use_subprocess(name):
                # In-process / sem tool: não há worker isolado para reiniciar.
                return {
                    "name": name,
                    "respawned": False,
                    "mode": "in-process",
                    "was_alive": False,
                    "had_model": had_model,
                    "reason": "backend sem worker subprocesso (desc.tool vazio ou GAMEDEV_UMS_SUBPROCESS=0)",
                }

            if self._subprocess_pool is None:
                return {
                    "name": name,
                    "respawned": False,
                    "mode": "no-pool",
                    "was_alive": False,
                    "had_model": had_model,
                    "reason": "SubprocessWorkerPool não activo",
                }

            # Recusar se há ref_count > 0 (job a correr) — segurança contra
            # matar um worker mid-generate. O dispatch no server.py já valida
            # fila ocupada antes de chegar aqui; isto é dupla trava.
            if state is not None and state.ref_count > 0:
                raise ShapeBusyError(
                    name,
                    stored=saved_shape,
                    requested=saved_shape,
                )

            # 1) Descarregar marcador + mandar pool matar o subprocesso.
            if state is not None:
                state.mark_unloaded()
            killed = self._subprocess_pool.shutdown(name)
            # O lazy deixa o worker em «morto» — o próximo ensure_loaded faz
            # _spawn (state.proc is None) + load.
            # Modo hot: recarregar com o mesmo load_shape para ficar quente.
            if not lazy and saved_shape:
                # Recriar state (shutdown removeu-o do pool interno).
                if state is None:
                    state = _LoadedState()
                    self._states[name] = state
                tool = desc.tool
                self._subprocess_pool.load(name, tool, dict(saved_shape))
                state.subprocess_loaded = True
                state.load_shape = dict(saved_shape)
                state.last_used = time.monotonic()
        self._clear_cache()
        _logger.info(
            f"[UMS] respawn {name!r}: worker {'morto' if killed else 'não estava vivo'} "
            f"(lazy={lazy}, had_model={had_model}, saved_shape={saved_shape})."
        )
        return {
            "name": name,
            "respawned": killed,
            "mode": "lazy" if lazy else "hot",
            "was_alive": was_alive,
            "had_model": had_model,
            "load_shape": saved_shape,
        }

    def respawn_all(self, *, lazy: bool = True) -> list[dict[str, Any]]:
        """Aplica :meth:`respawn` a todos os backends registados com ``tool:``.

        Útil para o fluxo de desenvolvimento após editar várias tools de uma
        vez. Retorna a lista de sumários (um por backend).
        """
        with self._struct_lock:
            names = [n for n in self._registry.names if self._use_subprocess(n)]
        return [self.respawn(n, lazy=lazy) for n in names]

    def idle_worker_candidates(self, idle_timeout_sec: float) -> list[tuple[str, float]]:
        """``(name, last_activity)`` dos subprocessos worker vivos e idle.

        Só considera workers **sem** modelo carregado (o ``unload`` já correu via
        :meth:`idle_candidates`) e sem refs. Terminar o processo devolve o
        contexto CUDA, que o ``unload`` não liberta. O tempo conta desde o
        último job, não desde o unload.

        Ignora backends com ``gen_lock`` tomado — ``ensure_loaded`` segura esse
        lock durante o spawn/load (pode demorar >1 min). Sem este guard, o
        IdleEvictor mata o worker a meio do load (``last_activity`` antiga do
        ciclo anterior) → ``worker não está vivo`` no generate seguinte.
        """
        pool = self._subprocess_pool
        if pool is None:
            return []
        now = time.monotonic()
        with self._struct_lock:
            out: list[tuple[str, float]] = []
            for name, state in self._states.items():
                if state.ref_count > 0 or state.is_loaded():
                    continue
                # load/generate a decorrer — nunca shutdown.
                if state.gen_lock.locked():
                    continue
                if not pool.is_alive(name):
                    continue
                if state.last_activity > 0 and now - state.last_activity >= idle_timeout_sec:
                    out.append((name, state.last_activity))
            return out

    def shutdown_worker(self, name: str) -> bool:
        """Termina o subprocesso worker de ``name`` (mantém o backend registado)."""
        pool = self._subprocess_pool
        if pool is None:
            return False
        with self._struct_lock:
            state = self._states.get(name)
            if state is not None and state.ref_count > 0:
                _logger.warn(f"[UMS] Recusa terminar worker {name!r}: {state.ref_count} ref(s) ativa(s).")
                return False
        # Correr shutdown sob ``gen_lock`` (non-blocking): se ``ensure_loaded`` /
        # ``generate`` tem o lock, abortar — senão a race load↔IdleEvictor mata
        # o worker recém-spawnado (ver idle_worker_candidates).
        acquired = False
        if state is not None:
            acquired = state.gen_lock.acquire(blocking=False)
            if not acquired:
                _logger.info(f"[UMS] Skip shutdown worker {name!r}: load/generate em curso.")
                return False
        try:
            try:
                done = bool(pool.shutdown(name))
            except Exception as e:
                _logger.warn(f"[UMS] shutdown do worker {name!r} falhou: {e}")
                return False
            if done:
                with self._struct_lock:
                    st = self._states.get(name)
                    if st is not None and st.is_loaded():
                        st.mark_unloaded()
                _logger.info(f"[UMS] Worker {name!r} terminado (contexto CUDA libertado).")
            return done
        finally:
            if acquired and state is not None:
                state.gen_lock.release()

    def health_check_workers(self) -> list[dict[str, Any]]:
        """Ping a cada worker vivo; termina os que não respondem.

        Um worker wedged (deadlock no adapter, driver preso) continua a segurar
        VRAM sem nunca terminar um job. Devolve uma entrada por worker testado.
        """
        pool = self._subprocess_pool
        if pool is None:
            return []
        with self._struct_lock:
            names = [n for n, s in self._states.items() if s.ref_count == 0]
        out: list[dict[str, Any]] = []
        for name in names:
            try:
                if not pool.is_alive(name):
                    continue
                ok = bool(pool.ping(name))
            except Exception as e:
                _logger.warn(f"[UMS] ping ao worker {name!r} falhou: {e}")
                ok = False
            out.append({"backend": name, "ok": ok})
            if not ok:
                self.shutdown_worker(name)
        return out

    def idle_candidates(self, idle_timeout_sec: float) -> list[tuple[str, float]]:
        """Lista ``(name, last_used)`` dos backends loaded idle há mais de ``idle_timeout_sec``.

        API pública usada pelo IdleEvictor — evita acesso externo a ``_states``
        e abstrai o modo (in-process vs subprocesso). Filtra: carregados (via
        ``is_loaded()``), ``ref_count == 0`` e ``last_used > 0``.
        """
        now = time.monotonic()
        with self._struct_lock:
            out: list[tuple[str, float]] = []
            for name, state in self._states.items():
                if (
                    state.is_loaded()
                    and state.ref_count == 0
                    and state.last_used > 0
                    and now - state.last_used >= idle_timeout_sec
                ):
                    out.append((name, state.last_used))
            return out

    def ensure_vram(
        self,
        needed_mib: int,
        *,
        backend: str | None = None,
        quant_mode: str = "none",
        memory_efficient: bool = False,
        allow_group_offload: bool | None = None,
    ) -> bool:
        """Evicta peso+LRU até haver ``needed_mib`` livres. Retorna ``True`` se OK.

        Se ``backend`` for dado, o alvo é ``max(needed_mib, peak_vram_mib(backend))``
        — assim clientes que pedem 5000 MiB ainda reservam activação de inferência.

        Peak de admit usa **pesos completos** (``group_offload=False``): ensure-vram
        liberta espaço para um load frio; largest-onloaded só vale pós-offload.

        Se não há backends evictáveis mas a VRAM livre ainda é baixa, faz scrub
        de residual morto (cache/contexto no próprio UMS) e reavalia.
        """
        target = int(needed_mib)
        if backend:
            with contextlib.suppress(KeyError):
                src: dict[str, Any] = {
                    "quant_mode": quant_mode,
                    "memory_efficient": memory_efficient,
                }
                if allow_group_offload is not None:
                    src["allow_group_offload"] = allow_group_offload
                quant, mem_eff, _go, streams = self.resolve_peak_params(backend, src)
                target = max(
                    target,
                    self.peak_vram_mib(
                        backend,
                        quant_mode=quant,
                        memory_efficient=mem_eff,
                        group_offload=streams,
                        footprint_key=src.get("footprint_key"),
                    ),
                )
        names_to_evict: list[str] = []
        with self._struct_lock:
            free = self._free_mib()
            if free is not None and free >= target:
                return True
            if free is None:
                # Sem leitura NVML/smi — não dá para verificar; não evictar cegamente.
                return True
            names_to_evict = plan_eviction(self._all_snapshots(), target, free)
            for victim in names_to_evict:
                self._evict_unlocked(victim)
        if names_to_evict:
            self._clear_cache()
            free = self._free_mib()
            if free is None or free >= target:
                return True
        # Sem vítimas (ou ainda curto): residual no próprio processo UMS.
        self.scrub_dead_vram()
        free = self._free_mib()
        # Último recurso: VRAM presa por supervisores/workers UMS órfãos de runs
        # anteriores. Recuperá-la é preferível a recusar o job.
        if free is not None and free < target and self.reap_strays():
            free = self._free_mib()
        return free is None or free >= target

    def reap_strays(self) -> bool:
        """Mata processos UMS órfãos que seguram VRAM. ``True`` se algo foi morto."""
        if self._reap_strays is None:
            return False
        try:
            report = self._reap_strays()
        except Exception as e:
            _logger.warn(f"[UMS] reap de órfãos falhou: {e}")
            return False
        count = int((report or {}).get("count") or 0)
        if count:
            freed = (report or {}).get("vram_mib_freed")
            _logger.warn(f"[UMS] {count} processo(s) UMS órfão(s) terminado(s) — ~{freed} MiB recuperados.")
            self._clear_cache()
        return count > 0

    def _evict_unlocked(self, name: str) -> bool:
        """Evicta SEM adquirir struct_lock (caller deve ter o lock). Retorna True se evicted."""
        import gc

        state = self._states.get(name)
        if state is None or not state.is_loaded():
            return False
        if state.ref_count > 0:
            _logger.warn(f"[UMS] Recusa evictar {name!r}: {state.ref_count} ref(s) ativa(s).")
            return False
        _logger.info(f"[UMS] A evictar backend {name!r}...")
        if state.subprocess_loaded and self._subprocess_pool is not None:
            # Modo subprocesso: o worker persiste vivo; só descarrega pesos.
            try:
                self._subprocess_pool.unload(name)
            except Exception as e:
                _logger.warn(f"[UMS] subprocess unload({name!r}) falhou: {e}")
            state.mark_unloaded()
            self.stats.record_evict(name)
            _logger.info(f"[UMS] Backend {name!r} evicted (subprocesso descarregado).")
            return True
        adapter = self._registry.adapter(name)
        model = state.model
        state.mark_unloaded()
        try:
            adapter.unload(model)
        except Exception as e:
            _logger.warn(f"[UMS] unload({name!r}) falhou: {e}")
        del model
        gc.collect()
        self.stats.record_evict(name)
        # Pesos Python libertados; o contexto CUDA do processo (~0.1-0.3 GiB)
        # fica — é baseline partilhado por todos os jobs futuros deste worker,
        # já contado nos peaks calibrados in-process (ver _admit_free_mib).
        residual = self._process_vram_mib()
        if residual is not None and residual >= int(P.DEAD_VRAM_MIB):
            _logger.info(f"[UMS] Backend {name!r} evicted; residual process={residual} MiB (contexto/cache).")
        else:
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
                loaded = state is not None and state.is_loaded()
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
            loaded_count = sum(1 for b in backends if b["loaded"])
            loaded_vram = sum(b["vram_mib"] for b in backends if b["loaded"])
        # Fora do lock: NVML/smi pode ser lento.
        process_vram = self._process_vram_mib()
        alloc_stats = self._torch_alloc_stats()
        return {
            "loaded_count": loaded_count,
            "loaded_vram_mib": loaded_vram,
            "process_vram_mib": process_vram,
            "worker_vram_mib": self.worker_vram_mib(),
            "torch_alloc": alloc_stats,
            "backends": backends,
        }

    def worker_vram_mib(self) -> int | None:
        """VRAM somada dos subprocessos worker deste supervisor.

        O ``process_vram_mib`` só vê o PID do supervisor; com
        subprocess-per-backend a VRAM real vive nos filhos.
        """
        pool = self._subprocess_pool
        if pool is None:
            return None
        try:
            from .process_guard import descendants, gpu_vram_by_pid

            mine = descendants(os.getpid())
            if not mine:
                return None
            by_pid = gpu_vram_by_pid()
            total = sum(mib for pid, mib in by_pid.items() if pid in mine)
            return int(total) or None
        except Exception:
            return None

    @staticmethod
    def _torch_alloc_stats() -> dict[str, int | None]:
        """Stats do allocator torch do processo (diagnóstico de VRAM residual).

        ``allocated`` = tensores vivos; ``reserved`` = segmentos do allocator
        (cache reutilizável pelo próximo job in-process); ``reusable`` =
        ``reserved - allocated`` (crédito de admit — ver ``_admit_free_mib``).
        ``None`` se torch ainda não inicializou CUDA neste processo.
        """
        try:
            import torch

            if not torch.cuda.is_available() or not torch.cuda.is_initialized():
                return {"allocated_mib": None, "reserved_mib": None, "reusable_mib": None}
            allocated = int(torch.cuda.memory_allocated() // (1024 * 1024))
            reserved = int(torch.cuda.memory_reserved() // (1024 * 1024))
            return {
                "allocated_mib": allocated,
                "reserved_mib": reserved,
                "reusable_mib": max(0, reserved - allocated),
            }
        except Exception:
            return {"allocated_mib": None, "reserved_mib": None, "reusable_mib": None}
