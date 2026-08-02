"""Builders de request UMS para Text2Icon generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts
from aigamekit_shared.ums_payload import build_request_body


def build_generate_request(
    *,
    prompt: str,
    output: str,
    width: int = 512,
    height: int = 512,
    steps: int = 2,
    guidance: float = 4.5,
    seed: int | None = None,
    transparent: bool = False,
    negative_prompt: str | None = None,
    transformer_quant_preset: str | None = None,
    model_id: str | None = None,
    gpu_ids: list[int] | str | None = None,
    memory_efficient: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload UMS text2icon com peak/load opts."""
    payload = build_request_body(
        prompt=prompt,
        output=output,
        core={
            "width": int(width),
            "height": int(height),
            "steps": int(steps),
            "guidance": float(guidance),
            "seed": seed,
            "transparent": bool(transparent),
        },
        optional={
            "negative_prompt": negative_prompt,
            "transformer_quant_preset": transformer_quant_preset,
            "model_id": model_id,
        },
        extra=extra,
    )

    from aigamekit_shared.ums_load import normalize_quant_preset

    quant = normalize_quant_preset(transformer_quant_preset)
    if quant == "auto":
        # "auto" = deixa o hw_auto decidir — sem sinal de peak no payload.
        quant = None
    mem = bool(memory_efficient) if memory_efficient is not None else bool(quant)
    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="text2icon",
        memory_efficient=mem,
        quant_preset=quant,
    )
