"""Mapeamento CLI ``--quantization`` → preset SDNQ runtime."""

from __future__ import annotations

# CLI Choice: auto | none | int8 | int4  (+ legacy sdnq-* / torchao-* se passados)
_CLI_TO_SDNQ: dict[str, str] = {
    "auto": "sdnq-uint8",
    "int8": "sdnq-int8",
    "int4": "sdnq-int4",
    "8bit": "sdnq-int8",
    "4bit": "sdnq-int4",
}


def resolve_sdnq_preset(
    quantization_mode: str,
    *,
    memory_efficient: bool,
    quantize_dit: bool,
) -> str | None:
    """Decide se/qual preset SDNQ aplicar ao DiT em runtime.

    Args:
        quantization_mode: Valor CLI (``auto``/``none``/``int8``/``int4``) ou
            preset já no formato ``sdnq-*``.
        memory_efficient: Perfil hw-auto / flag mem-eff activo.
        quantize_dit: Se False (``--no-quantize-dit``), nunca aplica SDNQ.

    Returns:
        Nome do preset SDNQ (ex. ``sdnq-uint8``) ou ``None`` se não quantizar.
    """
    if not quantize_dit:
        return None

    mode = (quantization_mode or "auto").strip().lower()
    if mode in ("none", "off", "fp16", "float16"):
        return None

    if mode.startswith("sdnq"):
        return mode

    if mode in _CLI_TO_SDNQ:
        # ``auto`` só quantiza em memory-efficient; int8/int4 são explícitos.
        if mode == "auto" and not memory_efficient:
            return None
        return _CLI_TO_SDNQ[mode]

    return None
