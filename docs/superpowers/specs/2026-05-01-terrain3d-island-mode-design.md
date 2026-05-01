# Terrain3D — Island Mode & Post-Processing Improvements

**Date:** 2026-05-01
**Status:** Approved
**Scope:** Terrain3D package

## Problem

Terrain3D generates realistic continental terrain via diffusion, but:

1. **No island mode** — output may be all-land, all-ocean, or mixed; no guarantee of an island shape suitable for game levels.
2. **Diffusion artifacts** — heightmaps can have staircase/stepping artifacts from the tiled generation process.
3. **Flat elevation distribution** — raw diffusion output often lacks contrast in mid-altitude slopes (encostas), producing terrain that looks flat or monotone.

## Solution

Post-processing chain applied after diffusion generation, before export. Pure numpy operations — no changes to vendored code (`vendor/`).

### Pipeline

```
Diffusion → heightmap bruto
  → normalize [0,1]
  → [if mode=island] island_falloff()    # Perlin-noise coast
  → [if smooth > 0]   taubin_smooth()    # remove artifacts
  → [if gamma/contrast] elevation_scurve() # contrast boost
  → re-normalize [0,1]
  → export (PNG + JSON)
```

## New Files

| File | Purpose |
|------|---------|
| `src/terrain3d/postprocess.py` | `island_falloff()`, `taubin_smooth()`, `elevation_scurve()` — pure numpy functions |
| `tests/test_postprocess.py` | Unit tests for each function + chain integration |

## Modified Files

| File | Changes |
|------|---------|
| `src/terrain3d/generator.py` | New fields in `TerrainConfig`; postprocess chain in `generate_terrain()` |
| `src/terrain3d/cli.py` | New CLI flags (`--mode`, `--island-*`, `--smooth-*`, `--elevation-*`) |
| `Shared/src/gamedev_shared/data/quality-profiles.yaml` | Terrain3D section gains postprocess params per tier |

## 1. Island Falloff — Organic Coastline

```python
def island_falloff(
    heightmap: np.ndarray,     # float64 [0,1]
    falloff: float = 0.35,     # base radius as fraction of half-size
    noise_scale: float = 0.15, # Perlin amplitude (±15% of half-size)
    noise_freq: float = 3.0,   # Perlin frequency
    seed: int = 0,
) -> np.ndarray:
```

### Algorithm

1. For each pixel (x, y), compute normalized distance `r` from center (0=center, 1=corner) and angle θ.
2. Modulate the falloff radius per-angle using Perlin noise: `r_modulated = falloff + perlin(θ * noise_freq, seed) * noise_scale`.
3. Compute mask via smoothstep: `mask = smoothstep(r_modulated - transition, r_modulated, r)` where `transition ≈ 0.15 * half_size`.
4. Apply: `result = heightmap * mask`.

**Effect:** Interior terrain preserved. Edges fade to 0 (ocean). Coastline varies organically — bays where Perlin dips below falloff, promontories where it rises above.

**Perlin implementation:** Uses `pyfastnoiselite` (already a dependency of Terrain3D).

### Quality-dependent defaults

| Quality | island_falloff | island_noise_scale | island_noise_freq |
|---------|---------------|-------------------|-------------------|
| fast | 0.35 | 0.12 | 2.5 |
| low | 0.35 | 0.14 | 2.8 |
| medium | 0.35 | 0.15 | 3.0 |
| high | 0.35 | 0.16 | 3.2 |
| highest | 0.35 | 0.18 | 3.5 |

## 2. Taubin Smoothing

```python
def taubin_smooth(
    heightmap: np.ndarray,    # float64 [0,1]
    iterations: int = 3,      # pairs of λ+μ steps
    lambda_val: float = 0.5,  # smoothing strength
    mu_val: float = -0.53,    # shrinkage compensation (negative)
) -> np.ndarray:
```

### Algorithm

Per iteration (Taubin 1995):

1. **Laplacian:** `L = conv2d(h, laplacian_kernel)` with reflect padding (via `scipy.ndimage.convolve`).
2. **λ-step:** `h = h + lambda_val * L` (smooth).
3. **Recalculate Laplacian.**
4. **μ-step:** `h = h + mu_val * L` (compensate shrinkage).

**Effect:** Removes high-frequency stepping artifacts from diffusion tiling. Preserves low-frequency terrain features (mountains, valleys). Volume-preserving — no terrain "shrinkage" unlike plain Laplacian smoothing.

`iterations=0` disables smoothing entirely.

## 3. Elevation S-Curve

```python
def elevation_scurve(
    heightmap: np.ndarray,    # float64 [0,1]
    gamma: float = 1.2,       # exponent (1.0=neutral, >1=expand lows)
    contrast: float = 0.1,    # sigmoid contrast (0=disabled)
) -> np.ndarray:
```

### Algorithm

Two composed effects:

1. **Gamma:** `h = h ** (1.0 / gamma)`. With `gamma=1.2`, expands low elevations slightly (more beach/plains resolution) without flattening peaks.
2. **Sigmoid contrast:** `h = sigmoid(k * (h - 0.5))` normalized back to [0,1]. With `contrast=0.1`, boosts contrast in mid-range (slopes/hillsides) while keeping flat areas stable.

**Effect:** More interesting vertical variation. Slopes are more pronounced. Flat areas (peaks, valley floors) stay stable.

### Quality-dependent defaults

| Quality | elevation_gamma | elevation_contrast |
|---------|----------------|-------------------|
| fast | 1.1 | 0.05 |
| low | 1.15 | 0.08 |
| medium | 1.2 | 0.1 |
| high | 1.2 | 0.12 |
| highest | 1.25 | 0.15 |

## 4. CLI Changes

### New flags for `terrain3d generate`

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--mode` | choice | `island` | `island` (falloff) or `continental` (no falloff) |
| `--island-falloff` | float | `0.35` | Base falloff radius (0.1–0.5) |
| `--island-noise-scale` | float | `0.15` | Perlin noise amplitude on coast |
| `--island-noise-freq` | float | `3.0` | Perlin noise frequency |
| `--smooth-iterations` | int | `3` | Taubin iterations (0=off) |
| `--elevation-gamma` | float | `1.2` | Gamma exponent (1.0=neutral) |
| `--elevation-contrast` | float | `0.1` | Sigmoid contrast (0=off) |

All postprocess parameters are subject to **QualityEngine soft resolution** — user CLI values override quality profile defaults.

### TerrainConfig new fields

```python
mode: str = "island"
island_falloff: float = 0.35
island_noise_scale: float = 0.15
island_noise_freq: float = 3.0
smooth_iterations: int = 3
elevation_gamma: float = 1.2
elevation_contrast: float = 0.1
```

## 5. Generator Integration

In `generate_terrain()`, after normalizing the raw diffusion output to [0,1]:

```python
if config.mode == "island":
    heightmap = postprocess.island_falloff(
        heightmap, config.island_falloff, config.island_noise_scale,
        config.island_noise_freq, config.seed,
    )

if config.smooth_iterations > 0:
    heightmap = postprocess.taubin_smooth(heightmap, config.smooth_iterations)

if config.elevation_gamma != 1.0 or config.elevation_contrast > 0:
    heightmap = postprocess.elevation_scurve(heightmap, config.elevation_gamma, config.elevation_contrast)

# Re-normalize after post-processing
h_min, h_max = heightmap.min(), heightmap.max()
if h_max > h_min:
    heightmap = (heightmap - h_min) / (h_max - h_min)
```

## 6. Quality Profiles

`Shared/src/gamedev_shared/data/quality-profiles.yaml` — Terrain3D section gains:

```yaml
terrain3d:
  # existing: size, world_size, coarse_window
  island_falloff: 0.35
  island_noise_scale: <varies by tier>
  island_noise_freq: <varies by tier>
  smooth_iterations: 3
  elevation_gamma: <varies by tier>
  elevation_contrast: <varies by tier>
```

## 7. Testing

### `tests/test_postprocess.py`

| Test class | Tests | Validates |
|------------|-------|-----------|
| `TestIslandFalloff` | 5-6 | Edges → 0; center preserved; symmetric with fixed seed; Perlin varies coast; smooth transition (no steps) |
| `TestTaubinSmooth` | 4-5 | Reduces high-freq noise; preserves broad features; flat heightmap unchanged; `iterations=0` is no-op |
| `TestElevationScurve` | 4-5 | `gamma=1.0` is no-op; `gamma>1` expands lows; `contrast>0` increases mid-range variation; output ∈ [0,1] |
| `TestPostprocessChain` | 3-4 | Full pipeline with default config; `mode=continental` skips falloff; final output ∈ [0,1] |

### Fixtures

- `heightmap_flat` — zeros/ones (degenerate case)
- `heightmap_gradient` — linear gradient
- `heightmap_noisy` — gradient + gaussian noise
- `heightmap_gaussian_peak` — gaussian peak at center

### Existing tests

`tests/test_export.py` — unchanged, continue to validate export layer.

## Constraints

- **No modifications to `vendor/`** — vendored terrain-diffusion code is excluded from lint and must not be changed.
- **Post-processing is optional** — every step can be disabled via CLI flags or config.
- **All postprocess functions are pure** — input ndarray → output ndarray, no side effects.
- **Backward compatible** — `mode=continental` with defaults produces the same output as before (except smoothing and elevation curve are now enabled by default).
