"""Mapeamento kwargs vramd → ``HunyuanTextTo3DGenerator`` (partilhado adapter/CLI)."""

from __future__ import annotations

from typing import Any

# Keys que o BackendManager pode injectar no load mas o ctor Omni não aceita.
_UMS_ONLY_LOAD_KEYS = frozenset(
    {
        "quant_mode",
        "memory_efficient",
        "torch_compile",
        "torch_compile_mode",
        "hunyuan_subfolder",  # legado 2.1
    }
)


def map_vramd_load_kwargs(raw: dict[str, Any]) -> dict[str, Any]:
    """Normaliza kwargs vramd → ``HunyuanTextTo3DGenerator``.

    Offload/SDNQ defaults só quando o request traz sinais de peak (hw_auto /
    ``with_vramd_peak_opts``). Sem re-decidir VRAM no adapter.

    Args:
        raw: kwargs vindos do BackendManager / request vramd.

    Returns:
        Dict pronto para ``HunyuanTextTo3DGenerator(**kwargs)``.
    """
    kwargs = dict(raw)

    if "torch_compile" in kwargs:
        kwargs.setdefault("compile_models", bool(kwargs.pop("torch_compile")))
    else:
        kwargs.pop("torch_compile", None)
    if "torch_compile_mode" in kwargs:
        kwargs.setdefault("compile_mode", str(kwargs.pop("torch_compile_mode") or "default"))
    else:
        kwargs.pop("torch_compile_mode", None)

    from aigamekit_shared.vramd_load import normalize_quant_preset

    if kwargs.get("sdnq_preset") is None and kwargs.get("quant_mode") is not None:
        kwargs["sdnq_preset"] = normalize_quant_preset(kwargs["quant_mode"]) or ""

    mem_eff = kwargs.pop("memory_efficient", None)
    if mem_eff is True:
        kwargs.setdefault("offload", True)
        kwargs.setdefault("allow_group_offload", True)
        if not kwargs.get("sdnq_preset"):
            kwargs["sdnq_preset"] = "sdnq-int4"
        kwargs.setdefault("volume_decoder", "flashvdm")

    for key in _UMS_ONLY_LOAD_KEYS:
        kwargs.pop(key, None)
    kwargs.pop("hunyuan_subfolder", None)

    kwargs.setdefault("verbose", False)
    return kwargs
