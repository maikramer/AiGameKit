"""AffinityScheduler — escolhe o próximo job com prioridade + afinidade VRAM.

Política:
  1. Atender primeiro a faixa de prioridade mais alta (``interactive`` > ``batch``).
  2. Dentro da faixa, FIFO salvo **afinidade**: se a cabeça precisa de um backend
     *não* carregado e existe mais atrás um job cujo backend já está em VRAM,
     saltar a cabeça (incrementa ``affinity_cuts`` nela) e atender o job quente.
  3. Após ``max_cuts`` (default 3) saltos contra a mesma cabeça, forçar atender
     a cabeça (anti-starvation) — mesmo que implique unload/evict.
  4. Opcional: ``starvation_timeout_sec`` — se a cabeça esperou mais de N segundos,
     forçar pick independentemente dos cuts.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection

from . import protocol as P
from .job_queue import Job


class AffinityScheduler:
    """Selecciona o próximo job a correr a partir da fila queued."""

    def __init__(
        self,
        *,
        max_cuts: int = P.MAX_AFFINITY_CUTS,
        starvation_timeout_sec: float = P.STARVATION_TIMEOUT_SEC,
    ) -> None:
        self.max_cuts = max_cuts
        self.starvation_timeout_sec = float(starvation_timeout_sec)

    def pick_next(
        self,
        jobs: list[Job],
        loaded: Collection[str],
        *,
        loaded_fn: Callable[[], Collection[str]] | None = None,
        is_hot: Callable[[Job], bool] | None = None,
    ) -> Job | None:
        """Devolve o job a despachar, ou ``None`` se a fila estiver vazia.

        ``is_hot(job)`` (opcional): True se o backend está carregado **com o
        mesmo load_shape** do pedido. Sem callback, hot = ``backend in loaded``.
        """
        if not jobs:
            return None
        loaded_set = set(loaded_fn() if loaded_fn is not None else loaded)

        def _hot(job: Job) -> bool:
            if is_hot is not None:
                return bool(is_hot(job))
            return job.backend in loaded_set

        eligible = [j for j in jobs if j.state == P.JOB_QUEUED and not j.cancel_requested]
        if not eligible:
            return None

        best_rank = min(P.PRIORITY_RANK.get(j.priority, 99) for j in eligible)
        band = [j for j in eligible if P.PRIORITY_RANK.get(j.priority, 99) == best_rank]
        band.sort(key=lambda j: j.seq)

        head = band[0]
        wait_sec = time.monotonic() - head.created_at
        starve = self.starvation_timeout_sec > 0 and wait_sec >= self.starvation_timeout_sec

        if _hot(head) or head.affinity_cuts >= self.max_cuts or starve:
            return head

        for candidate in band[1:]:
            if _hot(candidate):
                head.affinity_cuts += 1
                return candidate

        return head
