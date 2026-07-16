"""Consensus aggregation for P3-SAM multi-head masks.

Upstream ``mesh_sam`` picks one head per prompt (argmax IoU) and keeps only the
NMS cluster representative. That discards alternative granularities and member
votes. These helpers:

1. Keep multiple heads per prompt when scores are close enough.
2. Cluster with best-fit NMS (highest IoU match, not first-fit).
3. Fuse cluster members into an IoU-weighted consensus mask.
4. Assign points by soft vote with smallest-area tie-break.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np


def collect_prompt_heads(
    masks: np.ndarray,
    ious: np.ndarray,
    prompt_index: int,
    *,
    multi_head: bool = True,
    min_score: float = 0.5,
    score_ratio: float = 0.85,
    dedupe_iou: float = 0.95,
) -> list[tuple[np.ndarray, float, int]]:
    """Select heads for one prompt.

    Args:
        masks: ``[N, K, 3]`` bool/float masks for a prompt batch row ``j``.
        ious: ``[K, 3]`` predicted IoU (caller passes the row ``j`` as ``[3]``
            or the full batch — see ``collect_batch_heads``).
        prompt_index: Global prompt id for support counting.
        multi_head: If False, keep only argmax head (upstream behaviour).
        min_score: Absolute IoU gate for extra heads.
        score_ratio: Keep head if ``iou >= score_ratio * best``.
        dedupe_iou: Drop near-duplicate heads within the same prompt.

    Returns:
        List of ``(mask[N], iou, prompt_index)``.
    """
    heads = np.asarray(masks)
    scores = np.asarray(ious, dtype=np.float64).reshape(-1)
    if heads.ndim != 2 or heads.shape[1] != scores.shape[0]:
        raise ValueError("masks must be [N, H] matching ious length H")
    n_heads = scores.shape[0]
    best = int(np.argmax(scores))
    if not multi_head:
        return [(np.asarray(heads[:, best]).astype(bool), float(scores[best]), prompt_index)]

    kept: list[tuple[np.ndarray, float, int]] = []
    order = np.argsort(-scores)
    for h in order:
        score = float(scores[h])
        if h != best and score < max(float(min_score), float(score_ratio) * float(scores[best])):
            continue
        candidate = np.asarray(heads[:, h]).astype(bool)
        if any(_mask_iou(candidate, prev) >= dedupe_iou for prev, _, _ in kept):
            continue
        kept.append((candidate, score, prompt_index))
    return kept


def collect_batch_heads(
    pred_mask: np.ndarray,
    pred_iou: np.ndarray,
    base_prompt_index: int,
    *,
    multi_head: bool = True,
    min_score: float = 0.5,
    score_ratio: float = 0.85,
    dedupe_iou: float = 0.95,
) -> tuple[list[np.ndarray], list[float], list[int]]:
    """Collect heads for a batch of prompts.

    Args:
        pred_mask: ``[N, K, 3]``
        pred_iou: ``[K, 3]``
        base_prompt_index: Index of the first prompt in the batch.
    """
    masks_out: list[np.ndarray] = []
    ious_out: list[float] = []
    prompt_ids: list[int] = []
    k = int(pred_iou.shape[0])
    for j in range(k):
        selected = collect_prompt_heads(
            pred_mask[:, j, :],
            pred_iou[j],
            base_prompt_index + j,
            multi_head=multi_head,
            min_score=min_score,
            score_ratio=score_ratio,
            dedupe_iou=dedupe_iou,
        )
        for mask, iou, pid in selected:
            masks_out.append(mask)
            ious_out.append(iou)
            prompt_ids.append(pid)
    return masks_out, ious_out, prompt_ids


def _mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    a_b = np.asarray(a, dtype=bool)
    b_b = np.asarray(b, dtype=bool)
    inter = float(np.logical_and(a_b, b_b).sum())
    union = float(np.logical_or(a_b, b_b).sum())
    if union <= 0.0:
        return 0.0
    return inter / union


def pairwise_mask_iou(masks: np.ndarray) -> np.ndarray:
    """Vectorized pairwise IoU for stacked bool/float masks ``[M, N]``."""
    m = np.asarray(masks, dtype=np.float32)
    inter = m @ m.T
    areas = m.sum(axis=1)
    union = areas[:, None] + areas[None, :] - inter
    return inter / np.maximum(union, 1e-9)


def cluster_masks_bestfit(
    masks: list[np.ndarray] | np.ndarray,
    *,
    nms_iou: float = 0.9,
) -> dict[int, list[int]]:
    """Greedy best-fit NMS: assign each mask to the highest-IoU cluster ≥ thresh."""
    stacked = np.stack([np.asarray(m, dtype=np.float32) for m in masks], axis=0)
    iou = pairwise_mask_iou(stacked)
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(stacked.shape[0]):
        best_j = -1
        best_iou = float(nms_iou)
        for j in clusters:
            score = float(iou[i, j])
            if score > best_iou:
                best_iou = score
                best_j = j
        if best_j >= 0:
            clusters[best_j].append(i)
        else:
            clusters[i].append(i)
    return dict(clusters)


def distinct_prompt_support(member_indices: list[int], prompt_ids: list[int] | np.ndarray) -> int:
    """Count unique prompt ids among cluster members (avoids multi-head inflation)."""
    ids = np.asarray(prompt_ids)
    return int(len({int(ids[i]) for i in member_indices}))


def fuse_cluster_mask(
    masks: list[np.ndarray] | np.ndarray,
    ious: list[float] | np.ndarray,
    member_indices: list[int],
    *,
    vote: float = 0.5,
) -> np.ndarray:
    """IoU-weighted soft vote → binary consensus mask."""
    if not member_indices:
        raise ValueError("empty cluster")
    stacked = np.stack([np.asarray(masks[i], dtype=np.float32) for i in member_indices], axis=0)
    weights = np.asarray([float(ious[i]) for i in member_indices], dtype=np.float32)
    weights = np.maximum(weights, 1e-6)
    weights = weights / weights.sum()
    soft = weights @ stacked
    return soft >= float(vote)


def consensus_cluster_and_fuse(
    masks: list[np.ndarray],
    ious: list[float],
    prompt_ids: list[int],
    *,
    nms_iou: float = 0.9,
    vote: float = 0.5,
    min_cluster_support: int = 3,
    min_predicted_iou: float = 1.0,
) -> tuple[list[np.ndarray], list[float], list[list[int]], list[int]]:
    """Best-fit NMS + consensus fuse + support filter.

    Returns:
        fused_masks, fused_ious (rep IoU), member_lists, rep_indices
    """
    if not masks:
        return [], [], [], []
    clusters = cluster_masks_bestfit(masks, nms_iou=nms_iou)
    fused: list[np.ndarray] = []
    fused_ious: list[float] = []
    members_out: list[list[int]] = []
    reps: list[int] = []
    for rep, members in clusters.items():
        support = distinct_prompt_support(members, prompt_ids)
        rep_iou = float(ious[rep])
        if support < min_cluster_support and rep_iou < min_predicted_iou:
            continue
        fused.append(fuse_cluster_mask(masks, ious, members, vote=vote))
        fused_ious.append(rep_iou)
        members_out.append(list(members))
        reps.append(int(rep))
    return fused, fused_ious, members_out, reps


def assign_points_by_vote(
    masks: list[np.ndarray] | np.ndarray,
    ious: list[float] | np.ndarray | None = None,
    *,
    prefer_small: bool = True,
) -> np.ndarray:
    """Per-point soft assignment; ties broken toward smaller masks.

    Returns:
        ``result_mask[N]`` with cluster index into ``masks``, or ``-1``.
    """
    if len(masks) == 0:
        raise ValueError("no masks")
    stacked = np.stack([np.asarray(m, dtype=np.float32) for m in masks], axis=0)  # [C, N]
    if ious is None:
        weights = np.ones(stacked.shape[0], dtype=np.float32)
    else:
        weights = np.asarray(ious, dtype=np.float32)
        weights = np.maximum(weights, 1e-6)
    # mean confidence per cluster point: weight * mask
    votes = (weights[:, None] * stacked).T  # [N, C]
    areas = stacked.sum(axis=1)
    if prefer_small:
        # Prefer smaller parts on ties: subtract tiny epsilon * log(area).
        votes = votes - 1e-6 * np.log1p(areas)[None, :]
    best = np.argmax(votes, axis=1)
    present = stacked.T[np.arange(stacked.shape[1]), best] > 0.5
    result = np.full(stacked.shape[1], -1, dtype=np.int64)
    result[present] = best[present]
    return result


def build_sorted_mask_pool(
    masks: list[np.ndarray],
    ious: list[float],
    prompt_ids: list[int] | None = None,
) -> tuple[list[np.ndarray], list[float], list[int]]:
    """Sort masks by predicted IoU descending (upstream convention)."""
    if not masks:
        return [], [], []
    order = np.argsort(-np.asarray(ious, dtype=np.float64))
    sorted_masks = [masks[int(i)] for i in order]
    sorted_ious = [float(ious[int(i)]) for i in order]
    if prompt_ids is None:
        sorted_pids = [int(i) for i in order]
    else:
        sorted_pids = [int(prompt_ids[int(i)]) for i in order]
    return sorted_masks, sorted_ious, sorted_pids


def run_mask_consensus(
    masks: list[np.ndarray],
    ious: list[float],
    prompt_ids: list[int],
    *,
    nms_iou: float = 0.9,
    vote: float = 0.5,
    min_cluster_support: int = 3,
    min_predicted_iou: float = 1.0,
    prefer_small: bool = True,
) -> dict[str, Any]:
    """Full consensus path used by the patched ``mesh_sam``.

    Returns dict with ``fused_masks``, ``fused_ious``, ``result_mask``,
    ``clusters`` (rep → members in sorted pool), ``prompt_ids_sorted``.
    """
    sorted_masks, sorted_ious, sorted_pids = build_sorted_mask_pool(masks, ious, prompt_ids)
    fused, fused_ious, members, reps = consensus_cluster_and_fuse(
        sorted_masks,
        sorted_ious,
        sorted_pids,
        nms_iou=nms_iou,
        vote=vote,
        min_cluster_support=min_cluster_support,
        min_predicted_iou=min_predicted_iou,
    )
    if not fused:
        n = int(np.asarray(masks[0]).shape[0]) if masks else 0
        return {
            "fused_masks": [],
            "fused_ious": [],
            "result_mask": np.full(n, -1, dtype=np.int64),
            "clusters": {},
            "prompt_ids_sorted": sorted_pids,
            "reps": [],
        }
    # Area-descending order for downstream compatibility (smaller overwrites later).
    areas = [int(np.sum(m)) for m in fused]
    order = np.argsort(-np.asarray(areas))
    fused_sorted = [fused[int(i)] for i in order]
    ious_sorted = [fused_ious[int(i)] for i in order]
    reps_sorted = [reps[int(i)] for i in order]
    members_sorted = [members[int(i)] for i in order]
    result = assign_points_by_vote(fused_sorted, ious_sorted, prefer_small=prefer_small)
    clusters = {int(reps_sorted[i]): list(members_sorted[i]) for i in range(len(reps_sorted))}
    return {
        "fused_masks": fused_sorted,
        "fused_ious": ious_sorted,
        "result_mask": result,
        "clusters": clusters,
        "prompt_ids_sorted": sorted_pids,
        "reps": reps_sorted,
    }
