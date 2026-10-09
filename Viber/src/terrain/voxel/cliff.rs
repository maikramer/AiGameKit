//! `<Cliff>` as a three-dimensional solid.
//!
//! # What changes, and why it is not a carve any more
//!
//! The 2.5D cliff writes a steep gradient into the shared height grid. That
//! representation caps the face **at** vertical by construction — `angle="90"`
//! is impossible (`width = height/tan(angle)` → 0), and the docs say so
//! outright: *"the face stays at or below vertical"*. It also throws the cliff
//! away: after the carve the only trace is a steeper patch of `u16`, which is
//! why `CliffMask` has to re-derive by erode/dilate/BFS what the carve knew
//! exactly.
//!
//! Here the cliff stays an object, and the parametrisation is inverted.
//!
//! * The carve asks **"how far down is the ground at lateral distance `t`?"** —
//!   `profile_s(t)`, necessarily single-valued, necessarily no overhang.
//! * A solid asks **"how far out is the face at depth `v`?"** —
//!   [`profile_offset`], which may go *negative*: the face cuts back under the
//!   crest and there is rock over your head.
//!
//! # Shape
//!
//! One [`CliffFaceMod`] per crest segment, each **subtracting** the air wedge
//! on the low side. Subtraction composes: cutting A then B is
//! `max(d, -A, -B) = max(d, -min(A, B))`, and `min` is the union of the
//! wedges — so a chain of segments cuts one continuous wall with no seams to
//! reconcile.
//!
//! Splitting per segment is not cosmetic. A whole-polyline mod would make
//! every density sample walk every station: a 180 m wall is ~90 stations, and
//! the mesher takes ~39 k samples per chunk. Per-segment mods live in the
//! index buckets, so a sample pays only for the few segments beside it.
//!
//! Determinism is preserved the same way the carve preserves it: every probe
//! of the terrain happens up front, in [`CliffBand::build`], before any mod
//! exists. Nothing here ever reads a surface it might itself have changed.

use bevy::math::{Vec2, Vec3};

use super::super::brush::min_effective;
use super::super::cliffs::{
    CliffProfile, CliffSide, CliffSpec, Column, TERRACE_TREAD, hash01, segment_left, wave,
};
use super::super::mesh::HeightField;
use super::super::paths::{chaikin_smooth, resample};
use super::mods::{Bounds3, CapsuleMod, ModOp, VoxelMod};

/// How far past the toe the cut reaches, as a multiple of the local width.
///
/// The wedge has to remove the natural ramp all the way out to where the
/// ground has already fallen below the toe. Too small and a shelf of old
/// terrain survives in front of the wall; too large and the cut eats
/// unrelated landscape.
const OUTER_REACH: f32 = 1.6;

/// Overlap (meters) between neighbouring segment wedges, so their union has
/// no hairline gap at the joins.
const SEGMENT_OVERLAP: f32 = 1.5;

/// Undercut depth of the `concave` profile, as a fraction of the width.
const CONCAVE_UNDERCUT: f32 = 0.35;
/// How far the `convex` brow bulges past its own toe, as a fraction of width.
const CONVEX_BULGE: f32 = 0.15;
/// Fraction of the drop over which a `vertical` face stays plumb before it
/// flares into the ground.
const VERTICAL_PLUMB: f32 = 0.82;
/// How deep an `overhang` face cuts BACK under the crest, as a fraction of the
/// band width.
///
/// The mod is subtractive, so a brow is not built out — it is what survives
/// when the recess beneath it is removed. A bigger number is a deeper roof.
/// `concave` uses a third of this and reads as a leaning quarry wall; this one
/// reads as shelter.
const OVERHANG_LEAN: f32 = 0.55;
/// Lip depth of a `terraced` tread, as a fraction of width — the bit that
/// makes a ledge a ledge instead of a staircase of ramps.
const TERRACE_LIP: f32 = 0.06;
/// Radius of the `arch` window, as a fraction of the local drop.
const ARCH_WINDOW_FRACTION: f32 = 0.30;
/// Centre height of the `arch` window above the toe, as a fraction of the
/// drop — low enough to walk a gaze under, high enough to clear the scree.
const ARCH_WINDOW_HEIGHT: f32 = 0.42;
/// How far behind the crest the `arch` bore reaches, in drops — deep enough
/// that the opening reads as a mouth into the rock, not a decal.
const ARCH_BORE_DEPTH: f32 = 1.2;
/// A window in a wall lower than this is a mousehole, not an arch.
const ARCH_MIN_DROP: f32 = 3.0;

fn smoothstep(edge0: f32, edge1: f32, x: f32) -> f32 {
    let span = (edge1 - edge0).max(1e-6);
    let t = ((x - edge0) / span).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// Lateral offset of the face at depth fraction `v`, as a fraction of the
/// local band width. `0` = at the crest line, `1` = a full band out, and
/// **negative = undercut**: rock above, air below.
///
/// This is the inverse parametrisation of the carve's `profile_s(t)`, and the
/// sign is the whole point of the exercise.
pub fn profile_offset(
    v: f32,
    profile: CliffProfile,
    steps: f32,
    arc: f32,
    seed: u64,
    column: Option<&Column>,
) -> f32 {
    let v = v.clamp(0.0, 1.0);
    match profile {
        // A real plumb wall: dead vertical for most of the drop, flaring
        // into the ground only at the very foot. The 2.5D `vertical` could
        // never do this — its smoothstep spread the drop over the middle
        // third of the band, which is a ~60° ramp.
        CliffProfile::Vertical => smoothstep(VERTICAL_PLUMB, 1.0, v),
        // Quarry wall with a genuine undercut: the face cuts back behind the
        // crest through the upper-middle of the drop, so the brow overhangs.
        CliffProfile::Concave => {
            v * v * v - CONCAVE_UNDERCUT * (std::f32::consts::PI * v.powf(0.8)).sin() * (1.0 - v)
        }
        // Rounded brow that bulges out past its own foot — the eyebrow the
        // docs describe, now actually overhanging instead of merely convex.
        CliffProfile::Convex => smoothstep(0.0, 0.5, v) * (1.0 + CONVEX_BULGE) - CONVEX_BULGE * v,
        // Basalt columns: the per-column plan offset is now a real lateral
        // shift of the solid, not a sampling trick, and the facet kinks are
        // planar slabs in 3D. The dark slit between columns is geometry.
        CliffProfile::Columnar => {
            let col = match column {
                Some(c) => *c,
                None => Column::new(seed, 0, 0.0, 1.0),
            };
            let facet = if v <= col.k1 {
                0.42 * (v / col.k1.max(1e-3))
            } else if v <= col.k2 {
                0.42 + 0.30 * ((v - col.k1) / (col.k2 - col.k1).max(1e-3))
            } else {
                0.72 + 0.28 * ((v - col.k2) / (1.0 - col.k2).max(1e-3))
            };
            facet + col.off
        }
        // Cuts hard back under the crest through the upper half and returns
        // to the band edge at the toe: the plateau above survives as a roof
        // and the recess under it is open at the front. The offset is
        // strongly NEGATIVE mid-face — that negative number is the overhang.
        CliffProfile::Overhang => {
            v * v * v - OVERHANG_LEAN * (std::f32::consts::PI * v.powf(0.7)).sin()
        }
        // Plumb contour like `vertical` — an `arch`'s feature is not the
        // profile but the window `into_mods` bores through the wall.
        CliffProfile::Arch => smoothstep(VERTICAL_PLUMB, 1.0, v),
        // Quantized benches. Each tread gets a lip: the riser cuts slightly
        // back under the bench above it, so a ledge reads as a ledge.
        CliffProfile::Terraced => {
            let steps = steps.max(1.0);
            let x = v * steps;
            let k = x.floor();
            let frac = x - k;
            let edge = 0.72 + 0.2 * hash01(seed, arc.to_bits() as u64, k as u64);
            let base = ((k + smoothstep(edge, 1.0, frac)) / steps).clamp(0.0, 1.0);
            // The lip is a short recess at the START of the tread — the bit
            // that makes the bench above overhang. Spreading it across the
            // whole tread would tilt the bench and turn the staircase back
            // into a ramp, which is what the 2.5D profile already was.
            base - TERRACE_LIP * (1.0 - smoothstep(0.0, 0.22, frac))
        }
    }
}

/// The per-station geometry of one authored `<Cliff>`, resolved against the
/// terrain **before** any mod exists.
#[derive(Debug, Clone)]
pub struct CliffBand {
    pub stations: Vec<Vec2>,
    /// Unit XZ normal at each station pointing toward the low side.
    pub drop_normal: Vec<Vec2>,
    pub top_y: Vec<f32>,
    pub bot_y: Vec<f32>,
    pub width: Vec<f32>,
    pub arc: Vec<f32>,
    pub columns: Vec<Column>,
    pub profile: CliffProfile,
    pub seed: u64,
    /// Ground just beyond the toe, per station — the talus apron rests on it.
    pub toe_ground: Vec<f32>,
    /// How far the debris apron runs past the toe, per station (0 = none).
    pub talus_run: Vec<f32>,
    pub talus: bool,
    pub talus_angle: f32,
}

/// Authored run of the band in meters: `width`, or `height/tan(angle)` when
/// both are authored. Feeds the probe offsets and the wobble amplitude —
/// authored numbers, so it never depends on the grid.
fn authored_run(spec: &CliffSpec, texel: f32) -> f32 {
    match (spec.height, spec.angle) {
        (Some(h), Some(a)) if h.is_finite() && h > 0.0 => {
            let t = a.to_radians().tan().abs().max(1e-2);
            (h / t).max(min_effective(1.0, texel))
        }
        _ => spec.width,
    }
}

impl CliffBand {
    /// Longest talus run anywhere on the band.
    pub fn talus_run_max(&self) -> f32 {
        self.talus_run.iter().copied().fold(0.0, f32::max)
    }

    /// Talus run at a point resolved against the crest polyline.
    pub fn talus_run_at(&self, hit: &super::super::paths::PathHit) -> f32 {
        super::super::paths::station_lerp(&self.talus_run, hit)
    }
}

impl CliffBand {
    /// Resolves stations, side, crest/toe planes, notches and band wobble.
    ///
    /// A faithful port of the front half of `carve_cliff`: same smoothing,
    /// same spacing, same four probes, same side rule, same seeded harmonics.
    /// Worlds keep the walls they were authored with; only the third
    /// dimension is new.
    ///
    /// The geometry half (stations, widths, columns) is height-independent
    /// and resolved once. Every terrain probe lives in
    /// [`Self::probe_heights`], which `build` runs exactly once — and which
    /// the bootstrap may run again when a later pass (sharpen) rewrites the
    /// ground under an already-resolved band.
    pub fn build(spec: &CliffSpec, base: &dyn HeightField, texel: f32) -> Option<Self> {
        if spec.path.len() < 2 || !spec.width.is_finite() || spec.width <= 0.0 {
            return None;
        }
        if let Some(a) = spec.angle
            && (!a.is_finite() || a <= 0.0)
        {
            return None;
        }
        let spacing = (texel * 2.0).max(1.0);
        let smoothed = chaikin_smooth(&spec.path, 2, false);
        let stations = resample(&smoothed, spacing);
        if stations.len() < 2 {
            return None;
        }

        // Authored angle (with height) sets the run. Unlike the carve, an
        // angle at or past 90° is now meaningful — it is an overhang — so the
        // run is taken from the absolute tangent and the lean is applied by
        // the profile.
        let base_width = authored_run(spec, texel);
        if !base_width.is_finite() || base_width <= 0.0 {
            return None;
        }

        // Band wobble + gullies, exactly as the carve seeds them.
        let (p1, p2, p3) = (
            hash01(spec.seed, 1, 7) * std::f32::consts::TAU,
            hash01(spec.seed, 2, 11) * std::f32::consts::TAU,
            hash01(spec.seed, 3, 13) * std::f32::consts::TAU,
        );
        let (g1p, g2p) = (
            hash01(spec.seed, 4, 17) * std::f32::consts::TAU,
            hash01(spec.seed, 5, 19) * std::f32::consts::TAU,
        );
        let gully_cut = |arc: f32| -> f32 {
            if spec.gullies <= 0.0 {
                return 0.0;
            }
            let a = 0.5 + 0.5 * (arc * 0.55 + g1p).sin();
            let b = 0.5 + 0.5 * (arc * 0.83 + g2p).sin();
            (a * a * 0.65 + b * b * 0.35).powf(1.6)
        };

        let mut arc = Vec::with_capacity(stations.len());
        let mut acc = 0.0;
        arc.push(0.0);
        for w in stations.windows(2) {
            acc += w[0].distance(w[1]);
            arc.push(acc);
        }
        let width: Vec<f32> = arc
            .iter()
            .map(|a| {
                (wave(*a, p1, p2, p3, spec.noise, base_width)
                    - spec.gullies * base_width * gully_cut(*a))
                .max(min_effective(1.0, texel))
            })
            .collect();

        let columns: Vec<Column> = if spec.profile == CliffProfile::Columnar {
            let total = acc + base_width * 4.0;
            let mut cols = Vec::new();
            let mut start = -base_width;
            let mut i = 0u64;
            while start < total {
                let end = start + 2.2 + hash01(spec.seed, i, 7) * 3.3;
                cols.push(Column::new(spec.seed, i, start, end));
                start = end;
                i += 1;
            }
            cols
        } else {
            Vec::new()
        };

        let mut band = Self {
            stations,
            drop_normal: Vec::new(),
            top_y: Vec::new(),
            bot_y: Vec::new(),
            width,
            arc,
            columns,
            profile: spec.profile,
            seed: spec.seed,
            toe_ground: Vec::new(),
            talus_run: Vec::new(),
            talus: spec.talus,
            talus_angle: spec.talus_angle,
        };
        band.probe_heights(spec, base, texel);
        Some(band)
    }

    /// Re-probes every terrain height this band baked in, against `base` as
    /// it stands NOW.
    ///
    /// `build` resolves a band against the carved grid — but the bootstrap
    /// builds the bands BEFORE the optional sharpen pass (the cliff mask
    /// needs their footprints to gate it), and sharpen then rewrites the
    /// ground inside those very footprints. Walls left resolved against the
    /// old field floated over or buried under the terraced terrain that the
    /// meshes and gameplay actually read. Only the height columns are
    /// re-sampled: stations, widths and columns are height-independent and
    /// stay exactly as authored.
    pub fn probe_heights(&mut self, spec: &CliffSpec, base: &dyn HeightField, texel: f32) {
        let base_width = authored_run(spec, texel);
        let inner_off = texel * 1.5;
        let outer_off = base_width + texel * 2.0;
        let last = self.stations.len() - 1;
        let mut normals = Vec::with_capacity(self.stations.len());
        let (mut ip, mut op_, mut im, mut om) = (
            Vec::with_capacity(self.stations.len()),
            Vec::with_capacity(self.stations.len()),
            Vec::with_capacity(self.stations.len()),
            Vec::with_capacity(self.stations.len()),
        );
        for (i, st) in self.stations.iter().enumerate() {
            let n = segment_left(&self.stations, if i == last { i - 1 } else { i });
            normals.push(n);
            ip.push(base.sample(st.x + n.x * inner_off, st.y + n.y * inner_off));
            op_.push(base.sample(st.x + n.x * outer_off, st.y + n.y * outer_off));
            im.push(base.sample(st.x - n.x * inner_off, st.y - n.y * inner_off));
            om.push(base.sample(st.x - n.x * outer_off, st.y - n.y * outer_off));
        }

        let mean = |v: &[f32]| v.iter().sum::<f32>() / v.len().max(1) as f32;
        let crest_higher_plus = mean(&ip) >= mean(&im);
        let (mut top_y, mut bot_y, drop_sign) = match spec.side {
            CliffSide::Left => (ip, op_, 1.0f32),
            CliffSide::Right => (im, om, -1.0f32),
            CliffSide::Auto if crest_higher_plus => (ip, om, -1.0f32),
            CliffSide::Auto => (im, op_, 1.0f32),
        };

        if let Some(h) = spec.height.filter(|h| h.is_finite() && *h > 0.0) {
            for i in 0..bot_y.len() {
                bot_y[i] = bot_y[i].min(top_y[i] - h);
            }
        }

        // Crest notches — one-sided dips, scaled by the LOCAL drop.
        if spec.notches > 0.0 {
            let (n1p, n2p) = (
                hash01(spec.seed, 6, 23) * std::f32::consts::TAU,
                hash01(spec.seed, 7, 29) * std::f32::consts::TAU,
            );
            let mut n_arc = 0.0;
            for i in 0..top_y.len() {
                if i > 0 {
                    n_arc += self.stations[i].distance(self.stations[i - 1]);
                }
                let a = 0.5 + 0.5 * (n_arc * 0.16 + n1p).sin();
                let b = 0.5 + 0.5 * (n_arc * 0.29 + n2p).sin();
                let dip = (a * a * 0.7 + b * b * 0.3).powf(1.4);
                top_y[i] -= spec.notches * (top_y[i] - bot_y[i]).max(0.0) * dip;
            }
        }

        // Drop normals and the ground the talus apron would rest on.
        self.drop_normal = normals.iter().map(|n| *n * drop_sign).collect();
        // Ground the apron dies into, probed past the band AND past the
        // apron's own run — the carve does the same, and probing too close
        // would rest the cone on the wall's own foot instead of on the valley.
        let cot = 1.0 / self.talus_angle.clamp(5.0, 60.0).to_radians().tan();
        self.toe_ground = self
            .stations
            .iter()
            .enumerate()
            .map(|(i, st)| {
                let run = if self.talus {
                    ((top_y[i] - bot_y[i]).max(0.0) * cot * 0.55).min(40.0)
                } else {
                    0.0
                };
                let d = self.width[i] + run + texel * 2.0;
                let p = *st + self.drop_normal[i] * d;
                base.sample(p.x, p.y)
            })
            .collect();

        self.talus_run = if self.talus {
            (0..self.stations.len())
                .map(|i| ((top_y[i] - bot_y[i]).max(0.0) * cot * 0.55).min(40.0))
                .collect()
        } else {
            vec![0.0; self.stations.len()]
        };
        self.top_y = top_y;
        self.bot_y = bot_y;
    }

    /// Column covering this arc position, if the profile is columnar.
    fn column_at(&self, arc: f32) -> Option<&Column> {
        self.columns
            .iter()
            .find(|c| arc >= c.start && arc < c.end)
            .or(self.columns.last())
    }

    /// Turns the band into the mods that carve it out of the world.
    pub fn into_mods(self, label: &str) -> Vec<Box<dyn VoxelMod>> {
        let mut mods: Vec<Box<dyn VoxelMod>> = Vec::new();
        let n = self.stations.len();
        for i in 0..n - 1 {
            let col = self
                .column_at((self.arc[i] + self.arc[i + 1]) * 0.5)
                .copied();
            mods.push(Box::new(CliffFaceMod {
                a: self.stations[i],
                b: self.stations[i + 1],
                normal: (self.drop_normal[i] + self.drop_normal[i + 1]).normalize_or_zero(),
                top: (self.top_y[i], self.top_y[i + 1]),
                bot: (self.bot_y[i], self.bot_y[i + 1]),
                width: (self.width[i], self.width[i + 1]),
                arc: self.arc[i],
                profile: self.profile,
                seed: self.seed,
                column: col,
                over_start: if i == 0 { 0.0 } else { SEGMENT_OVERLAP },
                over_end: if i == n - 2 { 0.0 } else { SEGMENT_OVERLAP },
                label: format!("{label}:face:{i}"),
            }));
            if self.talus {
                let run = |k: usize| self.talus_run[k];
                if run(i) > 0.25 || run(i + 1) > 0.25 {
                    mods.push(Box::new(TalusMod {
                        a: self.stations[i],
                        b: self.stations[i + 1],
                        normal: (self.drop_normal[i] + self.drop_normal[i + 1]).normalize_or_zero(),
                        start: (self.width[i], self.width[i + 1]),
                        run: (run(i), run(i + 1)),
                        crest: (self.bot_y[i], self.bot_y[i + 1]),
                        toe: (self.toe_ground[i], self.toe_ground[i + 1]),
                        label: format!("{label}:talus:{i}"),
                    }));
                }
            }
        }
        // `arch`: one window bored through the wall at the band's middle — a
        // horizontal capsule crossing the whole band depth and reaching back
        // behind the crest. The contour above stays plumb; the void is the
        // feature, and only the voxel field can hold rock above, air and
        // rock below at the same XZ. Deterministic by construction: the
        // middle station, no RNG anywhere.
        if self.profile == CliffProfile::Arch {
            let mid = n / 2;
            let drop = (self.top_y[mid] - self.bot_y[mid]).max(1e-3);
            if drop >= ARCH_MIN_DROP {
                let radius = drop * ARCH_WINDOW_FRACTION;
                let centre_y = self.bot_y[mid] + drop * ARCH_WINDOW_HEIGHT;
                let crest = self.stations[mid];
                let normal = self.drop_normal[mid];
                let reach = self.width[mid] + radius + 2.0;
                let a = Vec3::new(
                    crest.x - normal.x * drop * ARCH_BORE_DEPTH,
                    centre_y,
                    crest.y - normal.y * drop * ARCH_BORE_DEPTH,
                );
                let b = Vec3::new(
                    crest.x + normal.x * reach,
                    centre_y,
                    crest.y + normal.y * reach,
                );
                mods.push(Box::new(CapsuleMod::new(
                    format!("{label}:window"),
                    a,
                    b,
                    radius,
                    ModOp::Subtract,
                )));
            }
        }
        mods
    }
}

/// Local frame of a point against a crest segment.
struct Local {
    /// Fraction along the segment (clamped).
    h: f32,
    /// Signed lateral distance; positive toward the low side.
    d: f32,
    /// Distance along the segment from `a` (unclamped).
    s: f32,
    /// Segment length.
    len: f32,
}

fn local_frame(p: Vec3, a: Vec2, b: Vec2, normal: Vec2) -> Local {
    let ab = b - a;
    let len = ab.length();
    let dir = if len > 1e-6 { ab / len } else { Vec2::X };
    let rel = Vec2::new(p.x, p.z) - a;
    let s = rel.dot(dir);
    let h = if len > 1e-6 {
        (s / len).clamp(0.0, 1.0)
    } else {
        0.0
    };
    let c = a + dir * s.clamp(0.0, len);
    let d = (Vec2::new(p.x, p.z) - c).dot(normal);
    Local { h, d, s, len }
}

fn lerp(a: f32, b: f32, t: f32) -> f32 {
    a + (b - a) * t
}

/// The air wedge on the low side of one crest segment.
///
/// Subtracting it turns whatever ramp the terrain had into the authored face.
#[derive(Debug, Clone)]
pub struct CliffFaceMod {
    a: Vec2,
    b: Vec2,
    normal: Vec2,
    top: (f32, f32),
    bot: (f32, f32),
    width: (f32, f32),
    arc: f32,
    profile: CliffProfile,
    seed: u64,
    column: Option<Column>,
    over_start: f32,
    over_end: f32,
    label: String,
}

impl VoxelMod for CliffFaceMod {
    fn distance(&self, p: Vec3) -> f32 {
        let f = local_frame(p, self.a, self.b, self.normal);
        let top = lerp(self.top.0, self.top.1, f.h);
        let bot = lerp(self.bot.0, self.bot.1, f.h);
        let w = lerp(self.width.0, self.width.1, f.h).max(0.05);
        let drop = (top - bot).max(1e-3);
        let v = ((top - p.y) / drop).clamp(0.0, 1.0);
        let steps = (w / TERRACE_TREAD).round().clamp(2.0, 8.0);
        let off = w * profile_offset(
            v,
            self.profile,
            steps,
            self.arc,
            self.seed,
            self.column.as_ref(),
        );

        // Intersection of half-spaces: inside the wedge when every term is
        // negative. Beyond the face, above the toe, below the crest, within
        // the segment's span and within reach of the band.
        let outer = w * OUTER_REACH;
        [
            off - f.d,
            bot - p.y,
            p.y - top,
            f.d - outer,
            -(f.s + self.over_start),
            f.s - f.len - self.over_end,
        ]
        .into_iter()
        .fold(f32::NEG_INFINITY, f32::max)
    }

    fn bounds(&self) -> Bounds3 {
        let w = self.width.0.max(self.width.1);
        let reach = w * OUTER_REACH + 1.0;
        let tan = Vec2::new(self.normal.y, -self.normal.x);
        let pad = SEGMENT_OVERLAP + 1.0;
        let corners = [
            self.a - tan * pad,
            self.b + tan * pad,
            self.a + self.normal * reach - tan * pad,
            self.b + self.normal * reach + tan * pad,
        ];
        let (mut lo, mut hi) = (Vec2::splat(f32::INFINITY), Vec2::splat(f32::NEG_INFINITY));
        for c in corners {
            lo = lo.min(c);
            hi = hi.max(c);
        }
        // The undercut reaches back behind the crest, so the box has to as
        // well — clip it at the crest and the roof loses its back wall. The
        // margin follows the profile's real deepest cut: `concave` bottoms
        // out around −0.21·w, `overhang` at v³ − LEAN·sin(π·v^0.7) reaches
        // ≈ −0.51·w (measured in the tests) — a margin sized for `concave`
        // clipped wide overhang bands and left an uncut flat wall inside the
        // recess.
        let lean = match self.profile {
            CliffProfile::Overhang => OVERHANG_LEAN,
            _ => CONCAVE_UNDERCUT,
        };
        let back = w * (lean + 0.1) + 1.0;
        let lo = lo - self.normal.abs() * back;
        let y_lo = self.bot.0.min(self.bot.1) - 1.0;
        let y_hi = self.top.0.max(self.top.1) + 1.0;
        Bounds3::from_corners(Vec3::new(lo.x, y_lo, lo.y), Vec3::new(hi.x, y_hi, hi.y))
    }

    fn op(&self) -> ModOp {
        ModOp::Subtract
    }

    fn label(&self) -> &str {
        &self.label
    }
}

/// Debris apron at the foot of a cliff segment — additive rock, resting on
/// the natural ground beyond the toe at the angle of repose.
#[derive(Debug, Clone)]
pub struct TalusMod {
    a: Vec2,
    b: Vec2,
    normal: Vec2,
    /// Lateral distance where the apron starts (the band toe).
    start: (f32, f32),
    /// How far out it runs.
    run: (f32, f32),
    /// Height at the wall foot.
    crest: (f32, f32),
    /// Ground it dies into.
    toe: (f32, f32),
    label: String,
}

impl VoxelMod for TalusMod {
    fn distance(&self, p: Vec3) -> f32 {
        let f = local_frame(p, self.a, self.b, self.normal);
        let start = lerp(self.start.0, self.start.1, f.h);
        let run = lerp(self.run.0, self.run.1, f.h).max(1e-3);
        let crest = lerp(self.crest.0, self.crest.1, f.h);
        let toe = lerp(self.toe.0, self.toe.1, f.h);
        // Cone profile: full height at the wall, dying into the ground at the
        // end of the run.
        let t = ((f.d - start) / run).clamp(0.0, 1.0);
        let surface = lerp(crest, toe, t * t);
        [
            p.y - surface,
            start - f.d,
            f.d - (start + run),
            -f.s,
            f.s - f.len,
        ]
        .into_iter()
        .fold(f32::NEG_INFINITY, f32::max)
    }

    fn bounds(&self) -> Bounds3 {
        let reach = self.start.0.max(self.start.1) + self.run.0.max(self.run.1) + 1.0;
        let tan = Vec2::new(self.normal.y, -self.normal.x);
        let corners = [
            self.a - tan,
            self.b + tan,
            self.a + self.normal * reach - tan,
            self.b + self.normal * reach + tan,
        ];
        let (mut lo, mut hi) = (Vec2::splat(f32::INFINITY), Vec2::splat(f32::NEG_INFINITY));
        for c in corners {
            lo = lo.min(c);
            hi = hi.max(c);
        }
        let y_lo = self.toe.0.min(self.toe.1) - 2.0;
        let y_hi = self.crest.0.max(self.crest.1) + 1.0;
        Bounds3::from_corners(Vec3::new(lo.x, y_lo, lo.y), Vec3::new(hi.x, y_hi, hi.y))
    }

    fn op(&self) -> ModOp {
        ModOp::Union
    }

    fn label(&self) -> &str {
        &self.label
    }
}

/// Builds every mod for one authored `<Cliff>`.
pub fn build_cliff_mods(
    spec: &CliffSpec,
    base: &dyn HeightField,
    texel: f32,
    index: usize,
) -> Vec<Box<dyn VoxelMod>> {
    let label = format!("cliff:{index}");
    match CliffBand::build(spec, base, texel) {
        Some(band) => band.into_mods(&label),
        None => Vec::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::terrain::voxel::VoxelField;

    /// Plateau to the west, valley to the east, with a natural step at x = 0.
    #[derive(Debug)]
    struct Step;

    impl HeightField for Step {
        fn sample(&self, x: f32, _z: f32) -> f32 {
            if x < 0.0 { 40.0 } else { 16.0 }
        }
        fn sample_normal(&self, _x: f32, _z: f32, _e: f32) -> Vec3 {
            Vec3::Y
        }
        fn max_height(&self) -> f32 {
            40.0
        }
        fn range_over(&self, min_x: f32, _a: f32, max_x: f32, _b: f32) -> Option<(f32, f32)> {
            let lo: f32 = if max_x < 0.0 { 40.0 } else { 16.0 };
            let hi: f32 = if min_x < 0.0 { 40.0 } else { 16.0 };
            Some((lo.min(hi), lo.max(hi)))
        }
    }

    fn spec(profile: CliffProfile) -> CliffSpec {
        CliffSpec {
            path: vec![Vec2::new(0.0, -40.0), Vec2::new(0.0, 40.0)],
            width: 10.0,
            height: Some(20.0),
            profile,
            seed: 7,
            ..CliffSpec::default()
        }
    }

    // ------------------------------------------------------- profile shape

    #[test]
    fn test_vertical_profile_is_actually_plumb() {
        // The 2.5D `vertical` spread its drop over the middle third of the
        // band — a ~60° ramp wearing the name. A plumb face keeps the offset
        // at zero for most of the drop.
        for v in [0.1, 0.3, 0.5, 0.7] {
            let off = profile_offset(v, CliffProfile::Vertical, 4.0, 0.0, 7, None);
            assert!(
                off.abs() < 0.02,
                "vertical face wandered {off} at depth {v} — not plumb"
            );
        }
        // And it still meets the ground at the toe.
        let toe = profile_offset(1.0, CliffProfile::Vertical, 4.0, 0.0, 7, None);
        assert!(
            (toe - 1.0).abs() < 1e-5,
            "toe must reach the band edge: {toe}"
        );
    }

    #[test]
    fn test_concave_profile_undercuts() {
        // The headline: a NEGATIVE offset is rock over your head. The 2.5D
        // profile could only ever lean back — "as close to an undercut as a
        // 2.5D heightfield gets", per its own doc comment.
        let mut deepest: f32 = 0.0;
        for i in 0..=100 {
            let v = i as f32 / 100.0;
            deepest = deepest.min(profile_offset(v, CliffProfile::Concave, 4.0, 0.0, 7, None));
        }
        assert!(
            deepest < -0.08,
            "concave never cut back behind the crest (min offset {deepest}) — no overhang"
        );
        assert!(
            (profile_offset(1.0, CliffProfile::Concave, 4.0, 0.0, 7, None) - 1.0).abs() < 1e-5,
            "the toe still has to land on the band edge"
        );
    }

    #[test]
    fn test_convex_brow_bulges_past_its_own_foot() {
        let foot = profile_offset(1.0, CliffProfile::Convex, 4.0, 0.0, 7, None);
        let mut widest: f32 = f32::NEG_INFINITY;
        for i in 0..=100 {
            widest = widest.max(profile_offset(
                i as f32 / 100.0,
                CliffProfile::Convex,
                4.0,
                0.0,
                7,
                None,
            ));
        }
        assert!(
            widest > foot + 0.02,
            "convex brow {widest} does not overhang its foot {foot}"
        );
    }

    #[test]
    fn test_terraced_profile_quantizes_into_benches() {
        let samples: Vec<f32> = (0..=200)
            .map(|i| profile_offset(i as f32 / 200.0, CliffProfile::Terraced, 5.0, 0.0, 7, None))
            .collect();
        // A staircase has flat runs: many consecutive samples barely move.
        let flat = samples
            .windows(2)
            .filter(|w| (w[1] - w[0]).abs() < 1e-3)
            .count();
        assert!(
            flat > 60,
            "terraced profile is a ramp, not benches ({flat})"
        );
    }

    #[test]
    fn test_overhang_profile_leans_out_over_its_own_foot() {
        // A subtractive mod cannot push rock out; the brow is what is left
        // when the recess under it is cut away. So the measure of an overhang
        // is how far the offset goes NEGATIVE — behind the crest line.
        let mut deepest: f32 = 0.0;
        let mut at = 0.0;
        for i in 0..=100 {
            let v = i as f32 / 100.0;
            let off = profile_offset(v, CliffProfile::Overhang, 4.0, 0.0, 7, None);
            if off < deepest {
                deepest = off;
                at = v;
            }
        }
        assert!(
            deepest < -0.35,
            "overhang only cut back {deepest} — that is a lean, not a roof"
        );
        assert!(
            (0.1..0.8).contains(&at),
            "the roof should be cut in the upper face, not at an end (at {at})"
        );
        // And it still lands on the band edge at the toe.
        let toe = profile_offset(1.0, CliffProfile::Overhang, 4.0, 0.0, 7, None);
        assert!((toe - 1.0).abs() < 0.05, "toe drifted to {toe}");
        // Deliberately deeper than `concave`, which is the milder version.
        let mut concave_deepest: f32 = 0.0;
        for i in 0..=100 {
            concave_deepest = concave_deepest.min(profile_offset(
                i as f32 / 100.0,
                CliffProfile::Concave,
                4.0,
                0.0,
                7,
                None,
            ));
        }
        assert!(
            deepest < concave_deepest,
            "overhang ({deepest}) must undercut more than concave ({concave_deepest})"
        );
    }

    #[test]
    fn test_an_overhang_cliff_roofs_a_recess_you_can_stand_in() {
        let field = field_for(CliffProfile::Overhang);
        let mut best: Option<(f32, f32)> = None;
        for i in 0..=80 {
            let x = -4.0 + i as f32 * 0.25;
            let spans = field.column(&Step, x, 0.0);
            if spans.len() < 2 {
                continue;
            }
            let head = spans[0].bottom - spans[1].top;
            if best.is_none_or(|(h, _)| head > h) {
                best = Some((head, x));
            }
        }
        let (head, x) = best.expect("an overhang profile must roof something");
        assert!(head > 1.0, "the recess at x={x} is only {head:.2} m tall");
    }

    #[test]
    fn test_every_profile_stays_finite_across_the_whole_drop() {
        for profile in [
            CliffProfile::Vertical,
            CliffProfile::Concave,
            CliffProfile::Convex,
            CliffProfile::Columnar,
            CliffProfile::Terraced,
            CliffProfile::Overhang,
        ] {
            for i in 0..=100 {
                let v = i as f32 / 100.0;
                let off = profile_offset(v, profile, 4.0, 3.5, 7, None);
                assert!(off.is_finite(), "{profile:?} produced {off} at {v}");
                assert!(off.abs() < 4.0, "{profile:?} ran away to {off} at {v}");
            }
        }
    }

    // ------------------------------------------------------------- the band

    #[test]
    fn test_band_resolves_the_high_side_as_the_crest() {
        let band = CliffBand::build(&spec(CliffProfile::Vertical), &Step, 1.0).expect("band");
        // Plateau is west (x < 0), so the face has to drop toward +X.
        for n in &band.drop_normal {
            assert!(n.x > 0.5, "drop normal {n} should point at the valley");
        }
        for (t, b) in band.top_y.iter().zip(band.bot_y.iter()) {
            assert!(t > b, "crest {t} must stand above the toe {b}");
        }
    }

    #[test]
    fn test_authored_height_pins_the_toe() {
        let band = CliffBand::build(&spec(CliffProfile::Vertical), &Step, 1.0).expect("band");
        for (t, b) in band.top_y.iter().zip(band.bot_y.iter()) {
            assert!(
                (t - b) >= 19.9,
                "authored height=20 must hold the drop, got {}",
                t - b
            );
        }
    }

    #[test]
    fn test_band_build_is_deterministic() {
        let a = CliffBand::build(&spec(CliffProfile::Columnar), &Step, 1.0).expect("band");
        let b = CliffBand::build(&spec(CliffProfile::Columnar), &Step, 1.0).expect("band");
        assert_eq!(a.top_y, b.top_y);
        assert_eq!(a.bot_y, b.bot_y);
        assert_eq!(a.width, b.width);
        assert_eq!(a.columns.len(), b.columns.len());
    }

    #[test]
    fn test_degenerate_specs_build_nothing() {
        let mut s = spec(CliffProfile::Vertical);
        s.path = vec![Vec2::ZERO];
        assert!(CliffBand::build(&s, &Step, 1.0).is_none());
        let mut s = spec(CliffProfile::Vertical);
        s.width = 0.0;
        s.height = None;
        s.angle = None;
        assert!(CliffBand::build(&s, &Step, 1.0).is_none());
        let mut s = spec(CliffProfile::Vertical);
        s.angle = Some(-10.0);
        assert!(CliffBand::build(&s, &Step, 1.0).is_none());
    }

    // ------------------------------------------------------- the solid

    fn field_for(profile: CliffProfile) -> VoxelField {
        let mods = build_cliff_mods(&spec(profile), &Step, 1.0, 0);
        assert!(!mods.is_empty(), "{profile:?} produced no mods");
        VoxelField::new(mods, 512.0, 64.0)
    }

    #[test]
    fn test_the_face_removes_the_ramp_in_front_of_the_crest() {
        let field = field_for(CliffProfile::Vertical);
        // Just past the crest, high up: the step said 16 m of valley floor,
        // and the wall does not add rock there — but it must not leave the
        // old ramp either. Sample right at the face: solid behind, air ahead.
        let behind = field.density(&Step, Vec3::new(-2.0, 30.0, 0.0));
        let ahead = field.density(&Step, Vec3::new(8.0, 30.0, 0.0));
        assert!(behind < 0.0, "the plateau behind the crest must stay rock");
        assert!(ahead > 0.0, "the air in front of the wall must stay air");
    }

    #[test]
    fn test_a_concave_cliff_puts_rock_over_your_head() {
        // The whole reason the system exists. Somewhere along the undercut
        // there must be a column with two solid spans and standing room
        // between them.
        let field = field_for(CliffProfile::Concave);
        let mut best: Option<(f32, f32, f32)> = None;
        for i in 0..=60 {
            let x = -6.0 + i as f32 * 0.2;
            for j in 0..9 {
                let z = -20.0 + j as f32 * 5.0;
                let spans = field.column(&Step, x, z);
                if spans.len() < 2 {
                    continue;
                }
                let head = spans[0].bottom - spans[1].top;
                if best.is_none_or(|(h, _, _)| head > h) {
                    best = Some((head, x, z));
                }
            }
        }
        let (head, x, z) = best.expect(
            "no column under the concave face had rock above air — the undercut \
             did not survive into the solid",
        );
        assert!(
            head > 0.5,
            "the undercut at ({x}, {z}) is only {head:.2} m deep"
        );
    }

    #[test]
    fn test_the_top_surface_query_still_answers_the_plateau() {
        // Every existing gameplay call site asks `sample()`; over a cliff it
        // must keep meaning "the ground you stand on", not the cave roof.
        let field = field_for(CliffProfile::Concave);
        let top = field.surface_top(&Step, -6.0, 0.0);
        assert!(
            (top - 40.0).abs() < 1.0,
            "on the plateau the top surface must still be the plateau, got {top}"
        );
    }

    #[test]
    fn test_talus_adds_rock_at_the_foot_instead_of_removing_it() {
        let mut s = spec(CliffProfile::Vertical);
        s.talus = true;
        let mods = build_cliff_mods(&s, &Step, 1.0, 0);
        let talus: Vec<_> = mods.iter().filter(|m| m.op() == ModOp::Union).collect();
        assert!(!talus.is_empty(), "talus must contribute additive mods");
        for m in talus {
            assert!(m.label().contains("talus"));
        }
    }

    #[test]
    fn test_face_bounds_reach_behind_the_crest_for_the_undercut() {
        // A box clipped at the crest line would cut the roof off the overhang.
        let mods = build_cliff_mods(&spec(CliffProfile::Concave), &Step, 1.0, 0);
        let face = mods
            .iter()
            .find(|m| m.op() == ModOp::Subtract)
            .expect("a face mod");
        let b = face.bounds();
        assert!(
            b.min.x < -0.5,
            "bounds stop at the crest ({}), the undercut has nowhere to live",
            b.min.x
        );
    }

    #[test]
    fn test_overhang_bounds_reach_past_the_deepest_cut_of_the_profile() {
        // The box has to contain every offset the distance function can
        // generate: the overhang curve bottoms out at v³ − LEAN·sin(π·v^0.7)
        // ≈ −0.51·w. A back margin sized for `concave` (0.45·w + 1) clipped
        // that on wide bands and the recess kept an uncut flat wall.
        let w = 30.0;
        let face = CliffFaceMod {
            a: Vec2::new(0.0, -40.0),
            b: Vec2::new(0.0, 40.0),
            normal: Vec2::X,
            top: (40.0, 40.0),
            bot: (16.0, 16.0),
            width: (w, w),
            arc: 0.0,
            profile: CliffProfile::Overhang,
            seed: 7,
            column: None,
            over_start: 0.0,
            over_end: 0.0,
            label: "overhang".into(),
        };
        // The real minimum of the curve, measured — the constant and the
        // margin in `bounds` are sized against it.
        let mut deepest = 0.0_f32;
        for i in 0..=1000 {
            let v = i as f32 / 1000.0;
            deepest = deepest.min(profile_offset(v, CliffProfile::Overhang, 4.0, 0.0, 7, None));
        }
        assert!(
            deepest > -OVERHANG_LEAN && deepest < -(OVERHANG_LEAN - 0.05),
            "the profile's real lean {} has drifted from OVERHANG_LEAN {} — \
             `bounds` sizes its back margin from the constant",
            -deepest,
            OVERHANG_LEAN
        );
        let b = face.bounds();
        assert!(
            b.min.x < deepest * w,
            "bounds stop at {} but the face cuts to {}·w — the recess keeps a \
             wall the profile never removed",
            b.min.x,
            deepest
        );
    }

    #[test]
    fn test_mods_are_split_per_segment_so_a_sample_pays_for_a_few() {
        // A whole-polyline mod would make every density sample walk every
        // station: ~90 stations x ~39k samples per chunk.
        let mods = build_cliff_mods(&spec(CliffProfile::Vertical), &Step, 1.0, 0);
        assert!(
            mods.len() > 8,
            "an 80 m crest should split up: {}",
            mods.len()
        );
        for m in &mods {
            let b = m.bounds();
            let span = b.max - b.min;
            assert!(
                span.z < 30.0,
                "segment mod covers {span:?} — too coarse to bucket usefully"
            );
        }
    }
}
