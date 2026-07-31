"""Builders de request UMS para Paint3D texture (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts
from aigamekit_shared.ums_payload import build_request_body


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
    payload = build_request_body(
        core={
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
        },
        optional={
            "bake_exp": bake_exp,
            "smooth_passes": None if smooth_passes is None else int(smooth_passes),
            "upscale": True if upscale else None,
            "upscale_factor": None if upscale_factor is None else float(upscale_factor),
            "torch_compile_mode": torch_compile_mode,
        },
        extra=extra,
    )

    mem_eff = bool(memory_efficient)
    preset = sdnq_preset
    if preset is None and mem_eff:
        preset = "sdnq-uint8"
    elif preset in (None, "none", ""):
        preset = "none" if not mem_eff else "sdnq-uint8"

    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="paint3d",
        memory_efficient=mem_eff,
        sdnq_preset=None if preset in (None, "none", "") else preset,
    )
