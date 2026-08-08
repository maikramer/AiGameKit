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


def prefer_fp8_preset(preset: str | None, *, fp8_supported: bool | None = None) -> str | None:
    """Troca ``sdnq-uint8``/``sdnq-int8`` por ``sdnq-fp8`` quando o hardware suporta.

    O fp8 (e4m3fn) ocupa o mesmo que o uint8/int8 (fator 0.55) mas com melhor
    qualidade — é a escolha certa em Ada/Hopper/Blackwell. Sem suporte, o
    preset volta intacto.

    Args:
        preset: Preset SDNQ (ex.: ``sdnq-uint8``) ou ``None``.
        fp8_supported: Cache do resultado de :func:`aigamekit_shared.gpu.supports_fp8`
            (evita re-probes em loops); ``None`` = sondar.

    Returns:
        ``sdnq-fp8`` quando aplicável; senão o preset original.
    """
    if not preset:
        return preset
    s = str(preset).strip().lower()
    if s not in ("sdnq-uint8", "sdnq-int8", "uint8", "int8"):
        return preset
    if fp8_supported is None:
        from .gpu import supports_fp8

        fp8_supported = supports_fp8()
    return "sdnq-fp8" if fp8_supported else preset
