"""Mapeamento kwargs UMS → ``KleinFluxGenerator`` (partilhado adapter/CLI)."""

from __future__ import annotations

from typing import Any

# Keys que o BackendManager pode injectar mas o ctor Text2D não aceita.
_UMS_ONLY_LOAD_KEYS = frozenset(
    {
        "quant_mode",
        "sdnq_preset",  # admit UMS; ctor usa ``quant_preset``
        "offload",
        "allow_group_offload",
    }
)


def map_ums_load_kwargs(raw: dict[str, Any], *, low_vram: bool) -> dict[str, Any]:
    """Normaliza kwargs UMS → ``KleinFluxGenerator``.

    Args:
        raw: kwargs vindos do BackendManager / request UMS.
        low_vram: True se GPU abaixo do limiar (~7 GB) — activa mem_eff default.

    Returns:
        Dict pronto para ``KleinFluxGenerator(**kwargs)``.
    """
    kwargs = dict(raw)

    # Admit UMS usa sdnq_preset; ctor Text2D usa quant_preset.
    if kwargs.get("quant_preset") is None and kwargs.get("sdnq_preset") is not None:
        qm = str(kwargs["sdnq_preset"]).strip().lower()
        kwargs["quant_preset"] = None if qm in ("none", "null", "") else kwargs["sdnq_preset"]
    elif kwargs.get("quant_preset") is not None:
        qm = str(kwargs["quant_preset"]).strip().lower()
        if qm in ("none", "null", ""):
            kwargs["quant_preset"] = None

    mem_eff = kwargs.get("memory_efficient")
    if mem_eff is None:
        mem_eff = low_vram
    kwargs["memory_efficient"] = bool(mem_eff)

    # Defaults UMS hot-path (amortiza cold compile) — request explícito ganha.
    kwargs.setdefault("torch_compile", True)
    kwargs.setdefault("torch_compile_mode", "default")
    kwargs.setdefault("channels_last", True)
    kwargs.setdefault("verbose", False)

    for key in _UMS_ONLY_LOAD_KEYS:
        kwargs.pop(key, None)

    return kwargs
