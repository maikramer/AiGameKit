"""Nomes canónicos das vistas Hi3D e parsing de ``refine.views``.

Módulo puro (sem imports do GameAssets) para ``profile``/``manifest`` o poderem
usar sem ciclo de importação. A orquestração das vistas vive em
``gameassets.multiview``.
"""

from __future__ import annotations

from typing import Any

#: Ordem canónica: a frontal é a imagem que já alimenta o shape; as restantes
#: são geradas por Text2D com a frontal como referência.
VIEW_ORDER: tuple[str, ...] = ("front", "back", "left", "right", "top", "bottom")
VIEW_SET = frozenset(VIEW_ORDER)


def parse_views(raw: Any, *, where: str = "views") -> tuple[str, ...] | None:
    """Parse de ``views`` (inteiro 1-6 ou lista de nomes) para tuplo ordenado.

    ``None`` devolve ``None`` (comportamento mono-vista). Um inteiro ``N``
    expande para as primeiras ``N`` vistas canónicas — inclui sempre ``front``.
    Uma lista define o conjunto **e** a ordem da cadeia; ``front`` é obrigatória
    porque é a imagem que o shape já usa e a condição mais fiável.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"{where} deve ser inteiro 1-{len(VIEW_ORDER)} ou lista de vistas, não bool")
    if isinstance(raw, int):
        if raw < 1 or raw > len(VIEW_ORDER):
            raise ValueError(f"{where} deve estar entre 1 e {len(VIEW_ORDER)}")
        return VIEW_ORDER[:raw]
    if isinstance(raw, str):
        names = tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    elif isinstance(raw, (list, tuple)):
        names = tuple(str(p).strip().lower() for p in raw)
    else:
        raise ValueError(f"{where} deve ser inteiro 1-{len(VIEW_ORDER)} ou lista de vistas")

    if not names:
        raise ValueError(f"{where} não pode ser vazio")
    unknown = [n for n in names if n not in VIEW_SET]
    if unknown:
        raise ValueError(
            f"{where}: vistas desconhecidas {unknown} (válidas: {', '.join(VIEW_ORDER)})"
        )
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ValueError(f"{where}: vista repetida {name!r}")
        seen.add(name)
    if "front" not in seen:
        raise ValueError(f"{where}: 'front' é obrigatória (é a imagem do shape)")
    return names
