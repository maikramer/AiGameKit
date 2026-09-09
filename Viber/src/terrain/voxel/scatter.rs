//! `<RockFeatures>` — arches, caves and rock bridges seeded over a region.
//!
//! Every volumetric feature so far is authored point by point: someone picks
//! the XZ, reads the terrain, and writes the tag. That is the right tool for
//! the landmark you meant, and the wrong one for the twenty background
//! features that make a badlands read as badlands.
//!
//! This tag seeds them. It is **not** a new kind of solid: it resolves into
//! ordinary [`ArchSpec`], [`CaveSpec`] and [`BridgeSpec`] values, which then
//! take exactly the same path through the bootstrap as if they had been
//! typed by hand. Nothing downstream knows the difference, so nothing
//! downstream had to change.
//!
//! # Site discipline
//!
//! A cave is the only seeded feature that SUBTRACTS — its mouth ramp cuts an
//! open notch into the ground wherever the terrain does not absorb it. So a
//! cave is only seeded where the ground climbs ahead of the site (the
//! pé-de-montanha test, `min-rise`), and the WHOLE resampled path must
//! clear every guard — water, roads, cliff walls, pads, features already in
//! the world — without ever diving into somebody else's valley. A candidate
//! that fails is skipped, never forced: budgets are ceilings, not quotas.
//! Arches and bridges are union solids and keep the lighter site checks.
//!
//! # Determinism
//!
//! The seeding is a pure function of `(spec, height field, guards)`: a
//! jittered lattice hashed with [`hash01`], the same generator the cliffs use.
//! No RNG state, no iteration order from a hash map — the same world authored
//! twice seeds the same rocks in the same places, which is the contract every
//! [`VoxelMod`](super::mods::VoxelMod) build already keeps.
//!
//! ```xml
//! <RockFeatures name="badlands" region="-200 -200 200 200" seed="9"
//!               arches="6" caves="3" bridges="2"
//!               min-slope="25" max-slope="70" min-drop="6" spacing="40" />
//! ```

use bevy::math::Vec2;

use super::super::cliffs::{CliffMask, hash01};
use super::super::mesh::HeightField;
use super::super::paths::resample;
use super::super::roads::RoadPath;
use super::super::sampler::ResolvedPad;
use super::super::water::WaterBody;
use super::arch::{ArchProfile, ArchSpec};
use super::bridge::{BridgeSpec, BridgeStyle};
use super::cave::CaveSpec;

/// Default spacing of the candidate lattice (meters).
pub const DEFAULT_SCATTER_SPACING: f32 = 40.0;
/// Default slope band a feature will accept, in degrees.
pub const DEFAULT_MIN_SLOPE: f32 = 22.0;
pub const DEFAULT_MAX_SLOPE: f32 = 72.0;
/// Default height difference a site must show over its own neighbourhood.
pub const DEFAULT_MIN_DROP: f32 = 5.0;
/// Default clearance kept from any road ribbon (meters).
pub const DEFAULT_CLEAR_OF_ROADS: f32 = 10.0;
/// Default climb a cave site must see ahead of it, along the tunnel
/// direction (meters). Below this the mouth ramp trenches a flat instead of
/// opening a doorway in a slope.
pub const DEFAULT_MIN_RISE: f32 = 2.5;

/// Clearance kept between any two seeded features, on top of their own
/// footprints (meters).
const FEATURE_CLEARANCE: f32 = 4.0;
/// Station pitch for validating a seeded cave's path (meters) — the same
/// sampling the cave build itself uses.
const CAVE_PATH_STEP: f32 = 4.0;
/// Pitch of the pé-de-montanha probe, walking uphill from a cave site.
const RISE_PROBE_STEP: f32 = 4.0;
/// How many probes a cave site walks uphill (so the reach is
/// `RISE_PROBE_STEP × RISE_PROBE_STEPS` = 16 m).
const RISE_PROBE_STEPS: u32 = 4;
/// How far below the mouth's height a tunnel path may dip before it reads
/// as diving into somebody else's ditch (meters).
const PATH_DESCENT_SLACK: f32 = 1.0;
/// Extra clearance on top of the tube radius when validating a cave path;
/// the mouth flare can widen the carve by half again.
const PATH_MARGIN: f32 = 1.5;

/// Directions probed when looking for a gap a bridge could cross.
const CROSSING_DIRECTIONS: usize = 8;
/// Epsilon for the slope probe (meters).
const SLOPE_EPS: f32 = 1.5;

/// Why a world point is not a legal spot for seeded rock.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Reject {
    Water,
    Road,
    Cliff,
    Pad,
    Taken,
    /// The path itself reads badly — it dives into a valley the tube would
    /// have to trench through.
    Path,
}

/// One claimed spot of a feature already in the world (authored, or seeded
/// by an earlier field): a disc the scatter keeps clear of.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TakenDisc {
    pub at: Vec2,
    pub radius: f32,
}

/// What the seeding is allowed to avoid — the registries the feature pass
/// already produced.
#[derive(Debug, Clone, Copy, Default)]
pub struct ScatterGuards<'a> {
    pub water: &'a [WaterBody],
    pub roads: &'a [RoadPath],
    /// Regional cliff mask — a seeded feature must not tear a wall open or
    /// stand inside one. `None` only in tests; the bootstrap always has the
    /// mask by the time the scatter runs.
    pub cliffs: Option<&'a CliffMask>,
    /// Flat pads carved for buildings — nothing may punch through one.
    pub pads: &'a [ResolvedPad],
    /// Discs claimed by features already in the world — authored caves,
    /// arches and bridges, plus whatever earlier fields seeded.
    pub taken: &'a [TakenDisc],
}

/// Rejection tally of one [`RockFeaturesSpec::resolve_with_stats`] run, for
/// the boot log — the same one-line honesty the spawner's PlacementStats
/// keeps.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct ScatterStats {
    /// Lattice candidates considered.
    pub cells: u32,
    pub water: u32,
    pub roads: u32,
    pub cliffs: u32,
    pub pads: u32,
    pub taken: u32,
    /// Relief below `min-drop` in the site's neighbourhood.
    pub drop: u32,
    /// Slope outside the authored band (arches and caves need a hillside).
    pub slope: u32,
    /// No climb ahead of the site (cave-only gate).
    pub rise: u32,
    /// Cave path failed its terrain profile (dives into a valley, or the
    /// hill never climbs enough to absorb the run).
    pub path: u32,
    /// Features actually seeded.
    pub seeded: u32,
}

impl ScatterStats {
    fn bump(&mut self, reason: Reject) {
        match reason {
            Reject::Water => self.water += 1,
            Reject::Road => self.roads += 1,
            Reject::Cliff => self.cliffs += 1,
            Reject::Pad => self.pads += 1,
            Reject::Taken => self.taken += 1,
            Reject::Path => self.path += 1,
        }
    }
}

/// Declarative `<RockFeatures>` spec.
#[derive(Debug, Clone, PartialEq)]
pub struct RockFeaturesSpec {
    pub name: Option<String>,
    /// World XZ bounds to seed inside.
    pub min: Vec2,
    pub max: Vec2,
    pub seed: u64,
    pub arches: u32,
    pub caves: u32,
    pub bridges: u32,
    /// Slope band a site must fall in, in degrees.
    pub min_slope: f32,
    pub max_slope: f32,
    /// Height difference a site must show over its own neighbourhood.
    pub min_drop: f32,
    /// Candidate lattice pitch, and the minimum distance between two seeded
    /// features (meters).
    pub spacing: f32,
    /// Clearance kept from any road ribbon (meters).
    pub clear_of_roads: f32,
    /// Climb a cave site must see ahead of it, along the tunnel direction
    /// (meters) — the pé-de-montanha gate. Arches and bridges do not need
    /// it: one stands across the contour, the other in the floor of a gap.
    pub min_rise: f32,
}

impl Default for RockFeaturesSpec {
    fn default() -> Self {
        Self {
            name: None,
            min: Vec2::splat(-100.0),
            max: Vec2::splat(100.0),
            seed: 0,
            arches: 0,
            caves: 0,
            bridges: 0,
            min_slope: DEFAULT_MIN_SLOPE,
            max_slope: DEFAULT_MAX_SLOPE,
            min_drop: DEFAULT_MIN_DROP,
            spacing: DEFAULT_SCATTER_SPACING,
            clear_of_roads: DEFAULT_CLEAR_OF_ROADS,
            min_rise: DEFAULT_MIN_RISE,
        }
    }
}

/// The authored-equivalent specs a `<RockFeatures>` resolved into.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ScatterResult {
    pub arches: Vec<ArchSpec>,
    pub caves: Vec<CaveSpec>,
    pub bridges: Vec<BridgeSpec>,
}

impl ScatterResult {
    pub fn is_empty(&self) -> bool {
        self.arches.is_empty() && self.caves.is_empty() && self.bridges.is_empty()
    }

    pub fn len(&self) -> usize {
        self.arches.len() + self.caves.len() + self.bridges.len()
    }
}

/// One accepted lattice site.
#[derive(Debug, Clone, Copy)]
struct Site {
    at: Vec2,
    /// Lattice cell, part of the deterministic ordering key.
    cell: (i64, i64),
    /// Terrain slope in degrees.
    slope: f32,
    /// Slope falls in the authored band. Arches and caves need it; a bridge
    /// does not — the site of a crossing is the floor of the gap, which is
    /// flat by definition.
    slope_ok: bool,
    /// The ground climbs ahead of the site, along `uphill`. Caves need it —
    /// the mouth ramp has to be absorbed by a slope, or it reads as a
    /// trench. Recorded per site, consumed only by the cave picker.
    rise_ok: bool,
    /// Unit XZ direction of steepest ascent.
    uphill: Vec2,
    /// Shuffle key — deterministic, but not correlated with position.
    score: f32,
}

impl RockFeaturesSpec {
    /// How many features this spec asks for in total.
    pub fn requested(&self) -> u32 {
        self.arches + self.caves + self.bridges
    }

    /// Seeds the region and returns ordinary feature specs.
    ///
    /// Reads the height field and the guards up front; like every mod build,
    /// it never touches mutable state.
    pub fn resolve(&self, base: &dyn HeightField, guards: &ScatterGuards) -> ScatterResult {
        self.resolve_with_stats(base, guards).0
    }

    /// The same seeding, plus the rejection tally for the boot log.
    pub fn resolve_with_stats(
        &self,
        base: &dyn HeightField,
        guards: &ScatterGuards,
    ) -> (ScatterResult, ScatterStats) {
        let mut out = ScatterResult::default();
        let mut stats = ScatterStats::default();
        if self.requested() == 0 || self.spacing <= 0.0 || !self.spacing.is_finite() {
            return (out, stats);
        }
        let sites = self.sites(base, guards, &mut stats);
        // Bridges are the fussiest — they need a gap to cross — so they pick
        // first. Arches next, caves last: a cave needs a hillside AND rising
        // ground, which is the rarest site.
        let mut taken: Vec<Vec2> = Vec::new();
        let mut used = vec![false; sites.len()];
        for (i, site) in sites.iter().enumerate() {
            if out.bridges.len() as u32 >= self.bridges {
                break;
            }
            if self.too_close(&taken, site.at) {
                continue;
            }
            let Some((a, b)) = self.crossing(base, site) else {
                continue;
            };
            let bridge = self.bridge_at(site, a, b, out.bridges.len());
            // The abutments stand ON the banks — they, not the span above
            // the channel, must be dry and clear. The span itself may cross
            // water and roads (that is what a crossing is), but never stand
            // over another feature or inside a wall.
            let mut bad = [a, b]
                .iter()
                .find_map(|p| self.reject_reason(*p, bridge.width * 0.5, guards));
            if bad.is_none() {
                bad = resample(&[a, site.at, b], CAVE_PATH_STEP).into_iter().find_map(
                    |p| self.reject_solid(p, bridge.width * 0.5, guards),
                );
            }
            if let Some(reason) = bad {
                stats.bump(reason);
                continue;
            }
            out.bridges.push(bridge);
            taken.push(site.at);
            used[i] = true;
            stats.seeded += 1;
        }
        for (i, site) in sites.iter().enumerate() {
            if out.arches.len() as u32 >= self.arches {
                break;
            }
            if used[i] {
                continue;
            }
            if !site.slope_ok {
                stats.slope += 1;
                continue;
            }
            if self.too_close(&taken, site.at) {
                continue;
            }
            let arch = self.arch_at(site, out.arches.len());
            let mut bad = arch
                .path
                .iter()
                .copied()
                .chain(std::iter::once(site.at))
                .find_map(|p| self.reject_reason(p, arch.thickness, guards));
            if bad.is_none() {
                bad = resample(&arch.path, CAVE_PATH_STEP)
                    .into_iter()
                    .find_map(|p| self.reject_solid(p, arch.thickness, guards));
            }
            if let Some(reason) = bad {
                stats.bump(reason);
                continue;
            }
            out.arches.push(arch);
            taken.push(site.at);
            used[i] = true;
            stats.seeded += 1;
        }
        for (i, site) in sites.iter().enumerate() {
            if out.caves.len() as u32 >= self.caves {
                break;
            }
            if used[i] {
                continue;
            }
            if !site.slope_ok {
                stats.slope += 1;
                continue;
            }
            if !site.rise_ok {
                stats.rise += 1;
                continue;
            }
            if self.too_close(&taken, site.at) {
                continue;
            }
            let cave = self.cave_at(site, out.caves.len());
            if !self.cave_path_ok(&cave, base, guards, &mut stats) {
                continue;
            }
            out.caves.push(cave);
            taken.push(site.at);
            stats.seeded += 1;
        }
        (out, stats)
    }

    fn too_close(&self, taken: &[Vec2], p: Vec2) -> bool {
        taken.iter().any(|q| q.distance(p) < self.spacing)
    }

    /// Why a world point is not a legal spot for seeded rock — `None` when
    /// it is. `margin` is the clearance demanded around the point itself (a
    /// tube radius, a leg thickness); the road corridor keeps its own
    /// authored number, whichever is wider.
    fn reject_reason(&self, p: Vec2, margin: f32, guards: &ScatterGuards) -> Option<Reject> {
        if guards.water.iter().any(|w| w.is_near(p, margin)) {
            return Some(Reject::Water);
        }
        if guards
            .roads
            .iter()
            .any(|r| r.distance_to_road(p) < self.clear_of_roads.max(margin))
        {
            return Some(Reject::Road);
        }
        self.reject_solid(p, margin, guards)
    }

    /// The solid-only half of [`Self::reject_reason`]: cliff walls, pads and
    /// features already in the world. Spans of bridges and arches are checked
    /// with THIS alone — a crossing exists to pass over water and roads, but
    /// no span may stand over another feature or inside a wall.
    fn reject_solid(&self, p: Vec2, margin: f32, guards: &ScatterGuards) -> Option<Reject> {
        if guards.cliffs.is_some_and(|m| m.is_cliff_within(p, margin)) {
            return Some(Reject::Cliff);
        }
        if guards.pads.iter().any(|pad| {
            // Conservative core rectangle: the pad's falloff ramp is fair
            // game at half, the core itself is never touchable.
            let half = pad.size * 0.5 + Vec2::splat(margin + pad.falloff * 0.5);
            let d = (p - pad.at).abs();
            d.x < half.x && d.y < half.y
        }) {
            return Some(Reject::Pad);
        }
        if guards
            .taken
            .iter()
            .any(|d| d.at.distance(p) < d.radius + FEATURE_CLEARANCE + margin)
        {
            return Some(Reject::Taken);
        }
        None
    }

    /// Does the ground climb ahead of the site? The mouth ramp of a tunnel
    /// reads as a doorway on a slope and as an open trench on a flat, so a
    /// cave only goes where the hill is already rising within a short walk
    /// uphill.
    fn rises_ahead(&self, base: &dyn HeightField, at: Vec2, uphill: Vec2) -> bool {
        let here = base.sample(at.x, at.y);
        (1..=RISE_PROBE_STEPS).any(|i| {
            let d = uphill * (i as f32 * RISE_PROBE_STEP);
            base.sample(at.x + d.x, at.y + d.y) - here >= self.min_rise
        })
    }

    /// Full validation of a seeded cave. Every station of the resampled
    /// path must clear the guards; the ground may never dip below the
    /// mouth's height minus a slack (the tube must not dive into valleys it
    /// would trench through); and the hill must climb enough over the run
    /// to absorb the tube, so BOTH mouths open on rising ground.
    /// Returns false with the reason tallied.
    fn cave_path_ok(
        &self,
        cave: &CaveSpec,
        base: &dyn HeightField,
        guards: &ScatterGuards,
        stats: &mut ScatterStats,
    ) -> bool {
        let stations = resample(&cave.path, CAVE_PATH_STEP);
        if stations.len() < 2 {
            stats.path += 1;
            return false;
        }
        let r_max = cave.radius.iter().copied().fold(0.0_f32, f32::max);
        let margin = r_max * 1.5 + PATH_MARGIN;
        let h0 = base.sample(stations[0].x, stations[0].y);
        let mut high = h0;
        for s in &stations {
            if let Some(reason) = self.reject_reason(*s, margin, guards) {
                stats.bump(reason);
                return false;
            }
            let h = base.sample(s.x, s.y);
            if h < h0 - PATH_DESCENT_SLACK {
                stats.bump(Reject::Path);
                return false;
            }
            high = high.max(h);
        }
        let need = self.min_rise.max(cave.depth * 0.5);
        if high - h0 < need {
            stats.rise += 1;
            return false;
        }
        true
    }

    /// Every lattice cell that clears the relief, water, road, cliff, pad
    /// and feature tests, ordered deterministically by its hash.
    ///
    /// The slope band is recorded per site rather than filtered here: an arch
    /// or a cave needs a hillside, but a bridge's site is the FLOOR of the gap
    /// it crosses, which is flat by definition. Filtering on slope up front
    /// would have made rock spans impossible to seed.
    fn sites(
        &self,
        base: &dyn HeightField,
        guards: &ScatterGuards,
        stats: &mut ScatterStats,
    ) -> Vec<Site> {
        let (lo, hi) = (self.min.min(self.max), self.min.max(self.max));
        let cols = (((hi.x - lo.x) / self.spacing).floor() as i64).max(0);
        let rows = (((hi.y - lo.y) / self.spacing).floor() as i64).max(0);
        let mut out = Vec::new();
        for iz in 0..=rows {
            for ix in 0..=cols {
                // Jitter inside the cell so the field does not read as a grid.
                let jx = hash01(self.seed, ix as u64, (iz as u64) ^ 0x9e37);
                let jz = hash01(self.seed ^ 0x51ed, ix as u64, iz as u64);
                let at = Vec2::new(
                    lo.x + (ix as f32 + jx) * self.spacing,
                    lo.y + (iz as f32 + jz) * self.spacing,
                );
                if at.x > hi.x || at.y > hi.y {
                    continue;
                }
                stats.cells += 1;
                if let Some(reason) = self.reject_reason(at, 0.0, guards) {
                    stats.bump(reason);
                    continue;
                }
                if self.local_drop(base, at) < self.min_drop {
                    stats.drop += 1;
                    continue;
                }
                let n = base.sample_normal(at.x, at.y, SLOPE_EPS);
                let slope = n.y.clamp(-1.0, 1.0).acos().to_degrees();
                // The surface normal tilts downhill, so its XZ part points the
                // way water runs; negate it to face the climb.
                let uphill = Vec2::new(-n.x, -n.z).try_normalize().unwrap_or(Vec2::X);
                out.push(Site {
                    at,
                    cell: (ix, iz),
                    slope,
                    slope_ok: slope >= self.min_slope && slope <= self.max_slope,
                    rise_ok: self.rises_ahead(base, at, uphill),
                    uphill,
                    score: hash01(self.seed ^ 0xa53f, ix as u64, iz as u64),
                });
            }
        }
        // Deterministic order: the hash decides, the cell breaks ties. Never
        // the iteration order of anything.
        out.sort_by(|a, b| {
            a.score
                .total_cmp(&b.score)
                .then_with(|| a.cell.cmp(&b.cell))
        });
        out
    }

    /// Height range over the site's own neighbourhood — how much relief there
    /// is to work with.
    fn local_drop(&self, base: &dyn HeightField, at: Vec2) -> f32 {
        let r = self.spacing * 0.5;
        if let Some((lo, hi)) = base.range_over(at.x - r, at.y - r, at.x + r, at.y + r) {
            return hi - lo;
        }
        // No O(1) range: probe the four corners rather than give up.
        let mut lo = f32::INFINITY;
        let mut hi = f32::NEG_INFINITY;
        for (dx, dz) in [(-r, -r), (r, -r), (-r, r), (r, r), (0.0, 0.0)] {
            let h = base.sample(at.x + dx, at.y + dz);
            lo = lo.min(h);
            hi = hi.max(h);
        }
        hi - lo
    }

    /// Looks for a gap the site could bridge: a direction where the ground
    /// climbs by `min_drop` on BOTH sides. That signature is a ravine or a
    /// river channel, and it is the only place a rock span makes sense.
    fn crossing(&self, base: &dyn HeightField, site: &Site) -> Option<(Vec2, Vec2)> {
        let here = base.sample(site.at.x, site.at.y);
        let reach = self.spacing * 0.6;
        let mut best: Option<(f32, Vec2, Vec2)> = None;
        for k in 0..CROSSING_DIRECTIONS {
            let theta = std::f32::consts::PI * k as f32 / CROSSING_DIRECTIONS as f32;
            let d = Vec2::new(theta.cos(), theta.sin());
            let a = site.at - d * reach;
            let b = site.at + d * reach;
            let (ha, hb) = (base.sample(a.x, a.y), base.sample(b.x, b.y));
            let rise = (ha - here).min(hb - here);
            if rise < self.min_drop {
                continue;
            }
            if best.is_none_or(|(r, _, _)| rise > r) {
                best = Some((rise, a, b));
            }
        }
        best.map(|(_, a, b)| (a, b))
    }

    fn label(&self, kind: &str, i: usize) -> Option<String> {
        Some(format!(
            "{}:{kind}:{i}",
            self.name.as_deref().unwrap_or("rocks")
        ))
    }

    /// A rock span across the gap the site found.
    fn bridge_at(&self, site: &Site, a: Vec2, b: Vec2, i: usize) -> BridgeSpec {
        let (h0, h1) = self.pair(site, 0x11);
        let width = 5.0 + 6.0 * h0;
        BridgeSpec {
            name: self.label("bridge", i),
            path: vec![a, site.at, b],
            width,
            // Enough camber to clear the channel, scaled to the relief the
            // site actually has.
            rise: (self.min_drop * (0.6 + 0.5 * h1)).max(1.5),
            thickness: (width * 0.45).max(2.0),
            style: BridgeStyle::Natural,
            parapet: 0.0,
            ..BridgeSpec::default()
        }
    }

    /// A natural arch standing across the contour, so it reads as a fin the
    /// weather punched through rather than a gate someone built.
    fn arch_at(&self, site: &Site, i: usize) -> ArchSpec {
        let (h0, h1) = self.pair(site, 0x23);
        // Along the contour: perpendicular to the climb.
        let along = Vec2::new(-site.uphill.y, site.uphill.x);
        let half = (self.spacing * (0.18 + 0.14 * h0)).max(4.0);
        ArchSpec {
            name: self.label("arch", i),
            path: vec![site.at - along * half, site.at + along * half],
            profile: ArchProfile::Natural,
            height: (self.min_drop * (0.8 + 0.7 * h1)).max(4.0),
            thickness: (half * 0.28).max(1.2),
            ..ArchSpec::default()
        }
    }

    /// A tunnel burrowing into the hillside, mouth on the slope the site sits
    /// on and the far end deeper into the hill.
    fn cave_at(&self, site: &Site, i: usize) -> CaveSpec {
        let (h0, h1) = self.pair(site, 0x37);
        let run = self.spacing * (1.0 + 0.8 * h0);
        // Bend it: a dead-straight tunnel reads as a pipe.
        let side = Vec2::new(-site.uphill.y, site.uphill.x) * (run * 0.18 * (h1 - 0.5));
        let mid = site.at + site.uphill * (run * 0.5) + side;
        let end = site.at + site.uphill * run;
        let r = 2.2 + 1.4 * h1;
        CaveSpec {
            name: self.label("cave", i),
            path: vec![site.at, mid, end],
            radius: vec![r, r * 1.6, r * 0.9],
            depth: (r * 1.6 + 3.0 + self.min_drop * 0.4).max(6.0),
            // Steeper ground gives a taller mouth without breaching the roof.
            mouth_flare: 1.0 + 0.5 * (site.slope / 90.0),
            ..CaveSpec::default()
        }
    }

    /// Two more decorrelated hashes for one site.
    fn pair(&self, site: &Site, salt: u64) -> (f32, f32) {
        let (ix, iz) = (site.cell.0 as u64, site.cell.1 as u64);
        (
            hash01(self.seed ^ salt, ix, iz),
            hash01(self.seed ^ (salt << 8), iz, ix),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bevy::math::Vec3;
    use super::super::super::water::{LakeShape, WaterKind};

    /// A ridge running along Z: flat top at x = 0, falling away to both sides,
    /// so every site has slope and relief and the middle is a crossable gap.
    #[derive(Debug)]
    struct Ridge;

    impl HeightField for Ridge {
        fn sample(&self, x: f32, _z: f32) -> f32 {
            // A narrow valley at x = 0 between two 30 m shoulders.
            let t = (x.abs() / 25.0).clamp(0.0, 1.0);
            30.0 * t * t
        }
        fn sample_normal(&self, x: f32, _z: f32, e: f32) -> Vec3 {
            let dx = (self.sample(x + e, 0.0) - self.sample(x - e, 0.0)) / (2.0 * e);
            Vec3::new(-dx, 1.0, 0.0).normalize()
        }
        fn max_height(&self) -> f32 {
            30.0
        }
    }

    fn field(arches: u32, caves: u32, bridges: u32) -> RockFeaturesSpec {
        RockFeaturesSpec {
            name: Some("rochedos".into()),
            min: Vec2::new(-120.0, -120.0),
            max: Vec2::new(120.0, 120.0),
            seed: 7,
            arches,
            caves,
            bridges,
            min_slope: 5.0,
            max_slope: 80.0,
            min_drop: 3.0,
            spacing: 30.0,
            clear_of_roads: 0.0,
            min_rise: DEFAULT_MIN_RISE,
        }
    }

    /// A river along the ridge valley (x = 0), narrow enough that the slope
    /// sites stay dry.
    fn valley_river() -> WaterBody {
        WaterBody {
            kind: WaterKind::River,
            at: Vec2::new(0.0, 0.0),
            radius: 0.0,
            carve_radius: 6.0,
            water_y: 0.0,
            mirror_reach: 1.0,
            shape: LakeShape::default(),
            stations: vec![Vec2::new(0.0, -300.0), Vec2::new(0.0, 300.0)],
            surface_y: vec![0.0, 0.0],
            water_width: 8.0,
            half_width: Vec::new(),
            depths: Vec::new(),
            cascades: Vec::new(),
        }
    }

    #[test]
    fn test_seeding_is_a_pure_function_of_the_seed() {
        let spec = field(3, 2, 1);
        let guards = ScatterGuards::default();
        assert_eq!(spec.resolve(&Ridge, &guards), spec.resolve(&Ridge, &guards));
        let other = RockFeaturesSpec {
            seed: 8,
            ..spec.clone()
        };
        assert_ne!(spec.resolve(&Ridge, &guards), other.resolve(&Ridge, &guards));
    }

    #[test]
    fn test_it_never_exceeds_the_requested_budget() {
        let spec = field(3, 2, 1);
        let got = spec.resolve(&Ridge, &ScatterGuards::default());
        assert!(got.arches.len() <= 3);
        assert!(got.caves.len() <= 2);
        assert!(got.bridges.len() <= 1);
        assert!(!got.is_empty(), "a ridge with relief must seed something");
        assert_eq!(got.len(), got.arches.len() + got.caves.len() + got.bridges.len());
    }

    #[test]
    fn test_asking_for_nothing_builds_nothing() {
        assert!(field(0, 0, 0).resolve(&Ridge, &ScatterGuards::default()).is_empty());
        let bad = RockFeaturesSpec {
            spacing: 0.0,
            ..field(5, 5, 5)
        };
        assert!(bad.resolve(&Ridge, &ScatterGuards::default()).is_empty());
    }

    #[test]
    fn test_flat_ground_offers_no_sites() {
        #[derive(Debug)]
        struct Flat;
        impl HeightField for Flat {
            fn sample(&self, _x: f32, _z: f32) -> f32 {
                12.0
            }
            fn sample_normal(&self, _x: f32, _z: f32, _e: f32) -> Vec3 {
                Vec3::Y
            }
            fn max_height(&self) -> f32 {
                12.0
            }
        }
        // No slope, no relief: the slope band and the drop test both reject
        // every cell, and the field seeds nothing rather than littering a
        // plain with arches.
        assert!(field(4, 4, 4).resolve(&Flat, &ScatterGuards::default()).is_empty());
    }

    #[test]
    fn test_seeded_features_are_spread_out_not_stacked() {
        let spec = field(3, 3, 0);
        let got = spec.resolve(&Ridge, &ScatterGuards::default());
        let mut anchors: Vec<Vec2> = got.arches.iter().map(|a| a.path[0]).collect();
        anchors.extend(got.caves.iter().map(|c| c.path[0]));
        for (i, a) in anchors.iter().enumerate() {
            for b in &anchors[i + 1..] {
                assert!(
                    a.distance(*b) > 1.0,
                    "two features landed on top of each other at {a:?} / {b:?}"
                );
            }
        }
    }

    #[test]
    fn test_a_seeded_bridge_actually_spans_a_gap() {
        let spec = field(0, 0, 1);
        let got = spec.resolve(&Ridge, &ScatterGuards::default());
        let bridge = got.bridges.first().expect("the valley is crossable");
        assert_eq!(bridge.style, BridgeStyle::Natural);
        let (a, b) = (bridge.path[0], bridge.path[bridge.path.len() - 1]);
        let mid = bridge.path[1];
        let here = Ridge.sample(mid.x, mid.y);
        assert!(
            Ridge.sample(a.x, a.y) >= here + spec.min_drop
                && Ridge.sample(b.x, b.y) >= here + spec.min_drop,
            "both abutments must be above the gap they cross"
        );
    }

    #[test]
    fn test_rises_ahead_demands_a_climb_within_reach() {
        let spec = field(0, 1, 0);
        // On the ridge slope the hill climbs right ahead of you; on the flat
        // top it never does, and toward the valley the ground FALLS.
        assert!(spec.rises_ahead(&Ridge, Vec2::new(10.0, 0.0), Vec2::X));
        assert!(!spec.rises_ahead(&Ridge, Vec2::new(40.0, 0.0), Vec2::X));
        assert!(!spec.rises_ahead(&Ridge, Vec2::new(10.0, 0.0), -Vec2::X));
    }

    #[test]
    fn test_a_cave_mouth_needs_rising_ground_ahead() {
        let hills = field(0, 3, 0);
        let gated = RockFeaturesSpec {
            min_rise: 1000.0,
            ..hills.clone()
        };
        assert!(
            !hills.resolve(&Ridge, &ScatterGuards::default()).caves.is_empty(),
            "the ridge offers real hillsides to burrow into"
        );
        assert!(
            gated.resolve(&Ridge, &ScatterGuards::default()).caves.is_empty(),
            "an impossible min-rise must leave every cave unseeded"
        );
    }

    #[test]
    fn test_a_cave_path_is_rejected_for_water_not_just_the_site() {
        let spec = field(0, 1, 0);
        // A stream crossing the slope at x = 24 — not the valley.
        let mut river = valley_river();
        for s in &mut river.stations {
            s.x = 24.0;
        }
        river.at.x = 24.0;
        let guards = ScatterGuards {
            water: std::slice::from_ref(&river),
            ..ScatterGuards::default()
        };
        let mut stats = ScatterStats::default();
        // Burrowing uphill from x = 10, the run would cross the stream at
        // x = 24 head-on. The SITE is dry; the PATH is not.
        let crossing = CaveSpec {
            path: vec![Vec2::new(10.0, 0.0), Vec2::new(18.0, 3.0), Vec2::new(34.0, 0.0)],
            radius: vec![3.0],
            ..CaveSpec::default()
        };
        assert!(!spec.cave_path_ok(&crossing, &Ridge, &guards, &mut stats));
        assert_eq!(stats.water, 1, "the stream, not the site, kills this run");
        // The same run heading away from the water climbs cleanly.
        let away = CaveSpec {
            path: vec![Vec2::new(-20.0, 0.0), Vec2::new(-35.0, 2.0), Vec2::new(-50.0, 0.0)],
            radius: vec![3.0],
            ..CaveSpec::default()
        };
        assert!(spec.cave_path_ok(&away, &Ridge, &guards, &mut stats));
    }

    #[test]
    fn test_a_cave_path_never_dives_into_a_valley() {
        let spec = field(0, 1, 0);
        let mut stats = ScatterStats::default();
        // Downhill: from the shoulder toward the valley the ground falls —
        // the tube would trench its way down somebody else's ditch.
        let down = CaveSpec {
            path: vec![Vec2::new(-30.0, 0.0), Vec2::new(-15.0, 0.0), Vec2::new(0.0, 0.0)],
            radius: vec![3.0],
            ..CaveSpec::default()
        };
        assert!(!spec.cave_path_ok(&down, &Ridge, &ScatterGuards::default(), &mut stats));
        assert_eq!(stats.path, 1, "the descent is what fails this run");
        // Uphill the same ground is a legal burrow.
        let up = CaveSpec {
            path: vec![Vec2::new(0.0, 0.0), Vec2::new(15.0, 0.0), Vec2::new(30.0, 0.0)],
            radius: vec![3.0],
            ..CaveSpec::default()
        };
        assert!(spec.cave_path_ok(&up, &Ridge, &ScatterGuards::default(), &mut stats));
    }

    #[test]
    fn test_taken_discs_keep_the_scatter_off_existing_features() {
        let spec = field(3, 3, 2);
        // One disc claims the whole region: an authored feature is there.
        let claimed = [TakenDisc {
            at: Vec2::ZERO,
            radius: 500.0,
        }];
        let guards = ScatterGuards {
            taken: &claimed,
            ..ScatterGuards::default()
        };
        let (got, stats) = spec.resolve_with_stats(&Ridge, &guards);
        assert!(got.is_empty(), "nothing seeds inside a claimed disc");
        assert!(stats.taken > 0, "the rejections are tallied as `taken`");
        // Without the claim the same seed seeds something.
        assert!(!spec.resolve(&Ridge, &ScatterGuards::default()).is_empty());
    }

    #[test]
    fn test_a_bridge_abutment_in_the_water_kills_the_span() {
        let spec = field(0, 0, 1);
        // A wide river fills the valley to the abutment reach: the banks the
        // span would stand on are under the carve.
        let mut flood = valley_river();
        flood.carve_radius = 40.0;
        let guards = ScatterGuards {
            water: std::slice::from_ref(&flood),
            ..ScatterGuards::default()
        };
        let (got, stats) = spec.resolve_with_stats(&Ridge, &guards);
        assert!(got.bridges.is_empty(), "no dry bank, no span");
        assert!(stats.water > 0, "the flood is what the log blames");
    }

    #[test]
    fn test_the_stats_report_what_the_seed_rejected() {
        let spec = field(3, 3, 2);
        let (got, stats) = spec.resolve_with_stats(&Ridge, &ScatterGuards::default());
        assert_eq!(
            stats.seeded as usize,
            got.len(),
            "the tally and the specs agree on what was seeded"
        );
        assert!(stats.cells > 0);
    }
}
