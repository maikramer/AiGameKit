"""Builders de request UMS para Skymap2D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts
from aigamekit_shared.ums_payload import build_request_body


def build_generate_request(
    *,
    prompt: str,
    output: str,
    width: int = 2048,
    height: int = 1024,
    steps: int = 28,
    guidance: float = 3.5,
    seed: int | None = None,
    negative_prompt: str | None = None,
    cfg_scale: float | None = None,
    lora_strength: float | None = None,
    preset: str | None = None,
    exr_scale: float | None = None,
    gpu_ids: list[int] | str | None = None,
    memory_efficient: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload UMS skymap2d com peak/load opts."""
    payload = build_request_body(
        prompt=prompt,
        output=output,
        core={
            "width": int(width),
            "height": int(height),
            "steps": int(steps),
            "guidance": float(guidance),
            "seed": seed,
        },
        optional={
            "negative_prompt": negative_prompt,
            "cfg_scale": None if cfg_scale is None else float(cfg_scale),
            "lora_strength": None if lora_strength is None else float(lora_strength),
            "preset": preset,
            "exr_scale": None if exr_scale is None else float(exr_scale),
        },
        extra=extra,
    )

    mem = bool(memory_efficient) if memory_efficient is not None else False
    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="skymap2d",
        memory_efficient=mem,
    )
