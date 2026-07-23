"""Cobertura alargada Part3D (sem GPU): defaults, hardware, utils, CLI."""

from __future__ import annotations

import numpy as np
import part3d.defaults as defaults
import pytest
from click.testing import CliRunner
from part3d.cli import main
from part3d.hardware import (
    HW_AUTO_ENV,
    Part3DHardwareProfile,
    detect_hardware_profile,
    hw_auto_enabled,
    profile_from_specs,
)
from part3d.utils.hierarchical import detail_partition_is_useful, prune_detail_partition
from part3d.utils.kernel_accel import resolve_mc_algo, resolve_volume_decoder
from part3d.utils.mask_consensus import (
    assign_points_by_vote,
    build_sorted_mask_pool,
    cluster_masks_bestfit,
    collect_prompt_heads,
    consensus_cluster_and_fuse,
    distinct_prompt_support,
    fuse_cluster_mask,
    pairwise_mask_iou,
    run_mask_consensus,
)
from part3d.utils.sdnq_resolve import resolve_sdnq_preset

GIB = 1024**3


def _specs(vram_gib: float, count: int = 1) -> list[tuple[int, int]]:
    return [(i, int(vram_gib * GIB)) for i in range(count)]


def test_defaults_default_hf_repo() -> None:
    assert isinstance(defaults.DEFAULT_HF_REPO, str)
    assert "Hunyuan3D" in defaults.DEFAULT_HF_REPO


def test_defaults_default_num_inference_steps() -> None:
    assert defaults.DEFAULT_NUM_INFERENCE_STEPS == 30


def test_defaults_default_guidance_scale() -> None:
    assert defaults.DEFAULT_GUIDANCE_SCALE == -1.0


def test_defaults_default_octree_resolution() -> None:
    assert defaults.DEFAULT_OCTREE_RESOLUTION == 256


def test_defaults_default_num_chunks() -> None:
    assert defaults.DEFAULT_NUM_CHUNKS == 20000


def test_defaults_default_mc_level() -> None:
    assert abs(defaults.DEFAULT_MC_LEVEL - (-1 / 512)) < 1e-9


def test_defaults_default_mc_algo() -> None:
    assert defaults.DEFAULT_MC_ALGO == "mc"


def test_defaults_default_volume_decoder() -> None:
    assert defaults.DEFAULT_VOLUME_DECODER == "auto"


def test_defaults_default_channels_last() -> None:
    assert defaults.DEFAULT_CHANNELS_LAST is True


def test_defaults_default_postprocess() -> None:
    assert defaults.DEFAULT_POSTPROCESS is True


def test_defaults_default_postprocess_threshold() -> None:
    assert 0.0 < defaults.DEFAULT_POSTPROCESS_THRESHOLD <= 1.0


def test_defaults_space_part_area_merge() -> None:
    assert defaults.SPACE_PART_AREA_MERGE < defaults.SPACE_BBOX_MERGE_IOU


def test_defaults_space_area_ratio_keep() -> None:
    assert defaults.SPACE_AREA_RATIO_KEEP < defaults.SPACE_PART_AREA_MERGE


def test_defaults_space_bbox_merge_iou() -> None:
    assert 0.5 < defaults.SPACE_BBOX_MERGE_IOU < 1.0


def test_defaults_space_mask_nms_iou() -> None:
    assert defaults.SPACE_MASK_NMS_IOU >= defaults.SPACE_BBOX_MERGE_IOU


def test_defaults_space_secondary_mask_iou() -> None:
    assert 0.0 < defaults.SPACE_SECONDARY_MASK_IOU < 1.0


def test_defaults_space_min_cluster_support() -> None:
    assert defaults.SPACE_MIN_CLUSTER_SUPPORT >= 1


def test_defaults_space_min_predicted_iou() -> None:
    assert defaults.SPACE_MIN_PREDICTED_IOU == 1.0


def test_defaults_space_prompt_batch_size() -> None:
    assert defaults.SPACE_PROMPT_BATCH_SIZE >= 1


def test_defaults_space_multi_head() -> None:
    assert defaults.SPACE_MULTI_HEAD is True


def test_defaults_space_head_min_score() -> None:
    assert 0.0 < defaults.SPACE_HEAD_MIN_SCORE <= 1.0


def test_defaults_space_head_score_ratio() -> None:
    assert 0.0 < defaults.SPACE_HEAD_SCORE_RATIO <= 1.0


def test_defaults_space_consensus() -> None:
    assert defaults.SPACE_CONSENSUS is True


def test_defaults_space_consensus_vote() -> None:
    assert 0.0 < defaults.SPACE_CONSENSUS_VOTE <= 1.0


def test_defaults_default_refine_labels() -> None:
    assert defaults.DEFAULT_REFINE_LABELS is True


def test_defaults_default_refine_iterations() -> None:
    assert defaults.DEFAULT_REFINE_ITERATIONS > 0


def test_defaults_default_refine_smooth_angle_deg() -> None:
    assert defaults.DEFAULT_REFINE_SMOOTH_ANGLE_DEG > 0


def test_defaults_default_refine_concave_factor() -> None:
    assert 0.0 < defaults.DEFAULT_REFINE_CONCAVE_FACTOR < 1.0


def test_defaults_default_refine_island_min_frac() -> None:
    assert 0.0 < defaults.DEFAULT_REFINE_ISLAND_MIN_FRAC < 1.0


def test_defaults_default_refine_island_min_faces() -> None:
    assert defaults.DEFAULT_REFINE_ISLAND_MIN_FACES >= 1


def test_defaults_default_refine_data_weight() -> None:
    assert 0.0 < defaults.DEFAULT_REFINE_DATA_WEIGHT < 1.0


def test_defaults_default_refine_boundary_hops() -> None:
    assert defaults.DEFAULT_REFINE_BOUNDARY_HOPS >= 1


def test_defaults_default_detail_levels() -> None:
    assert defaults.DEFAULT_DETAIL_LEVELS == 0


def test_defaults_default_detail_parent_min_area_frac() -> None:
    assert 0.0 < defaults.DEFAULT_DETAIL_PARENT_MIN_AREA_FRAC < 1.0


def test_defaults_default_detail_child_min_area_frac() -> None:
    assert 0.0 < defaults.DEFAULT_DETAIL_CHILD_MIN_AREA_FRAC < 1.0


def test_defaults_default_detail_max_dominant_frac() -> None:
    assert 0.0 < defaults.DEFAULT_DETAIL_MAX_DOMINANT_FRAC <= 1.0


def test_defaults_default_detail_max_parents() -> None:
    assert defaults.DEFAULT_DETAIL_MAX_PARENTS >= 1


def test_defaults_default_detail_point_num() -> None:
    assert defaults.DEFAULT_DETAIL_POINT_NUM > 1000


def test_defaults_default_detail_prompt_num() -> None:
    assert defaults.DEFAULT_DETAIL_PROMPT_NUM > 0


def test_defaults_default_segment_mode() -> None:
    assert defaults.DEFAULT_SEGMENT_MODE in ("p3sam", "geometry", "hybrid")


def test_defaults_default_fine_segment_mode() -> None:
    assert defaults.DEFAULT_FINE_SEGMENT_MODE == "hybrid"


def test_defaults_default_parts_mode() -> None:
    assert defaults.DEFAULT_PARTS_MODE == "faces"


def test_defaults_default_fine_parts_mode() -> None:
    assert defaults.DEFAULT_FINE_PARTS_MODE == "faces"


def test_defaults_default_xpart_max_area_frac() -> None:
    assert 0.0 < defaults.DEFAULT_XPART_MAX_AREA_FRAC < 1.0


def test_defaults_default_xpart_large_octree() -> None:
    assert defaults.DEFAULT_XPART_LARGE_OCTREE >= 64


def test_defaults_default_aabb_margin_frac() -> None:
    assert 0.0 < defaults.DEFAULT_AABB_MARGIN_FRAC < 0.5


def test_defaults_default_preserve_thin_topology() -> None:
    assert defaults.DEFAULT_PRESERVE_THIN_TOPOLOGY is False


def test_defaults_default_xpart_skip_thin_ratio() -> None:
    assert 0.0 < defaults.DEFAULT_XPART_SKIP_THIN_RATIO < 1.0


def test_defaults_default_xpart_skip_aspect() -> None:
    assert defaults.DEFAULT_XPART_SKIP_ASPECT > 1.0


def test_defaults_default_cap_part_holes() -> None:
    assert defaults.DEFAULT_CAP_PART_HOLES is False


def test_defaults_default_exclusive_partition() -> None:
    assert defaults.DEFAULT_EXCLUSIVE_PARTITION is False


def test_defaults_default_exclusive_samples_per_part() -> None:
    assert defaults.DEFAULT_EXCLUSIVE_SAMPLES_PER_PART >= 100


def test_defaults_default_dtype() -> None:
    assert defaults.DEFAULT_DTYPE == "float16"


def test_defaults_default_cpu_offload() -> None:
    assert defaults.DEFAULT_CPU_OFFLOAD is False


def test_defaults_default_memory_efficient() -> None:
    assert defaults.DEFAULT_MEMORY_EFFICIENT is False


def test_defaults_default_hw_auto() -> None:
    assert defaults.DEFAULT_HW_AUTO is True


def test_defaults_default_quantization_mode() -> None:
    assert defaults.DEFAULT_QUANTIZATION_MODE == "auto"


def test_defaults_default_quantize_dit() -> None:
    assert defaults.DEFAULT_QUANTIZE_DIT is False


def test_defaults_default_enable_attention_slicing() -> None:
    assert defaults.DEFAULT_ENABLE_ATTENTION_SLICING is True


def test_defaults_default_torch_compile() -> None:
    assert defaults.DEFAULT_TORCH_COMPILE is False


def test_defaults_default_torch_compile_mode() -> None:
    assert defaults.DEFAULT_TORCH_COMPILE_MODE == "default"


def test_profile_from_specs_empty_cpu() -> None:
    p = profile_from_specs([])
    assert p.device == "cpu"
    assert p.memory_efficient is True


def test_profile_from_specs_single_5g() -> None:
    p = profile_from_specs(_specs(5.0))
    assert p.device == "cuda"
    assert p.memory_efficient is True


def test_profile_from_specs_single_9g() -> None:
    p = profile_from_specs(_specs(9.0))
    assert p.device == "cuda"
    assert p.memory_efficient is False


def test_profile_from_specs_single_12g() -> None:
    p = profile_from_specs(_specs(12.0))
    assert p.device == "cuda"
    assert p.memory_efficient is False


def test_profile_from_specs_dual_6g() -> None:
    p = profile_from_specs(_specs(6.0, 2))
    assert p.device == "cuda"
    assert p.memory_efficient is False


def test_hw_auto_env_1() -> None:
    import os

    old = os.environ.get(HW_AUTO_ENV)
    os.environ[HW_AUTO_ENV] = "1"
    try:
        assert hw_auto_enabled() is True
    finally:
        if old is None:
            os.environ.pop(HW_AUTO_ENV, None)
        else:
            os.environ[HW_AUTO_ENV] = old


def test_hw_auto_env_0() -> None:
    import os

    old = os.environ.get(HW_AUTO_ENV)
    os.environ[HW_AUTO_ENV] = "0"
    try:
        assert hw_auto_enabled() is False
    finally:
        if old is None:
            os.environ.pop(HW_AUTO_ENV, None)
        else:
            os.environ[HW_AUTO_ENV] = old


def test_hw_auto_env_false() -> None:
    import os

    old = os.environ.get(HW_AUTO_ENV)
    os.environ[HW_AUTO_ENV] = "false"
    try:
        assert hw_auto_enabled() is False
    finally:
        if old is None:
            os.environ.pop(HW_AUTO_ENV, None)
        else:
            os.environ[HW_AUTO_ENV] = old


def test_hw_auto_env_no() -> None:
    import os

    old = os.environ.get(HW_AUTO_ENV)
    os.environ[HW_AUTO_ENV] = "no"
    try:
        assert hw_auto_enabled() is False
    finally:
        if old is None:
            os.environ.pop(HW_AUTO_ENV, None)
        else:
            os.environ[HW_AUTO_ENV] = old


def test_detect_hardware_profile_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("part3d.hardware.cuda_gpu_specs", lambda: [])
    p = detect_hardware_profile()
    assert isinstance(p, Part3DHardwareProfile)


def test_profile_summary_mid_tier_offload() -> None:
    p = profile_from_specs(_specs(8.5))
    assert "FP16+offload" in p.summary()


def test_profile_summary_multi_gpu_ids() -> None:
    p = profile_from_specs(_specs(6.0, 2))
    assert "gpus=[0, 1]" in p.summary()


def test_sdnq_auto_mem_quant() -> None:
    assert resolve_sdnq_preset("auto", memory_efficient=True, quantize_dit=True) == "sdnq-uint8"


def test_sdnq_auto_no_mem() -> None:
    assert resolve_sdnq_preset("auto", memory_efficient=False, quantize_dit=True) == None


def test_sdnq_auto_no_quant() -> None:
    assert resolve_sdnq_preset("auto", memory_efficient=True, quantize_dit=False) == None


def test_sdnq_none_mode() -> None:
    assert resolve_sdnq_preset("none", memory_efficient=True, quantize_dit=True) == None


def test_sdnq_int8_explicit() -> None:
    assert resolve_sdnq_preset("int8", memory_efficient=False, quantize_dit=True) == "sdnq-int8"


def test_sdnq_int4_explicit() -> None:
    assert resolve_sdnq_preset("int4", memory_efficient=True, quantize_dit=True) == "sdnq-int4"


def test_sdnq_8bit_alias() -> None:
    assert resolve_sdnq_preset("8bit", memory_efficient=False, quantize_dit=True) == "sdnq-int8"


def test_sdnq_4bit_alias() -> None:
    assert resolve_sdnq_preset("4bit", memory_efficient=True, quantize_dit=True) == "sdnq-int4"


def test_sdnq_sdnq_pass_through() -> None:
    assert resolve_sdnq_preset("sdnq-int8", memory_efficient=False, quantize_dit=True) == "sdnq-int8"


def test_sdnq_fp16_off() -> None:
    assert resolve_sdnq_preset("fp16", memory_efficient=True, quantize_dit=True) == None


def test_sdnq_off_mode() -> None:
    assert resolve_sdnq_preset("off", memory_efficient=True, quantize_dit=True) == None


def test_sdnq_unknown_mode() -> None:
    assert resolve_sdnq_preset("weird-quant", memory_efficient=True, quantize_dit=True) == None


def test_volume_decoder_explicit_hierarchical() -> None:
    assert resolve_volume_decoder("hierarchical") == "hierarchical"


def test_volume_decoder_explicit_flashvdm() -> None:
    assert resolve_volume_decoder("flashvdm") == "flashvdm"


def test_volume_decoder_explicit_vanilla() -> None:
    assert resolve_volume_decoder("vanilla") == "vanilla"


def test_volume_decoder_explicit_fast() -> None:
    assert resolve_volume_decoder("fast") == "fast"


def test_volume_decoder_auto_mem_eff() -> None:
    assert resolve_volume_decoder("auto", memory_efficient=True) == "flashvdm"


def test_volume_decoder_auto_high_quality() -> None:
    assert resolve_volume_decoder("auto", quality="high") == "hierarchical"


def test_volume_decoder_auto_default() -> None:
    assert resolve_volume_decoder("auto") == "hierarchical"


def test_volume_decoder_invalid_raises() -> None:
    with pytest.raises(ValueError, match="volume_decoder"):
        resolve_volume_decoder("not-a-decoder")


def test_resolve_mc_algo_mc() -> None:
    assert resolve_mc_algo("mc") == "mc"


def test_resolve_mc_algo_MC() -> None:
    assert resolve_mc_algo("MC") == "mc"


def test_resolve_mc_algo_dmc() -> None:
    out = resolve_mc_algo("dmc", device="cpu")
    assert out == "mc"


def test_resolve_mc_algo_weird() -> None:
    assert resolve_mc_algo("weird") == "mc"


def test_pairwise_mask_iou_identity() -> None:
    m = np.array([[1, 1, 0], [0, 1, 1]], dtype=np.float32)
    iou = pairwise_mask_iou(m)
    assert iou.shape == (2, 2)
    assert iou[0, 0] == pytest.approx(1.0)


def test_collect_prompt_heads_single_head() -> None:
    masks = np.array([[1, 0, 1], [0, 1, 0], [1, 1, 0]], dtype=bool)
    ious = np.array([0.9, 0.4, 0.3])
    heads = collect_prompt_heads(masks, ious, 0, multi_head=False)
    assert len(heads) == 1
    assert heads[0][1] == pytest.approx(0.9)


def test_collect_prompt_heads_multi_keeps_close() -> None:
    masks = np.array([[1, 0, 1], [0, 1, 0], [1, 1, 0]], dtype=bool)
    ious = np.array([0.9, 0.86, 0.2])
    heads = collect_prompt_heads(masks, ious, 2, multi_head=True, score_ratio=0.85)
    assert len(heads) >= 2


def test_collect_prompt_heads_bad_shape_raises() -> None:
    with pytest.raises(ValueError):
        collect_prompt_heads(np.zeros((3, 2)), np.array([0.5]), 0)


def test_cluster_masks_bestfit_two_clusters() -> None:
    a = np.array([1, 1, 0, 0], dtype=bool)
    b = np.array([0, 0, 1, 1], dtype=bool)
    clusters = cluster_masks_bestfit([a, b], nms_iou=0.1)
    assert len(clusters) == 2


def test_fuse_cluster_mask_majority() -> None:
    m0 = np.array([1, 1, 0], dtype=float)
    m1 = np.array([1, 0, 0], dtype=float)
    fused = fuse_cluster_mask([m0, m1], [0.9, 0.8], [0, 1], vote=0.5)
    assert fused[0]


def test_fuse_cluster_empty_raises() -> None:
    with pytest.raises(ValueError):
        fuse_cluster_mask([np.ones(3)], [1.0], [], vote=0.5)


def test_distinct_prompt_support_counts_unique() -> None:
    assert distinct_prompt_support([0, 1, 2], [10, 10, 11]) == 2


def test_build_sorted_mask_pool_order() -> None:
    masks = [np.array([1, 0]), np.array([0, 1])]
    sm, si, sp = build_sorted_mask_pool(masks, [0.2, 0.9], [5, 7])
    assert si[0] == pytest.approx(0.9)
    assert sp[0] == 7


def test_consensus_cluster_and_fuse_filters_low_support() -> None:
    masks = [np.array([1, 0, 0]), np.array([0, 1, 0])]
    fused, fi, mem, reps = consensus_cluster_and_fuse(
        masks, [0.95, 0.94], [0, 1], min_cluster_support=3, min_predicted_iou=1.0
    )
    assert fused == []


def test_assign_points_by_vote_prefers_smaller() -> None:
    big = np.array([1, 1, 1, 0], dtype=float)
    small = np.array([0, 0, 1, 1], dtype=float)
    out = assign_points_by_vote([big, small], [0.5, 0.5], prefer_small=True)
    assert out[2] in (0, 1)


def test_assign_points_no_masks_raises() -> None:
    with pytest.raises(ValueError):
        assign_points_by_vote([])


def test_run_mask_consensus_empty_masks() -> None:
    out = run_mask_consensus([], [], [])
    assert out["fused_masks"] == []


def test_run_mask_consensus_pipeline() -> None:
    masks = [np.array([1, 1, 0, 0], dtype=float), np.array([0, 0, 1, 1], dtype=float)]
    out = run_mask_consensus(masks, [0.9, 0.85], [0, 1], min_cluster_support=1, min_predicted_iou=0.0)
    assert len(out["fused_masks"]) >= 1
    assert out["result_mask"].shape == (4,)


class _FakeMesh:
    def __init__(self, areas: list[float]) -> None:
        self.area_faces = np.asarray(areas, dtype=np.float64)
        self.face_adjacency = np.zeros((0, 2), dtype=np.int64)


def test_detail_partition_is_useful_two_children() -> None:
    areas = np.array([0.4, 0.3, 0.2, 0.1])
    child = np.array([0, 0, 1, 1])
    assert detail_partition_is_useful(areas, child, min_child_frac=0.05) is True


def test_detail_partition_is_useful_single_child() -> None:
    areas = np.array([1.0, 1.0, 1.0])
    child = np.array([0, 0, 0])
    assert detail_partition_is_useful(areas, child) is False


def test_prune_detail_partition_drops_small() -> None:
    mesh = _FakeMesh([0.9, 0.05, 0.05])
    labels = np.array([0, 1, 2])
    pruned = prune_detail_partition(mesh, labels, min_child_frac=0.1)
    assert pruned[1] == -1


def test_cli_main_help() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["--help"])
    assert r.exit_code == 0
    assert "decompose" in r.output


def test_cli_decompose_help() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--quality" in r.output


def test_cli_decompose_ums() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--ums-priority" in r.output


def test_cli_decompose_no_ums() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--no-ums" in r.output


def test_cli_version_flag() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["--version"])
    assert r.exit_code == 0
    assert "0.1.0" in r.output


def test_profile_boundary_0() -> None:
    p = profile_from_specs(_specs(7.99))
    assert p.memory_efficient is True


def test_profile_boundary_1() -> None:
    p = profile_from_specs(_specs(10.0))
    assert p.memory_efficient is False


def test_profile_boundary_2() -> None:
    p = profile_from_specs(_specs(7.99))
    assert p.memory_efficient is True


def test_profile_boundary_3() -> None:
    p = profile_from_specs(_specs(8.01))
    assert p.memory_efficient is False


def test_decompose_help_has_segment_mode() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--segment-mode" in r.output


def test_decompose_help_has_parts_mode() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--parts-mode" in r.output


def test_decompose_help_has_refine_labels() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--refine-labels" in r.output


def test_decompose_help_has_kernel_modern() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--kernel-modern" in r.output


def test_decompose_help_has_torch_compile() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--torch-compile" in r.output


def test_decompose_help_has_no_quantize_dit() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--no-quantize-dit" in r.output


def test_decompose_help_mentions_memory_efficient() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "memory-efficient" in r.output.lower()


def test_decompose_help_has_gpu_ids() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--gpu-ids" in r.output


def test_decompose_help_has_ums_stream() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--ums-stream" in r.output


def test_decompose_help_has_fine_parts() -> None:
    runner = CliRunner()
    r = runner.invoke(main, ["decompose", "--help"])
    assert r.exit_code == 0
    assert "--fine-parts" in r.output
