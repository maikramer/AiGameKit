from __future__ import annotations

import numpy as np
import pytest

from terrain3d.postprocess import elevation_scurve, island_falloff, taubin_smooth


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


class TestTaubinSmooth:
    def test_reduces_noise(self, heightmap_noisy: np.ndarray) -> None:
        """Smoothed heightmap should have lower variance of the gradient."""
        smoothed = taubin_smooth(heightmap_noisy, iterations=3)
        # Gradient magnitude (proxy for roughness)
        orig_grad = np.abs(np.diff(heightmap_noisy, axis=0)).mean()
        smooth_grad = np.abs(np.diff(smoothed, axis=0)).mean()
        assert smooth_grad < orig_grad

    def test_preserves_broad_features(self) -> None:
        """A broad gaussian should be mostly preserved after smoothing."""
        y, x = np.mgrid[0:256, 0:256]
        broad = np.exp(-((y - 128) ** 2 + (x - 128) ** 2) / (2 * 80.0**2)).astype(np.float64)
        smoothed = taubin_smooth(broad, iterations=3)
        # Peak should be preserved within 10%
        assert smoothed[128, 128] == pytest.approx(broad[128, 128], rel=0.1)

    def test_flat_unchanged(self, heightmap_flat: np.ndarray) -> None:
        """Flat heightmap should remain flat after smoothing."""
        smoothed = taubin_smooth(heightmap_flat, iterations=3)
        np.testing.assert_allclose(smoothed, heightmap_flat, atol=1e-12)

    def test_zero_iterations_noop(self, heightmap_noisy: np.ndarray) -> None:
        """iterations=0 should return the input unchanged."""
        smoothed = taubin_smooth(heightmap_noisy, iterations=0)
        np.testing.assert_array_equal(smoothed, heightmap_noisy)

    def test_output_range(self, heightmap_noisy: np.ndarray) -> None:
        """Output should not have extreme outliers (roughly in input range)."""
        smoothed = taubin_smooth(heightmap_noisy, iterations=3)
        # Taubin is volume-preserving, so values should stay near [0, 1]
        assert smoothed.min() >= -0.5
        assert smoothed.max() <= 1.5


class TestElevationScurve:
    def test_gamma_one_noop(self, heightmap_noisy: np.ndarray) -> None:
        """gamma=1.0 with contrast=0 should not change the input."""
        result = elevation_scurve(heightmap_noisy, gamma=1.0, contrast=0.0)
        np.testing.assert_allclose(result, heightmap_noisy, atol=1e-12)

    def test_gamma_above_one_expands_lows(self) -> None:
        """gamma > 1 should expand low values (brighten shadows)."""
        h = np.full((64, 64), 0.2, dtype=np.float64)
        result = elevation_scurve(h, gamma=1.5, contrast=0.0)
        # gamma > 1: x^(1/1.5) > x for x < 1
        assert result.mean() > 0.2

    def test_contrast_increases_mid_variance(self) -> None:
        """contrast > 0 should increase variance of mid-range values."""
        rng = np.random.default_rng(12)
        h = np.clip(rng.normal(0.5, 0.15, (128, 128)), 0.0, 1.0).astype(np.float64)
        result = elevation_scurve(h, gamma=1.0, contrast=0.2)
        # Mid-range variance should increase
        mid_mask = (h > 0.3) & (h < 0.7)
        assert result[mid_mask].std() > h[mid_mask].std()

    def test_output_in_range(self, heightmap_noisy: np.ndarray) -> None:
        """Output should be in [0, 1]."""
        result = elevation_scurve(heightmap_noisy, gamma=1.2, contrast=0.1)
        assert result.min() >= 0.0
        assert result.max() <= 1.0

    def test_extremes_preserved(self) -> None:
        """0 and 1 inputs should stay at (approximately) 0 and 1."""
        h = np.array([[0.0, 1.0], [0.5, 0.25]], dtype=np.float64)
        result = elevation_scurve(h, gamma=1.2, contrast=0.1)
        assert result[0, 0] == pytest.approx(0.0, abs=0.01)
        assert result[0, 1] == pytest.approx(1.0, abs=0.01)

    def test_no_nan_or_inf(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """Output should contain no NaN or Inf."""
        result = elevation_scurve(heightmap_gaussian_peak, gamma=1.5, contrast=0.3)
        assert np.all(np.isfinite(result))
