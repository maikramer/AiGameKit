"""Builders de request vramd para Text3D generate (CLI + GameAssets).

Vive no Shared (e não só em ``text3d.vramd_payload``) para o coordenador de
waves do GameAssets montar os payloads sem importar o pacote Text3D — o
``text3d/__init__`` puxa o gerador (torch) e não existe no venv do GameAssets.
Sem isto a wave de shape devolvia lista vazia e o resume caía no
``generate-batch`` sequencial: uma GPU remota de cada vez.
O Text3D re-exporta esta API no seu ``vramd_payload``.
"""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_vramd_load_opts, with_vramd_peak_opts
from aigamekit_shared.hardware import GIB
from aigamekit_shared.vramd_payload import build_request_body

# O mesmo corte de ``text3d.hardware.profile_from_specs``: Omni fp16 pede ~10 GiB.
_FP16_MIN_GIB = 10.0


def quant_for_mib(size_mib: int) -> tuple[str | None, bool]:
    """``(sdnq_preset, memory_efficient)`` para uma GPU de ``size_mib``.

    Espelha o ramo de quantização do hw-auto do Text3D, para os ``hw_tiers``
    da wave existirem mesmo quando o pacote ``text3d`` não está instalado no
    processo que submete (GameAssets). Uma de 12 GiB fica em fp16; abaixo de
    10 GiB, ``sdnq-int4``.
    """
    gib = (int(size_mib) * 1024 * 1024) / GIB
    if gib >= _FP16_MIN_GIB:
        return None, False
    return "sdnq-int4", True


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
    preset: str | None = None,
    bbox_tune: bool = True,
    control_type: str | None = None,
    pose_preset: str | None = None,
    bbox_preset: str | None = None,
    size_m: Any = None,
    height_m: float | None = None,
    footprint_m: float | None = None,
    bbox: Any = None,
    pose_file: str | None = None,
    point_cloud: str | None = None,
    voxel_mesh: str | None = None,
    gpu_ids: list[int] | str | None = None,
    sdnq_preset: str | None = None,
    memory_efficient: bool | None = None,
    dino_image_size: int | None = None,
    image_processor_size: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload vramd text3d com peak/load opts.

    ``bbox_tune=True`` deixa o adapter/vramd afinar steps/octree/chunks.
    ``octree_resolution``/``steps``/``num_chunks`` só entram no payload quando
    explícitos — se presentes, ``vramd_generate`` trata como override e não
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
            "auto_num_chunks": True,
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
            "preset": preset,
            "control_type": control_type,
            "pose_preset": pose_preset,
            "bbox_preset": bbox_preset,
            "size_m": size_m,
            "height_m": None if height_m is None else float(height_m),
            "footprint_m": None if footprint_m is None else float(footprint_m),
            "bbox": bbox,
            "pose_file": None if not pose_file else str(pose_file),
            "point_cloud": None if not point_cloud else str(point_cloud),
            "voxel_mesh": None if not voxel_mesh else str(voxel_mesh),
            # Condicionamento avançado (default None = config do modelo: 512→518).
            "dino_image_size": None if dino_image_size is None else int(dino_image_size),
            "image_processor_size": None if image_processor_size is None else int(image_processor_size),
        },
        extra=extra,
    )

    mem_eff = memory_efficient
    if mem_eff is None:
        # allow_group_offload ≠ memory_efficient (load strategy vs peak/SDNQ).
        # Inferir só de offload/sdnq; callers (CLI/hw_auto/vramd_batch) devem
        # passar mem_eff explícito quando hw_auto resolveu o perfil.
        mem_eff = bool(offload) or (sdnq_preset not in (None, "none", ""))
    sdnq_out = None if sdnq_preset in (None, "none", "") else sdnq_preset

    return with_vramd_peak_opts(
        with_vramd_load_opts(
            payload,
            gpu_ids=gpu_ids,
            volume_decoder=volume_decoder,
            allow_group_offload=allow_group_offload,
            channels_last=channels_last,
            offload=offload,
        ),
        backend="text3d",
        memory_efficient=bool(mem_eff),
        sdnq_preset=sdnq_out,
    )
