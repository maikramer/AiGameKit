"""Testes do planner low-VRAM (puro, sem GPU)."""

from __future__ import annotations

from gamedev_shared.lowvram import (
    GIB,
    OFFLOAD_MODEL,
    OFFLOAD_NONE,
    OFFLOAD_SEQUENTIAL,
    ModelFootprint,
    OffloadPlan,
    plan_offload,
)


def _gpu(gib: float, idx: int = 0) -> tuple[int, int]:
    return (idx, int(gib * GIB))


# Modelo de referência tipo FLUX 9B: ~18 GiB fp16.
FLUX_9B = ModelFootprint(fp16_weights_gib=18.0, activation_gib=1.5, largest_module_gib=7.2)
# Modelo pequeno tipo 4B: ~8 GiB fp16.
SMALL_4B = ModelFootprint(fp16_weights_gib=8.0, activation_gib=1.5)


class TestNoGpu:
    def test_empty_specs_runs_on_cpu(self) -> None:
        plan = plan_offload([], FLUX_9B)
        assert plan.device == "cpu"
        assert plan.offload == OFFLOAD_NONE
        assert not plan.low_vram


class TestHighVram:
    def test_24gb_runs_full_gpu_unquantized(self) -> None:
        plan = plan_offload([_gpu(24)], FLUX_9B)
        assert plan.device == "cuda"
        assert plan.quant_mode == "none"
        assert plan.offload == OFFLOAD_NONE
        assert plan.primary_gpu == 0

    def test_12gb_quantizes_but_stays_full_gpu(self) -> None:
        plan = plan_offload([_gpu(12)], FLUX_9B)
        assert plan.offload == OFFLOAD_NONE
        assert plan.quant_mode == "sdnq-int4"
        assert plan.est_peak_gib <= plan.usable_vram_gib


class TestSixGbTarget:
    def test_big_model_fits_6gb_via_quant_plus_offload(self) -> None:
        plan = plan_offload([_gpu(6)], FLUX_9B)
        assert plan.device == "cuda"
        assert plan.quant_mode == "sdnq-int4"
        assert plan.offload == OFFLOAD_MODEL
        assert plan.vae_tiling and plan.attention_slicing
        assert plan.est_peak_gib <= plan.usable_vram_gib
        assert plan.low_vram

    def test_small_model_fits_6gb_full_gpu(self) -> None:
        # 8 GiB fp16 → sdnq-int4 ≈ 2.56 + 1.5 = 4.06 ≤ 5.4 → full GPU.
        plan = plan_offload([_gpu(6)], SMALL_4B)
        assert plan.offload == OFFLOAD_NONE
        assert plan.quant_mode == "sdnq-int4"

    def test_sdnq_only_tool_still_fits_6gb(self) -> None:
        plan = plan_offload([_gpu(6)], FLUX_9B, allow_quant=("none", "sdnq-int4"))
        assert plan.quant_mode == "sdnq-int4"
        assert plan.offload == OFFLOAD_MODEL


class TestSequentialFallback:
    def test_tiny_vram_huge_model_falls_to_sequential(self) -> None:
        # 4 GiB GPU, modelo gigante cujo maior módulo não cabe nem quantizado.
        huge = ModelFootprint(fp16_weights_gib=80.0, activation_gib=2.0, largest_module_gib=40.0)
        plan = plan_offload([_gpu(4)], huge)
        assert plan.offload == OFFLOAD_SEQUENTIAL
        assert plan.est_peak_gib <= plan.usable_vram_gib


class TestMultiGpu:
    def test_two_gpus_split_without_offload(self) -> None:
        plan = plan_offload([_gpu(12, 0), _gpu(12, 1)], FLUX_9B)
        assert plan.multi_gpu_ids == [0, 1]
        assert plan.offload == OFFLOAD_NONE
        assert plan.quant_mode == "none"

    def test_multi_gpu_disabled_uses_single(self) -> None:
        plan = plan_offload([_gpu(12, 0), _gpu(12, 1)], FLUX_9B, allow_multi_gpu=False)
        assert plan.multi_gpu_ids is None


class TestSummary:
    def test_summary_is_stringy(self) -> None:
        plan = plan_offload([_gpu(6)], FLUX_9B)
        s = plan.summary()
        assert "quant" in s and "GiB" in s
        assert isinstance(plan, OffloadPlan)
