# Terrain3D Island Mode & Post-Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add island mode (default), Taubin smoothing, and elevation S-curve as post-processing to Terrain3D's diffusion pipeline.

**Architecture:** Post-processing chain applied after diffusion output, before export. New `postprocess.py` module with pure numpy functions. No changes to vendored code (`vendor/`).

**Tech Stack:** Python 3.10+, numpy, scipy (ndimage.convolve), pyfastnoiselite (Perlin noise for coast), pytest.

---

### Task 1: Create `postprocess.py` — `island_falloff()` (TDD)

**Files:**
- Create: `Terrain3D/src/terrain3d/postprocess.py`
- Create: `Terrain3D/tests/test_postprocess.py`

- [ ] **Step 1: Write the failing tests**

Create `Terrain3D/tests/test_postprocess.py`:

```python
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
        assert result[64, 64] > 0.1  # well inside

    def test_no_nan_or_inf(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """Output should contain no NaN or Inf."""
        result = island_falloff(heightmap_gaussian_peak, seed=0)
        assert np.all(np.isfinite(result))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Terrain3D && python -m pytest tests/test_postprocess.py::TestIslandFalloff -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'terrain3d.postprocess'`

- [ ] **Step 3: Implement `postprocess.py` with `island_falloff()`**

Create `Terrain3D/src/terrain3d/postprocess.py`:

```python
from __future__ import annotations

import numpy as np


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    """Hermite smoothstep interpolation between edge0 and edge1."""
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def island_falloff(
    heightmap: np.ndarray,
    falloff: float = 0.35,
    noise_scale: float = 0.15,
    noise_freq: float = 3.0,
    seed: int = 0,
) -> np.ndarray:
    """Apply island falloff with Perlin-noise organic coastline.

    Multiplies the heightmap by a smooth circular falloff mask whose radius
    is modulated by 1D Perlin noise sampled around the compass angle.  This
    produces an organic coastline with bays and promontories.

    Args:
        heightmap: 2D float64 array in [0, 1].
        falloff: Base radius as fraction of half the smallest dimension (0.1–0.5).
        noise_scale: Amplitude of Perlin modulation on the radius.
        noise_freq: Frequency of the Perlin noise around the circle.
        seed: Random seed for the Perlin noise.

    Returns:
        Heightmap with edges smoothly faded to 0 (ocean).
    """
    h, w = heightmap.shape
    cy, cx = h / 2.0, w / 2.0
    half = min(h, w) / 2.0

    # Distance from center, normalised so corner ≈ sqrt(2)
    y, x = np.mgrid[0:h, 0:w]
    dist = np.sqrt((y - cy) ** 2 + (x - cx) ** 2) / half

    # Angle per pixel
    angles = np.arctan2(y - cy, x - cx)  # [-π, π]

    # --- Perlin noise sampled around the unit circle ---
    # Generate 1D noise profile (N_SAMPLES points around 2π)
    noise_1d = _circular_perlin(seed, noise_freq)

    # Map each pixel's angle to a noise value
    angles_pos = (angles + 2.0 * np.pi) % (2.0 * np.pi)  # [0, 2π]
    noise_2d = _sample_circular_noise(noise_1d, angles_pos)

    # Modulated radius: base falloff + Perlin variation
    r_modulated = falloff + noise_2d * noise_scale

    # Transition zone width (beach / shallow water)
    transition = 0.12

    # Smooth mask: 1 inside island, 0 outside, smooth transition
    mask = _smoothstep(r_modulated - transition, r_modulated, dist)

    return (heightmap * mask).astype(np.float64)


def _circular_perlin(seed: int, noise_freq: float, n_samples: int = 1024) -> np.ndarray:
    """Sample Perlin noise around the unit circle and return 1D profile.

    Samples at n_samples evenly-spaced angles, returning values in [-1, 1].
    """
    from pyfastnoiselite import FastNoiseLite

    noise_gen = FastNoiseLite(seed)
    noise_gen.noise_type = FastNoiseLite.NoiseType.Perlin

    angles = np.linspace(0, 2.0 * np.pi, n_samples, endpoint=False)
    # Sample on a circle of radius=noise_freq in 2D Perlin space
    xs = np.cos(angles) * noise_freq
    ys = np.sin(angles) * noise_freq

    values = np.array([noise_gen.get_noise_2d(float(x), float(y)) for x, y in zip(xs, ys)])
    return values


def _sample_circular_noise(noise_1d: np.ndarray, angles: np.ndarray) -> np.ndarray:
    """Linearly interpolate 1D circular noise at arbitrary angles.

    Args:
        noise_1d: 1D noise profile (n_samples,), assumed to cover [0, 2π).
        angles: Array of angles in [0, 2π].

    Returns:
        Interpolated noise values matching the shape of *angles*.
    """
    n = len(noise_1d)
    idx_float = angles / (2.0 * np.pi) * n
    idx_floor = np.floor(idx_float).astype(int) % n
    idx_ceil = (idx_floor + 1) % n
    frac = idx_float - np.floor(idx_float)
    return noise_1d[idx_floor] * (1.0 - frac) + noise_1d[idx_ceil] * frac
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Terrain3D && python -m pytest tests/test_postprocess.py::TestIslandFalloff -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add Terrain3D/src/terrain3d/postprocess.py Terrain3D/tests/test_postprocess.py
git commit -m "feat(terrain3d): add island_falloff post-processing with organic coastline"
```

---

### Task 2: Add `taubin_smooth()` to `postprocess.py` (TDD)

**Files:**
- Modify: `Terrain3D/src/terrain3d/postprocess.py`
- Modify: `Terrain3D/tests/test_postprocess.py`

- [ ] **Step 1: Write the failing tests**

Append to `Terrain3D/tests/test_postprocess.py` (after existing code, add import):

```python
from terrain3d.postprocess import island_falloff, taubin_smooth
```

Add new test class:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Terrain3D && python -m pytest tests/test_postprocess.py::TestTaubinSmooth -v`
Expected: FAIL — `ImportError: cannot import name 'taubin_smooth'`

- [ ] **Step 3: Implement `taubin_smooth()`**

Append to `Terrain3D/src/terrain3d/postprocess.py`:

```python
def taubin_smooth(
    heightmap: np.ndarray,
    iterations: int = 3,
    lambda_val: float = 0.5,
    mu_val: float = -0.53,
) -> np.ndarray:
    """Taubin smoothing — removes high-frequency noise while preserving volume.

    Alternates λ-step (smooth) and μ-step (shrinkage compensation) per
    iteration.  Uses a discrete Laplacian kernel with reflect padding.

    Args:
        heightmap: 2D float64 array.
        iterations: Number of λ+μ iteration pairs (0 disables smoothing).
        lambda_val: Smoothing strength (0–1, typically 0.5).
        mu_val: Shrinkage compensation (negative, typically -0.53).

    Returns:
        Smoothed heightmap (same shape, float64).
    """
    if iterations <= 0:
        return heightmap.copy()

    from scipy.ndimage import convolve

    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
    h = heightmap.copy()

    for _ in range(iterations):
        # λ-step (smooth)
        laplacian = convolve(h, kernel, mode="reflect")
        h = h + lambda_val * laplacian

        # μ-step (compensate shrinkage)
        laplacian = convolve(h, kernel, mode="reflect")
        h = h + mu_val * laplacian

    return h.astype(np.float64)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Terrain3D && python -m pytest tests/test_postprocess.py::TestTaubinSmooth -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add Terrain3D/src/terrain3d/postprocess.py Terrain3D/tests/test_postprocess.py
git commit -m "feat(terrain3d): add Taubin smoothing post-processing"
```

---

### Task 3: Add `elevation_scurve()` to `postprocess.py` (TDD)

**Files:**
- Modify: `Terrain3D/src/terrain3d/postprocess.py`
- Modify: `Terrain3D/tests/test_postprocess.py`

- [ ] **Step 1: Write the failing tests**

Update import in `Terrain3D/tests/test_postprocess.py`:

```python
from terrain3d.postprocess import elevation_scurve, island_falloff, taubin_smooth
```

Add new test class:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Terrain3D && python -m pytest tests/test_postprocess.py::TestElevationScurve -v`
Expected: FAIL — `ImportError: cannot import name 'elevation_scurve'`

- [ ] **Step 3: Implement `elevation_scurve()`**

Append to `Terrain3D/src/terrain3d/postprocess.py`:

```python
def elevation_scurve(
    heightmap: np.ndarray,
    gamma: float = 1.2,
    contrast: float = 0.1,
) -> np.ndarray:
    """Apply gamma correction and sigmoid contrast enhancement.

    Gamma expands or compresses the elevation range.  The sigmoid S-curve
    boosts contrast in the mid-range (slopes/hillsides) while keeping
    flat areas stable.

    Args:
        heightmap: 2D float64 array in [0, 1].
        gamma: Exponent for gamma correction (1.0 = neutral, >1 = expand lows).
        contrast: Sigmoid contrast strength (0 = disabled, typical 0.05–0.2).

    Returns:
        Remapped heightmap in [0, 1], float64.
    """
    h = heightmap.copy()

    # --- Gamma correction ---
    if abs(gamma - 1.0) > 1e-9:
        h = np.power(np.clip(h, 1e-10, 1.0), 1.0 / gamma)

    # --- Sigmoid contrast ---
    if contrast > 1e-9:
        k = 1.0 + contrast * 20.0  # e.g. contrast=0.1 → k=3
        sigmoid = 1.0 / (1.0 + np.exp(-k * (h - 0.5)))
        # Normalise sigmoid so f(0)→0 and f(1)→1
        s_min = 1.0 / (1.0 + np.exp(k * 0.5))
        s_max = 1.0 / (1.0 + np.exp(-k * 0.5))
        if s_max - s_min > 1e-12:
            h = (sigmoid - s_min) / (s_max - s_min)
        else:
            h = np.full_like(h, 0.5)

    return np.clip(h, 0.0, 1.0).astype(np.float64)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Terrain3D && python -m pytest tests/test_postprocess.py::TestElevationScurve -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add Terrain3D/src/terrain3d/postprocess.py Terrain3D/tests/test_postprocess.py
git commit -m "feat(terrain3d): add elevation S-curve / gamma post-processing"
```

---

### Task 4: Postprocess chain integration test (TDD)

**Files:**
- Modify: `Terrain3D/tests/test_postprocess.py`

- [ ] **Step 1: Write the chain integration test**

Add import and test class to `Terrain3D/tests/test_postprocess.py`:

```python
from terrain3d.postprocess import apply_postprocess_chain


class TestPostprocessChain:
    def test_full_chain_island(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """Full chain with mode=island should produce output in [0, 1]."""
        result = apply_postprocess_chain(
            heightmap_gaussian_peak,
            mode="island",
            seed=0,
        )
        assert result.min() >= 0.0
        assert result.max() <= 1.0
        assert result.dtype == np.float64
        # Edges should be near zero (island falloff)
        assert result[0, 0] < 0.01

    def test_continental_skips_falloff(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """mode=continental should not apply island falloff."""
        result = apply_postprocess_chain(
            heightmap_gaussian_peak,
            mode="continental",
            seed=0,
        )
        # Center should still have high value
        assert result[128, 128] > 0.5
        # Edges should NOT be zeroed (no falloff)
        # Original gaussian peak has low edges anyway, so check it's > island mode edges
        result_island = apply_postprocess_chain(
            heightmap_gaussian_peak,
            mode="island",
            seed=0,
        )
        # Continental edges should be higher than island edges
        assert result[0, 0] >= result_island[0, 0]

    def test_chain_deterministic(self, heightmap_gaussian_peak: np.ndarray) -> None:
        """Same parameters should produce identical results."""
        r1 = apply_postprocess_chain(heightmap_gaussian_peak, mode="island", seed=42)
        r2 = apply_postprocess_chain(heightmap_gaussian_peak, mode="island", seed=42)
        np.testing.assert_array_equal(r1, r2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Terrain3D && python -m pytest tests/test_postprocess.py::TestPostprocessChain -v`
Expected: FAIL — `ImportError: cannot import name 'apply_postprocess_chain'`

- [ ] **Step 3: Implement `apply_postprocess_chain()`**

Append to `Terrain3D/src/terrain3d/postprocess.py`:

```python
def apply_postprocess_chain(
    heightmap: np.ndarray,
    mode: str = "island",
    seed: int = 0,
    island_falloff_radius: float = 0.35,
    island_noise_scale: float = 0.15,
    island_noise_freq: float = 3.0,
    smooth_iterations: int = 3,
    smooth_lambda: float = 0.5,
    smooth_mu: float = -0.53,
    elevation_gamma: float = 1.2,
    elevation_contrast: float = 0.1,
) -> np.ndarray:
    """Run the full post-processing chain on a heightmap.

    Pipeline: island_falloff → taubin_smooth → elevation_scurve → normalise.

    Args:
        heightmap: 2D float64 array in [0, 1].
        mode: ``"island"`` (apply falloff) or ``"continental"`` (skip falloff).
        seed: Random seed for island falloff Perlin noise.
        island_falloff_radius: Base radius for island falloff.
        island_noise_scale: Perlin amplitude for coast variation.
        island_noise_freq: Perlin frequency for coast variation.
        smooth_iterations: Taubin iterations (0 = disabled).
        smooth_lambda: Taubin λ parameter.
        smooth_mu: Taubin μ parameter.
        elevation_gamma: Gamma exponent (1.0 = neutral).
        elevation_contrast: Sigmoid contrast strength (0 = disabled).

    Returns:
        Post-processed heightmap in [0, 1], float64.
    """
    h = heightmap.copy()

    # 1. Island falloff
    if mode == "island":
        h = island_falloff(h, island_falloff_radius, island_noise_scale, island_noise_freq, seed)

    # 2. Taubin smoothing
    if smooth_iterations > 0:
        h = taubin_smooth(h, smooth_iterations, smooth_lambda, smooth_mu)

    # 3. Elevation S-curve
    if abs(elevation_gamma - 1.0) > 1e-9 or elevation_contrast > 1e-9:
        h = elevation_scurve(h, elevation_gamma, elevation_contrast)

    # 4. Re-normalise to [0, 1]
    h_min, h_max = float(h.min()), float(h.max())
    if h_max - h_min > 1e-12:
        h = (h - h_min) / (h_max - h_min)
    else:
        h = np.zeros_like(h, dtype=np.float64)

    return h.astype(np.float64)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Terrain3D && python -m pytest tests/test_postprocess.py -v`
Expected: All PASS (all test classes)

- [ ] **Step 5: Commit**

```bash
git add Terrain3D/src/terrain3d/postprocess.py Terrain3D/tests/test_postprocess.py
git commit -m "feat(terrain3d): add postprocess chain integration"
```

---

### Task 5: Update `TerrainConfig` and `generate_terrain()`

**Files:**
- Modify: `Terrain3D/src/terrain3d/generator.py`

- [ ] **Step 1: Add new fields to `TerrainConfig`**

In `Terrain3D/src/terrain3d/generator.py`, update the `TerrainConfig` dataclass (lines 18–32).

Replace the `TerrainConfig` class with:

```python
@dataclass
class TerrainConfig:
    """Configuration for terrain generation."""

    model_id: str = ""  # resolved lazily via _resolve_model_id
    seed: int | None = None
    size: int = 2048
    world_size: float = 512.0
    max_height: float = 50.0
    device: str | None = None
    num_inference_steps: int = 20
    dtype: str | None = None  # "fp32", "bf16", "fp16"
    cache_size: str = "100M"
    coarse_window: int = 4  # number of coarse tiles to generate (each ~7.7km for 30m model)
    prompt: str | None = None  # stored as metadata only (model is unconditional)
    # --- Post-processing ---
    mode: str = "island"  # "island" | "continental"
    island_falloff: float = 0.35
    island_noise_scale: float = 0.15
    island_noise_freq: float = 3.0
    smooth_iterations: int = 3
    elevation_gamma: float = 1.2
    elevation_contrast: float = 0.1
```

- [ ] **Step 2: Update `generate_terrain()` to call postprocess chain**

In `Terrain3D/src/terrain3d/generator.py`, after the existing normalisation block (lines 126–133), add the postprocess chain. Add import at the top and modify `generate_terrain()`.

Add import after the existing imports:

```python
from terrain3d.postprocess import apply_postprocess_chain
```

In `generate_terrain()`, replace lines 126–133 (the normalisation block) with:

```python
        # Convert to float64 numpy and normalise to 0-1
        heightmap = elev.cpu().numpy().astype(np.float64)
        h_min = float(heightmap.min())
        h_max = float(heightmap.max())
        if h_max - h_min > 1e-12:
            heightmap = (heightmap - h_min) / (h_max - h_min)
        else:
            heightmap = np.zeros_like(heightmap, dtype=np.float64)

        # --- Post-processing chain ---
        heightmap = apply_postprocess_chain(
            heightmap,
            mode=config.mode,
            seed=config.seed if config.seed is not None else 0,
            island_falloff_radius=config.island_falloff,
            island_noise_scale=config.island_noise_scale,
            island_noise_freq=config.island_noise_freq,
            smooth_iterations=config.smooth_iterations,
            elevation_gamma=config.elevation_gamma,
            elevation_contrast=config.elevation_contrast,
        )
```

- [ ] **Step 3: Run existing tests to verify nothing is broken**

Run: `cd Terrain3D && python -m pytest tests/test_export.py -v`
Expected: All PASS (export tests don't run generate_terrain)

- [ ] **Step 4: Run all tests**

Run: `cd Terrain3D && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add Terrain3D/src/terrain3d/generator.py
git commit -m "feat(terrain3d): integrate postprocess chain into generate_terrain"
```

---

### Task 6: Update CLI with new flags

**Files:**
- Modify: `Terrain3D/src/terrain3d/cli.py`

- [ ] **Step 1: Add new CLI options**

In `Terrain3D/src/terrain3d/cli.py`, add new options to the `generate_cmd` decorator chain (after the `--coarse-window` option at line 70, before `--quiet` at line 71).

Insert after line 70 (`--coarse-window`):

```python
@click.option(
    "--mode",
    type=click.Choice(["island", "continental"]),
    default="island",
    show_default=True,
    help="Terrain mode: island (falloff) or continental (raw)",
)
@click.option(
    "--island-falloff",
    type=float,
    default=0.35,
    show_default=True,
    help="Island falloff radius (0.1-0.5)",
)
@click.option(
    "--island-noise-scale",
    type=float,
    default=0.15,
    show_default=True,
    help="Perlin noise amplitude for coast variation",
)
@click.option(
    "--island-noise-freq",
    type=float,
    default=3.0,
    show_default=True,
    help="Perlin noise frequency for coast variation",
)
@click.option(
    "--smooth-iterations",
    type=int,
    default=3,
    show_default=True,
    help="Taubin smoothing iterations (0=off)",
)
@click.option(
    "--elevation-gamma",
    type=float,
    default=1.2,
    show_default=True,
    help="Gamma exponent for elevation (1.0=neutral)",
)
@click.option(
    "--elevation-contrast",
    type=float,
    default=0.1,
    show_default=True,
    help="Sigmoid contrast for elevation (0=off)",
)
```

- [ ] **Step 2: Update function signature**

Update `generate_cmd` function signature to include new parameters (after `coarse_window`, before `quiet`):

```python
def generate_cmd(
    prompt: str | None,
    seed: int | None,
    output: str,
    metadata_path: str,
    size: int,
    world_size: float,
    max_height: float,
    quality: str,
    device: str | None,
    dtype: str,
    cache_size: str,
    coarse_window: int,
    mode: str,
    island_falloff: float,
    island_noise_scale: float,
    island_noise_freq: float,
    smooth_iterations: int,
    elevation_gamma: float,
    elevation_contrast: float,
    quiet: bool,
) -> None:
```

- [ ] **Step 3: Update QualityEngine soft resolution**

After the existing QualityEngine block (lines 98–110), add soft resolution for the new postprocess parameters. Insert after the `coarse_window` resolution and before the `if seed is None:` block:

```python
    _user_set_mode = ctx.get_parameter_source("mode") != ParameterSource.DEFAULT
    _user_set_island_falloff = ctx.get_parameter_source("island_falloff") != ParameterSource.DEFAULT
    _user_set_island_noise_scale = ctx.get_parameter_source("island_noise_scale") != ParameterSource.DEFAULT
    _user_set_island_noise_freq = ctx.get_parameter_source("island_noise_freq") != ParameterSource.DEFAULT
    _user_set_smooth_iterations = ctx.get_parameter_source("smooth_iterations") != ParameterSource.DEFAULT
    _user_set_elevation_gamma = ctx.get_parameter_source("elevation_gamma") != ParameterSource.DEFAULT
    _user_set_elevation_contrast = ctx.get_parameter_source("elevation_contrast") != ParameterSource.DEFAULT
```

Inside the `try` block, after the `coarse_window` resolution line (line 108), add:

```python
        if not _user_set_mode and "mode" in _qresolved.params:
            mode = _qresolved.params["mode"]
        if not _user_set_island_falloff and "island_falloff" in _qresolved.params:
            island_falloff = _qresolved.params["island_falloff"]
        if not _user_set_island_noise_scale and "island_noise_scale" in _qresolved.params:
            island_noise_scale = _qresolved.params["island_noise_scale"]
        if not _user_set_island_noise_freq and "island_noise_freq" in _qresolved.params:
            island_noise_freq = _qresolved.params["island_noise_freq"]
        if not _user_set_smooth_iterations and "smooth_iterations" in _qresolved.params:
            smooth_iterations = _qresolved.params["smooth_iterations"]
        if not _user_set_elevation_gamma and "elevation_gamma" in _qresolved.params:
            elevation_gamma = _qresolved.params["elevation_gamma"]
        if not _user_set_elevation_contrast and "elevation_contrast" in _qresolved.params:
            elevation_contrast = _qresolved.params["elevation_contrast"]
```

- [ ] **Step 4: Update TerrainConfig construction**

Update the `config = TerrainConfig(...)` call (lines 117–127) to include new fields:

```python
    config = TerrainConfig(
        seed=seed,
        size=size,
        world_size=world_size,
        max_height=max_height,
        device=device,
        dtype=dtype if dtype != "fp32" else None,
        cache_size=cache_size,
        coarse_window=coarse_window,
        prompt=prompt,
        mode=mode,
        island_falloff=island_falloff,
        island_noise_scale=island_noise_scale,
        island_noise_freq=island_noise_freq,
        smooth_iterations=smooth_iterations,
        elevation_gamma=elevation_gamma,
        elevation_contrast=elevation_contrast,
    )
```

- [ ] **Step 5: Update summary table**

After the existing `table.add_row("Height std", ...)` line (around line 163), add:

```python
    table.add_row("Mode", mode)
```

- [ ] **Step 6: Verify CLI loads**

Run: `cd Terrain3D && python -m terrain3d generate --help`
Expected: Help text shows all new options with correct defaults

- [ ] **Step 7: Commit**

```bash
git add Terrain3D/src/terrain3d/cli.py
git commit -m "feat(terrain3d): add postprocess CLI flags with QualityEngine integration"
```

---

### Task 7: Update quality profiles

**Files:**
- Modify: `Shared/src/gamedev_shared/data/quality-profiles.yaml`

- [ ] **Step 1: Add postprocess params to each terrain3d section**

For each tier in `quality-profiles.yaml`, add the postprocess parameters to the `terrain3d:` section. The existing params (`size`, `world_size`, `coarse_window`) stay; new params are appended.

**fast tier** (after `coarse_window: 2` at line 58):

```yaml
    terrain3d:
      size: 512
      world_size: 256.0
      coarse_window: 2
      island_noise_scale: 0.12
      island_noise_freq: 2.5
      smooth_iterations: 2
      elevation_gamma: 1.1
      elevation_contrast: 0.05
```

**low tier** (after `coarse_window: 3` at line 108):

```yaml
    terrain3d:
      size: 1024
      world_size: 256.0
      coarse_window: 3
      island_noise_scale: 0.14
      island_noise_freq: 2.8
      smooth_iterations: 3
      elevation_gamma: 1.15
      elevation_contrast: 0.08
```

**medium tier** (after `coarse_window: 4` at line 158):

```yaml
    terrain3d:
      size: 2048
      world_size: 512.0
      coarse_window: 4
      island_noise_scale: 0.15
      island_noise_freq: 3.0
      smooth_iterations: 3
      elevation_gamma: 1.2
      elevation_contrast: 0.1
```

**high tier** (after `coarse_window: 6` at line 208):

```yaml
    terrain3d:
      size: 4096
      world_size: 512.0
      coarse_window: 6
      island_noise_scale: 0.16
      island_noise_freq: 3.2
      smooth_iterations: 4
      elevation_gamma: 1.2
      elevation_contrast: 0.12
```

**highest tier** (after `coarse_window: 8` at line 258):

```yaml
    terrain3d:
      size: 4096
      world_size: 1024.0
      coarse_window: 8
      island_noise_scale: 0.18
      island_noise_freq: 3.5
      smooth_iterations: 5
      elevation_gamma: 1.25
      elevation_contrast: 0.15
```

- [ ] **Step 2: Commit**

```bash
git add Shared/src/gamedev_shared/data/quality-profiles.yaml
git commit -m "feat(terrain3d): add postprocess params to quality profiles"
```

---

### Task 8: Final verification

- [ ] **Step 1: Run all Terrain3D tests**

Run: `cd Terrain3D && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Run lint**

Run: `ruff check Terrain3D/src/terrain3d/postprocess.py Terrain3D/src/terrain3d/generator.py Terrain3D/src/terrain3d/cli.py Terrain3D/tests/test_postprocess.py`
Expected: No errors

- [ ] **Step 3: Run format check**

Run: `ruff format --check Terrain3D/src/terrain3d/postprocess.py Terrain3D/src/terrain3d/generator.py Terrain3D/src/terrain3d/cli.py Terrain3D/tests/test_postprocess.py`
Expected: All files formatted

- [ ] **Step 4: Fix lint/format issues if any**

Run: `ruff check --fix Terrain3D/src/terrain3d/postprocess.py Terrain3D/src/terrain3d/generator.py Terrain3D/src/terrain3d/cli.py Terrain3D/tests/test_postprocess.py && ruff format Terrain3D/src/terrain3d/postprocess.py Terrain3D/src/terrain3d/generator.py Terrain3D/src/terrain3d/cli.py Terrain3D/tests/test_postprocess.py`

- [ ] **Step 5: Run full check**

Run: `make check`
Expected: All checks pass

- [ ] **Step 6: Commit any fixes**

```bash
git add -A
git commit -m "style: fix lint/format after terrain3d postprocess implementation"
```
