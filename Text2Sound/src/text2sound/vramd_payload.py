"""Builders de request vramd para Text2Sound generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_vramd_load_opts, with_vramd_peak_opts
from aigamekit_shared.vramd_payload import build_request_body


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
    """Monta payload vramd text2sound com peak/load opts."""
    payload = build_request_body(
        prompt=prompt,
        output=output,
        core={
            "duration": float(duration),
            "steps": int(steps),
            "cfg_scale": float(cfg_scale),
            "seed": seed,
        },
        optional={
            "sigma_min": None if sigma_min is None else float(sigma_min),
            "sigma_max": None if sigma_max is None else float(sigma_max),
            "sampler_type": sampler_type,
            "negative_prompt": negative_prompt,
            "half_precision": None if half_precision is None else bool(half_precision),
            "quality": quality,
            "category": category,
        },
        extra=extra,
    )

    half = bool(half_precision) if half_precision is not None else False
    return with_vramd_peak_opts(
        with_vramd_load_opts(payload, gpu_ids=gpu_ids),
        backend="text2sound",
        memory_efficient=half,
    )
