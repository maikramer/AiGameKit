"""Builders de request vramd para Motion3D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any, Literal

from aigamekit_shared.cli_helpers import with_vramd_load_opts, with_vramd_peak_opts
from aigamekit_shared.vramd_payload import build_request_body

from .hardware import estimate_peak_mib, footprint_key_for

ModelVariant = Literal["lite", "full"]


def build_generate_request(
    *,
    prompt: str,
    output: str,
    duration: float | None = None,
    max_frames: int | None = None,
    seed: int | None = None,
    cfg_scale: float | None = None,
    temperature: float | None = None,
    model: ModelVariant | str | None = None,
    sdnq_preset: str | None = None,
    memory_efficient: bool | None = None,
    allow_group_offload: bool | None = None,
    offload_text_encoder: bool | None = None,
    validation_steps: int | None = None,
    half_precision: bool | None = None,
    gpu_ids: list[int] | str | None = None,
    quality: str | None = None,
    category: str | None = None,
    also_npz: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload vramd motion3d com peak/load opts (padrão Text3D)."""
    del half_precision  # Motius-era; HY uses sdnq/offload signals
    merged_extra = dict(extra or {})
    if also_npz is not None:
        merged_extra["also_npz"] = bool(also_npz)
    if offload_text_encoder is not None:
        merged_extra["offload_text_encoder"] = bool(offload_text_encoder)
    if validation_steps is not None:
        merged_extra["validation_steps"] = int(validation_steps)

    variant: ModelVariant = "full" if str(model or "lite").lower() == "full" else "lite"
    merged_extra["model"] = variant

    payload = build_request_body(
        prompt=prompt,
        output=output,
        core={
            "duration": duration,
            "max_frames": max_frames,
            "seed": seed,
            "cfg_scale": cfg_scale,
            "temperature": temperature,
        },
        optional={
            "quality": quality,
            "category": category,
        },
        extra=merged_extra or None,
    )

    mem_eff = memory_efficient
    if mem_eff is None:
        mem_eff = bool(sdnq_preset) and str(sdnq_preset).lower() not in ("none", "", "null")
    preset = None if sdnq_preset in (None, "none", "") else sdnq_preset
    go = allow_group_offload
    if go is None:
        go = bool(mem_eff)

    payload["peak_mib_hint"] = estimate_peak_mib(
        model=variant,
        sdnq_preset=preset,
        memory_efficient=bool(mem_eff),
    )
    payload = with_vramd_load_opts(
        payload,
        gpu_ids=gpu_ids,
        allow_group_offload=go,
        offload=bool(offload_text_encoder) if offload_text_encoder is not None else go,
        sdnq_preset=preset,
        memory_efficient=bool(mem_eff) if mem_eff is not None else None,
    )
    return with_vramd_peak_opts(
        payload,
        backend="motion3d",
        memory_efficient=bool(mem_eff),
        sdnq_preset=preset,
        footprint_key=footprint_key_for(variant),
    )
