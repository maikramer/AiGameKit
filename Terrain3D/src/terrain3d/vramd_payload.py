"""Builders de request vramd para Terrain3D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_vramd_load_opts, with_vramd_peak_opts
from aigamekit_shared.vramd_payload import build_request_body


def build_generate_request(
    *,
    output: str,
    metadata_path: str | None = None,
    seed: int | None = None,
    size: int | None = None,
    world_size: float | None = None,
    max_height: float | None = None,
    mode: str | None = None,
    device: str | None = None,
    prompt: str | None = None,
    dtype: str | None = None,
    cache_size: str | None = None,
    coarse_window: int | None = None,
    num_inference_steps: int | None = None,
    offset_i: int | None = None,
    offset_j: int | None = None,
    island_falloff: float | None = None,
    island_noise_scale: float | None = None,
    island_noise_freq: float | None = None,
    smooth_iterations: int | None = None,
    elevation_gamma: float | None = None,
    elevation_contrast: float | None = None,
    format: str | None = None,
    gpu_ids: list[int] | str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload vramd terrain3d com peak/load opts."""
    payload = build_request_body(
        output=output,
        optional={
            "metadata_path": None if metadata_path is None else str(metadata_path),
            "seed": seed,
            "size": None if size is None else int(size),
            "world_size": None if world_size is None else float(world_size),
            "max_height": None if max_height is None else float(max_height),
            "mode": mode,
            "device": device,
            "prompt": prompt,
            "dtype": dtype,
            "cache_size": cache_size,
            "coarse_window": None if coarse_window is None else int(coarse_window),
            "num_inference_steps": None if num_inference_steps is None else int(num_inference_steps),
            "offset_i": None if offset_i is None else int(offset_i),
            "offset_j": None if offset_j is None else int(offset_j),
            "island_falloff": None if island_falloff is None else float(island_falloff),
            "island_noise_scale": None if island_noise_scale is None else float(island_noise_scale),
            "island_noise_freq": None if island_noise_freq is None else float(island_noise_freq),
            "smooth_iterations": None if smooth_iterations is None else int(smooth_iterations),
            "elevation_gamma": None if elevation_gamma is None else float(elevation_gamma),
            "elevation_contrast": None if elevation_contrast is None else float(elevation_contrast),
            "format": format,
        },
        extra=extra,
    )

    return with_vramd_peak_opts(
        with_vramd_load_opts(payload, gpu_ids=gpu_ids),
        backend="terrain3d",
    )
