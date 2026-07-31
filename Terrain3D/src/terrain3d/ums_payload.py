"""Builders de request UMS para Terrain3D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from gamedev_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts


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
    """Monta payload UMS terrain3d com peak/load opts."""
    payload: dict[str, Any] = {"output": str(output)}
    if metadata_path is not None:
        payload["metadata_path"] = str(metadata_path)
    if seed is not None:
        payload["seed"] = int(seed)
    if size is not None:
        payload["size"] = int(size)
    if world_size is not None:
        payload["world_size"] = float(world_size)
    if max_height is not None:
        payload["max_height"] = float(max_height)
    if mode is not None:
        payload["mode"] = mode
    if device is not None:
        payload["device"] = device
    if prompt is not None:
        payload["prompt"] = prompt
    if dtype is not None:
        payload["dtype"] = dtype
    if cache_size is not None:
        payload["cache_size"] = cache_size
    if coarse_window is not None:
        payload["coarse_window"] = int(coarse_window)
    if num_inference_steps is not None:
        payload["num_inference_steps"] = int(num_inference_steps)
    if offset_i is not None:
        payload["offset_i"] = int(offset_i)
    if offset_j is not None:
        payload["offset_j"] = int(offset_j)
    if island_falloff is not None:
        payload["island_falloff"] = float(island_falloff)
    if island_noise_scale is not None:
        payload["island_noise_scale"] = float(island_noise_scale)
    if island_noise_freq is not None:
        payload["island_noise_freq"] = float(island_noise_freq)
    if smooth_iterations is not None:
        payload["smooth_iterations"] = int(smooth_iterations)
    if elevation_gamma is not None:
        payload["elevation_gamma"] = float(elevation_gamma)
    if elevation_contrast is not None:
        payload["elevation_contrast"] = float(elevation_contrast)
    if format is not None:
        payload["format"] = format
    if extra:
        payload.update(extra)

    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="terrain3d",
    )
