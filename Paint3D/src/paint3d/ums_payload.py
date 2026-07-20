"""Builders de request UMS para Paint3D texture (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from gamedev_shared.cli_helpers import with_ums_peak_opts


def build_texture_request(
    *,
    mesh_path: str,
    image_path: str,
    output: str,
    max_num_view: int = 6,
    view_resolution: int = 512,
    render_size: int = 1024,
    texture_size: int = 1024,
    bake_exp: float | None = None,
    verbose: bool = False,
    preserve_origin: bool = True,
    smooth: bool = True,
    smooth_passes: int | None = None,
    upscale: bool = False,
    upscale_factor: float | None = None,
    gpu_ids: list[int] | str | None = None,
    torch_compile: bool = False,
    torch_compile_mode: str | None = None,
    channels_last: bool = False,
    allow_group_offload: bool = False,
    memory_efficient: bool = False,
    sdnq_preset: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload UMS paint3d com peak opts."""
    payload: dict[str, Any] = {
        "mesh_path": str(mesh_path),
        "image_path": str(image_path),
        "output": str(output),
        "max_num_view": int(max_num_view),
        "view_resolution": int(view_resolution),
        "render_size": int(render_size),
        "texture_size": int(texture_size),
        "verbose": bool(verbose),
        "preserve_origin": bool(preserve_origin),
        "smooth": bool(smooth),
        "torch_compile": bool(torch_compile),
        "channels_last": bool(channels_last),
        "allow_group_offload": bool(allow_group_offload),
    }
    if bake_exp is not None:
        payload["bake_exp"] = bake_exp
    if smooth_passes is not None:
        payload["smooth_passes"] = int(smooth_passes)
    if upscale:
        payload["upscale"] = True
    if upscale_factor is not None:
        payload["upscale_factor"] = float(upscale_factor)
    if torch_compile_mode is not None:
        payload["torch_compile_mode"] = torch_compile_mode
    if gpu_ids is not None:
        if isinstance(gpu_ids, str):
            parsed = [int(x.strip()) for x in gpu_ids.split(",") if x.strip()]
        else:
            parsed = [int(x) for x in gpu_ids]
        if parsed:
            payload["gpu_ids"] = parsed
    if extra:
        payload.update(extra)

    mem_eff = bool(memory_efficient)
    preset = sdnq_preset
    if preset is None and mem_eff:
        preset = "sdnq-uint8"
    elif preset in (None, "none", ""):
        preset = "none" if not mem_eff else "sdnq-uint8"

    return with_ums_peak_opts(
        payload,
        backend="paint3d",
        memory_efficient=mem_eff,
        sdnq_preset=None if preset in (None, "none", "") else preset,
    )
