"""Builders de request UMS para Text3D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from gamedev_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts


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
    payload: dict[str, Any] = {
        "from_image": str(from_image),
        "output": str(output),
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
    }
    if steps is not None:
        payload["steps"] = int(steps)
    if octree_resolution is not None:
        payload["octree_resolution"] = int(octree_resolution)
    if num_chunks is not None:
        payload["num_chunks"] = int(num_chunks)
    if seed_fingerprint is not None:
        # Override de re-roll (GameAssets manifest seed:) — entra no sidecar
        # Omni; distinto do RNG ``seed``.
        payload["seed_fingerprint"] = int(seed_fingerprint)
    if volume_decoder is not None:
        payload["volume_decoder"] = volume_decoder
    if mc_algo is not None:
        payload["mc_algo"] = mc_algo
    if torch_compile_mode is not None:
        payload["torch_compile_mode"] = torch_compile_mode
    if category is not None:
        payload["category"] = category
    if quality is not None:
        payload["quality"] = quality
    if control_type is not None:
        payload["control_type"] = control_type
    if pose_preset is not None:
        payload["pose_preset"] = pose_preset
    if bbox_preset is not None:
        payload["bbox_preset"] = bbox_preset
    if size_m is not None:
        payload["size_m"] = size_m
    if bbox is not None:
        payload["bbox"] = bbox
    if pose_file:
        payload["pose_file"] = str(pose_file)
    if point_cloud:
        payload["point_cloud"] = str(point_cloud)
    if voxel_mesh:
        payload["voxel_mesh"] = str(voxel_mesh)
    if extra:
        payload.update(extra)

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
