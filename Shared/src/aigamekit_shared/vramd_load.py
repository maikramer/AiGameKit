"""Helpers do mapeamento kwargs vramd → ctors das tools (``vramd_load.py``).

O esqueleto de ``map_vramd_load_kwargs`` (Text2D/Text3D) copia o request e
normaliza o preset SDNQ: o vramd injecta ``sdnq_preset``/``quant_mode`` com
``"none"``/``"null"``/``""`` para "sem quantização" — cada tool mapeia isso
para o seu ctor (``quant_preset=None``, ``sdnq_preset=None``, …). A
normalização do valor é comum; os mapeamentos ficam por-tool.
"""

from __future__ import annotations

from typing import Any

_EMPTY_QUANT = frozenset({"", "none", "null"})


def normalize_quant_preset(value: Any) -> str | None:
    """Normaliza um preset SDNQ: ``None``/``"none"``/``"null"``/``""`` → ``None``.

    Presets reais (``sdnq-int4``, ``sdnq-uint8``, …) passam intactos (trim).

    Args:
        value: Valor cru do request vramd (``sdnq_preset`` / ``quant_mode``).

    Returns:
        Preset efetivo ou ``None`` para "sem quantização".
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    return None if s in _EMPTY_QUANT else str(value).strip()
