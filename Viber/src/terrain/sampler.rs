//! CPU height sampling — the single source of truth for terrain shape.
//!
//! Ported from the VibeGame `HeightSampler` (`height-sampler.ts`): mesh,
//! colliders, pads and gameplay queries all read the same grid, and any
//! mutation bumps [`HeightSampler::revision`] so caches can invalidate
//! cheaply (VibeGame `getGroundRevision`).
//!
//! Interpolation modes (VibeGame `height-smoothing`):
//! * `0.0` — bilinear taps. C0 but not C1: the derivative jumps at every
//!   texel boundary, so slopes read as a grid of flat facets.
//! * `1.0` — **monotone** Catmull-Rom (`monotone()` in `height-sampler.ts`):
//!   C1 cubic Hermite clamped to the span of the two central samples, so a
//!   step (road cut, pad edge) never rings into a lip. Applied separably
//!   over the 4x4 texel neighbourhood, borders clamped (one-sided fit).
//! * in between — linear blend of the two.

use bevy::math::Vec2;
use bevy::math::Vec3;

use super::mesh::HeightField;
use super::spec::TerrainPadSpec;

/// Bilinear-stencil reach in texel steps (VibeGame `TEXEL_INFLUENCE_REACH`
/// = `SQRT2`): the full diagonal of the 2x2 reconstruction stencil.
const TEXEL_INFLUENCE_REACH: f32 = core::f32::consts::SQRT_2;
/// Minimum effective falloff in texel steps (VibeGame `minEffectiveFalloff`
/// default `minTexels = 1.5`) — a brush narrower than the texel aliases away.
const MIN_FALLOFF_TEXELS: f32 = 1.5;

/// Resolved pad: the declarative spec with the auto height filled in.
#[derive(Debug, Clone, PartialEq)]
pub struct ResolvedPad {
    pub at: Vec2,
    pub size: Vec2,
    pub falloff: f32,
    pub corner_radius: f32,
    /// Target height in meters (auto pads have it sampled from the center).
    pub height: f32,
}

/// Smoothed heightfield over a `u16` grid (row-major, `[z * width + x]`).
///
/// Heights in the grid are raw `u16`; world height is
/// `(raw / 65535) * max_height`. World XZ `(-world_size/2, +world_size/2)`
/// maps to the grid; positions outside are clamped.
#[derive(Debug, Clone, PartialEq)]
pub struct HeightSampler {
    grid: Vec<u16>,
    width: usize,
    depth: usize,
    world_size: f32,
    max_height: f32,
    /// 0.0 = bilinear, 1.0 = monotone Catmull-Rom; values in between lerp.
    smoothing: f32,
    /// Bumped on every mutation (pads, reloads) — cache invalidation counter.
    revision: u64,
}

impl HeightSampler {
    /// Builds a sampler from a raw `u16` grid.
    ///
    /// # Errors
    /// Fails when the grid is empty, not `width * depth`, or dimensions/metrics are not finite and positive.
    pub fn from_grid(
        grid: Vec<u16>,
        width: usize,
        depth: usize,
        world_size: f32,
        max_height: f32,
        smoothing: f32,
    ) -> anyhow::Result<Self> {
        let Some(area) = width.checked_mul(depth) else {
            anyhow::bail!("terrain grid dimensions {width} x {depth} overflow usize");
        };
        if area == 0 {
            anyhow::bail!("terrain grid must have at least 1 sample (got {width} x {depth})");
        }
        if grid.len() != area {
            anyhow::bail!(
                "terrain grid length {} does not match width * depth ({width} * {depth} = {area})",
                grid.len()
            );
        }
        if !world_size.is_finite() || world_size <= 0.0 {
            anyhow::bail!("terrain world_size must be finite and > 0, got {world_size}");
        }
        if !max_height.is_finite() || max_height <= 0.0 {
            anyhow::bail!("terrain max_height must be finite and > 0, got {max_height}");
        }
        let smoothing = if smoothing.is_finite() {
            smoothing.clamp(0.0, 1.0)
        } else {
            0.0
        };
        Ok(Self {
            grid,
            width,
            depth,
            world_size,
            max_height,
            smoothing,
            revision: 0,
        })
    }

    /// Grid width (samples along X).
    pub fn width(&self) -> usize {
        self.width
    }

    /// Grid depth (samples along Z).
    pub fn depth(&self) -> usize {
        self.depth
    }

    /// World span on X and Z (meters).
    pub fn world_size(&self) -> f32 {
        self.world_size
    }

    /// Peak height (meters) of a fully-white sample.
    pub fn max_height(&self) -> f32 {
        self.max_height
    }

    /// Height of a raw grid cell (meters).
    pub fn cell_height(&self, x: usize, z: usize) -> f32 {
        Self::raw_to_height(self.raw(x, z), self.max_height)
    }

    /// Cache invalidation counter; bumped on every mutation.
    pub fn revision(&self) -> u64 {
        self.revision
    }

    /// Applies pads in order and returns the resolved pads (auto heights
    /// filled in). The flattening algorithm is ported from VibeGame
    /// `flatten.ts`: rounded-rect SDF core kept exactly flat, smoothstep
    /// falloff ring, and a first-falloff-texel guard that clamps the bilinear
    /// stencil back to the pad plane (prevents the stencil "lip").
    ///
    /// Auto pads (`height == None`) sample the terrain at the pad center
    /// **before** that pad stamps, so structures anchored to the pad agree
    /// with the ground (VibeGame `TerrainPadApplySystem`).
    ///
    /// Bumps [`Self::revision`] once per pad that changed any texel.
    pub fn apply_pads(&mut self, pads: &[TerrainPadSpec]) -> Vec<ResolvedPad> {
        let mut resolved = Vec::with_capacity(pads.len());
        for pad in pads {
            let target = pad
                .height
                .unwrap_or_else(|| self.sample(pad.at.x, pad.at.y));
            let changed =
                self.flatten_rect(pad.at, pad.size, target, pad.falloff, pad.corner_radius);
            resolved.push(ResolvedPad {
                at: pad.at,
                size: pad.size,
                falloff: pad.falloff,
                corner_radius: pad.corner_radius,
                height: target,
            });
            if changed {
                self.revision += 1;
            }
        }
        resolved
    }

    /// Levels a rounded-rect pad into the grid in place. Returns `true` when
    /// at least one texel changed (VibeGame `flattenRect`).
    ///
    /// Distance metric is the signed distance to the rounded-rect core
    /// (flatten.ts formulation): `d = length(max(|p - center| - core_half, 0))
    /// - corner_radius`. Inside the core (`d <= 0`) the terrain is set exactly
    /// to the target (raising hollows and shaving bumps); a smoothstep ring of
    /// width `falloff` blends back into the untouched terrain.
    fn flatten_rect(
        &mut self,
        center: Vec2,
        size: Vec2,
        target_y: f32,
        falloff: f32,
        corner_radius: f32,
    ) -> bool {
        if self.width < 2 || self.depth < 2 || !center.is_finite() {
            return false;
        }
        let half_world = self.world_size / 2.0;
        let step_x = self.world_size / (self.width - 1) as f32;
        let step_z = self.world_size / (self.depth - 1) as f32;
        let texel_step = step_x.min(step_z);

        let half_x = (size.x * 0.5).max(0.0);
        let half_z = (size.y * 0.5).max(0.0);
        let cr = corner_radius.max(0.0).min(half_x).min(half_z);
        let core_x = (half_x - cr).max(0.01);
        let core_z = (half_z - cr).max(0.01);
        // Falloff clamped to the sampler resolution: a pad whose falloff is
        // narrower than the texel step would alias into a hard stamp (VibeGame
        // `minEffectiveFalloff`).
        let fall = falloff.max(0.01).max(texel_step * MIN_FALLOFF_TEXELS);
        // Quanto o stencil bilinear de um texel invade em direção ao core.
        let influence_reach = texel_step * TEXEL_INFLUENCE_REACH;
        // Blend target clamped to the valid height range first (the VibeGame
        // brush normalizes targetY/maxHeight into [0,1] before blending).
        let target = if target_y.is_finite() {
            target_y.clamp(0.0, self.max_height)
        } else {
            0.0
        };

        // Inclusive texel index range of the pad AABB expanded by falloff and
        // by 1 texel so corners never fall outside by floor/ceil rounding
        // (VibeGame `texelIndexRange`).
        let to_index = |world: f32, step: f32| ((world + half_world) / step).floor() as i64;
        let reach_x = half_x + fall;
        let reach_z = half_z + fall;
        let x_lo = (to_index(center.x - reach_x, step_x) - 1).max(0);
        let x_hi = (to_index(center.x + reach_x, step_x) + 1).min(self.width as i64 - 1);
        let z_lo = (to_index(center.y - reach_z, step_z) - 1).max(0);
        let z_hi = (to_index(center.y + reach_z, step_z) + 1).min(self.depth as i64 - 1);

        let mut changed = false;
        for zi in z_lo..=z_hi {
            let wz = zi as f32 * step_z - half_world;
            let dz_ring = (wz - center.y).abs() - core_z;
            for xi in x_lo..=x_hi {
                let wx = xi as f32 * step_x - half_world;
                // Signed distance to the rounded-rect core: <= 0 inside.
                let dx = ((wx - center.x).abs() - core_x).max(0.0);
                let dz = dz_ring.max(0.0);
                let d = dx.hypot(dz) - cr;
                if d >= fall {
                    continue;
                }
                // Blend weight: 1 in the core, smoothstep down to 0 at the
                // falloff edge.
                let weight = if d > 0.0 {
                    let t = d / fall;
                    1.0 - t * t * (3.0 - 2.0 * t)
                } else {
                    1.0
                };

                let idx = zi as usize * self.width + xi as usize;
                let cur = Self::raw_to_height(self.grid[idx], self.max_height);
                let mut next = cur + (target - cur) * weight.min(1.0);
                // Cell-aware core clamp: the first falloff texels hold
                // `natural + (plane - natural) * w`, and their bilinear stencil
                // reaches into the core — a one-texel lip at the pad edge.
                // Clamp them exactly to the pad plane: cuts can't leave a lip
                // above it and fills can't leave a dip below it (VibeGame
                // `guardAt`, applied in both directions).
                if d > 0.0 && d < influence_reach {
                    if target < cur {
                        next = next.min(target);
                    } else if target > cur {
                        next = next.max(target);
                    }
                }

                let raw = Self::height_to_raw(next, self.max_height);
                if raw != self.grid[idx] {
                    self.grid[idx] = raw;
                    changed = true;
                }
            }
        }
        changed
    }

    /// Raw grid value with a panic-safe clamp on both axes.
    fn raw(&self, x: usize, z: usize) -> u16 {
        if self.grid.is_empty() {
            return 0;
        }
        let x = x.min(self.width - 1);
        let z = z.min(self.depth - 1);
        self.grid[z * self.width + x]
    }

    /// Raw `u16` to world meters.
    fn raw_to_height(raw: u16, max_height: f32) -> f32 {
        raw as f32 / u16::MAX as f32 * max_height
    }

    /// World meters to raw `u16` (rounded, clamped to the `u16` span).
    fn height_to_raw(height: f32, max_height: f32) -> u16 {
        let raw = height / max_height * u16::MAX as f32;
        if raw.is_nan() {
            return 0;
        }
        raw.round().clamp(0.0, u16::MAX as f32) as u16
    }

    /// Grid coordinate along one axis: `(world + world_size/2) / world_size *
    /// (n - 1)`, clamped to `[0, n - 1]` (positions outside the world clamp
    /// to the border; NaN falls back to the minimum edge).
    fn world_to_grid(world: f32, world_size: f32, n: usize) -> f32 {
        let max = (n - 1) as f32;
        if max <= 0.0 {
            return 0.0;
        }
        let g = (world + world_size / 2.0) / world_size * max;
        if g.is_nan() { 0.0 } else { g.clamp(0.0, max) }
    }

    /// Bilinear blend of the 4 texels around a fractional grid position.
    fn bilinear(&self, x0: usize, z0: usize, fx: f32, fz: f32) -> f32 {
        let x1 = (x0 + 1).min(self.width - 1);
        let z1 = (z0 + 1).min(self.depth - 1);
        let h00 = self.cell_height(x0, z0);
        let h10 = self.cell_height(x1, z0);
        let h01 = self.cell_height(x0, z1);
        let h11 = self.cell_height(x1, z1);
        h00 * (1.0 - fx) * (1.0 - fz)
            + h10 * fx * (1.0 - fz)
            + h01 * (1.0 - fx) * fz
            + h11 * fx * fz
    }

    /// Separable monotone Catmull-Rom over the clamped 4x4 neighbourhood
    /// (VibeGame `sampleCatmullRom`): edge texels degrade to a one-sided fit
    /// instead of wrapping onto the far side of the map.
    fn catmull(&self, x0: usize, z0: usize, fx: f32, fz: f32) -> f32 {
        let xm = x0.saturating_sub(1).min(self.width - 1);
        let x1 = (x0 + 1).min(self.width - 1);
        let x2 = (x0 + 2).min(self.width - 1);
        let zm = z0.saturating_sub(1).min(self.depth - 1);
        let z1 = (z0 + 1).min(self.depth - 1);
        let z2 = (z0 + 2).min(self.depth - 1);
        let mut rows = [0.0f32; 4];
        for (k, z) in [zm, z0, z1, z2].into_iter().enumerate() {
            rows[k] = monotone(
                self.cell_height(xm, z),
                self.cell_height(x0, z),
                self.cell_height(x1, z),
                self.cell_height(x2, z),
                fx,
            );
        }
        monotone(rows[0], rows[1], rows[2], rows[3], fz)
    }

    /// Shortest texel step in meters (min over both axes).
    fn texel_step(&self) -> f32 {
        let axis = |n: usize| {
            if n > 1 {
                self.world_size / (n - 1) as f32
            } else {
                self.world_size
            }
        };
        axis(self.width).min(axis(self.depth))
    }
}

/// Catmull-Rom weight blend of four consecutive samples at `t` in [0,1).
fn cubic(p0: f32, p1: f32, p2: f32, p3: f32, t: f32) -> f32 {
    let a = 2.0 * p1;
    let b = p2 - p0;
    let c = 2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3;
    let d = -p0 + 3.0 * p1 - 3.0 * p2 + p3;
    0.5 * (a + b * t + c * t * t + d * t * t * t)
}

/// Catmull-Rom clamped to the span of the two central samples.
///
/// The unclamped fit overshoots at a step, and terrain steps are not rare —
/// every road cut, pad edge and lake carve stamps one deliberately. Inside
/// smooth terrain the cubic already lands between p1 and p2, so the clamp
/// costs nothing there and only bites where the data is a cliff (ported from
/// VibeGame `height-sampler.ts::monotone`).
fn monotone(p0: f32, p1: f32, p2: f32, p3: f32, t: f32) -> f32 {
    let v = cubic(p0, p1, p2, p3, t);
    let lo = p1.min(p2);
    let hi = p1.max(p2);
    v.clamp(lo, hi)
}

impl HeightField for HeightSampler {
    fn sample(&self, world_x: f32, world_z: f32) -> f32 {
        let gx = Self::world_to_grid(world_x, self.world_size, self.width);
        let gz = Self::world_to_grid(world_z, self.world_size, self.depth);
        let x0 = gx.floor() as usize;
        let z0 = gz.floor() as usize;
        let fx = gx - x0 as f32;
        let fz = gz - z0 as f32;
        let bilinear = self.bilinear(x0, z0, fx, fz);
        if self.smoothing <= 0.0 {
            return bilinear;
        }
        let smooth = self.catmull(x0, z0, fx, fz);
        if self.smoothing >= 1.0 {
            smooth
        } else {
            bilinear + (smooth - bilinear) * self.smoothing
        }
    }

    fn sample_normal(&self, world_x: f32, world_z: f32, epsilon: f32) -> Vec3 {
        // Callers pass a terrain-wide constant epsilon; degenerate values fall
        // back to one texel step so the result stays a valid unit-ish normal.
        let e = if epsilon.is_finite() && epsilon > 0.0 {
            epsilon
        } else {
            self.texel_step().max(1e-3)
        };
        let hx0 = self.sample(world_x - e, world_z);
        let hx1 = self.sample(world_x + e, world_z);
        let hz0 = self.sample(world_x, world_z - e);
        let hz1 = self.sample(world_x, world_z + e);
        Vec3::new(hx0 - hx1, 2.0 * e, hz0 - hz1).normalize_or_zero()
    }

    fn max_height(&self) -> f32 {
        self.max_height
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Max height chosen so 1 raw unit == 0.001 m: authored raw values map to
    /// round meters (`raw 1000 -> ~1 m`) and quantization error stays tiny.
    const MAX_H: f32 = 65.535;
    const WORLD: f32 = 16.0;
    /// Ramps use a grid whose texel step is exactly 1 m (16 texels, 15 m).
    const RAMP_WORLD: f32 = 15.0;
    /// Quantization error bound for assertions (1 raw unit = 1 mm, slack 10x).
    const Q_EPS: f32 = 0.01;

    fn sampler(raw: Vec<u16>, width: usize, depth: usize, smoothing: f32) -> HeightSampler {
        HeightSampler::from_grid(raw, width, depth, WORLD, MAX_H, smoothing)
            .expect("test grids are valid")
    }

    /// Builds a row-major grid (`[z][x]`) from a per-column value function.
    fn grid_from_fn(width: usize, depth: usize, value: impl Fn(usize) -> u16) -> Vec<u16> {
        let mut raw = vec![0u16; width * depth];
        for z in 0..depth {
            for x in 0..width {
                raw[z * width + x] = value(x);
            }
        }
        raw
    }

    /// 1 m-per-texel ramp along +x (`raw x = 1000 * x`), 2 rows deep.
    fn ramp_sampler(smoothing: f32) -> HeightSampler {
        let raw = grid_from_fn(16, 2, |x| (x * 1000) as u16);
        HeightSampler::from_grid(raw, 16, 2, RAMP_WORLD, MAX_H, smoothing)
            .expect("ramp grid is valid")
    }

    /// Step along x: 10 m plateau (`raw 10000`) then 40 m plateau (`raw 40000`),
    /// boundary after texel `width / 2 - 1`.
    fn step_grid(width: usize, depth: usize) -> Vec<u16> {
        grid_from_fn(
            width,
            depth,
            |x| if x < width / 2 { 10_000 } else { 40_000 },
        )
    }

    fn flat_grid(width: usize, depth: usize, raw: u16) -> Vec<u16> {
        vec![raw; width * depth]
    }

    #[test]
    fn test_from_grid_rejects_wrong_length() {
        let result = HeightSampler::from_grid(vec![0; 3], 2, 2, WORLD, MAX_H, 1.0);
        assert!(result.is_err(), "3 samples for a 2x2 grid must be rejected");
    }

    #[test]
    fn test_from_grid_rejects_empty_grid() {
        assert!(HeightSampler::from_grid(Vec::new(), 0, 0, WORLD, MAX_H, 1.0).is_err());
        assert!(HeightSampler::from_grid(Vec::new(), 2, 2, WORLD, MAX_H, 1.0).is_err());
    }

    #[test]
    fn test_from_grid_rejects_non_positive_world_size() {
        assert!(HeightSampler::from_grid(vec![0], 1, 1, 0.0, MAX_H, 1.0).is_err());
        assert!(HeightSampler::from_grid(vec![0], 1, 1, -1.0, MAX_H, 1.0).is_err());
    }

    #[test]
    fn test_from_grid_rejects_non_finite_metrics() {
        assert!(HeightSampler::from_grid(vec![0], 1, 1, f32::NAN, MAX_H, 1.0).is_err());
        assert!(HeightSampler::from_grid(vec![0], 1, 1, f32::INFINITY, MAX_H, 1.0).is_err());
        assert!(HeightSampler::from_grid(vec![0], 1, 1, WORLD, 0.0, 1.0).is_err());
        assert!(HeightSampler::from_grid(vec![0], 1, 1, WORLD, f32::NEG_INFINITY, 1.0).is_err());
    }

    #[test]
    fn test_from_grid_clamps_smoothing_out_of_range() {
        let grid = step_grid(16, 4);
        let clamped = sampler(grid.clone(), 16, 4, 5.0);
        let exact = sampler(grid, 16, 4, 1.0);
        for i in 0..64 {
            let (x, z) = ((i % 16) as f32 - 8.0 + 0.5, (i / 16) as f32 - 2.0 + 0.5);
            let a = HeightField::sample(&clamped, x, z);
            let b = HeightField::sample(&exact, x, z);
            assert!((a - b).abs() < 1e-4, "smoothing 5.0 must clamp to 1.0");
            // Monotone fit never leaves the [low, high] span of the step.
            assert!((10.0 - Q_EPS..=40.0 + Q_EPS).contains(&a), "no overshoot: {a}");
        }
    }

    #[test]
    fn test_from_grid_revision_starts_at_zero() {
        let s = sampler(flat_grid(2, 2, 100), 2, 2, 1.0);
        assert_eq!(s.revision(), 0);
    }

    #[test]
    fn test_cell_height_scales_raw_to_meters() {
        let s = sampler(vec![0, 1000, 32768, 65535], 2, 2, 0.0);
        assert_eq!(s.cell_height(0, 0), 0.0);
        assert!((s.cell_height(1, 0) - 1.0).abs() < Q_EPS, "raw 1000 -> 1 m");
        assert!((s.cell_height(0, 1) - MAX_H / 2.0).abs() < Q_EPS);
        assert!((s.cell_height(1, 1) - MAX_H).abs() < Q_EPS);
    }

    #[test]
    fn test_cell_height_clamps_out_of_bounds() {
        let s = sampler(flat_grid(2, 2, 200), 2, 2, 0.0);
        assert_eq!(s.cell_height(99, 0), s.cell_height(1, 0));
        assert_eq!(s.cell_height(0, 99), s.cell_height(0, 1));
    }

    #[test]
    fn test_flat_grid_samples_constant_bilinear() {
        let s = sampler(flat_grid(4, 4, 20_000), 4, 4, 0.0);
        let expected = 20_000.0 / 65535.0 * MAX_H;
        for i in 0..25 {
            let x = i as f32 / 4.0 - 8.0;
            let z = (i % 5) as f32 / 4.0 - 8.0;
            let h = HeightField::sample(&s, x, z);
            assert!((h - expected).abs() < 1e-4, "flat at ({x},{z}): {h}");
        }
    }

    #[test]
    fn test_flat_grid_samples_constant_catmull() {
        let s = sampler(flat_grid(4, 4, 20_000), 4, 4, 1.0);
        let expected = 20_000.0 / 65535.0 * MAX_H;
        for i in 0..25 {
            let x = i as f32 / 4.0 - 8.0;
            let z = (i % 5) as f32 / 4.0 - 8.0;
            let h = HeightField::sample(&s, x, z);
            assert!((h - expected).abs() < 1e-4, "flat at ({x},{z}): {h}");
        }
    }

    #[test]
    fn test_single_texel_grid_is_flat_everywhere() {
        let s = sampler(vec![30_000], 1, 1, 1.0);
        let expected = 30_000.0 / 65535.0 * MAX_H;
        for i in 0..5 {
            let h = HeightField::sample(&s, i as f32 - 2.0, i as f32);
            assert!((h - expected).abs() < 1e-4);
        }
    }

    #[test]
    fn test_ramp_bilinear_is_exact_between_texels() {
        // 1 m per texel ramp: texel i sits at world x = i - 7.5, i meters high.
        let s = ramp_sampler(0.0);
        for &x in &[-7.2f32, -3.25, 0.0, 2.5, 7.2] {
            for &z in &[-3.5f32, 0.0, 3.5] {
                let h = HeightField::sample(&s, x, z);
                let expected = x + 7.5;
                assert!(
                    (h - expected).abs() < 1e-2,
                    "bilinear ramp at ({x},{z}): {h} vs {expected}"
                );
            }
        }
    }

    #[test]
    fn test_catmull_reproduces_linear_ramp_and_texel_centers() {
        let s = ramp_sampler(1.0);
        // Monotone Catmull-Rom reproduces linear data exactly, including texel
        // centers (where the cubic evaluates to the central sample).
        for i in 0..16u32 {
            let x = i as f32 - 7.5;
            let h = HeightField::sample(&s, x, 0.0);
            assert!((h - i as f32).abs() < 1e-2, "texel center {x}: {h}");
        }
        let h = HeightField::sample(&s, 3.37, -6.2);
        assert!((h - 10.87).abs() < 1e-2, "linear interpolation off: {h}");
    }

    #[test]
    fn test_catmull_never_overshoots_a_step() {
        // Vertical step at x = 0: 10 m plateau then 40 m plateau.
        let s = sampler(step_grid(16, 4), 16, 4, 1.0);
        for i in 0..50 {
            let x = i as f32 * 0.32 - 8.0;
            let z = i as f32 * 0.61 - 4.0;
            let h = HeightField::sample(&s, x, z);
            assert!(
                (10.0 - Q_EPS..=40.0 + Q_EPS).contains(&h),
                "catmull rang past the step at ({x},{z}): {h}"
            );
        }
    }

    #[test]
    fn test_partial_smoothing_lerps_bilinear_to_catmull() {
        // A wavy profile so bilinear != catmull in the cell interiors.
        let mut raw = vec![0u16, 30_000, 5_000, 25_000, 40_000, 10_000];
        raw.resize(6 * 6, 20_000);
        let bilinear = sampler(raw.clone(), 6, 6, 0.0);
        let mid = sampler(raw.clone(), 6, 6, 0.5);
        let catmull = sampler(raw, 6, 6, 1.0);
        for i in 0..36 {
            let x = (i % 6) as f32 - 3.0 + 0.37;
            let z = (i / 6) as f32 - 3.0 + 0.61;
            let a = HeightField::sample(&bilinear, x, z);
            let c = HeightField::sample(&catmull, x, z);
            let m = HeightField::sample(&mid, x, z);
            assert!(
                (m - (a + c) * 0.5).abs() < 1e-4,
                "lerp at ({x},{z}): {m} vs {}",
                (a + c) * 0.5
            );
        }
    }

    #[test]
    fn test_sample_clamps_outside_world_to_border_heights() {
        for smoothing in [0.0, 1.0] {
            let s = ramp_sampler(smoothing);
            let left = HeightField::sample(&s, -1e6, 0.0);
            let right = HeightField::sample(&s, 1e6, 0.0);
            assert!((left - HeightField::sample(&s, -7.5, 0.0)).abs() < 1e-3);
            assert!((right - HeightField::sample(&s, 7.5, 0.0)).abs() < 1e-3);
            // Far corners too (both axes clamped).
            let corner = HeightField::sample(&s, -1e6, 1e6);
            assert!((corner - HeightField::sample(&s, -7.5, 7.5)).abs() < 1e-3);
        }
    }

    #[test]
    fn test_sample_with_non_finite_input_does_not_panic() {
        let s = sampler(flat_grid(4, 4, 100), 4, 4, 1.0);
        let h = HeightField::sample(&s, f32::NAN, f32::INFINITY);
        assert!(h.is_finite(), "NaN world input clamps to the border: {h}");
    }

    #[test]
    fn test_sample_normal_flat_is_straight_up() {
        let s = sampler(flat_grid(4, 4, 10_000), 4, 4, 1.0);
        let n = HeightField::sample_normal(&s, 0.0, 0.0, 0.5);
        assert!((n - Vec3::Y).length() < 1e-5, "flat normal: {n}");
    }

    #[test]
    fn test_sample_normal_45_degree_ramp_tilts_halfway() {
        let s = ramp_sampler(1.0);
        let n = HeightField::sample_normal(&s, 0.0, 0.0, 0.5);
        assert!(n.x < 0.0, "ramp rises along +x so normal tips -x: {n}");
        assert!((n.x.abs() - n.y).abs() < 1e-2, "45 degrees: {n}");
        assert!(n.z.abs() < 1e-3, "no z tilt: {n}");
        assert!((n.length() - 1.0).abs() < 1e-5, "normalized: {n}");
    }

    #[test]
    fn test_sample_normal_degenerate_epsilon_falls_back() {
        let s = ramp_sampler(1.0);
        let n0 = HeightField::sample_normal(&s, 0.0, 0.0, 0.0);
        let nneg = HeightField::sample_normal(&s, 0.0, 0.0, -1.0);
        let nref = HeightField::sample_normal(&s, 0.0, 0.0, 1.0);
        assert!(n0.is_finite() && n0.length() > 0.5, "zero epsilon: {n0}");
        assert!(
            nneg.is_finite() && nneg.length() > 0.5,
            "negative epsilon: {nneg}"
        );
        assert!(
            (n0 - nref).length() < 0.5,
            "fallback ~1 texel step: {n0} vs {nref}"
        );
    }

    #[test]
    fn test_max_height_trait_matches_sampler() {
        let s = sampler(flat_grid(2, 2, 0), 2, 2, 0.0);
        assert_eq!(HeightField::max_height(&s), MAX_H);
    }

    #[test]
    fn test_apply_pads_core_is_exactly_flat_at_target() {
        let mut s = sampler(flat_grid(32, 32, 0), 32, 32, 0.0);
        let pad = TerrainPadSpec {
            at: Vec2::ZERO,
            size: Vec2::splat(10.0),
            falloff: 6.0,
            corner_radius: 2.0,
            height: Some(25.0),
        };
        let resolved = s.apply_pads(&[pad]);
        assert_eq!(resolved.len(), 1);
        assert_eq!(resolved[0].height, 25.0);
        // Core interior: |wx| <= 5 (core half 3 + corner radius 2).
        for i in 0..9 {
            let x = i as f32 - 4.0;
            for j in 0..9 {
                let z = j as f32 - 4.0;
                let h = HeightField::sample(&s, x, z);
                assert!((h - 25.0).abs() < Q_EPS, "core at ({x},{z}): {h}");
            }
        }
    }

    #[test]
    fn test_apply_pads_auto_uses_center_height_before_flatten() {
        // 1 m-per-texel ramp, 33 texels over 32 m: the pad center (world 0,0)
        // sits exactly on texel 16 => 16 m. The auto target must be that
        // untouched value, not a re-sample of terrain the pad flattened.
        let raw = grid_from_fn(33, 33, |x| (x * 1000) as u16);
        let mut s =
            HeightSampler::from_grid(raw, 33, 33, 32.0, MAX_H, 0.0).expect("ramp grid is valid");
        let pad = TerrainPadSpec {
            at: Vec2::ZERO,
            size: Vec2::splat(8.0),
            falloff: 4.0,
            corner_radius: 2.0,
            height: None,
        };
        let resolved = s.apply_pads(&[pad]);
        assert!(
            (resolved[0].height - 16.0).abs() < 1e-2,
            "auto target: {}",
            resolved[0].height
        );
        for i in 0..5 {
            let x = i as f32 - 2.0;
            let h = HeightField::sample(&s, x, 0.0);
            assert!((h - 16.0).abs() < Q_EPS, "auto core at {x}: {h}");
        }
    }

    #[test]
    fn test_apply_pads_returns_resolved_specs_in_order() {
        let mut s = sampler(flat_grid(32, 32, 10_000), 32, 32, 0.0);
        let pads = vec![
            TerrainPadSpec {
                at: Vec2::new(-6.0, 4.0),
                size: Vec2::new(8.0, 6.0),
                falloff: 3.0,
                corner_radius: 1.0,
                height: Some(12.0),
            },
            TerrainPadSpec {
                at: Vec2::new(7.0, -5.0),
                size: Vec2::splat(6.0),
                falloff: 3.0,
                corner_radius: 1.0,
                height: None,
            },
        ];
        let resolved = s.apply_pads(&pads);
        assert_eq!(resolved.len(), 2);
        assert_eq!(resolved[0].at, pads[0].at);
        assert_eq!(resolved[0].size, pads[0].size);
        assert_eq!(resolved[0].falloff, pads[0].falloff);
        assert_eq!(resolved[0].corner_radius, pads[0].corner_radius);
        assert_eq!(resolved[0].height, 12.0);
        // Second pad (auto) samples the terrain as left by the first pad's
        // falloff; far enough away that it is still the untouched 10000 raw.
        assert_eq!(resolved[1].at, pads[1].at);
        assert!((resolved[1].height - 10_000.0 / 65535.0 * MAX_H).abs() < 1e-3);
    }

    #[test]
    fn test_apply_pads_falloff_blends_monotonically() {
        // Fill: target above the flat terrain — heights descend smoothly from
        // the target at the core edge to the untouched terrain beyond.
        // 49 texels over 48 m -> 1 m texel step, 24 m half-world.
        let mut s = HeightSampler::from_grid(flat_grid(49, 49, 0), 49, 49, 48.0, MAX_H, 0.0)
            .expect("flat grid is valid");
        let pad = TerrainPadSpec {
            at: Vec2::ZERO,
            size: Vec2::splat(10.0),
            falloff: 12.0,
            corner_radius: 2.0,
            height: Some(30.0),
        };
        s.apply_pads(&[pad]);
        let mut prev = 30.0;
        for i in 0..24 {
            let x = 4.0 + i as f32 * 0.75; // core edge (~5 m) to past falloff
            let h = HeightField::sample(&s, x, 0.0);
            assert!(
                h <= prev + 1e-3,
                "fill falloff must descend: {h} after {prev} at {x}"
            );
            assert!(
                (-Q_EPS..=30.0 + Q_EPS).contains(&h),
                "within [terrain, target]: {h}"
            );
            prev = h;
        }
        let outside = HeightField::sample(&s, 23.0, 0.0);
        assert!(
            outside.abs() < Q_EPS,
            "beyond falloff terrain is untouched: {outside}"
        );
    }

    #[test]
    fn test_apply_pads_guard_clamps_first_falloff_band_on_cut() {
        // Step along x: 10 m low plateau (wx < 0) then 40 m high plateau.
        // 25 texels over 24 m -> exact 1 m texel step, texel i at wx = i - 12.
        // A pad pinned to 10 m whose core ends at wx = 8: the first falloff
        // texel (grid 21, d = 1 m < one stencil diagonal) must be clamped
        // exactly onto the plane — no lip above the target.
        let mut s = HeightSampler::from_grid(step_grid(25, 25), 25, 25, 24.0, MAX_H, 0.0)
            .expect("step grid is valid");
        let pad = TerrainPadSpec {
            at: Vec2::ZERO, // core |wx| <= 8 (half 8): grid 4..=20
            size: Vec2::splat(16.0),
            falloff: 8.0,
            corner_radius: 4.0,
            height: Some(10.0),
        };
        s.apply_pads(&[pad]);
        let target_raw = 10_000u16; // quantized 10 m == the low plateau
        for gx in 12..=21 {
            let raw = s.grid[12 * 25 + gx];
            assert_eq!(
                raw, target_raw,
                "guard band texel at grid x {gx} must sit exactly on the pad plane"
            );
        }
        // Reconstruction just inside the edge reads the plane, not a lip.
        let lip_free = HeightField::sample(&s, 5.0, 0.0);
        assert!(
            (lip_free - 10.0).abs() < Q_EPS,
            "inside the edge: {lip_free}"
        );
    }

    #[test]
    fn test_apply_pads_guard_clamps_first_falloff_band_on_fill() {
        // Mirror of the cut guard: a pad filling up to 40 m over the 10 m
        // floor must lift the first falloff texel (grid 9, d = 1 m) exactly
        // onto the plane — no dip below the target.
        let mut s = HeightSampler::from_grid(step_grid(25, 25), 25, 25, 24.0, MAX_H, 0.0)
            .expect("step grid is valid");
        let pad = TerrainPadSpec {
            at: Vec2::new(-10.0, 0.0), // core wx in [-16, -4]: grid 0..=8
            size: Vec2::splat(12.0),
            falloff: 8.0,
            corner_radius: 2.0,
            height: Some(40.0),
        };
        s.apply_pads(&[pad]);
        let target_raw = 40_000u16; // quantized 40 m
        for gx in 0..=9 {
            let raw = s.grid[12 * 25 + gx];
            assert_eq!(raw, target_raw, "fill guard band texel at grid x {gx}");
        }
    }

    #[test]
    fn test_apply_pads_revision_bumps_once_per_changed_pad() {
        let mut s = sampler(flat_grid(32, 32, 10_000), 32, 32, 0.0);
        let noop = TerrainPadSpec {
            at: Vec2::ZERO,
            size: Vec2::splat(10.0),
            falloff: 6.0,
            corner_radius: 2.0,
            height: Some(10_000.0 / 65535.0 * MAX_H), // already the terrain height
        };
        s.apply_pads(std::slice::from_ref(&noop));
        assert_eq!(s.revision(), 0, "no-op pad must not bump the revision");
        let cutting = TerrainPadSpec {
            height: Some(5.0),
            ..noop
        };
        s.apply_pads(&[cutting]);
        assert_eq!(s.revision(), 1, "one changed pad bumps the revision once");
    }

    #[test]
    fn test_apply_pads_far_outside_terrain_is_a_noop() {
        let mut s = sampler(flat_grid(16, 16, 20_000), 16, 16, 0.0);
        let far = TerrainPadSpec {
            at: Vec2::splat(100.0 * WORLD),
            size: Vec2::splat(20.0),
            falloff: 8.0,
            corner_radius: 4.0,
            height: Some(3.0),
        };
        let resolved = s.apply_pads(&[far]);
        assert_eq!(resolved.len(), 1);
        assert_eq!(
            s.revision(),
            0,
            "pad entirely outside the grid changes nothing"
        );
        assert_eq!(s.grid, flat_grid(16, 16, 20_000), "grid untouched");
    }

    #[test]
    fn test_apply_pads_nan_center_is_ignored() {
        let mut s = sampler(flat_grid(16, 16, 20_000), 16, 16, 0.0);
        let bad = TerrainPadSpec {
            at: Vec2::new(f32::NAN, 0.0),
            size: Vec2::splat(10.0),
            falloff: 6.0,
            corner_radius: 2.0,
            height: Some(3.0),
        };
        let resolved = s.apply_pads(&[bad]);
        assert_eq!(resolved.len(), 1);
        assert_eq!(s.revision(), 0, "NaN pad center must not stamp anything");
    }

    #[test]
    fn test_apply_pads_second_auto_pad_sees_first_pads_flatten() {
        let mut s = sampler(flat_grid(40, 40, 0), 40, 40, 0.0);
        let first = TerrainPadSpec {
            at: Vec2::ZERO,
            size: Vec2::splat(8.0),
            falloff: 4.0,
            corner_radius: 2.0,
            height: Some(20.0),
        };
        // Overlapping pad with auto height: must sample the already-flattened
        // plane (20 m), not the original 0 m terrain.
        let second = TerrainPadSpec {
            at: Vec2::new(2.0, 0.0),
            size: Vec2::splat(8.0),
            falloff: 4.0,
            corner_radius: 2.0,
            height: None,
        };
        let resolved = s.apply_pads(&[first, second]);
        assert!(
            (resolved[1].height - 20.0).abs() < 1e-2,
            "auto height after a previous pad: {}",
            resolved[1].height
        );
    }

    #[test]
    fn test_world_size_accessors() {
        let s = sampler(flat_grid(4, 2, 0), 4, 2, 0.0);
        assert_eq!(s.width(), 4);
        assert_eq!(s.depth(), 2);
        assert_eq!(s.world_size(), WORLD);
        assert_eq!(s.max_height(), MAX_H);
    }
}
