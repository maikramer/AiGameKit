"""Builders de request UMS para Text3D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from gamedev_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts
from gamedev_shared.ums_payload import build_request_body


def build_generate_request(
    *,
    from_image: str,
    output: str,
    steps: int | None = None,
    guidance: float = 4.5,
    octree_resolution: int | None = None,
    num_chunks: int | None = None,
    seed: int | None = None,
    seed_fingerprint: int | None = None,
    mc_level: float | str = "auto",
    bounds_mode: str = "auto",
    origin_mode: str = "feet",
    topology_fix: bool = True,
    volume_decoder: str | None = None,
    mc_algo: str | None = None,
    torch_compile: bool = False,
    torch_compile_mode: str | None = None,
    channels_last: bool = False,
    allow_group_offload: bool = True,
    fp8_layerwise: bool = False,
    sdnq_quantized_matmul: bool = False,
    sage_attention: bool = False,
    offload: bool = False,
    verbose: bool = False,
    category: str | None = None,
    quality: str | None = None,
    bbox_tune: bool = True,
    control_type: str | None = None,
    pose_preset: str | None = None,
    bbox_preset: str | None = None,
    size_m: Any = None,
    bbox: Any = None,
    pose_file: str | None = None,
    point_cloud: str | None = None,
    voxel_mesh: str | None = None,
    gpu_ids: list[int] | str | None = None,
    sdnq_preset: str | None = None,
    memory_efficient: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload UMS text3d com peak/load opts.

    ``bbox_tune=True`` deixa o adapter/UMS afinar steps/octree/chunks.
    ``octree_resolution``/``steps``/``num_chunks`` só entram no payload quando
    explícitos — se presentes, ``ums_generate`` trata como override e **não**
    sobe o eixo no size-tune (era o bug que fixava 384 e matava buildings).
    """
    payload = build_request_body(
        output=output,
        core={
            "from_image": str(from_image),
            "guidance": float(guidance),
            "seed": seed,
            "mc_level": mc_level,
            "bounds_mode": bounds_mode,
            "auto_num_chunks": False,
            "origin_mode": origin_mode,
            "topology_fix": bool(topology_fix),
            "torch_compile": bool(torch_compile),
            "channels_last": bool(channels_last),
            "allow_group_offload": bool(allow_group_offload),
            "fp8_layerwise": bool(fp8_layerwise),
            "sdnq_quantized_matmul": bool(sdnq_quantized_matmul),
            "sage_attention": bool(sage_attention),
            "offload": bool(offload),
            "verbose": bool(verbose),
            "bbox_tune": bool(bbox_tune),
        },
        optional={
            "steps": None if steps is None else int(steps),
            "octree_resolution": None if octree_resolution is None else int(octree_resolution),
            "num_chunks": None if num_chunks is None else int(num_chunks),
            # Override de re-roll (GameAssets manifest seed:) — entra no sidecar
            # Omni; distinto do RNG ``seed``.
            "seed_fingerprint": None if seed_fingerprint is None else int(seed_fingerprint),
            "volume_decoder": volume_decoder,
            "mc_algo": mc_algo,
            "torch_compile_mode": torch_compile_mode,
            "category": category,
            "quality": quality,
            "control_type": control_type,
            "pose_preset": pose_preset,
            "bbox_preset": bbox_preset,
            "size_m": size_m,
            "bbox": bbox,
            "pose_file": None if not pose_file else str(pose_file),
            "point_cloud": None if not point_cloud else str(point_cloud),
            "voxel_mesh": None if not voxel_mesh else str(voxel_mesh),
        },
        extra=extra,
    )

    mem_eff = memory_efficient
    if mem_eff is None:
        # allow_group_offload ≠ memory_efficient (load strategy vs peak/SDNQ).
        # Inferir só de offload/sdnq; callers (CLI/hw_auto/ums_batch) devem
        # passar mem_eff explícito quando hw_auto resolveu o perfil.
        mem_eff = bool(offload) or (sdnq_preset not in (None, "none", ""))
    preset = None if sdnq_preset in (None, "none", "") else sdnq_preset

    return with_ums_peak_opts(
        with_ums_load_opts(
            payload,
            gpu_ids=gpu_ids,
            volume_decoder=volume_decoder,
            allow_group_offload=allow_group_offload,
            channels_last=channels_last,
            offload=offload,
        ),
        backend="text3d",
        memory_efficient=bool(mem_eff),
        sdnq_preset=preset,
    )
