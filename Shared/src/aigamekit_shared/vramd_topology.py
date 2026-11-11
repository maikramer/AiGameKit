"""Topologia do vramd (GPUs locais, peers) e ``hw_tiers`` para os payloads.

Com várias GPUs — locais ou de outras máquinas — duas coisas deixam de poder ser
decididas no cliente:

* **Onde** corre cada job: é o router do vramd que olha para a fila e escolhe
  entre a GPU onde o modelo está quente, uma réplica noutra GPU, o modelo
  dividido ou outro nó. O cliente só tem de manter a fila cheia — pelo menos
  ``capacity`` jobs em voo.
* **Com que qualidade**: o hw-auto de cada tool escolhia quant/offload pela GPU
  da máquina que *submete*. Um portátil de 6 GB a mandar para um servidor de
  24 GB pedia int4; um batch com uma GPU de 24 GB e outra de 6 GB mandava tudo
  no perfil de uma delas. Agora o cliente calcula o perfil hw-auto para cada
  tamanho de GPU disponível (``gpu_sizes_mib``) e envia-os como ``hw_tiers`` —
  o vramd aplica o da GPU onde o job cai.

Um vramd antigo (sem o comando ``topology`` e sem ``routing`` no status)
devolve ``None`` em :func:`fetch_topology` e tudo segue como antes. O 0.7.10,
que já anuncia GPUs/lanes em ``status.routing`` mas ainda não tem ``topology``,
é traduzido por :func:`_topology_from_status` — sem isso a wave ficava com
``capacity=1`` e só usava uma GPU.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

MIB = 1024 * 1024
FEATURE_HW_TIERS = "hw_tiers"
FEATURE_CRITICALITY = "criticality"
FEATURE_ROUTING = "routing"
_CACHE_TTL_SEC = 30.0

CRIT_HIGH = "high"
CRIT_NORMAL = "normal"
CRIT_LOW = "low"
CRIT_AUTO = "auto"
_CRIT_ALIASES = {
    "high": CRIT_HIGH,
    "hero": CRIT_HIGH,
    "critical": CRIT_HIGH,
    "critico": CRIT_HIGH,
    "crítico": CRIT_HIGH,
    "normal": CRIT_NORMAL,
    "standard": CRIT_NORMAL,
    "medium": CRIT_NORMAL,
    "low": CRIT_LOW,
    "filler": CRIT_LOW,
    "background": CRIT_LOW,
    "auto": CRIT_AUTO,
    "": CRIT_AUTO,
}


def normalize_criticality(value: object) -> str:
    """``hero``→``high``, ``filler``→``low``, vazio→``auto``.

    Raises:
        ValueError: valor desconhecido (o manifesto deve falhar alto).
    """
    key = str(value or "").strip().lower()
    if key not in _CRIT_ALIASES:
        raise ValueError(f"criticality deve ser high/normal/low (ou hero/filler/auto); recebi {value!r}")
    return _CRIT_ALIASES[key]


@dataclass(frozen=True)
class Topology:
    """O que o vramd anuncia em ``{"cmd": "topology"}``."""

    capacity: int = 1
    routed: bool = False
    gpu_sizes_mib: tuple[int, ...] = ()
    features: frozenset[str] = field(default_factory=frozenset)
    lanes: tuple[Mapping[str, Any], ...] = ()
    peers: tuple[Mapping[str, Any], ...] = ()

    @property
    def supports_tiers(self) -> bool:
        return FEATURE_HW_TIERS in self.features and bool(self.gpu_sizes_mib)

    @property
    def supports_criticality(self) -> bool:
        return FEATURE_CRITICALITY in self.features

    @property
    def parallel(self) -> bool:
        """Há mais de um sítio onde correr ao mesmo tempo (GPUs ou peers)."""
        return self.capacity > 1

    @classmethod
    def from_response(cls, resp: Mapping[str, Any]) -> Topology:
        raw_sizes = resp.get("gpu_sizes_mib") or []
        sizes = tuple(sorted({int(s) for s in raw_sizes if isinstance(s, (int, float)) and s > 0}))
        try:
            capacity = max(1, int(resp.get("capacity") or 1))
        except (TypeError, ValueError):
            capacity = 1
        return cls(
            capacity=capacity,
            routed=bool(resp.get("routed")),
            gpu_sizes_mib=sizes,
            features=frozenset(str(f) for f in resp.get("features") or []),
            lanes=tuple(r for r in resp.get("lanes") or [] if isinstance(r, Mapping)),
            peers=tuple(r for r in resp.get("peers") or [] if isinstance(r, Mapping)),
        )


_cache: dict[str, tuple[float, Topology | None]] = {}
_cache_lock = threading.Lock()


def _topology_from_status(status: Any) -> dict[str, Any] | None:
    """Sintetiza a topologia a partir de ``{"cmd": "status"}``.

    O vramd 0.7.10 anuncia as GPUs/peers em ``status.routing``/``lanes`` mas
    ainda não implementa o comando ``topology``. Sem esta ponte o cliente vê
    ``capacity=1`` e a wave submete um job de cada vez — a segunda GPU remota
    fica parada. Se o status não tiver routing/GPUs (vramd antigo), devolve
    ``None`` e o comportamento anterior mantém-se.
    """
    if not isinstance(status, Mapping) or status.get("status") not in ("ok", "status"):
        # O comando ``status`` do vramd responde com ``"status": "status"``
        # (nome do comando), não ``"ok"`` como o ``topology``. Aceitar os dois.
        return None
    routing = status.get("routing")
    gpus = routing.get("gpus") if isinstance(routing, Mapping) else None
    lanes = status.get("lanes") or []
    peers = status.get("peers") or []
    if not gpus and not peers:
        return None
    sizes = sorted(
        {
            int(g["total_mib"])
            for g in gpus or []
            if isinstance(g, Mapping) and isinstance(g.get("total_mib"), (int, float)) and g["total_mib"] > 0
        }
    )
    lane_names = {str(g.get("lane")) for g in gpus or [] if isinstance(g, Mapping) and g.get("lane")}
    capacity = max(1, len(lane_names) or len(gpus or []), len(peers))
    return {
        "status": "ok",
        "capacity": capacity,
        "routed": bool(routing.get("active", True)) if isinstance(routing, Mapping) else True,
        "gpu_sizes_mib": sizes,
        # O routing do 0.7.10 já aplica tiers/criticality; o ``topology``
        # nunca chegou a anunciá-los como features próprias.
        "features": [FEATURE_ROUTING, FEATURE_HW_TIERS, FEATURE_CRITICALITY],
        "lanes": list(lanes),
        "peers": list(peers),
    }


def _default_fetch(force_local: bool) -> dict[str, Any] | None:
    from .vramd_client import VRAMD_SOCKET, ensure_vramd_running, send_request

    if not ensure_vramd_running(auto_start=False, force_local=force_local):
        return None
    resp = send_request({"cmd": "topology"}, VRAMD_SOCKET, timeout_sec=10.0, force_local=force_local)
    if isinstance(resp, Mapping) and resp.get("status") == "ok":
        return dict(resp)
    # Compatibilidade 0.7.10: routing/lanes no status, sem comando topology.
    status = send_request({"cmd": "status"}, VRAMD_SOCKET, timeout_sec=10.0, force_local=force_local)
    return _topology_from_status(status)


def fetch_topology(
    *,
    remote: bool | None = None,
    refresh: bool = False,
    fetch: Callable[[bool], dict[str, Any] | None] | None = None,
) -> Topology | None:
    """Topologia do vramd (cache de 30 s). ``None`` se down ou antigo.

    Args:
        remote: ``False`` força o vramd local; ``True``/``None`` seguem
            ``VRAMD_REMOTE`` como o resto do cliente.
        refresh: Ignora a cache.
        fetch: Injectável nos testes.
    """
    if os.environ.get("VRAMD_TOPOLOGY", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    force_local = remote is False
    key = "local" if force_local else "default"
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and not refresh and (now - cached[0]) < _CACHE_TTL_SEC:
            return cached[1]
    try:
        resp = (fetch or _default_fetch)(force_local)
    except Exception:
        resp = None
    topology = Topology.from_response(resp) if isinstance(resp, Mapping) and resp.get("status") == "ok" else None
    with _cache_lock:
        _cache[key] = (now, topology)
    return topology


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _diff(base: Mapping[str, Any], variant: Mapping[str, Any], keys: Iterable[str] | None) -> dict[str, Any]:
    """Chaves em que ``variant`` difere de ``base``; ``None`` = remover do base."""
    names = set(keys) if keys is not None else set(base) | set(variant)
    out: dict[str, Any] = {}
    for name in sorted(names):
        if name in variant:
            if base.get(name) != variant[name] or name not in base:
                out[name] = variant[name]
        elif name in base:
            out[name] = None
    return out


def build_hw_tiers(
    base_payload: Mapping[str, Any],
    variant_for_size: Callable[[int], Mapping[str, Any] | None],
    sizes_mib: Iterable[int],
    *,
    keys: Iterable[str] | None = None,
    label_for_size: Callable[[int], str] | None = None,
) -> list[dict[str, Any]]:
    """``hw_tiers`` de um payload: o perfil hw-auto da tool para cada tamanho de GPU.

    Args:
        base_payload: O payload tal como seria enviado (perfil admit-safe).
        variant_for_size: Constrói o payload para uma GPU de ``size`` MiB com o
            hw-auto da tool (o mesmo builder, outros valores). ``None`` = sem
            perfil para esse tamanho — e então não há tiers nenhuns: um tier
            fp16 que cobrisse uma GPU sem perfil conhecido mandava-a para OOM.
        sizes_mib: Tamanhos de GPU onde o job pode cair (``Topology.gpu_sizes_mib``).
        keys: Restringe o diff a estas chaves (as que o hw-auto controla) —
            evita que um campo volátil do builder vire "diferença de tier".
        label_for_size: Nome legível do tier (default ``"24g+"``).

    Returns:
        Lista pronta para ``payload["hw_tiers"]``, por VRAM crescente. Tamanhos
        consecutivos com o mesmo perfil fundem-se num tier; o primeiro tier
        aplica-se a qualquer GPU (``min_vram_mib: 0``). Vazia quando todos os
        tamanhos dão o mesmo payload que o base — não há nada a escolher.
    """
    tiers: list[dict[str, Any]] = []
    last_set: dict[str, Any] | None = None
    for size in sorted({int(s) for s in sizes_mib if int(s) > 0}):
        variant = variant_for_size(size)
        if variant is None:
            return []
        values = _diff(base_payload, variant, keys)
        if values == last_set:
            continue
        label = label_for_size(size) if label_for_size else f"{max(1, round(size / 1024))}g+"
        tiers.append({"min_vram_mib": 0 if not tiers else size, "label": label, "set": values})
        last_set = values
    if len(tiers) == 1 and not tiers[0]["set"]:
        return []
    for rank, tier in enumerate(tiers):
        tier["rank"] = rank
    return tiers


def attach_routing(
    payload: dict[str, Any],
    *,
    topology: Topology | None,
    tiers: list[dict[str, Any]] | None = None,
    criticality: str | None = None,
) -> dict[str, Any]:
    """Acrescenta ``hw_tiers``/``criticality`` só se o vramd os entende.

    Um vramd antigo passaria campos desconhecidos ao adapter; por isso cada um
    depende da feature anunciada na topologia.
    """
    if topology is None:
        return payload
    if tiers and topology.supports_tiers:
        payload["hw_tiers"] = tiers
    if criticality and criticality not in (CRIT_AUTO, CRIT_NORMAL) and topology.supports_criticality:
        payload["criticality"] = criticality
    return payload
