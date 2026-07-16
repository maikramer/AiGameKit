"""AffinityScheduler — escolhe o próximo job com prioridade + afinidade VRAM.

Política:
  1. Atender primeiro a faixa de prioridade mais alta (``interactive`` > ``batch``).
  2. Dentro da faixa, FIFO salvo **afinidade**: se a cabeça precisa de um backend
     *não* carregado e existe mais atrás um job cujo backend já está em VRAM,
     saltar a cabeça (incrementa ``affinity_cuts`` nela) e atender o job quente.
  3. Após ``max_cuts`` (default 3) saltos contra a mesma cabeça, forçar atender
     a cabeça (anti-starvation) — mesmo que implique unload/evict.
"""

from __future__ import annotations

from collections.abc import Callable, Collection

from . import protocol as P
from .job_queue import Job


class AffinityScheduler:
    """Selecciona o próximo job a correr a partir da fila queued."""

    def __init__(self, *, max_cuts: int = P.MAX_AFFINITY_CUTS) -> None:
        self.max_cuts = max_cuts

    def pick_next(
        self,
        jobs: list[Job],
        loaded: Collection[str],
        *,
        loaded_fn: Callable[[], Collection[str]] | None = None,
    ) -> Job | None:
        """Devolve o job a despachar, ou ``None`` se a fila estiver vazia.

        Args:
            jobs: Jobs em estado ``queued`` (não removidos ainda).
            loaded: Nomes de backends actualmente em VRAM.
            loaded_fn: Opcional; se dado, reconsulta loaded no momento do pick
                (útil quando o inventário muda entre chamadas).
        """
        if not jobs:
            return None
        loaded_set = set(loaded_fn() if loaded_fn is not None else loaded)

        # Filtrar só queued (safety).
        eligible = [j for j in jobs if j.state == P.JOB_QUEUED and not j.cancel_requested]
        if not eligible:
            return None

        # Melhor faixa de prioridade presente.
        best_rank = min(P.PRIORITY_RANK.get(j.priority, 99) for j in eligible)
        band = [j for j in eligible if P.PRIORITY_RANK.get(j.priority, 99) == best_rank]
        # FIFO dentro da faixa (seq crescente).
        band.sort(key=lambda j: j.seq)

        head = band[0]
        if head.backend in loaded_set or head.affinity_cuts >= self.max_cuts:
            return head

        # Procurar job quente mais antigo na mesma faixa.
        for candidate in band[1:]:
            if candidate.backend in loaded_set:
                head.affinity_cuts += 1
                return candidate

        # Nenhum job quente — atender a cabeça (vai carregar/evictar).
        return head
