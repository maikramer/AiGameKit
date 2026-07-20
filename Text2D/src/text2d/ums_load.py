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


def map_ums_load_kwargs(raw: dict[str, Any]) -> dict[str, Any]:
    """Normaliza kwargs UMS → ``KleinFluxGenerator``.

    Peak/offload vêm do request (CLI hw_auto / peak opts) — sem re-decidir VRAM
    localmente no adapter.

    Args:
        raw: kwargs vindos do BackendManager / request UMS.

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
    kwargs["memory_efficient"] = bool(mem_eff) if mem_eff is not None else False

    # torch.compile default OFF: compile frio (minutos) não compensa fora de
    # batches longos — request explícito (batch/CLI) ganha sempre.
    kwargs.setdefault("torch_compile", False)
    kwargs.setdefault("torch_compile_mode", "default")
    kwargs.setdefault("channels_last", True)
    kwargs.setdefault("verbose", False)

    for key in _UMS_ONLY_LOAD_KEYS:
        kwargs.pop(key, None)

    return kwargs
