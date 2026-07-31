"""Testes do planner low-VRAM (puro, sem GPU)."""

from __future__ import annotations

from aigamekit_shared.lowvram import (
    FOOTPRINTS,
    GIB,
    OFFLOAD_GROUP_STREAM,
    OFFLOAD_NONE,
    ModelFootprint,
    OffloadPlan,
    get_footprint,
    plan_offload,
)


def _gpu(gib: float, idx: int = 0) -> tuple[int, int]:
    return (idx, int(gib * GIB))


def _gpu_free(free_gib: float, total_gib: float, idx: int = 0) -> tuple[int, int, int]:
    """Spec 3-tuple (idx, free, total) — simula cuda_gpu_free_specs()."""
    return (idx, int(free_gib * GIB), int(total_gib * GIB))


# Modelo de referência tipo FLUX 9B: ~18 GiB fp16.
FLUX_9B = ModelFootprint(fp16_weights_gib=18.0, activation_gib=1.5, largest_module_gib=7.2)
# Modelo pequeno tipo 4B: ~8 GiB fp16.
SMALL_4B = ModelFootprint(fp16_weights_gib=8.0, activation_gib=1.5)


class TestNoGpu:
    def test_empty_specs_runs_on_cpu(self) -> None:
        plan = plan_offload([], FLUX_9B)
        assert plan.device == "cpu"
        assert plan.offload == OFFLOAD_NONE
        assert not plan.memory_efficient


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

    def test_fp8_layerwise_preferred_over_sdnq_when_fits(self) -> None:
        """fp8-layerwise (fator 0.55) é tentado antes de SDNQ; se cabe, é preferido
        (melhor qualidade, sem needing Triton/kernels)."""
        # Modelo 10 GiB fp16; fp8-layerwise: 10*0.55=5.5 + 1.5 = 7.0 ≤ 9.0 (10GB*0.9).
        medium = ModelFootprint(fp16_weights_gib=10.0, activation_gib=1.5)
        plan = plan_offload([_gpu(10)], medium)
        assert plan.offload == OFFLOAD_NONE
        assert plan.quant_mode == "fp8-layerwise"


class TestSixGbTarget:
    def test_big_model_6gb_uses_group_stream(self) -> None:
        """GPU apertada (6 GiB) + FLUX 9B → group offload com streams (preferido)."""
        plan = plan_offload([_gpu(6)], FLUX_9B)
        assert plan.device == "cuda"
        assert plan.quant_mode == "sdnq-int4"
        assert plan.offload == OFFLOAD_GROUP_STREAM
        assert plan.group_config is not None
        assert plan.vae_tiling and plan.attention_slicing
        assert plan.memory_efficient

    def test_force_group_leaf_even_when_fits(self) -> None:
        """force+prefer_leaf: small model em 6 GiB → group leaf (não full-GPU)."""
        plan = plan_offload(
            [_gpu(6)],
            SMALL_4B,
            force_group_offload=True,
            prefer_leaf_offload=True,
        )
        assert plan.offload == OFFLOAD_GROUP_STREAM
        assert plan.group_config is not None
        assert plan.group_config.offload_type == "leaf_level"
        assert plan.group_config.use_stream is True

    def test_small_model_fits_6gb_full_gpu(self) -> None:
        # 8 GiB fp16 → sdnq-int4 ≈ 2.56 + 1.5 = 4.06 ≤ 5.4 → full GPU.
        plan = plan_offload([_gpu(6)], SMALL_4B)
        assert plan.offload == OFFLOAD_NONE
        assert plan.quant_mode == "sdnq-int4"

    def test_sdnq_only_tool_6gb_uses_group_stream(self) -> None:
        plan = plan_offload([_gpu(6)], FLUX_9B, allow_quant=("none", "sdnq-int4"))
        assert plan.quant_mode == "sdnq-int4"
        assert plan.offload == OFFLOAD_GROUP_STREAM


class TestSequentialFallback:
    def test_tiny_vram_huge_model_uses_group_stream_leaf(self) -> None:
        """Modelo gigante em GPU minúscula: group_stream leaf_level (mesma pegada
        que sequential, mas 2-4x mais rápido via streams). Sequential fica como
        fallback de runtime (quando group offload falha), não de planning."""
        huge = ModelFootprint(fp16_weights_gib=80.0, activation_gib=2.0, largest_module_gib=40.0)
        plan = plan_offload([_gpu(4)], huge)
        assert plan.offload == OFFLOAD_GROUP_STREAM
        assert plan.group_config.offload_type == "leaf_level"
        assert plan.est_peak_gib <= plan.usable_vram_gib


class TestGroupStreamConfig:
    """O rung group_stream resolve a config (leaf_level vs block_level) via fórmula."""

    def test_group_config_present_when_offload_group_stream(self) -> None:
        plan = plan_offload([_gpu(6)], FLUX_9B)
        assert plan.offload == OFFLOAD_GROUP_STREAM
        assert plan.group_config is not None
        # fórmula: 6 GiB usable 5.4, FLUX int4 weights 5.76, largest 2.3;
        # headroom 3.9 >= largest*1.2 2.76 → block_level (folga para um block).
        assert plan.group_config.offload_type == "block_level"
        assert plan.group_config.use_stream is True

    def test_group_config_leaf_when_extremely_tight(self) -> None:
        """GPU minúscula: headroom < largest*1.2 → leaf_level (VRAM mínima)."""
        # usable 3.6 (4 GiB * 0.9); FLUX int4 weights 5.76; largest 2.3;
        # headroom = 3.6 - 1.5 = 2.1 < largest*1.2 = 2.76 → leaf_level.
        plan = plan_offload([_gpu(4)], FLUX_9B)
        assert plan.offload == OFFLOAD_GROUP_STREAM
        assert plan.group_config.offload_type == "leaf_level"

    def test_group_config_none_when_full_gpu(self) -> None:
        plan = plan_offload([_gpu(24)], FLUX_9B)
        assert plan.offload == OFFLOAD_NONE
        assert plan.group_config is None

    def test_summary_includes_group_offload(self) -> None:
        plan = plan_offload([_gpu(6)], FLUX_9B)
        s = plan.summary()
        assert "group-offload" in s


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


class TestFootprintRegistry:
    """Registry centralizado de footprints (get_footprint)."""

    def test_known_footprint(self) -> None:
        fp = get_footprint("flux-klein-9b")
        assert fp.fp16_weights_gib == 26.0
        assert fp.architecture == "flux"

    def test_hunyuan_footprint_has_architecture(self) -> None:
        fp = get_footprint("hunyuan3d-2.1-dit")
        assert fp.architecture == "hunyuan3d"

    def test_hunyuan_omni_footprint(self) -> None:
        fp = get_footprint("hunyuan3d-omni")
        assert fp.architecture == "hunyuan3d"
        assert fp.fp16_weights_gib >= 10.0

    def test_unknown_key_returns_fallback(self) -> None:
        fp = get_footprint("nonexistent-model")
        assert fp.fp16_weights_gib > 0  # fallback genérico
        assert fp is not None

    def test_all_registry_entries_have_architecture(self) -> None:
        """Todas as entradas do registry devem ter architecture para multi-GPU."""
        for key, fp in FOOTPRINTS.items():
            assert fp.architecture is not None, f"{key} sem architecture"

    def test_flux_dev_uint4_uses_quantized_size(self) -> None:
        """flux-dev-uint4: fp16_weights_gib reflete tamanho JÁ quantizado."""
        fp = get_footprint("flux-dev-uint4")
        assert fp.fp16_weights_gib < 10.0  # ~7.4 GiB, não 23 GiB


class TestFreeVram:
    """plan_offload com 3-tuple (VRAM livre) — respeita GPUs ocupadas."""

    def test_free_vram_reduces_budget_when_occupied(self) -> None:
        """GPU 12 GiB mas 60% ocupada -> budget menor que total*0.9."""
        # 2-tuple: budget = 12 * 0.9 = 10.8 GiB
        plan_total = plan_offload([_gpu(12)], FLUX_9B)
        # 3-tuple: 12 GiB total, 5 GiB livre -> budget = min(10.8, 5*0.95) = 4.75
        plan_free = plan_offload([_gpu_free(5.0, 12.0)], FLUX_9B)
        # Com menos budget, o plano deve offload mais agressivo (ou igual).
        assert plan_free.usable_vram_gib <= plan_total.usable_vram_gib

    def test_free_vram_full_gpu_stays_none(self) -> None:
        """GPU com VRAM livre suficiente -> full GPU (sem offload)."""
        # 24 GiB total, 24 GiB livre -> budget = min(21.6, 22.8) = 21.6 -> cabe.
        plan = plan_offload([_gpu_free(24.0, 24.0)], FLUX_9B)
        assert plan.offload == OFFLOAD_NONE

    def test_free_vram_near_empty_triggers_offload(self) -> None:
        """GPU 24 GiB mas so 3 GiB livre -> offload (nao cabe)."""
        # budget = min(21.6, 3*0.95=2.85) = 2.85 -> FLUX 9B nao cabe -> offload.
        plan = plan_offload([_gpu_free(3.0, 24.0)], FLUX_9B)
        assert plan.offload != OFFLOAD_NONE

    def test_2tuple_and_full_3tuple_equivalent(self) -> None:
        """2-tuple e 3-tuple-com-free=total dão o mesmo budget."""
        plan_2 = plan_offload([_gpu(12)], FLUX_9B)
        plan_3 = plan_offload([_gpu_free(12.0, 12.0)], FLUX_9B)
        # min(10.8, 11.4) = 10.8 → igual ao 2-tuple.
        assert plan_2.usable_vram_gib == plan_3.usable_vram_gib
