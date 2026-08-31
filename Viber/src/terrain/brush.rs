//! Height brush engine — the single mutation path for the terrain grid.
//!
//! Ported from the VibeGame terrain plugin's `height-brush.ts` +
//! `flatten.ts`. Every ground mutation (pads, lakes, rivers, roads, and later
//! Luau-authored brushes) goes through [`BrushGrid::apply`]:
//!
//! * **One grid authority** — [`BrushGrid`] owns the raw `u16` height grid and
//!   bumps [`BrushGrid::revision`] whenever a stroke writes (VibeGame
//!   `getGroundRevision`); caches (chunks, colliders, spawner heights) key off
//!   it.
//! * **Owner journal** — strokes are recorded per `owner` (`"pad:0"`,
//!   `"road:3"`, …) and [`BrushGrid::revert_last_stroke`] undoes the owner's
//!   last stroke before it re-carves, so re-applying the same brush never
//!   accumulates depth (VibeGame `revertHeightBrush` idempotency rule).
//! * **Modes** — [`BrushMode::Blend`] mixes toward the design surface,
//!   [`BrushMode::Lower`]/[`BrushMode::Raise`] only ever cut/fill. Water is
//!   always lower-only, so overlapping bodies and pre-existing valleys stay
//!   safe.
//! * **Guard clamp** — after a stroke, texels whose bilinear stencil touches
//!   the brush region are lowered to the design surface when they rise above
//!   it (the `guardAt` lower-only clamp). This removes the "lip" the 1-texel
//!   bilinear influence reach (`√2·texel`) would otherwise leave at the edge
//!   of flat pads and road beds.
//! * **Minimum effective width** — [`min_effective`] clamps brush widths and
//!   falloffs to ≥1.5 texels of the grid: smaller beds cannot touch a texel
//!   center and would silently no-op (heightmap 4000 m / 64 texels ≈ 32 m per
//!   texel).

use bevy::math::{Vec2, Vec3};

use super::heightmap::HeightMapU16;

/// Weight/target evaluation at a world XZ position (meters).
pub type BrushFn<'a> = &'a mut dyn FnMut(Vec2) -> f32;

/// Read-only design-surface evaluation for the guard clamp.
pub type GuardFn<'a> = &'a dyn Fn(Vec2) -> f32;

/// How candidates move toward the design surface.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrushMode {
    /// `cur += (target - cur) · weight` — cut **and** fill (pads, roads).
    Blend,
    /// Like blend, but only writes when the candidate is lower (water).
    Lower,
    /// Like blend, but only writes when the candidate is higher.
    Raise,
}

/// One brush stroke request.
pub struct BrushRequest<'a> {
    pub mode: BrushMode,
    /// World-space XZ bounds of the brush (expanded by one texel internally;
    /// the closures decide the actual falloff).
    pub min_x: f32,
    pub min_z: f32,
    pub max_x: f32,
    pub max_z: f32,
    /// Design surface height (meters) at a world position.
    pub target: BrushFn<'a>,
    /// Brush weight `0..=1` at a world position (0 = untouched).
    pub weight: BrushFn<'a>,
    /// Optional design surface used by the guard clamp: texels near the brush
    /// that still rise above it are lowered onto it. Typically the same
    /// surface as `target` evaluated without falloff.
    pub guard: Option<GuardFn<'a>>,
}

/// A recorded stroke: every `(cell, old_raw)` the owner wrote, in order.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Stroke {
    owner: String,
    writes: Vec<(usize, u16)>,
}

/// Mutable height grid with brush semantics — the mutation-side counterpart
/// of the read-only [`super::mesh::HeightField`] contract (which it
/// implements, so mesh/collider builders consume a carved grid directly).
#[derive(Debug, Clone)]
pub struct BrushGrid {
    grid: Vec<u16>,
    width: usize,
    depth: usize,
    world_size: f32,
    max_height: f32,
    /// 0.0 = bilinear, 1.0 = monotone Catmull-Rom (matches the sampler docs).
    smoothing: f32,
    revision: u64,
    /// Completed strokes (committed), oldest first.
    history: Vec<Stroke>,
    /// The stroke currently recording writes (between `begin_stroke` and
    /// `commit_stroke`).
    open: Option<Stroke>,
}

/// Raw quantization step (meters) of a `u16` grid with this `max_height`.
fn raw_quantum(max_height: f32) -> f32 {
    max_height / u16::MAX as f32
}

fn height_to_raw(h: f32, max_height: f32) -> u16 {
    let norm = (h / max_height).clamp(0.0, 1.0);
    (norm * u16::MAX as f32).round() as u16
}

/// Clamps a brush width/falloff to at least 1.5 grid texels (VibeGame
/// `minEffectiveWidth` / `minEffectiveFalloff`): smaller values cannot reach a
/// texel center and would silently do nothing.
pub fn min_effective(value: f32, texel: f32) -> f32 {
    value.max(1.5 * texel)
}

impl BrushGrid {
    /// Builds a brush grid from a raw `u16` buffer (`z * width + x`).
    ///
    /// # Errors
    /// Fails when the buffer length does not match `width * depth` or the
    /// metrics are not finite and positive.
    pub fn new(
        grid: Vec<u16>,
        width: usize,
        depth: usize,
        world_size: f32,
        max_height: f32,
        smoothing: f32,
    ) -> anyhow::Result<Self> {
        if width == 0 || depth == 0 {
            anyhow::bail!("brush grid needs at least 1x1 texels, got {width}x{depth}");
        }
        if grid.len() != width * depth {
            anyhow::bail!(
                "brush grid buffer is {} texels but {}x{} = {} were expected",
                grid.len(),
                width,
                depth,
                width * depth
            );
        }
        if world_size <= 0.0 || !world_size.is_finite() {
            anyhow::bail!("brush grid world_size must be finite and positive, got {world_size}");
        }
        if max_height <= 0.0 || !max_height.is_finite() {
            anyhow::bail!("brush grid max_height must be finite and positive, got {max_height}");
        }
        Ok(Self {
            grid,
            width,
            depth,
            world_size,
            max_height,
            smoothing: smoothing.clamp(0.0, 1.0),
            revision: 0,
            history: Vec::new(),
            open: None,
        })
    }

    /// Builds from a decoded [`HeightMapU16`]; the map spans
    /// `(-world_size/2, +world_size/2)` on XZ.
    pub fn from_height_map(
        map: &HeightMapU16,
        world_size: f32,
        max_height: f32,
        smoothing: f32,
    ) -> anyhow::Result<Self> {
        Self::new(
            map.data.clone(),
            map.width,
            map.depth,
            world_size,
            max_height,
            smoothing,
        )
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

    /// Height (meters) of a fully-white sample.
    pub fn max_height(&self) -> f32 {
        self.max_height
    }

    /// Cache invalidation counter; bumped by every writing stroke.
    pub fn revision(&self) -> u64 {
        self.revision
    }

    /// World distance between texel centers (meters).
    pub fn texel(&self) -> f32 {
        self.world_size / (self.width.max(2) - 1) as f32
    }

    /// Raw grid access (`z * width + x`), for building read-side samplers.
    pub fn raw(&self) -> &[u16] {
        &self.grid
    }

    /// Consumes the grid into its raw buffer (hand-off to read-side types).
    pub fn into_raw(self) -> Vec<u16> {
        self.grid
    }

    /// Height (meters) of one texel; out-of-bounds indices clamp.
    pub fn cell_height(&self, x: usize, z: usize) -> f32 {
        let x = x.min(self.width - 1);
        let z = z.min(self.depth - 1);
        self.grid[z * self.width + x] as f32 / u16::MAX as f32 * self.max_height
    }

    /// World XZ of a texel center.
    pub fn cell_center(&self, x: usize, z: usize) -> Vec2 {
        let half = self.world_size * 0.5;
        Vec2::new(
            x as f32 * self.texel() - half,
            z as f32 * self.texel() - half,
        )
    }

    /// Writes one texel (meters), recording the old value in the open stroke.
    /// Returns `true` when the raw value actually changed.
    pub fn set_cell_height(&mut self, x: usize, z: usize, h: f32) -> bool {
        let x = x.min(self.width - 1);
        let z = z.min(self.depth - 1);
        let idx = z * self.width + x;
        let raw = height_to_raw(h, self.max_height);
        if self.grid[idx] == raw {
            return false;
        }
        if let Some(stroke) = self.open.as_mut() {
            stroke.writes.push((idx, self.grid[idx]));
        }
        self.grid[idx] = raw;
        true
    }

    /// Opens a stroke journal for `owner` (must be closed with
    /// [`Self::commit_stroke`]).
    pub fn begin_stroke(&mut self, owner: &str) {
        self.open = Some(Stroke {
            owner: owner.to_string(),
            writes: Vec::new(),
        });
    }

    /// Closes the open stroke; bumps [`Self::revision`] when it wrote.
    /// Returns the number of changed texels.
    pub fn commit_stroke(&mut self) -> usize {
        let Some(stroke) = self.open.take() else {
            return 0;
        };
        let count = stroke.writes.len();
        if count > 0 {
            self.revision += 1;
            self.history.push(stroke);
        }
        count
    }

    /// Restores the texels of `owner`'s most recent stroke. Returns `false`
    /// when the owner never wrote. Reverted texels do not bump the revision
    /// (the caller re-carves immediately afterwards).
    pub fn revert_last_stroke(&mut self, owner: &str) -> bool {
        let Some(pos) = self.history.iter().rposition(|s| s.owner == owner) else {
            return false;
        };
        let stroke = self.history.remove(pos);
        if stroke.writes.is_empty() {
            return false;
        }
        for (idx, raw) in stroke.writes.iter().rev() {
            self.grid[*idx] = *raw;
        }
        true
    }

    /// Applies one brush stroke (see [`BrushRequest`]). Texel centers inside
    /// the request AABB expanded by one texel are visited; returns the number
    /// of changed texels (a stroke that writes nothing does not bump the
    /// revision).
    pub fn apply(&mut self, req: BrushRequest) -> usize {
        let texel = self.texel();
        let half = self.world_size * 0.5;
        // Texel index range covering the AABB expanded ±1 texel (the bilinear
        // stencil of any sample near the brush reaches this far).
        let x0 = (((req.min_x - texel) + half) / texel).floor().max(0.0) as usize;
        let x1 = ((((req.max_x + texel) + half) / texel).ceil() as usize + 1).min(self.width);
        let z0 = (((req.min_z - texel) + half) / texel).floor().max(0.0) as usize;
        let z1 = ((((req.max_z + texel) + half) / texel).ceil() as usize + 1).min(self.depth);
        let quantum = raw_quantum(self.max_height);
        let eps = quantum.max(1e-4);

        for z in z0..z1 {
            for x in x0..x1 {
                let p = self.cell_center(x, z);
                let w = (req.weight)(p).clamp(0.0, 1.0);
                if w <= 0.0 {
                    continue;
                }
                let cur = self.cell_height(x, z);
                let target = (req.target)(p);
                let cand = cur + (target - cur) * w;
                let changed = match req.mode {
                    BrushMode::Blend => (cand - cur).abs() > eps,
                    BrushMode::Lower => cur - cand > eps,
                    BrushMode::Raise => cand - cur > eps,
                };
                if changed {
                    self.set_cell_height(x, z, cand);
                }
            }
        }

        // Guard clamp: texels whose 3x3 stencil touches a weighted texel but
        // that sit above the design surface are lowered onto it (lower-only).
        // This is what keeps flat pads/road beds free of bilinear-stencil
        // lips at their falloff edges.
        if let Some(guard) = req.guard {
            let reach = texel * std::f32::consts::SQRT_2;
            for z in z0..z1 {
                for x in x0..x1 {
                    let p = self.cell_center(x, z);
                    if (req.weight)(p) > 0.0 {
                        continue; // the main pass owns weighted texels
                    }
                    let touches = [
                        Vec2::new(p.x - reach, p.y),
                        Vec2::new(p.x + reach, p.y),
                        Vec2::new(p.x, p.y - reach),
                        Vec2::new(p.x, p.y + reach),
                    ]
                    .into_iter()
                    .any(|q| (req.weight)(q) > 0.0);
                    if !touches {
                        continue;
                    }
                    let g = guard(p);
                    let cur = self.cell_height(x, z);
                    if cur - g > eps {
                        self.set_cell_height(x, z, g);
                    }
                }
            }
        }

        self.commit_stroke()
    }

    /// Flattens a rounded rectangle to `height` — the `<TerrainPad>` carve
    /// (`flattenRect`): SDF core at weight 1 (cut **and** fill), smoothstep
    /// falloff ring, guard clamped to the plane. When `height` is `None` the
    /// pad center is sampled first (auto mode) so callers can resolve it.
    /// Returns the resolved height.
    pub fn flatten_rect(
        &mut self,
        at: Vec2,
        size: Vec2,
        falloff: f32,
        corner_radius: f32,
        height: Option<f32>,
        owner: &str,
    ) -> f32 {
        let texel = self.texel();
        let falloff = min_effective(falloff, texel);
        let core_half = size * 0.5;
        let radius = corner_radius.clamp(0.0, core_half.x.min(core_half.y));
        let inner = core_half - Vec2::splat(radius);
        let target_height = height.unwrap_or_else(|| self.sample(at.x, at.y));

        self.begin_stroke(owner);
        let mut weight = |p: Vec2| {
            let d = (p - at).abs() - inner;
            let sd = Vec2::max(d, Vec2::ZERO).length() + d.max_element().min(0.0) - radius;
            if sd <= 0.0 {
                1.0
            } else if sd < falloff {
                1.0 - smoothstep01(sd / falloff)
            } else {
                0.0
            }
        };
        let mut target = |_| target_height;
        let guard: GuardFn = &|_| target_height;
        self.apply(BrushRequest {
            mode: BrushMode::Blend,
            min_x: at.x - core_half.x - falloff,
            min_z: at.y - core_half.y - falloff,
            max_x: at.x + core_half.x + falloff,
            max_z: at.y + core_half.y + falloff,
            target: &mut target,
            weight: &mut weight,
            guard: Some(guard),
        });
        self.commit_stroke();
        target_height
    }
}

/// Monotone smoothstep `0→1` on `0..1` (clamped).
pub fn smoothstep01(t: f32) -> f32 {
    let t = t.clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// Quintic ` smootherstep ` (C2) — road falloff edges use it so the second
/// derivative vanishes at both ends (no visible crease).
pub fn smootherstep01(t: f32) -> f32 {
    let t = t.clamp(0.0, 1.0);
    t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
}

impl BrushGrid {
    /// Bilinear blend of the 4 texels around `p`; out-of-domain clamps.
    fn bilinear(&self, x: f32, z: f32) -> f32 {
        let half = self.world_size * 0.5;
        let fx = ((x + half) / self.texel()).clamp(0.0, (self.width - 1) as f32);
        let fz = ((z + half) / self.texel()).clamp(0.0, (self.depth - 1) as f32);
        let x0 = fx.floor() as usize;
        let z0 = fz.floor() as usize;
        let x1 = (x0 + 1).min(self.width - 1);
        let z1 = (z0 + 1).min(self.depth - 1);
        let tx = fx - x0 as f32;
        let tz = fz - z0 as f32;
        let h00 = self.cell_height(x0, z0);
        let h10 = self.cell_height(x1, z0);
        let h01 = self.cell_height(x0, z1);
        let h11 = self.cell_height(x1, z1);
        (h00 * (1.0 - tx) + h10 * tx) * (1.0 - tz) + (h01 * (1.0 - tx) + h11 * tx) * tz
    }

    /// Monotone Catmull-Rom on one axis (Fritsch–Carlson tangent clamp so the
    /// spline never overshoots neighbouring texels).
    fn catmull_axis(h0: f32, h1: f32, h2: f32, h3: f32, t: f32) -> f32 {
        let m1 = monotone_tangent(h0, h1, h2);
        let m2 = monotone_tangent(h1, h2, h3);
        let t2 = t * t;
        let t3 = t2 * t;
        (2.0 * h1 - 2.0 * h2 + m1 + m2) * t3
            + (-3.0 * h1 + 3.0 * h2 - 2.0 * m1 - m2) * t2
            + m1 * t
            + h1
    }

    fn smoothed_axis(&self, x: f32, z: f32, axis: Axis) -> f32 {
        let half = self.world_size * 0.5;
        let texel = self.texel();
        let (cells, f, n) = match axis {
            Axis::X => (
                (((x + half) / texel).clamp(0.0, (self.width - 1) as f32)),
                z,
                self.width,
            ),
            Axis::Z => (
                (((z + half) / texel).clamp(0.0, (self.depth - 1) as f32)),
                x,
                self.depth,
            ),
        };
        let i0 = cells.floor() as usize;
        let t = cells - i0 as f32;
        let at = |i: usize| -> f32 {
            match axis {
                Axis::X => self.cell_height(i.min(n - 1), (f / texel + half / texel) as usize),
                Axis::Z => self.cell_height((f / texel + half / texel) as usize, i.min(n - 1)),
            }
        };
        let h1 = at(i0);
        let h2 = at(i0 + 1);
        if self.smoothing <= 0.0 {
            return h1 + (h2 - h1) * t;
        }
        let h0 = at(i0.saturating_sub(1));
        let h3 = at(i0 + 2);
        let cr = Self::catmull_axis(h0, h1, h2, h3, t);
        let bilinear = h1 + (h2 - h1) * t;
        bilinear + (cr - bilinear) * self.smoothing
    }

    /// Terrain height at a world XZ position (smoothed per `smoothing`).
    pub fn sample(&self, x: f32, z: f32) -> f32 {
        if self.smoothing <= 0.0 {
            return self.bilinear(x, z);
        }
        let lin_x = self.smoothed_axis(x, z, Axis::X);
        let lin_z = self.smoothed_axis(x, z, Axis::Z);
        // Tensor-product evaluation converges with the axis pair order kept
        // stable; averaging both orders removes the residual bias instead.
        (lin_x + lin_z) * 0.5
    }

    /// Surface normal via central differences with a world epsilon.
    pub fn sample_normal(&self, x: f32, z: f32, epsilon: f32) -> Vec3 {
        let e = if epsilon > 0.0 { epsilon } else { self.texel() };
        let hx0 = self.sample(x - e, z);
        let hx1 = self.sample(x + e, z);
        let hz0 = self.sample(x, z - e);
        let hz1 = self.sample(x, z + e);
        Vec3::new(hx0 - hx1, 2.0 * e, hz0 - hz1).normalize_or_zero()
    }
}

#[derive(Clone, Copy)]
enum Axis {
    X,
    Z,
}

/// Fritsch–Carlson monotone tangent at `h1` between `h0` and `h2`: the secant
/// average clamped to `3·min(|d0|, |d1|)` — this is what forbids overshoot.
fn monotone_tangent(h0: f32, h1: f32, h2: f32) -> f32 {
    let d0 = h1 - h0;
    let d1 = h2 - h1;
    if d0 == 0.0 || d1 == 0.0 || d0.signum() != d1.signum() {
        return 0.0;
    }
    let cap = 3.0 * d0.abs().min(d1.abs());
    (0.5 * (d0 + d1)).clamp(-cap, cap)
}

impl super::mesh::HeightField for BrushGrid {
    fn sample(&self, world_x: f32, world_z: f32) -> f32 {
        BrushGrid::sample(self, world_x, world_z)
    }

    fn sample_normal(&self, world_x: f32, world_z: f32, epsilon: f32) -> Vec3 {
        BrushGrid::sample_normal(self, world_x, world_z, epsilon)
    }

    fn max_height(&self) -> f32 {
        self.max_height
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bevy::math::Vec2;

    /// Flat 64x64 grid, world 64 m, max height 50 m.
    fn flat_grid(h: f32) -> BrushGrid {
        let raw = height_to_raw(h, 50.0);
        BrushGrid::new(vec![raw; 64 * 64], 64, 64, 64.0, 50.0, 0.0).expect("valid grid")
    }

    #[test]
    fn test_new_rejects_bad_buffer() {
        assert!(BrushGrid::new(vec![0; 10], 4, 4, 64.0, 50.0, 0.0).is_err());
        assert!(BrushGrid::new(vec![0; 16], 4, 4, 0.0, 50.0, 0.0).is_err());
        assert!(BrushGrid::new(vec![0; 16], 4, 4, 64.0, -1.0, 0.0).is_err());
        assert!(BrushGrid::new(vec![0; 16], 0, 4, 64.0, 50.0, 0.0).is_err());
        assert!(BrushGrid::new(vec![0; 16], 4, 4, 64.0, 50.0, 0.0).is_ok());
    }

    #[test]
    fn test_cell_and_texel_metrics() {
        let grid = flat_grid(10.0);
        assert_eq!(grid.width(), 64);
        assert_eq!(grid.depth(), 64);
        // Endpoints-inclusive grid: world_size / (n - 1) texel steps.
        assert!((grid.texel() - 64.0 / 63.0).abs() < 1e-5);
        assert!((grid.cell_height(0, 0) - 10.0).abs() < 1e-3);
        let c = grid.cell_center(32, 32);
        assert!(
            c.distance(Vec2::new(
                32.0 * 64.0 / 63.0 - 32.0,
                32.0 * 64.0 / 63.0 - 32.0
            )) < 1e-4
        );
    }

    #[test]
    fn test_flatten_cut_and_fill() {
        // Hill under the pad gets cut, the valley outside stays.
        let mut grid = flat_grid(5.0);
        grid.begin_stroke("hill");
        for z in 0..64 {
            for x in 0..64 {
                let p = grid.cell_center(x, z);
                let hill = 5.0 + 20.0 * (-(p.length_squared()) / 200.0).exp();
                grid.set_cell_height(x, z, hill);
            }
        }
        grid.commit_stroke();

        let at = Vec2::ZERO;
        let resolved = grid.flatten_rect(at, Vec2::splat(20.0), 8.0, 4.0, None, "pad:0");
        let core = grid.sample(0.0, 0.0);
        assert!(
            (core - resolved).abs() < 0.05,
            "core sits on the resolved plane: {core} vs {resolved}"
        );
        // The resolved height is the pre-carve center sample (auto mode) —
        // the hill peak, flattened at its own top.
        assert!(
            (resolved - 25.0).abs() < 0.3,
            "auto height from the peak: {resolved}"
        );
        // Far corner keeps the base height.
        let far = grid.sample(30.0, 30.0);
        assert!(
            far < 6.5,
            "outside the falloff the hill is untouched: {far}"
        );
    }

    #[test]
    fn test_flatten_absolute_height() {
        let mut grid = flat_grid(10.0);
        let resolved = grid.flatten_rect(
            Vec2::ZERO,
            Vec2::splat(10.0),
            6.0,
            2.0,
            Some(20.0),
            "pad:abs",
        );
        assert_eq!(resolved, 20.0, "explicit height wins");
        assert!((grid.sample(0.0, 0.0) - 20.0).abs() < 0.05);
    }

    #[test]
    fn test_flatten_guard_removes_stencil_lip() {
        // A wall exactly at the falloff boundary: without the guard, the
        // weight-0 texels just outside keep their full height and the bilinear
        // stencil leaves a step lip at the pad edge.
        let mut grid = flat_grid(0.0);
        grid.begin_stroke("wall");
        let wall_x = 21.0;
        for z in 0..64 {
            for x in 0..64 {
                let p = grid.cell_center(x, z);
                let h = if p.x >= wall_x { 12.0 } else { 0.0 };
                grid.set_cell_height(x, z, h);
            }
        }
        grid.commit_stroke();
        // Pad centered at (10, 0): core to ±5, falloff to ~21 m.
        let resolved = grid.flatten_rect(
            Vec2::new(10.0, 0.0),
            Vec2::splat(10.0),
            6.0,
            2.0,
            Some(0.0),
            "pad:lip",
        );
        assert_eq!(resolved, 0.0);
        // Texels just past the falloff (inside the guard reach) are clamped to
        // the plane instead of leaving a 12 m step next to the falloff ring.
        let lip = grid.sample(wall_x + 0.6, 0.0);
        assert!(lip < 0.6, "guard clamps the wall lip: {lip}");
        // The wall far from the pad is untouched.
        assert!(
            (grid.sample(wall_x + 1.2, 30.0) - 12.0).abs() < 0.05,
            "guard only acts near the brush"
        );
    }

    #[test]
    fn test_lower_mode_never_raises() {
        let mut grid = flat_grid(10.0);
        grid.begin_stroke("lake");
        let mut weight = |p: Vec2| (1.0 - p.length() / 10.0).clamp(0.0, 1.0);
        let mut target = |_| 2.0_f32; // would raise nothing, lower everywhere
        grid.apply(BrushRequest {
            mode: BrushMode::Lower,
            min_x: -10.0,
            min_z: -10.0,
            max_x: 10.0,
            max_z: 10.0,
            target: &mut target,
            weight: &mut weight,
            guard: None,
        });
        grid.commit_stroke();
        assert!(grid.sample(0.0, 0.0) < 10.0, "center carved down");
        // Corner of the AABB: weight is exactly 0 there (length >= 10).
        let outside = grid.sample(9.9, 9.9);
        assert!((outside - 10.0).abs() < 0.05, "weight 0 texels untouched");
    }

    #[test]
    fn test_raise_mode_never_lowers() {
        let mut grid = flat_grid(10.0);
        grid.begin_stroke("bank");
        let mut weight = |p: Vec2| (1.0 - (p.length() / 5.0)).clamp(0.0, 1.0);
        let mut target = |_| 2.0_f32; // below the surface: raise must skip
        grid.apply(BrushRequest {
            mode: BrushMode::Raise,
            min_x: -6.0,
            min_z: -6.0,
            max_x: 6.0,
            max_z: 6.0,
            target: &mut target,
            weight: &mut weight,
            guard: None,
        });
        grid.commit_stroke();
        assert!(
            (grid.sample(0.0, 0.0) - 10.0).abs() < 0.05,
            "nothing was lowered"
        );
    }

    #[test]
    fn test_journal_revert_restores_exactly() {
        let mut grid = flat_grid(8.0);
        grid.begin_stroke("road:1");
        let mut weight = |p: Vec2| (1.0 - p.length() / 8.0).clamp(0.0, 1.0);
        let mut target = |_| 1.0_f32;
        let written = grid.apply(BrushRequest {
            mode: BrushMode::Blend,
            min_x: -8.0,
            min_z: -8.0,
            max_x: 8.0,
            max_z: 8.0,
            target: &mut target,
            weight: &mut weight,
            guard: None,
        });
        grid.commit_stroke();
        assert!(written > 0);
        assert!(grid.revert_last_stroke("road:1"), "owner had a stroke");
        assert_eq!(
            grid.raw(),
            vec![height_to_raw(8.0, 50.0); 64 * 64],
            "revert restores the exact pre-carve texels"
        );
        assert!(!grid.revert_last_stroke("road:2"), "unknown owner fails");
    }

    #[test]
    fn test_revert_only_touches_the_owner() {
        let mut grid = flat_grid(8.0);
        grid.flatten_rect(
            Vec2::new(-12.0, 0.0),
            Vec2::splat(8.0),
            4.0,
            2.0,
            Some(2.0),
            "pad:0",
        );
        let after_pad = grid.raw().to_vec();
        grid.begin_stroke("road:0");
        let mut weight =
            |p: Vec2| (1.0 - (p - Vec2::new(12.0, 0.0)).length() / 8.0).clamp(0.0, 1.0);
        let mut target = |_| 3.0_f32;
        grid.apply(BrushRequest {
            mode: BrushMode::Blend,
            min_x: 4.0,
            min_z: -8.0,
            max_x: 20.0,
            max_z: 8.0,
            target: &mut target,
            weight: &mut weight,
            guard: None,
        });
        grid.commit_stroke();
        grid.revert_last_stroke("road:0");
        assert_eq!(grid.raw(), after_pad, "pad texels survive the road revert");
    }

    #[test]
    fn test_revision_semantics() {
        let mut grid = flat_grid(8.0);
        assert_eq!(grid.revision(), 0);
        // A no-op stroke does not bump.
        grid.begin_stroke("noop");
        let mut weight = |_| 0.0_f32;
        let mut target = |_| 0.0_f32;
        let written = grid.apply(BrushRequest {
            mode: BrushMode::Blend,
            min_x: -1.0,
            min_z: -1.0,
            max_x: 1.0,
            max_z: 1.0,
            target: &mut target,
            weight: &mut weight,
            guard: None,
        });
        grid.commit_stroke();
        assert_eq!(written, 0);
        assert_eq!(grid.revision(), 0);
        // A writing stroke bumps exactly once.
        grid.begin_stroke("write");
        let mut weight = |_| 1.0_f32;
        let mut target = |_| 3.0_f32;
        grid.apply(BrushRequest {
            mode: BrushMode::Lower,
            min_x: -1.0,
            min_z: -1.0,
            max_x: 1.0,
            max_z: 1.0,
            target: &mut target,
            weight: &mut weight,
            guard: None,
        });
        grid.commit_stroke();
        assert_eq!(grid.revision(), 1);
    }

    #[test]
    fn test_min_effective_clamps_small_brushes() {
        let grid = flat_grid(8.0); // texel = 64/63 ≈ 1.0159
        let texel = grid.texel();
        assert!((min_effective(0.1, texel) - 1.5 * texel).abs() < 1e-4);
        assert!((min_effective(100.0, texel) - 100.0).abs() < 1e-4);
    }

    #[test]
    fn test_sample_bilinear_and_smoothing_match_flat() {
        let mut grid = flat_grid(6.0);
        assert!((grid.sample(3.3, 7.7) - 6.0).abs() < 1e-3);
        grid.smoothing = 1.0;
        assert!(
            (grid.sample(3.3, 7.7) - 6.0).abs() < 1e-3,
            "flat stays flat"
        );
    }

    #[test]
    fn test_sample_interpolates_a_ramp() {
        // world = n - 1 -> texel exactly 1 m, so height == x coordinate.
        let mut grid = BrushGrid::new(vec![0; 8 * 8], 8, 8, 7.0, 8.0, 0.0).expect("grid");
        grid.begin_stroke("ramp");
        for z in 0..8 {
            for x in 0..8 {
                grid.set_cell_height(x, z, x as f32);
            }
        }
        grid.commit_stroke();
        // Index i (height i) sits at world i - 3.5; the midpoint between the
        // texels with heights 3 and 4 is world 0.
        let mid = grid.sample(0.0, 3.0);
        assert!((mid - 3.5).abs() < 0.05, "bilinear midpoint: {mid}");
    }

    #[test]
    fn test_catmull_smoothing_does_not_overshoot() {
        // Two texels at 0, one at 10, one at 0: the monotone spline must stay
        // within [0, 10] between texels.
        let mut grid = BrushGrid::new(vec![0; 8 * 8], 8, 8, 8.0, 50.0, 1.0).expect("grid");
        grid.begin_stroke("peak");
        for z in 0..8 {
            for x in 0..8 {
                let h = match x {
                    2 | 3 => 10.0,
                    _ => 0.0,
                };
                grid.set_cell_height(x, z, h);
            }
        }
        grid.commit_stroke();
        for i in 0..40 {
            let x = i as f32 / 40.0 * 8.0 - 4.0 + 0.05;
            let h = grid.sample(x, 4.0);
            assert!(
                (-0.2..=10.2).contains(&h),
                "monotone CR overshoot at x={x}: {h}"
            );
        }
    }

    #[test]
    fn test_sample_normal_points_up_on_flat() {
        let grid = flat_grid(4.0);
        let n = grid.sample_normal(10.0, 10.0, 0.5);
        assert!((n - Vec3::Y).length() < 1e-4);
    }

    #[test]
    fn test_sample_normal_tilts_down_a_slope() {
        let mut grid = BrushGrid::new(vec![0; 16 * 16], 16, 16, 16.0, 50.0, 0.0).expect("grid");
        grid.begin_stroke("slope");
        for z in 0..16 {
            for x in 0..16 {
                grid.set_cell_height(x, z, x as f32 * 2.0);
            }
        }
        grid.commit_stroke();
        let n = grid.sample_normal(8.0, 8.0, 1.0);
        assert!(n.x < 0.0, "normal leans against +X slope: {n}");
        assert!(n.y > 0.0);
    }

    #[test]
    fn test_height_map_roundtrip() {
        let map = HeightMapU16::filled(9, 7, 12_345);
        let grid = BrushGrid::from_height_map(&map, 90.0, 40.0, 0.0).expect("grid");
        assert_eq!((grid.width(), grid.depth()), (9, 7));
        assert!((grid.cell_height(4, 3) - 12_345.0 / 65_535.0 * 40.0).abs() < 1e-3);
    }

    #[test]
    fn test_flatten_auto_and_explicit_agree_after_write() {
        let mut a = flat_grid(10.0);
        let mut b = flat_grid(10.0);
        let auto = a.flatten_rect(Vec2::ZERO, Vec2::splat(12.0), 6.0, 3.0, None, "p");
        b.flatten_rect(Vec2::ZERO, Vec2::splat(12.0), 6.0, 3.0, Some(auto), "p");
        assert_eq!(
            a.raw(),
            b.raw(),
            "auto mode equals explicit resolved height"
        );
    }
}
