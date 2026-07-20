"""Builders de request UMS para Text2D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from gamedev_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts


def build_generate_request(
    *,
    prompt: str,
    output: str,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
    guidance: float = 1.0,
    seed: int | None = None,
    model_id: str | None = None,
    gpu_ids: list[int] | str | None = None,
    memory_efficient: bool | None = None,
    quant_preset: str | None = None,
    footprint_key: str | None = None,
    torch_compile: bool = False,
    torch_compile_mode: str | None = None,
    channels_last: bool = False,
    step_cache: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload UMS text2d com peak/load opts."""
    payload: dict[str, Any] = {
        "prompt": str(prompt),
        "output": str(output),
        "width": int(width),
        "height": int(height),
        "steps": int(steps),
        "guidance": float(guidance),
        "seed": seed,
        "torch_compile": bool(torch_compile),
        "channels_last": bool(channels_last),
    }
    if model_id is not None:
        payload["model_id"] = model_id
    if torch_compile_mode is not None:
        payload["torch_compile_mode"] = torch_compile_mode
    if step_cache is not None:
        payload["step_cache"] = step_cache
    if extra:
        payload.update(extra)

    mem = bool(memory_efficient) if memory_efficient is not None else False
    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="text2d",
        memory_efficient=mem,
        quant_preset=quant_preset,
        footprint_key=footprint_key,
    )
