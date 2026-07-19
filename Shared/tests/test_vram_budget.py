"""Testes do orçamento dinâmico de activação VRAM (puro, sem GPU)."""

from __future__ import annotations

from gamedev_shared.vram_budget import (
    PAINT_MESHRENDER_MIN_FREE_BYTES,
    PAINT_VIEWS_LO,
    TEXT3D_CHUNKS_HI,
    TEXT3D_CHUNKS_LO,
    budget_units,
    paint_bytes_per_view,
    paint_runtime_budget,
    text3d_num_chunks,
)


class TestBudgetUnits:
    def test_none_without_vram(self) -> None:
        assert budget_units(None, 1024, lo=1, hi=100) is None
        assert budget_units(0, 1024, lo=1, hi=100) is None

    def test_scales_and_clamps(self) -> None:
        gib = 1024**3
        n = budget_units(1 * gib, 96 * 1024, fraction=0.7, lo=100, hi=10_000)
        assert n == int(1 * gib * 0.7 / (96 * 1024))
        # Soft floor: free minúsculo NÃO força lo (evita OOM).
        assert budget_units(1, 96 * 1024, lo=TEXT3D_CHUNKS_LO, hi=TEXT3D_CHUNKS_HI) is None
        assert budget_units(10_000 * gib, 96 * 1024, lo=TEXT3D_CHUNKS_LO, hi=TEXT3D_CHUNKS_HI) == TEXT3D_CHUNKS_HI

    def test_soft_lo_does_not_inflate(self) -> None:
        """n < lo → devolve n (best-effort), nunca força lo acima do budget."""
        # ~500 units cabem; lo=8192 → soft, devolve ~500 (não 8192).
        free = int(500 * 96 * 1024 / 0.7)
        n = budget_units(free, 96 * 1024, fraction=0.7, lo=TEXT3D_CHUNKS_LO, hi=TEXT3D_CHUNKS_HI)
        assert n is not None
        assert n < TEXT3D_CHUNKS_LO
        assert 490 <= n <= 500


class TestText3dChunks:
    def test_alias(self) -> None:
        gib = 1024**3
        assert text3d_num_chunks(None) is None
        n = text3d_num_chunks(4 * gib)
        assert n == int(4 * gib * 0.7 / (96 * 1024))


class TestPaintRuntimeBudget:
    def test_reduces_views_when_tight(self) -> None:
        # ~0.8 GiB livre @ 512 → poucas vistas
        free = int(0.8 * 1024**3)
        b = paint_runtime_budget(free, requested_views=6, requested_resolution=512, memory_efficient=True)
        assert PAINT_VIEWS_LO <= b.max_views <= 6
        assert b.max_views < 6 or b.max_views == PAINT_VIEWS_LO
        assert b.dino_device == "cpu"
        assert b.cfg_batch_chunking is True
        assert b.esrgan_tile <= 256

    def test_keeps_views_when_roomy(self) -> None:
        free = 6 * 1024**3
        b = paint_runtime_budget(free, requested_views=6, requested_resolution=512, memory_efficient=False)
        assert b.max_views == 6
        assert b.dino_device == "cuda"
        assert b.esrgan_tile == 512

    def test_bytes_per_view_scales_res2(self) -> None:
        assert paint_bytes_per_view(1024) == 4 * paint_bytes_per_view(512)

    def test_memory_efficient_keeps_dino_on_cpu_even_when_roomy(self) -> None:
        """Regressão OOM 6GB: mem_eff + 3 GiB livres NÃO pode puxar DINO→CUDA."""
        free = int(3.0 * 1024**3)
        b = paint_runtime_budget(free, requested_views=6, requested_resolution=512, memory_efficient=True)
        assert b.dino_device == "cpu"
        assert b.offload_ref_unet is True
        assert PAINT_MESHRENDER_MIN_FREE_BYTES >= 128 * 1024 * 1024

    def test_requested_one_view_not_inflated(self) -> None:
        """Pedido de 1 vista não sobe para PAINT_VIEWS_LO."""
        free = 6 * 1024**3
        b = paint_runtime_budget(free, requested_views=1, requested_resolution=512, memory_efficient=False)
        assert b.max_views == 1

    def test_mem_eff_fail_closed_without_vram_signal(self) -> None:
        b = paint_runtime_budget(None, requested_views=8, requested_resolution=512, memory_efficient=True)
        assert b.max_views == PAINT_VIEWS_LO
        assert any("fail-closed" in n for n in b.notes)
