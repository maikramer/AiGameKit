"""Builders de request UMS para Part3D decompose (CLI + GameAssets).

Extraído do dict inline que vivia em ``cli.py`` — mesma montagem
(``build_request_body`` + wrap peak/load) das outras 7 tools.
"""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts
from aigamekit_shared.ums_payload import build_request_body


def build_decompose_request(
    *,
    mesh_path: str,
    output: str,
    output_segmented: str,
    seed: int | None = None,
    segment_only: bool = False,
    postprocess: bool = True,
    threshold: float = 0.99,
    refine_labels: bool = True,
    bbox_merge_iou: float = 0.7,
    mask_nms_iou: float = 0.9,
    secondary_mask_iou: float = 0.5,
    min_cluster_support: int | None = None,
    min_predicted_iou: float | None = None,
    prompt_batch_size: int = 8,
    multi_head: bool = False,
    consensus: bool = True,
    consensus_vote: float = 0.5,
    segment_mode: str = "auto",
    detail_levels: int = 0,
    segmentation_proxy: str | None = None,
    octree_resolution: int | None = None,
    steps: int | None = None,
    num_chunks: int | None = None,
    point_num: int | None = None,
    prompt_num: int | None = None,
    mc_algo: str | None = None,
    gpu_ids: list[int] | str | None = None,
    memory_efficient: bool = False,
    sdnq_preset: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload UMS part3d com peak/load opts.

    Os valores resolvidos vêm do CLI (incluindo hw_auto); aqui só se monta o
    dict — ``bbox_merge_iou`` etc. entram já com os defaults aplicados pelo
    caller. ``steps`` mapeia para ``num_inference_steps`` (chave do backend).
    """
    payload = build_request_body(
        core={
            "mesh_path": str(mesh_path),
            "output": str(output),
            "output_segmented": str(output_segmented),
            "seed": seed,
            "segment_only": bool(segment_only),
            "postprocess": bool(postprocess),
            "threshold": float(threshold),
            "refine_labels": bool(refine_labels),
            "bbox_merge_iou": float(bbox_merge_iou),
            "mask_nms_iou": float(mask_nms_iou),
            "secondary_mask_iou": float(secondary_mask_iou),
            "min_cluster_support": min_cluster_support,
            "min_predicted_iou": min_predicted_iou,
            "prompt_batch_size": int(prompt_batch_size),
            "multi_head": bool(multi_head),
            "consensus": bool(consensus),
            "consensus_vote": float(consensus_vote),
            "segment_mode": segment_mode,
            "detail_levels": int(detail_levels),
        },
        optional={
            "segmentation_proxy": None if segmentation_proxy is None else str(segmentation_proxy),
            "octree_resolution": octree_resolution,
            "num_inference_steps": None if steps is None else int(steps),
            "num_chunks": num_chunks,
            "point_num": point_num,
            "prompt_num": prompt_num,
            "mc_algo": mc_algo,
        },
        extra=extra,
    )

    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="part3d",
        memory_efficient=bool(memory_efficient),
        sdnq_preset=sdnq_preset,
    )
