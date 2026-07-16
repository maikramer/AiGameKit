"""VRAMPlanner — decide quais backends evictar quando a VRAM escasseia.

Estratégia: **peso + LRU**.

  1. Nunca evictar backends com referências ativas (ref_count > 0) — estão a meio
     de uma geração; evictá-los mataria o trabalho.
  2. Entre os idle (ref_count == 0), ordenar por prioridade **ascendente**
     (priority menor = evictado primeiro; backends "pesados" com priority alta
     compensa manter quentes). Tie-break por **last_used** ascendente (LRU —
     least-recently-used evictado primeiro).
  3. Evictar sequencialmente até acumular MiB suficientes.

**Peak (pesos + inferência):** admitir um backend exige VRAM livre ≥
``weights + activation + safety`` — não só o footprint de pesos. GPUs ~6 GB
não devem aceitar Hunyuan fp16 full (~8 GiB pico).

Este módulo é **puro** (sem torch, sem GPU, sem sockets) — totalmente testável
em CI sem hardware.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Margem contra fragmentação / contexto CUDA / compositor (MiB).
DEFAULT_VRAM_SAFETY_MIB = 384


def vram_safety_mib() -> int:
    """``GAMEDEV_UMS_VRAM_SAFETY_MIB`` ou default."""
    raw = os.environ.get("GAMEDEV_UMS_VRAM_SAFETY_MIB", "").strip()
    if not raw:
        return DEFAULT_VRAM_SAFETY_MIB
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_VRAM_SAFETY_MIB


def peak_vram_mib(
    weights_mib: int,
    activation_mib: int,
    *,
    safety_mib: int | None = None,
) -> int:
    """Pico estimado = pesos + activação de inferência + margem de segurança."""
    safety = vram_safety_mib() if safety_mib is None else max(0, safety_mib)
    return max(0, int(weights_mib)) + max(0, int(activation_mib)) + safety


def can_admit(free_mib: int | None, peak_mib: int) -> bool:
    """True se há VRAM livre suficiente para o pico (None = desconhecido → não bloquear)."""
    if free_mib is None:
        return True
    return int(free_mib) >= int(peak_mib)


def inference_headroom_mib(activation_mib: int, *, safety_mib: int | None = None) -> int:
    """Headroom livre necessário quando os pesos já estão carregados."""
    safety = vram_safety_mib() if safety_mib is None else max(0, safety_mib)
    return max(0, int(activation_mib)) + safety


@dataclass(frozen=True)
class LoadedBackend:
    """Snapshot imutável de um backend carregado, para o planner decidir.

    Attributes:
        name: Identificador do backend.
        vram_mib: Footprint estimado em MiB (do ``BackendDescriptor``).
        priority: Prioridade de evicção (menor = evictado primeiro).
        ref_count: Referências ativas (0 = idle, evictável; >0 = em uso).
        last_used: Timestamp monotónico do último uso (para LRU).
    """

    name: str
    vram_mib: int
    priority: int
    ref_count: int
    last_used: float


def plan_eviction(
    loaded: list[LoadedBackend],
    needed_mib: int,
    free_mib: int,
) -> list[str]:
    """Planear quais backends evictar para libertar ``needed_mib`` MiB.

    Footprint-aware: cada backend sabe quanto VRAM liberta (``vram_mib``, derivado
    do footprint registry quando disponível). A seleção minimiza a "prioridade
    perdida" ordenando por **efficiency** = ``vram_mib / max(priority, 1)`` — i.e.
    evicta primeiro os backends que libertam mais VRAM por unidade de prioridade.
    Tie-break: LRU (``last_used`` ascendente). Backends ativos (ref_count > 0)
    nunca são evicted.

    Args:
        loaded: Snapshots dos backends atualmente carregados.
        needed_mib: VRAM adicional necessária (MiB).
        free_mib: VRAM atualmente livre (MiB). Se já >= needed, retorna [].

    Returns:
        Lista de nomes de backends a evictar (por ordem). Pode ser vazia se já
        há VRAM suficiente, ou mais curta que o necessário se os backends
        carregados (idle) não chegam para libertar o pedido — o caller decide
        o que fazer (ex: carregar mesmo assim com offload, ou recusar).
    """
    # Fast path: já há VRAM suficiente.
    deficit = needed_mib - free_mib
    if deficit <= 0:
        return []

    # Só evictáveis: idle (ref_count == 0).
    evictable = [b for b in loaded if b.ref_count <= 0]
    # Efficiency: mais VRAM libertada por unidade de prioridade perdida = evictar
    # primeiro. Guarda prioridade para tie-break LRU quando efficiency é igual.
    evictable.sort(key=lambda b: (-b.vram_mib / max(b.priority, 1), b.priority, b.last_used))

    to_evict: list[str] = []
    freed = 0
    for b in evictable:
        if freed >= deficit:
            break
        to_evict.append(b.name)
        freed += b.vram_mib

    return to_evict
