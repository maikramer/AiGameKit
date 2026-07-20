"""Builders de request UMS para Skymap2D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from gamedev_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts


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
    payload: dict[str, Any] = {
        "prompt": str(prompt),
        "output": str(output),
        "width": int(width),
        "height": int(height),
        "steps": int(steps),
        "guidance": float(guidance),
        "seed": seed,
    }
    if negative_prompt is not None:
        payload["negative_prompt"] = negative_prompt
    if cfg_scale is not None:
        payload["cfg_scale"] = float(cfg_scale)
    if lora_strength is not None:
        payload["lora_strength"] = float(lora_strength)
    if preset is not None:
        payload["preset"] = preset
    if exr_scale is not None:
        payload["exr_scale"] = float(exr_scale)
    if extra:
        payload.update(extra)

    mem = bool(memory_efficient) if memory_efficient is not None else False
    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="skymap2d",
        memory_efficient=mem,
    )
