"""Builders de request UMS para Motion3D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts
from aigamekit_shared.ums_payload import build_request_body

from .hardware import estimate_peak_mib


def build_generate_request(
    *,
    prompt: str,
    output: str,
    max_frames: int | None = None,
    seed: int | None = None,
    temperature: float | None = None,
    half_precision: bool | None = None,
    gpu_ids: list[int] | str | None = None,
    quality: str | None = None,
    category: str | None = None,
    also_npz: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload UMS motion3d com peak/load opts."""
    merged_extra = dict(extra or {})
    if also_npz is not None:
        merged_extra["also_npz"] = bool(also_npz)

    payload = build_request_body(
        prompt=prompt,
        output=output,
        core={
            "max_frames": max_frames,
            "seed": seed,
            "temperature": temperature,
        },
        optional={
            "half_precision": None if half_precision is None else bool(half_precision),
            "quality": quality,
            "category": category,
        },
        extra=merged_extra or None,
    )

    half = bool(half_precision) if half_precision is not None else False
    payload["peak_mib_hint"] = estimate_peak_mib(half=half)
    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="motion3d",
        memory_efficient=half,
    )
