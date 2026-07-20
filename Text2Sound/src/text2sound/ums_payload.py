"""Builders de request UMS para Text2Sound generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from gamedev_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts


def build_generate_request(
    *,
    prompt: str,
    output: str,
    duration: float = 10.0,
    steps: int = 100,
    cfg_scale: float = 7.0,
    seed: int | None = None,
    sigma_min: float | None = None,
    sigma_max: float | None = None,
    sampler_type: str | None = None,
    negative_prompt: str | None = None,
    half_precision: bool | None = None,
    gpu_ids: list[int] | str | None = None,
    quality: str | None = None,
    category: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload UMS text2sound com peak/load opts."""
    payload: dict[str, Any] = {
        "prompt": str(prompt),
        "output": str(output),
        "duration": float(duration),
        "steps": int(steps),
        "cfg_scale": float(cfg_scale),
        "seed": seed,
    }
    if sigma_min is not None:
        payload["sigma_min"] = float(sigma_min)
    if sigma_max is not None:
        payload["sigma_max"] = float(sigma_max)
    if sampler_type is not None:
        payload["sampler_type"] = sampler_type
    if negative_prompt is not None:
        payload["negative_prompt"] = negative_prompt
    if half_precision is not None:
        payload["half_precision"] = bool(half_precision)
    if quality is not None:
        payload["quality"] = quality
    if category is not None:
        payload["category"] = category
    if extra:
        payload.update(extra)

    half = bool(half_precision) if half_precision is not None else False
    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="text2sound",
        memory_efficient=half,
    )
