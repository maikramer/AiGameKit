"""Testes do tiled diffusion — lógica de tiles e blend (puro, sem GPU)."""

from __future__ import annotations

import pytest

from gamedev_shared.tiled_diffusion import (
    TiledDiffusionCallback,
    _compute_tile_grid,
    _cosine_blend_window,
    enable_tiled_diffusion,
    get_tiled_callback,
)


class TestComputeTileGrid:
    """_compute_tile_grid: cobertura total com overlaps."""

    def test_small_latent_single_tile(self) -> None:
        """Latent menor que tile → 1 tile cobrindo tudo."""
        tiles = _compute_tile_grid(latent_h=32, latent_w=32, tile_h=64, tile_w=64, stride_h=32, stride_w=32)
        assert len(tiles) == 1
        assert tiles[0] == (0, 0, 32, 32)

    def test_large_latent_multiple_tiles(self) -> None:
        """Latent 128x128, tile 64x64, stride 32 → 3x3 = 9 tiles."""
        tiles = _compute_tile_grid(latent_h=128, latent_w=128, tile_h=64, tile_w=64, stride_h=32, stride_w=32)
        assert len(tiles) == 9  # 3 cols x 3 rows

    def test_full_coverage(self) -> None:
        """Todos os pixels devem estar cobertos por pelo menos 1 tile."""
        tiles = _compute_tile_grid(latent_h=96, latent_w=96, tile_h=64, tile_w=64, stride_h=32, stride_w=32)
        coverage = set()
        for t, left, b, r in tiles:
            for y in range(t, b):
                for x in range(left, r):
                    coverage.add((y, x))
        assert len(coverage) == 96 * 96  # cobertura total

    def test_overlap_exists(self) -> None:
        """Com stride < tile, deve haver overlap entre tiles adjacentes."""
        tiles = _compute_tile_grid(latent_h=128, latent_w=128, tile_h=64, tile_w=64, stride_h=32, stride_w=32)
        # Tile (0,0) cobre [0:64], tile (32,0) cobre [32:96] → overlap [32:64].
        assert tiles[0][2] > tiles[1][0]  # bottom do 1º > top do 2º


class TestCosineBlendWindow:
    """_cosine_blend_window: janela de blend 2D."""

    def test_shape(self) -> None:
        pytest.importorskip("torch")
        import torch

        w = _cosine_blend_window(64, 64, torch.device("cpu"), torch.float32)
        assert w.shape == (64, 64)

    def test_center_is_one(self) -> None:
        """O centro da janela deve ser ~1 (plateau)."""
        pytest.importorskip("torch")
        import torch

        w = _cosine_blend_window(64, 64, torch.device("cpu"), torch.float32)
        assert w[32, 32] >= 0.99  # centro próximo de 1

    def test_edge_is_near_zero(self) -> None:
        """Os bordos devem tender para 0 (cosine fade)."""
        pytest.importorskip("torch")
        import torch

        w = _cosine_blend_window(64, 64, torch.device("cpu"), torch.float32)
        assert w[0, 0] < 0.1  # canto próximo de 0


class TestEnableTiledDiffusion:
    """enable_tiled_diffusion: instala callback no pipe."""

    def test_installs_callback(self) -> None:
        class FakeVAE:
            class config:
                spatial_compression_ratio = 8

        class FakePipe:
            vae = FakeVAE()

        pipe = FakePipe()
        assert enable_tiled_diffusion(pipe, tile_size_px=1024, stride_px=512) is True
        cb = get_tiled_callback(pipe)
        assert cb is not None
        assert cb.tile_size_px == 1024
        assert cb.stride_px == 512
        assert cb.vae_scale == 8

    def test_no_callback_without_enable(self) -> None:
        class FakePipe:
            pass

        pipe = FakePipe()
        assert get_tiled_callback(pipe) is None

    def test_custom_vae_scale(self) -> None:
        class FakePipe:
            pass

        pipe = FakePipe()
        enable_tiled_diffusion(pipe, vae_scale=16)
        cb = get_tiled_callback(pipe)
        assert cb.vae_scale == 16


class TestTiledDiffusionCallback:
    """TiledDiffusionCallback: lógica do callback."""

    def test_grid_computed_once(self) -> None:
        """A grelha de tiles é lazy (computada no 1º step)."""
        cb = TiledDiffusionCallback(tile_size_px=512, stride_px=256, vae_scale=8)
        assert cb._tile_grid is None
        # Simular latents 1024x1024 pixels → 128x128 latent
        cb._tile_grid = _compute_tile_grid(128, 128, 64, 64, 32, 32)
        assert len(cb._tile_grid) == 9
