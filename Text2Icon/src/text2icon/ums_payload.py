"""Builders de request UMS para Text2Icon generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from gamedev_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts


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
    payload: dict[str, Any] = {
        "prompt": str(prompt),
        "output": str(output),
        "width": int(width),
        "height": int(height),
        "steps": int(steps),
        "guidance": float(guidance),
        "seed": seed,
        "transparent": bool(transparent),
    }
    if negative_prompt is not None:
        payload["negative_prompt"] = negative_prompt
    if transformer_quant_preset is not None:
        payload["transformer_quant_preset"] = transformer_quant_preset
    if model_id is not None:
        payload["model_id"] = model_id
    if extra:
        payload.update(extra)

    quant = transformer_quant_preset if transformer_quant_preset not in (None, "", "auto") else None
    mem = bool(memory_efficient) if memory_efficient is not None else bool(quant)
    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="text2icon",
        memory_efficient=mem,
        quant_preset=quant,
    )
