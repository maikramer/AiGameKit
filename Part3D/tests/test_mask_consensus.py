"""Unit tests for P3-SAM mask consensus helpers."""

from __future__ import annotations

import numpy as np
from part3d.utils.mask_consensus import (
    assign_points_by_vote,
    cluster_masks_bestfit,
    collect_batch_heads,
    collect_prompt_heads,
    consensus_cluster_and_fuse,
    distinct_prompt_support,
    fuse_cluster_mask,
    run_mask_consensus,
)


def _mask(n: int, active: slice | list[int]) -> np.ndarray:
    out = np.zeros(n, dtype=bool)
    out[active] = True
    return out


class TestCollectPromptHeads:
    def test_winner_take_all_when_disabled(self) -> None:
        masks = np.stack(
            [_mask(8, slice(0, 4)), _mask(8, slice(0, 6)), _mask(8, slice(2, 8))],
            axis=1,
        )
        ious = np.array([0.9, 0.7, 0.8])
        kept = collect_prompt_heads(masks, ious, 3, multi_head=False)
        assert len(kept) == 1
        assert kept[0][1] == 0.9
        assert kept[0][2] == 3
        np.testing.assert_array_equal(kept[0][0], masks[:, 0])

    def test_keeps_near_best_heads(self) -> None:
        masks = np.stack(
            [_mask(10, slice(0, 4)), _mask(10, slice(0, 5)), _mask(10, slice(5, 10))],
            axis=1,
        )
        ious = np.array([0.9, 0.85, 0.4])
        kept = collect_prompt_heads(masks, ious, 0, multi_head=True, min_score=0.5, score_ratio=0.9, dedupe_iou=0.99)
        assert len(kept) == 2
        scores = {round(s, 2) for _, s, _ in kept}
        assert scores == {0.9, 0.85}

    def test_batch_collect(self) -> None:
        pred_mask = np.zeros((6, 2, 3), dtype=bool)
        pred_mask[:3, 0, 0] = True
        pred_mask[:4, 0, 1] = True
        pred_mask[3:, 1, 0] = True
        pred_iou = np.array([[0.9, 0.88, 0.1], [0.8, 0.2, 0.1]])
        masks, _ious, pids = collect_batch_heads(
            pred_mask, pred_iou, base_prompt_index=10, multi_head=True, score_ratio=0.9
        )
        assert len(masks) >= 2
        assert set(pids) <= {10, 11}


class TestClusterAndFuse:
    def test_bestfit_prefers_highest_iou(self) -> None:
        a = _mask(20, slice(0, 10))
        b = _mask(20, slice(0, 9))  # high overlap with a
        c = _mask(20, slice(12, 20))
        clusters = cluster_masks_bestfit([a, b, c], nms_iou=0.5)
        # b should join a's cluster (rep 0), c alone
        assert 0 in clusters
        assert 1 in clusters[0] or 0 in clusters.get(1, [])
        assert any(2 in members or rep == 2 for rep, members in clusters.items())

    def test_fuse_soft_vote(self) -> None:
        a = _mask(10, slice(0, 6)).astype(np.float32)
        b = _mask(10, [0, 1, 2, 3, 4]).astype(np.float32)
        fused = fuse_cluster_mask([a, b], [0.9, 0.9], [0, 1], vote=0.5)
        assert fused[:5].all()
        assert not fused[6:].any()

    def test_distinct_prompt_support(self) -> None:
        assert distinct_prompt_support([0, 1, 2], [7, 7, 8]) == 2

    def test_consensus_filters_weak_singletons(self) -> None:
        masks = [_mask(12, slice(0, 4)), _mask(12, slice(0, 4)), _mask(12, slice(8, 12))]
        ious = [0.9, 0.85, 0.4]
        prompt_ids = [0, 1, 2]  # two prompts agree on door; singleton low-iou dropped
        fused, _fused_ious, _members, reps = consensus_cluster_and_fuse(
            masks,
            ious,
            prompt_ids,
            nms_iou=0.8,
            min_cluster_support=2,
            min_predicted_iou=1.0,
        )
        assert len(fused) == 1
        assert reps[0] == 0


class TestAssignAndRun:
    def test_prefer_small_on_overlap(self) -> None:
        wall = _mask(20, slice(0, 20))
        door = _mask(20, slice(5, 8))
        labels = assign_points_by_vote([wall, door], [0.9, 0.9], prefer_small=True)
        assert (labels[5:8] == 1).all()
        assert (labels[:5] == 0).all()

    def test_run_mask_consensus_end_to_end(self) -> None:
        masks = [
            _mask(30, slice(0, 20)),
            _mask(30, slice(0, 19)),
            _mask(30, slice(15, 22)),
            _mask(30, slice(15, 21)),
            _mask(30, slice(25, 30)),
            _mask(30, slice(25, 29)),
        ]
        ious = [0.95, 0.9, 0.88, 0.86, 0.8, 0.78]
        prompt_ids = [0, 1, 2, 3, 4, 5]
        out = run_mask_consensus(
            masks,
            ious,
            prompt_ids,
            nms_iou=0.7,
            min_cluster_support=2,
            min_predicted_iou=1.0,
        )
        assert len(out["fused_masks"]) >= 2
        assert out["result_mask"].shape == (30,)
        assert (out["result_mask"] >= -1).all()
