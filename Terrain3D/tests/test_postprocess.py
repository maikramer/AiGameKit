from __future__ import annotations

import numpy as np
import pytest

from terrain3d.postprocess import island_falloff


# --- Fixtures ---


@pytest.fixture
def heightmap_gaussian_peak() -> np.ndarray:
    """256x256 gaussian peak centered at (128, 128)."""
    y, x = np.mgrid[0:256, 0:256]
    cy, cx = 128, 128
    sigma = 40.0
    h = np.exp(-((y - cy) ** 2 + (x - cx) ** 2) / (2 * sigma**2))
    return h.astype(np.float64)


@pytest.fixture
def heightmap_flat() -> np.ndarray:
    """256x256 flat heightmap at 0.5."""
    return np.full((256, 256), 0.5, dtype=np.float64)


@pytest.fixture
def heightmap_noisy() -> np.ndarray:
    """256x256 gradient + gaussian noise."""
    rng = np.random.default_rng(42)
    gradient = np.linspace(0, 1, 256, dtype=np.float64).reshape(1, -1) * np.ones((256, 1))
    return np.clip(gradient + rng.normal(0, 0.05, (256, 256)), 0.0, 1.0).astype(np.float64)


# --- TestIslandFalloff ---


class TestIslandFalloff:
    def test_edges_near_zero(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """Corner and edge pixels should be approximately zero."""
        result = island_falloff(heightmap_gaussian_peak, seed=0)
        # Corners should be very close to zero
        assert result[0, 0] < 0.01
        assert result[0, -1] < 0.01
        assert result[-1, 0] < 0.01
        assert result[-1, -1] < 0.01

    def test_center_preserved(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """Center pixel should be close to original value."""
        result = island_falloff(heightmap_gaussian_peak, seed=0)
        # Center (128, 128) should be mostly preserved (mask ≈ 1)
        assert result[128, 128] > 0.5

    def test_output_range(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """Output should be in [0, 1]."""
        result = island_falloff(heightmap_gaussian_peak, seed=0)
        assert result.min() >= 0.0
        assert result.max() <= 1.0

    def test_flat_heightmap(self, heightmap_flat: np.ndarray) -> None:
        """Flat heightmap should have falloff applied (edges -> 0)."""
        result = island_falloff(heightmap_flat, seed=0)
        assert result[0, 0] < 0.01
        # Center should still be 0.5 (flat, but preserved)
        assert result[128, 128] == pytest.approx(0.5, abs=0.05)

    def test_seed_reproducible(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """Same seed produces same result."""
        r1 = island_falloff(heightmap_gaussian_peak, seed=42)
        r2 = island_falloff(heightmap_gaussian_peak, seed=42)
        np.testing.assert_array_equal(r1, r2)

    def test_different_seeds_vary(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """Different seeds produce different coastlines."""
        r1 = island_falloff(heightmap_gaussian_peak, seed=1)
        r2 = island_falloff(heightmap_gaussian_peak, seed=99)
        assert not np.allclose(r1, r2)

    def test_falloff_zero_no_mask(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """falloff=0.5 with noise_scale=0 should mask only the very corners."""
        result = island_falloff(heightmap_gaussian_peak, falloff=0.5, noise_scale=0.0, seed=0)
        # With falloff=0.5, most of the map should be preserved
        assert result[100, 100] > 0.1  # well inside the falloff radius

    def test_no_nan_or_inf(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """Output should contain no NaN or Inf."""
        result = island_falloff(heightmap_gaussian_peak, seed=0)
        assert np.all(np.isfinite(result))
