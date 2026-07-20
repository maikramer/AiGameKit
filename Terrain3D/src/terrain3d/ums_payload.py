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
    if extra:
        payload.update(extra)

    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="terrain3d",
    )
