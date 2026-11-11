"""GameAssets ↔ router do vramd: criticidade, tiers de hardware e prefetch.

Com várias GPUs (locais ou peers), três decisões passam do batch para o vramd —
e o batch só tem de lhe dar a informação e a fila para as tomar bem:

* **Criticidade** — um herói e uma pedra não valem o mesmo. Cada asset leva
  ``criticality`` (do manifesto ou da categoria): o vramd põe os críticos na
  GPU onde o tier de qualidade é melhor e os de enchimento na mais pequena.
* **Tiers de hardware** — o hw-auto de cada tool corre aqui para *cada* tamanho
  de GPU disponível; o payload leva todos e o vramd aplica o da GPU onde o job
  cai (fp16 numa de 24 GB, int4 numa de 6 GB). Antes valia a GPU de quem
  submetia, que num cliente remoto nem é a que gera.
* **Fila cheia** — o paint de cada asset é submetido assim que o shape dele
  acaba (:class:`PaintPrefetcher`), em vez de esperar pela wave inteira de
  shapes. Com uma GPU livre e um shape pronto, o vramd decide se põe o paint
  nessa GPU já — a barreira entre waves deixava-a parada.

Nada disto se activa com um vramd antigo ou com uma só GPU (``capacity == 1``):
o batch comporta-se como antes.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from aigamekit_shared.vramd_topology import (
    CRIT_AUTO,
    CRIT_HIGH,
    CRIT_LOW,
    CRIT_NORMAL,
    MIB,
    Topology,
    attach_routing,
    build_hw_tiers,
    fetch_topology,
    normalize_criticality,
)

# Criticidade por omissão de cada categoria. Personagens e o que o jogador
# segura dominam o ecrã; vegetação, pedras e adereços enchem o cenário.
CATEGORY_CRITICALITY: dict[str, str] = {
    "humanoid": CRIT_HIGH,
    "creature": CRIT_HIGH,
    "weapon": CRIT_HIGH,
    "armor": CRIT_HIGH,
    "vehicle": CRIT_HIGH,
    "building": CRIT_NORMAL,
    "chest": CRIT_NORMAL,
    "furniture": CRIT_NORMAL,
    "tool": CRIT_NORMAL,
    "tree": CRIT_LOW,
    "rock": CRIT_LOW,
    "mineral": CRIT_LOW,
    "prop": CRIT_LOW,
    "vegetation": CRIT_LOW,
    "food": CRIT_LOW,
    "terrain": CRIT_LOW,
    "effects": CRIT_LOW,
}


def effective_criticality(row: Any) -> str:
    """``criticality:`` explícito do asset, senão o da categoria, senão ``normal``."""
    explicit = str(getattr(row, "criticality", "") or "").strip().lower()
    if explicit and explicit != CRIT_AUTO:
        try:
            value = normalize_criticality(explicit)
        except ValueError:
            value = CRIT_AUTO
        if value != CRIT_AUTO:
            return value
    category = str(getattr(row, "category", "") or getattr(row, "kind", "") or "").strip().lower()
    if category and category not in CATEGORY_CRITICALITY:
        # ``hero``/``guerreiro``/``knight``… → ``humanoid`` (o mesmo mapa da
        # inferência, que procura o keyword como substring). Um exact-match
        # deixava ``ancient_stone_rock``/``boss_dragon`` em ``normal``.
        from .categories import _KEYWORD_MAP

        for keyword, mapped in _KEYWORD_MAP.items():
            if keyword in category:
                category = mapped
                break
    return CATEGORY_CRITICALITY.get(category, CRIT_NORMAL)


def topology_for(gpu: str | None = None) -> Topology | None:
    """Topologia do vramd que vai correr um asset com ``gpu: remote|local|auto``."""
    from .vramd_coord import _gpu_remote

    try:
        return fetch_topology(remote=_gpu_remote(gpu))
    except Exception:
        return None


def is_parallel(gpu: str | None = None) -> bool:
    """Há mais de uma GPU/peer a trabalhar ao mesmo tempo para este asset."""
    topo = topology_for(gpu)
    return topo is not None and topo.parallel


# ---------------------------------------------------------------------------
# Tiers por tool: o hw-auto da tool para uma GPU de ``size_mib``
# ---------------------------------------------------------------------------


def _single_gpu(size_mib: int) -> list[tuple[int, int]]:
    return [(0, int(size_mib) * MIB)]


def text3d_opts_for_size(size_mib: int) -> tuple[str | None, bool] | None:
    """``(sdnq_preset, memory_efficient)`` que o hw-auto do text3d escolhe para uma GPU."""
    try:
        from text3d.hardware import hw_auto_enabled, profile_from_specs
    except ImportError:
        return None
    if not hw_auto_enabled():
        return None
    hwp = profile_from_specs(_single_gpu(size_mib))
    preset = hwp.sdnq_preset
    return preset, bool(preset) or bool(getattr(hwp, "offload", False))


def paint3d_opts_for_size(size_mib: int) -> tuple[str | None, bool] | None:
    try:
        from paint3d.hardware import hw_auto_enabled, profile_from_specs
    except ImportError:
        return None
    if not hw_auto_enabled():
        return None
    mem = bool(profile_from_specs(_single_gpu(size_mib)).memory_efficient)
    return ("sdnq-uint8" if mem else None), mem


def text2d_opts_for_size(size_mib: int) -> tuple[str | None, bool, str | None, str | None] | None:
    try:
        from text2d.generator import model_footprint_key
        from text2d.hardware import hw_auto_enabled, profile_from_specs
    except ImportError:
        return None
    if not hw_auto_enabled():
        return None
    hwp = profile_from_specs(_single_gpu(size_mib))
    quant = None if hwp.quant_preset in (None, "none", "") else str(hwp.quant_preset)
    return quant, bool(hwp.memory_efficient), hwp.model_id, model_footprint_key(hwp.model_id)


def tiered_payload(
    payload: dict[str, Any],
    *,
    gpu: str | None,
    criticality: str | None,
    hw_auto: bool,
    variant_for_size: Callable[[int], Mapping[str, Any] | None] | None,
) -> dict[str, Any]:
    """Acrescenta ``hw_tiers``/``criticality`` a um payload, se o vramd os entender.

    Args:
        payload: Payload tal como já é construído (perfil hw-auto local).
        gpu: ``gpu:`` do asset (escolhe o vramd local ou remoto).
        criticality: ``high``/``normal``/``low``.
        hw_auto: ``False`` quando quant/memória foram fixados à mão (profile ou
            manifesto) — aí não há tiers: o pedido manda.
        variant_for_size: Constrói o mesmo payload para uma GPU de ``size`` MiB.
    """
    topo = topology_for(gpu)
    tiers: list[dict[str, Any]] = []
    if topo is not None and topo.supports_tiers and hw_auto and variant_for_size is not None:
        try:
            tiers = build_hw_tiers(payload, variant_for_size, topo.gpu_sizes_mib)
        except Exception:
            tiers = []
    return attach_routing(payload, topology=topo, tiers=tiers, criticality=criticality)


def routed_payload(
    payload: dict[str, Any],
    *,
    gpu: str | None,
    criticality: str | None,
) -> dict[str, Any]:
    """``criticality`` num payload sem ``hw_tiers`` (texture2d/áudio/terreno).

    Esses builders não têm perfil hw-auto por tamanho de GPU; ainda assim o
    router precisa da criticidade para pôr um herói na GPU melhor e o
    enchimento na mais pequena (antes o campo era descartado).
    """
    topo = topology_for(gpu)
    return attach_routing(payload, topology=topo, criticality=criticality)


# ---------------------------------------------------------------------------
# Prefetch shape → paint
# ---------------------------------------------------------------------------


def _prefetch_workers() -> int:
    raw = os.environ.get("VRAMD_PREFETCH_WORKERS", "").strip()
    try:
        return max(1, int(raw)) if raw else 2
    except ValueError:
        return 2


class PaintPrefetcher:
    """Submete o paint de cada asset ao vramd assim que o shape dele acaba.

    A preparação (topology-fix, refine Hi3D, ``_to_paint``) corre num pool em
    background: o thread do batch continua a esperar pelos shapes e a submeter
    os seguintes. Quando a wave de paint arranca, encontra os jobs já na fila
    (``take_prefetched``) — ou já feitos — e só espera por eles.

    Args:
        prepare: ``row_id → item de paint`` (o mesmo dict que a wave constrói)
            ou ``None`` se o asset não deve ser pintado agora.
        spec_kwargs: Argumentos de ``paint_specs_from_items`` da wave.
        enabled: ``False`` torna tudo num no-op (uma GPU, vramd antigo,
            ``--no-vramd``, paint não-hunyuan).
    """

    def __init__(
        self,
        prepare: Callable[[str], dict[str, Any] | None],
        spec_kwargs: Mapping[str, Any],
        *,
        enabled: bool,
        submit: Callable[..., dict[str, Any] | None] | None = None,
    ) -> None:
        self.enabled = enabled
        self._prepare = prepare
        self._spec_kwargs = dict(spec_kwargs)
        self._submit = submit
        self._lock = threading.Lock()
        self._futures: dict[str, Future[str | None]] = {}
        self._pool: ThreadPoolExecutor | None = None
        # Jobs submetidos por **este** prefetcher: (job_id, gpu, output). O
        # ``close`` só cancela os que ainda estejam no registo global com este
        # job_id; limpar o registo inteiro matava jobs de outro prefetcher.
        self._jobs: list[tuple[str, str, str | None]] = []
        self._closed = False
        self.submitted: dict[str, str] = {}
        self.errors: dict[str, str] = {}

    def chain(self, then: Callable[[Any], None] | None) -> Callable[[Any], None]:
        """Callback ``on_progress`` da wave de shapes: prefetch + o callback original."""

        def on_shape(result: Any) -> None:
            self.on_shape(result)
            if then is not None:
                then(result)

        return on_shape

    def on_shape(self, result: Any) -> None:
        if not self.enabled or self._closed or getattr(result, "status", None) not in ("ok", "skipped"):
            return
        asset_id = str(result.asset_id)
        with self._lock:
            if asset_id in self._futures:
                return
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=_prefetch_workers(), thread_name_prefix="paint-prefetch")
            self._futures[asset_id] = self._pool.submit(self._run, asset_id)

    def _run(self, asset_id: str) -> str | None:
        """Prepara e submete; devolve o ``mesh`` preparado (ou ``None``)."""
        from .vramd_batch import paint_specs_from_items
        from .vramd_coord import _gpu_remote, register_prefetched

        try:
            item = self._prepare(asset_id)
        except Exception as exc:
            # A wave volta a chamar a preparação e reporta o erro como sempre.
            with self._lock:
                self.errors[asset_id] = str(exc)
            return None
        if not item:
            return None
        # A construção do spec também pode falhar (payload/import); sem isto a
        # excepção ficava presa no Future, o prefetch parecia "sem erro" e a
        # wave repetia a preparação (topology-fix/refine) sem saber porquê.
        try:
            specs = paint_specs_from_items([item], **self._spec_kwargs)
        except Exception as exc:
            with self._lock:
                self.errors[asset_id] = str(exc)
            return None
        if not specs:
            return None
        spec = specs[0]
        submit = self._submit
        if submit is None:
            from aigamekit_shared.vramd_client import submit_to_vramd as submit
        try:
            resp = submit(
                "paint3d", dict(spec.payload), priority="batch", timeout_sec=30.0, remote=_gpu_remote(spec.gpu)
            )
        except Exception as exc:
            with self._lock:
                self.errors[asset_id] = str(exc)
            resp = None
        if isinstance(resp, Mapping) and resp.get("status") == "ok" and resp.get("job_id"):
            job_id = str(resp["job_id"])
            register_prefetched("paint3d", spec.output, job_id, spec.gpu)
            with self._lock:
                self._jobs.append((job_id, spec.gpu, spec.output))
                self.submitted[asset_id] = job_id
        return str(item.get("mesh")) if item.get("mesh") else None

    def prefetch(self, asset_id: str) -> None:
        """Prefetch de um asset cujo shape já existia (resume: não passa pela wave)."""

        class _Done:
            status = "ok"

        done = _Done()
        done.asset_id = asset_id  # type: ignore[attr-defined]
        self.on_shape(done)

    def owns(self, asset_id: str) -> bool:
        """O prefetch submeteu o paint deste asset (a wave só tem de esperar por ele).

        Espera pela preparação em curso: decidir antes dela acabar faria a wave
        tratar o asset como "já pintado" ou prepará-lo duas vezes.
        """
        with self._lock:
            future = self._futures.get(asset_id)
        if future is None:
            return False
        with contextlib.suppress(Exception):
            future.result()
        return asset_id in self.submitted

    def prepared_mesh(self, asset_id: str) -> str | None:
        """Mesh já preparada para o paint (espera pela preparação em curso).

        ``None`` = o prefetch não a preparou — a wave chama a preparação ela própria.
        """
        with self._lock:
            future = self._futures.get(asset_id)
        if future is None:
            return None
        try:
            return future.result()
        except Exception:
            return None

    def close(self) -> None:
        """Espera pelas preparações em curso e cancela jobs que ninguém reclamou.

        Idempotente. Só mexe nos jobs que **este** prefetcher submeteu e que
        ainda estejam no registo global com o mesmo ``job_id`` — um segundo
        prefetcher (ou um retry) pode ter uma entrada para o mesmo output.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=True)
        # Só depois do shutdown: um ``_run`` em curso pode ter submetido o job
        # e só agora o registou em ``_jobs``.
        with self._lock:
            own = list(self._jobs)
        from .vramd_coord import _gpu_remote, discard_prefetched

        try:
            from aigamekit_shared.vramd_client import cancel_vramd_job
        except ImportError:
            cancel_vramd_job = None  # type: ignore[assignment]
        for job_id, gpu, output in own:
            # Sem output o job nunca é reclamável pela wave → cancelar; com
            # output, só se a entrada ainda for nossa.
            if output is not None and not discard_prefetched("paint3d", output, job_id):
                continue
            if cancel_vramd_job is not None:
                with contextlib.suppress(Exception):
                    cancel_vramd_job(job_id, remote=_gpu_remote(gpu))

    def __enter__(self) -> PaintPrefetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
