//! `<Bridge>` — a crossing you can actually walk on.
//!
//! The engine already had a bridge of sorts: `<Segment profile="bridge">`
//! reports a flat deck ribbon at `deck_y` and skips the corridor carve
//! (`super::super::roads`). That ribbon is a *drawn* surface only — it never
//! reaches the voxel field, so it has no collider, and the hero walks onto it
//! and falls into the river.
//!
//! This tag is the volumetric answer. A bridge is a chain of union solids in
//! the [`VoxelField`](super::field::VoxelField), which means the transvoxel
//! mesher draws it and `ColumnColliderBake` bakes those exact triangles into
//! the column's trimesh. Standing on it is not a feature that had to be
//! written: it is what "the collider is the surface" already guarantees.
//!
//! # Authoring
//!
//! ```xml
//! <!-- masonry: arcades, piers on the bed, parapets -->
//! <Bridge name="ponte_velha" path="128 0  160 0" width="6" style="stone"
//!         rise="3" thickness="1.2" spans="3" pier-width="3" parapet="0.9" />
//!
//! <!-- natural rock span: no piers, organic haunches into both banks -->
//! <Bridge name="arco_do_rio" path="-20 40  10 55" width="9"
//!         style="natural" rise="5" thickness="4" />
//! ```
//!
//! Like `<Cave>` and `<Arch>`, `path` is XZ and the height comes from the
//! terrain: the deck springs from the ground at both ends, so a bridge drawn
//! across a valley lands on its two banks without hand-placed Y.

use bevy::math::{Vec2, Vec3};

use super::super::mesh::HeightField;
use super::super::paths::{chaikin_smooth, path_length, resample};
use super::mods::{ModOp, OrientedBoxMod, RoundConeMod, VoxelMod};

/// Default deck width (meters).
pub const DEFAULT_BRIDGE_WIDTH: f32 = 6.0;
/// Default deck slab thickness (meters).
pub const DEFAULT_BRIDGE_THICKNESS: f32 = 1.2;
/// Default camber of the deck above the straight chord (meters).
pub const DEFAULT_BRIDGE_RISE: f32 = 2.0;
/// Default pier thickness along the bridge axis (meters).
pub const DEFAULT_PIER_WIDTH: f32 = 2.5;
/// Default parapet height above the deck (meters); 0 disables it.
pub const DEFAULT_PARAPET: f32 = 0.9;
/// Default minimum air demanded under the deck, for the sanity warning.
pub const DEFAULT_CLEARANCE: f32 = 2.0;

/// Spacing between deck stations (meters). Same trade as `<Cave>`: short
/// enough that a bend reads as a curve, long enough that a 60 m bridge is
/// ~15 boxes rather than hundreds.
const STATION_SPACING: f32 = 4.0;
/// How far consecutive deck boxes overlap (meters). Without it a bend leaves
/// a hairline of air between two boxes, and the mesher is right to draw it.
const SEGMENT_OVERLAP: f32 = 0.5;
/// Half-thickness of a parapet wall (meters).
const PARAPET_HALF: f32 = 0.2;
/// Spandrel width as a fraction of the deck, so the deck oversails it.
const SPANDREL_FRACTION: f32 = 0.85;
/// Target width of one spandrel slice (meters). Near the LOD0 cell, so the
/// staircase the slices leave on the intrados stays under the voxel.
const SPANDREL_SLICE: f32 = 1.0;
/// Cap on slices per arch, so a 200 m span does not become 200 mods.
const MAX_SPANDREL_SLICES: usize = 48;
/// Rock kept between an arch crown and the deck underside (meters).
const CROWN_HEADROOM: f32 = 0.4;
/// How far a pier foot is buried below the lowest ground it crosses (meters).
const FOOT_EMBED: f32 = 1.0;

/// How a `<Bridge>` is built.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BridgeStyle {
    /// Masonry: deck, spandrel wall pierced by arcades, piers, parapets.
    #[default]
    Stone,
    /// A rock span: no piers and no parapets, haunches flaring into the banks.
    Natural,
}

impl BridgeStyle {
    pub fn parse(name: &str) -> Option<Self> {
        match name.trim().to_ascii_lowercase().as_str() {
            "stone" | "masonry" => Some(Self::Stone),
            "natural" | "rock" => Some(Self::Natural),
            _ => None,
        }
    }

    pub const NAMES: &'static str = "stone, natural";
}

/// Declarative `<Bridge>` spec.
#[derive(Debug, Clone, PartialEq)]
pub struct BridgeSpec {
    pub name: Option<String>,
    /// Deck centreline in world XZ.
    pub path: Vec<Vec2>,
    /// Deck width (meters).
    pub width: f32,
    /// Camber of the deck above the straight chord between the two banks.
    pub rise: f32,
    /// Deck slab thickness (meters).
    pub thickness: f32,
    pub style: BridgeStyle,
    /// Number of arcades; `None` derives one from the length and the height.
    pub spans: Option<u32>,
    /// Pier thickness along the bridge axis (meters).
    pub pier_width: f32,
    /// Parapet height above the deck (meters); 0 disables it.
    pub parapet: f32,
    /// Minimum air demanded under the deck — reported, never enforced.
    pub clearance: f32,
}

impl Default for BridgeSpec {
    fn default() -> Self {
        Self {
            name: None,
            path: Vec::new(),
            width: DEFAULT_BRIDGE_WIDTH,
            rise: DEFAULT_BRIDGE_RISE,
            thickness: DEFAULT_BRIDGE_THICKNESS,
            style: BridgeStyle::Stone,
            spans: None,
            pier_width: DEFAULT_PIER_WIDTH,
            parapet: DEFAULT_PARAPET,
            clearance: DEFAULT_CLEARANCE,
        }
    }
}

/// One sampled point of the deck centreline.
#[derive(Debug, Clone, Copy)]
struct Station {
    /// World XZ.
    xz: Vec2,
    /// Fraction along the deck, 0 at the first bank and 1 at the last.
    t: f32,
    /// Terrain height under this point, as carved.
    ground: f32,
    /// World Y of the deck's walking surface.
    deck_top: f32,
}

/// Yaw that puts a world heading along the local +X axis.
///
/// [`super::mods::yaw_local`] maps a world direction at angle `θ` to local
/// angle `θ + yaw`, so aligning with local +X means `yaw = -θ`. Getting this
/// sign wrong mirrors every deck box across its own centreline.
fn yaw_for(dir: Vec2) -> f32 {
    -dir.y.atan2(dir.x)
}

impl BridgeSpec {
    /// Builds the union (and, for masonry, subtractive) mods for this bridge.
    ///
    /// Reads the height field up front and never again, like every other mod
    /// build — that is what makes the world reproducible bit for bit.
    pub fn build(&self, base: &dyn HeightField) -> Vec<Box<dyn VoxelMod>> {
        let Some(stations) = self.stations(base) else {
            return Vec::new();
        };
        let label = self.name.clone().unwrap_or_else(|| "bridge".to_string());
        let mut mods: Vec<Box<dyn VoxelMod>> = Vec::new();
        self.push_deck(&mut mods, &stations, &label);
        match self.style {
            BridgeStyle::Stone => {
                self.push_masonry(&mut mods, &stations, &label);
                self.push_parapets(&mut mods, &stations, &label);
            }
            BridgeStyle::Natural => self.push_haunches(&mut mods, &stations, &label),
        }
        mods
    }

    /// The deck centreline, sampled at [`STATION_SPACING`].
    ///
    /// The profile is the straight chord between the two banks plus a
    /// `rise·sin(πt)` camber, so both ends meet the terrain exactly and the
    /// middle lifts clear of whatever it crosses.
    fn stations(&self, base: &dyn HeightField) -> Option<Vec<Station>> {
        if self.path.len() < 2
            || !self.width.is_finite()
            || self.width <= 0.0
            || !self.thickness.is_finite()
            || self.thickness <= 0.0
            || !self.rise.is_finite()
        {
            return None;
        }
        let smoothed = chaikin_smooth(&self.path, 2, false);
        let points = resample(&smoothed, STATION_SPACING);
        if points.len() < 2 {
            return None;
        }
        let first = points[0];
        let last = points[points.len() - 1];
        let start = base.sample(first.x, first.y);
        let end = base.sample(last.x, last.y);
        // Parametrise by ARC LENGTH: `resample` pins the authored end point, so
        // the last station is usually a short stub. Keying on the index would
        // push the camber's apex off the middle of the crossing.
        let mut run = 0.0_f32;
        let mut arc = Vec::with_capacity(points.len());
        arc.push(0.0);
        for pair in points.windows(2) {
            run += pair[0].distance(pair[1]);
            arc.push(run);
        }
        if run <= 1e-6 {
            return None;
        }
        Some(
            points
                .iter()
                .zip(&arc)
                .map(|(p, d)| {
                    let t = d / run;
                    let chord = start + (end - start) * t;
                    Station {
                        xz: *p,
                        t,
                        ground: base.sample(p.x, p.y),
                        deck_top: chord + self.rise * (std::f32::consts::PI * t).sin(),
                    }
                })
                .collect(),
        )
    }

    /// The walkable slab: one yawed box per station pair, overlapping so a
    /// bend has no hairline of air in it.
    fn push_deck(&self, out: &mut Vec<Box<dyn VoxelMod>>, stations: &[Station], label: &str) {
        for (i, pair) in stations.windows(2).enumerate() {
            let (a, b) = (pair[0], pair[1]);
            let dir = b.xz - a.xz;
            let len = dir.length();
            if len < 1e-4 {
                continue;
            }
            let mid_xz = (a.xz + b.xz) * 0.5;
            let top = (a.deck_top + b.deck_top) * 0.5;
            out.push(Box::new(OrientedBoxMod::new(
                format!("{label}:deck:{i}"),
                Vec3::new(mid_xz.x, top - self.thickness * 0.5, mid_xz.y),
                Vec3::new(
                    len * 0.5 + SEGMENT_OVERLAP,
                    self.thickness * 0.5,
                    self.width * 0.5,
                ),
                yaw_for(dir / len),
                ModOp::Union,
            )));
        }
    }

    /// Two low walls along the deck edges. Purely a silhouette job — nothing
    /// reads them, and a `parapet="0"` bridge is a bare slab.
    fn push_parapets(&self, out: &mut Vec<Box<dyn VoxelMod>>, stations: &[Station], label: &str) {
        if self.parapet <= 0.0 || !self.parapet.is_finite() {
            return;
        }
        let offset = (self.width * 0.5 - PARAPET_HALF).max(PARAPET_HALF);
        for (i, pair) in stations.windows(2).enumerate() {
            let (a, b) = (pair[0], pair[1]);
            let dir = b.xz - a.xz;
            let len = dir.length();
            if len < 1e-4 {
                continue;
            }
            let unit = dir / len;
            let perp = Vec2::new(-unit.y, unit.x);
            let mid_xz = (a.xz + b.xz) * 0.5;
            let top = (a.deck_top + b.deck_top) * 0.5;
            let yaw = yaw_for(unit);
            let half = Vec3::new(
                len * 0.5 + SEGMENT_OVERLAP,
                self.parapet * 0.5,
                PARAPET_HALF,
            );
            for (side, sign) in [("l", 1.0f32), ("r", -1.0f32)] {
                let c = mid_xz + perp * (offset * sign);
                out.push(Box::new(OrientedBoxMod::new(
                    format!("{label}:parapet:{side}:{i}"),
                    Vec3::new(c.x, top + self.parapet * 0.5, c.y),
                    half,
                    yaw,
                    ModOp::Union,
                )));
            }
        }
    }

    /// Masonry: piers on the ground, an arch barrel between them, and the
    /// spandrel filling from that barrel up to the deck.
    ///
    /// Built **additively**, and that is the whole design. The obvious
    /// alternative — fill everything under the deck and subtract the arcades
    /// back out — cannot work over a V-shaped gorge: the void spans the gap,
    /// the gap's walls slope up into it, and the subtraction eats the rock the
    /// bridge is supposed to be standing on. Nothing here is subtractive, so
    /// no bridge can damage the terrain it crosses, whatever its shape.
    fn push_masonry(&self, out: &mut Vec<Box<dyn VoxelMod>>, stations: &[Station], label: &str) {
        let length = path_length(&stations.iter().map(|s| s.xz).collect::<Vec<_>>());
        let spans = self.span_count(stations, length);
        let half_w = self.width * 0.5 * SPANDREL_FRACTION;
        for k in 0..spans {
            let lo = k as f32 / spans as f32;
            let hi = (k + 1) as f32 / spans as f32;
            // An arch spans where there is a gap to span, and nowhere else.
            // Sizing it from the deck length instead would put an arcade in
            // the middle of a bank on any bridge longer than its gorge.
            let clear: Vec<&Station> = stations
                .iter()
                .filter(|s| s.t >= lo && s.t <= hi)
                .filter(|s| s.deck_top - self.thickness > s.ground)
                .collect();
            if clear.len() < 2 {
                continue;
            }
            let (a, b) = (clear[0], clear[clear.len() - 1]);
            let gap = a.xz.distance(b.xz);
            if gap < 1.0 {
                continue;
            }
            let tangent = (b.xz - a.xz).try_normalize().unwrap_or(Vec2::X);
            let yaw = yaw_for(tangent);
            let clear_half = (gap * 0.5 - self.pier_width * 0.5).max(0.25);
            // The rise comes first and the springing follows from it, not the
            // other way round. Springing from the pier tops sounds right and
            // is not: the clear stretch ENDS where the deck meets the ground,
            // so its feet sit at zero headroom and the arch comes out flat.
            // A semicircle is the ceiling; the gap under the crown is the
            // other.
            let crown = clear[clear.len() / 2];
            let crown_bottom = crown.deck_top - self.thickness;
            let lowest = clear.iter().fold(f32::INFINITY, |m, s| m.min(s.ground));
            let rise = clear_half.min(crown_bottom - CROWN_HEADROOM - lowest);
            if rise <= 0.5 {
                continue;
            }
            let springing = crown_bottom - CROWN_HEADROOM - rise;
            // Deck underside at slice parameter `u`, so the spandrel meets the
            // camber instead of stopping short of it at the crown.
            let last = (clear.len() - 1) as f32;
            let deck_bottom_at = |u: f32| -> f32 {
                let f = ((u + 1.0) * 0.5).clamp(0.0, 1.0) * last;
                let i = (f.floor() as usize).min(clear.len() - 1);
                let j = (i + 1).min(clear.len() - 1);
                let frac = f - i as f32;
                (clear[i].deck_top + (clear[j].deck_top - clear[i].deck_top) * frac)
                    - self.thickness
            };

            // Piers: from the deck down past the ground under each foot.
            for (side, s) in [("a", a), ("b", b)] {
                let foot = s.ground - FOOT_EMBED;
                let top = s.deck_top - self.thickness;
                if top <= foot {
                    continue;
                }
                out.push(Box::new(OrientedBoxMod::new(
                    format!("{label}:pier:{k}{side}"),
                    Vec3::new(s.xz.x, (top + foot) * 0.5, s.xz.y),
                    Vec3::new(self.pier_width * 0.5, (top - foot) * 0.5, half_w),
                    yaw,
                    ModOp::Union,
                )));
            }

            // Spandrel: a vertical slice per step, from the arch's intrados up
            // to the deck. The slice BOTTOMS are the intrados — there is no
            // separate barrel solid, because the masonry above the curve is
            // what draws the curve.
            let slices = ((gap / SPANDREL_SLICE).round() as usize).clamp(8, MAX_SPANDREL_SLICES);
            let mid_xz = (a.xz + b.xz) * 0.5;
            let intrados = |u: f32| springing + rise * (1.0 - u * u).max(0.0).sqrt();
            for i in 0..slices {
                let u0 = -1.0 + 2.0 * i as f32 / slices as f32;
                let u1 = -1.0 + 2.0 * (i + 1) as f32 / slices as f32;
                // The lower of the two ends: erring toward MORE masonry keeps
                // the intrados a staircase that only ever steps outward, never
                // a gap the mesher would draw as a hole in the barrel.
                let bottom = intrados(u0).min(intrados(u1));
                let u = (u0 + u1) * 0.5;
                let at = mid_xz + tangent * (u * clear_half);
                let top = deck_bottom_at(u);
                if top <= bottom + 0.05 {
                    continue;
                }
                let step = clear_half * 2.0 / slices as f32;
                out.push(Box::new(OrientedBoxMod::new(
                    format!("{label}:spandrel:{k}:{i}"),
                    Vec3::new(at.x, (top + bottom) * 0.5, at.y),
                    Vec3::new(
                        step * 0.5 + SEGMENT_OVERLAP * 0.5,
                        (top - bottom) * 0.5,
                        half_w,
                    ),
                    yaw,
                    ModOp::Union,
                )));
            }
        }
    }

    /// How many arcades. Authored wins; otherwise one span per ~4 deck
    /// heights, which is the proportion a stone bridge reads as.
    fn span_count(&self, stations: &[Station], length: f32) -> u32 {
        if let Some(n) = self.spans {
            return n.max(1);
        }
        let foot = stations.iter().fold(f32::INFINITY, |m, s| m.min(s.ground)) - FOOT_EMBED;
        let mean_height = (stations.iter().map(|s| s.deck_top - foot).sum::<f32>()
            / stations.len() as f32)
            .max(1.0);
        ((length / (4.0 * mean_height)).round() as u32).clamp(1, 12)
    }

    /// Natural rock: a tapered underside instead of piers. Fat where it meets
    /// the banks, thin at the crown — the shape water leaves behind.
    fn push_haunches(&self, out: &mut Vec<Box<dyn VoxelMod>>, stations: &[Station], label: &str) {
        let radius = |t: f32| {
            // 1 at the ends, 0 in the middle — the haunch profile.
            let flare = (2.0 * t - 1.0).abs();
            self.thickness * (0.55 + 1.15 * flare * flare)
        };
        for (i, pair) in stations.windows(2).enumerate() {
            let (a, b) = (pair[0], pair[1]);
            if (b.xz - a.xz).length() < 1e-4 {
                continue;
            }
            let ya = a.deck_top - self.thickness * 0.5;
            let yb = b.deck_top - self.thickness * 0.5;
            out.push(Box::new(RoundConeMod::new(
                format!("{label}:haunch:{i}"),
                Vec3::new(a.xz.x, ya, a.xz.y),
                Vec3::new(b.xz.x, yb, b.xz.y),
                radius(a.t),
                radius(b.t),
                ModOp::Union,
            )));
        }
    }

    /// The best air gap the crossing offers: the largest distance between the
    /// deck underside and the ground under it, anywhere along the deck.
    ///
    /// The MAXIMUM, not the minimum, and deliberately so. Every bridge is
    /// buried at its abutments — that is what an abutment is — so a minimum
    /// would report `-thickness` for every bridge ever authored and the
    /// warning would be pure noise. The question an author is actually asking
    /// is "does this thing get clear of the ground at all?", and this answers
    /// it.
    ///
    /// Reported by the bootstrap, never enforced: a deck laid on the ground is
    /// a legal causeway, it is just rarely what was meant.
    pub fn clearance(&self, base: &dyn HeightField) -> Option<f32> {
        let stations = self.stations(base)?;
        stations
            .iter()
            .map(|s| s.deck_top - self.thickness - s.ground)
            .fold(None::<f32>, |acc, c| Some(acc.map_or(c, |m: f32| m.max(c))))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bevy::math::Vec3;

    /// Flat banks at 26 m with a 40 m wide gorge down to 2 m across x = 0.
    /// Wide enough that a bridge really spans it instead of resting on it.
    #[derive(Debug)]
    struct Gorge;

    impl HeightField for Gorge {
        fn sample(&self, x: f32, _z: f32) -> f32 {
            let t = (x.abs() / 20.0).clamp(0.0, 1.0);
            2.0 + 24.0 * t * t
        }
        fn sample_normal(&self, _x: f32, _z: f32, _e: f32) -> Vec3 {
            Vec3::Y
        }
        fn max_height(&self) -> f32 {
            26.0
        }
    }

    #[derive(Debug)]
    struct Flat;

    impl HeightField for Flat {
        fn sample(&self, _x: f32, _z: f32) -> f32 {
            5.0
        }
        fn sample_normal(&self, _x: f32, _z: f32, _e: f32) -> Vec3 {
            Vec3::Y
        }
        fn max_height(&self) -> f32 {
            5.0
        }
    }

    /// A bridge across the gorge, from bank to bank.
    fn spec(style: BridgeStyle) -> BridgeSpec {
        BridgeSpec {
            name: Some("ponte".into()),
            path: vec![Vec2::new(-30.0, 0.0), Vec2::new(30.0, 0.0)],
            width: 6.0,
            rise: 1.0,
            thickness: 2.0,
            style,
            spans: Some(1),
            parapet: 0.8,
            ..BridgeSpec::default()
        }
    }

    #[test]
    fn test_a_degenerate_spec_builds_nothing() {
        for bad in [
            BridgeSpec {
                path: vec![Vec2::ZERO],
                ..spec(BridgeStyle::Stone)
            },
            BridgeSpec {
                width: 0.0,
                ..spec(BridgeStyle::Stone)
            },
            BridgeSpec {
                thickness: f32::NAN,
                ..spec(BridgeStyle::Stone)
            },
        ] {
            assert!(bad.build(&Gorge).is_empty());
        }
    }

    #[test]
    fn test_the_deck_is_solid_over_the_gorge_and_air_hangs_under_it() {
        let field = super::super::field::VoxelField::new(
            spec(BridgeStyle::Stone).build(&Gorge),
            256.0,
            64.0,
        );
        // Mid-span: the deck is rock, and there is open air between it and the
        // gorge floor at y = 2.
        let deck_top = field.surface_top(&Gorge, 0.0, 0.0);
        assert!(
            deck_top > 20.0,
            "deck at {deck_top:.2} is not over the gorge"
        );
        assert!(
            field.density(&Gorge, Vec3::new(0.0, deck_top - 0.5, 0.0)) < 0.0,
            "just under the deck surface must be rock"
        );
        let floor = field
            .surface_below(&Gorge, 0.0, 0.0, deck_top - 4.0)
            .expect("the gorge floor is still there");
        assert!(
            (floor - 2.0).abs() < 0.6,
            "under the arch the walker gets the floor, got {floor:.2}"
        );
        assert!(
            deck_top - floor > 15.0,
            "only {:.1} m of headroom under a 24 m gorge span",
            deck_top - floor
        );
    }

    #[test]
    fn test_a_natural_span_carries_no_piers_and_no_parapets() {
        let mods = spec(BridgeStyle::Natural).build(&Gorge);
        assert!(mods.iter().all(|m| m.op() == ModOp::Union));
        assert!(
            !mods.iter().any(|m| m.label().contains("parapet")
                || m.label().contains("spandrel")
                || m.label().contains("arcade")),
            "a rock span must be deck + haunches only"
        );
        assert!(mods.iter().any(|m| m.label().contains("haunch")));
    }

    #[test]
    fn test_masonry_stands_a_pair_of_piers_per_span() {
        let three = BridgeSpec {
            spans: Some(3),
            ..spec(BridgeStyle::Stone)
        };
        let mods = three.build(&Gorge);
        for k in 0..3 {
            assert!(
                mods.iter()
                    .any(|m| m.label().contains(&format!("pier:{k}"))),
                "span {k} has no piers"
            );
        }
        assert!(mods.iter().any(|m| m.label().contains("spandrel")));
        assert!(mods.iter().any(|m| m.label().contains("parapet")));
    }

    #[test]
    fn test_a_bridge_is_additive_and_can_never_damage_the_terrain() {
        // The hard rule: nothing about a crossing is subtractive, so no bridge
        // shape can eat the gorge it spans. The fill-and-pierce construction
        // this replaced did exactly that on a V-shaped bed.
        for style in [BridgeStyle::Stone, BridgeStyle::Natural] {
            let mods = spec(style).build(&Gorge);
            assert!(
                mods.iter().all(|m| m.op() == ModOp::Union),
                "{style:?} emitted a subtractive mod"
            );
            let field = super::super::field::VoxelField::new(mods, 256.0, 64.0);
            for x in [-14.0f32, -6.0, 0.0, 6.0, 14.0] {
                let ground = Gorge.sample(x, 0.0);
                assert!(
                    field.density(&Gorge, Vec3::new(x, ground - 0.5, 0.0)) < 0.0,
                    "{style:?} carved the bed at x={x}"
                );
            }
        }
    }

    #[test]
    fn test_the_arch_leaves_air_under_its_crown() {
        // Between the intrados and the bed there must be open air, or the span
        // is a dam with a deck on top.
        let field = super::super::field::VoxelField::new(
            spec(BridgeStyle::Stone).build(&Gorge),
            256.0,
            64.0,
        );
        let spans = field.column(&Gorge, 0.0, 0.0);
        assert_eq!(spans.len(), 2, "deck-and-bed, got {spans:?}");
        assert!(
            spans[0].bottom - spans[1].top > 8.0,
            "only {:.1} m of air under the arch",
            spans[0].bottom - spans[1].top
        );
    }

    #[test]
    fn test_clearance_reports_the_best_gap_not_the_buried_abutments() {
        // Every bridge is buried at its ends. Reporting the minimum would say
        // `-thickness` for all of them and the warning would be noise.
        let clear = spec(BridgeStyle::Stone)
            .clearance(&Gorge)
            .expect("a valid bridge measures");
        assert!(
            clear > 15.0,
            "a 24 m gorge span reported only {clear:.2} m of clearance"
        );
        // A deck laid flat on flat ground really has none, and must say so.
        let causeway = BridgeSpec {
            rise: 0.0,
            ..spec(BridgeStyle::Stone)
        };
        assert!(causeway.clearance(&Flat).expect("measures") < 0.0);
    }

    #[test]
    fn test_yaw_puts_the_deck_along_the_path_not_across_it() {
        // A bridge running north-south must be long in Z. Getting the yaw sign
        // wrong mirrors every deck box across its own centreline.
        let ns = BridgeSpec {
            path: vec![Vec2::new(0.0, -30.0), Vec2::new(0.0, 30.0)],
            style: BridgeStyle::Natural,
            parapet: 0.0,
            ..spec(BridgeStyle::Natural)
        };
        let field = super::super::field::VoxelField::new(ns.build(&Flat), 256.0, 64.0);
        let ground = Flat.sample(0.0, 0.0);
        assert!(
            field.surface_top(&Flat, 0.0, 10.0) > ground + 0.5,
            "the deck is missing along its own path"
        );
        assert!(
            (field.surface_top(&Flat, 10.0, 0.0) - ground).abs() < 0.2,
            "the deck spilled sideways — the yaw is mirrored"
        );
    }
}
