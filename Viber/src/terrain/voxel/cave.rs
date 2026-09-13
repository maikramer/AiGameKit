//! `<Cave>` — a tunnel bored through the terrain.
//!
//! The first world feature that a heightfield cannot express at all. A cave is
//! not a carve: it is a chain of subtractive [`CapsuleMod`]s that removes rock
//! the base field says is solid, leaving a roof over the player's head.
//!
//! # Authoring
//!
//! ```xml
//! <Cave name="mina" path="-40 10  0 4  38 -6" radius="3.5" depth="10" />
//! ```
//!
//! `path` is XZ, like every other ground feature — the tunnel's height comes
//! from the terrain, not from the author, so a cave drawn across a hillside
//! follows it instead of needing hand-placed Y at every bend.
//!
//! `depth` is how far the tube centre sits **below the surface**. With
//! `open-ends` (the default) that depth ramps to zero at both ends, so the
//! tube breaks out of the hillside and the cave is enterable. A cave at a
//! constant depth is sealed rock with a hole inside it that nobody can reach.

use bevy::math::{Vec2, Vec3};

use super::super::mesh::HeightField;
use super::super::paths::{chaikin_smooth, resample};
use super::mods::{CapsuleMod, EllipsoidMod, ModOp, RoundConeMod, VoxelMod};

/// Default tunnel radius (meters) — wide enough to walk through.
pub const DEFAULT_CAVE_RADIUS: f32 = 3.0;
/// Default depth of the tube centre below the surface (meters).
pub const DEFAULT_CAVE_DEPTH: f32 = 8.0;
/// Spacing between capsule stations (meters).
///
/// Short enough that a bend reads as a curve, long enough that a 100 m cave is
/// ~25 mods rather than hundreds — the index buckets them per column, so this
/// is what a density query inside the cave actually pays for.
const STATION_SPACING: f32 = 4.0;
/// Fraction of the cave length over which the ends ramp up to the surface.
pub const DEFAULT_MOUTH_FRACTION: f32 = 0.18;
/// How far a shaft pokes above the ground so its mouth is unmistakably open.
const SHAFT_OVERSHOOT: f32 = 0.5;

/// A room hollowed out on the tunnel's line — `<Chamber>` inside a `<Cave>`.
///
/// A tunnel is a tube of one radius; widening the tube to make a hall widens
/// the corridor leading to it too. A chamber is the separate solid that lets
/// a cave system have rooms.
#[derive(Debug, Clone, PartialEq)]
pub struct CaveChamberSpec {
    pub name: Option<String>,
    /// World XZ of the room's centre.
    pub at: Vec2,
    /// Horizontal radius (meters).
    pub radius: f32,
    /// Floor-to-ceiling height (meters).
    pub height: f32,
    /// Depth of the room's centre below the surface; defaults to the cave's.
    pub depth: Option<f32>,
}

impl Default for CaveChamberSpec {
    fn default() -> Self {
        Self {
            name: None,
            at: Vec2::ZERO,
            radius: 8.0,
            height: 6.0,
            depth: None,
        }
    }
}

/// A vertical chimney from the tunnel to daylight — `<Shaft>` inside a
/// `<Cave>`. The second way in, and the only source of light down there.
#[derive(Debug, Clone, PartialEq)]
pub struct CaveShaftSpec {
    pub name: Option<String>,
    /// World XZ of the shaft.
    pub at: Vec2,
    /// Shaft radius (meters).
    pub radius: f32,
    /// Depth the shaft reaches below the surface; defaults to the cave's.
    pub depth: Option<f32>,
}

impl Default for CaveShaftSpec {
    fn default() -> Self {
        Self {
            name: None,
            at: Vec2::ZERO,
            radius: 1.5,
            depth: None,
        }
    }
}

/// Declarative `<Cave>` spec.
#[derive(Debug, Clone, PartialEq)]
pub struct CaveSpec {
    pub name: Option<String>,
    /// Tunnel centreline in world XZ.
    pub path: Vec<Vec2>,
    /// Tube radius (meters): one value for a constant tube, or a profile
    /// interpolated along the path — `radius="2.5 6 3"` narrows, opens into a
    /// gallery, and narrows again.
    pub radius: Vec<f32>,
    /// Depth of the tube centre below the terrain surface (meters).
    pub depth: f32,
    /// Ramp the ends up to the surface so the cave has mouths.
    pub open_ends: bool,
    /// Radius multiplier at the two mouths; 1.0 keeps the tube's own width.
    pub mouth_flare: f32,
    /// Fraction of the length over which each mouth ramps to the surface.
    pub mouth_fraction: f32,
    /// Rooms on the line (`<Chamber>`).
    pub chambers: Vec<CaveChamberSpec>,
    /// Chimneys to the surface (`<Shaft>`).
    pub shafts: Vec<CaveShaftSpec>,
}

impl Default for CaveSpec {
    fn default() -> Self {
        Self {
            name: None,
            path: Vec::new(),
            radius: vec![DEFAULT_CAVE_RADIUS],
            depth: DEFAULT_CAVE_DEPTH,
            open_ends: true,
            mouth_flare: 1.0,
            mouth_fraction: DEFAULT_MOUTH_FRACTION,
            chambers: Vec::new(),
            shafts: Vec::new(),
        }
    }
}

impl CaveSpec {
    /// Depth of the tube centre at path fraction `t` (0..1).
    ///
    /// Flat in the middle, ramping to zero at each mouth. `smoothstep` rather
    /// than a straight ramp so the mouth flares instead of ending in a cone.
    fn depth_at(&self, t: f32) -> f32 {
        if !self.open_ends {
            return self.depth;
        }
        let m = self.mouth_fraction.clamp(1e-3, 0.5);
        let edge = t.min(1.0 - t) / m;
        if edge >= 1.0 {
            return self.depth;
        }
        let s = edge.clamp(0.0, 1.0);
        self.depth * (s * s * (3.0 - 2.0 * s))
    }

    /// Tube radius at path fraction `t`, mouth flare included.
    ///
    /// The profile is keyed on `t` directly rather than through
    /// [`super::super::paths::station_lerp`]: `t` is already in hand here, and
    /// that helper would have to search the polyline for a point we placed.
    fn radius_at(&self, t: f32) -> f32 {
        let base = profile_at(&self.radius, t);
        if self.mouth_flare == 1.0 {
            return base;
        }
        let m = self.mouth_fraction.clamp(1e-3, 0.5);
        let edge = (t.min(1.0 - t) / m).clamp(0.0, 1.0);
        // 1 at the mouth, 0 once past the flare band.
        let s = 1.0 - edge * edge * (3.0 - 2.0 * edge);
        base * (1.0 + (self.mouth_flare - 1.0) * s)
    }

    /// Largest authored radius — what the "this breaches the surface" check
    /// has to measure against.
    pub fn radius_max(&self) -> f32 {
        self.radius
            .iter()
            .copied()
            .fold(f32::NEG_INFINITY, f32::max)
    }

    /// Smallest authored radius; `<= 0` is what makes a cave spec unbuildable.
    pub fn radius_min(&self) -> f32 {
        self.radius.iter().copied().fold(f32::INFINITY, f32::min)
    }

    /// Builds the subtractive mods for this cave against the carved terrain.
    ///
    /// Takes the height field because the tunnel follows the ground: this runs
    /// in the bootstrap **after** pads/lakes/rivers/roads have carved, so a
    /// cave under a road bed sits under the road bed as built, not under the
    /// terrain as generated.
    pub fn build(&self, base: &dyn HeightField) -> Vec<Box<dyn VoxelMod>> {
        let r_min = self.radius_min();
        if self.path.len() < 2 || self.radius.is_empty() || r_min <= 0.0 || !r_min.is_finite() {
            return Vec::new();
        }
        // Same treatment every other path feature gets: smooth the authored
        // polyline, then walk it at a fixed spacing.
        let smoothed = chaikin_smooth(&self.path, 2, false);
        let stations = resample(&smoothed, STATION_SPACING);
        if stations.len() < 2 {
            return Vec::new();
        }

        let label = self.name.clone().unwrap_or_else(|| "cave".to_string());
        // Parametrise by ARC LENGTH, not by station index. `resample` pins the
        // authored end point, so the last station is usually a short stub —
        // keying on the index would put the middle of a `radius="2 6 2"`
        // profile wherever the stations happened to fall, not halfway along
        // the tunnel.
        let ts = arc_fractions(&stations);
        let centre = |i: usize| -> Vec3 {
            let p = stations[i];
            Vec3::new(p.x, base.sample(p.x, p.y) - self.depth_at(ts[i]), p.y)
        };

        let mut mods: Vec<Box<dyn VoxelMod>> = Vec::with_capacity(stations.len());
        for i in 0..stations.len() - 1 {
            let (ra, rb) = (self.radius_at(ts[i]), self.radius_at(ts[i + 1]));
            // A constant tube stays a capsule — same mod, same numbers, so a
            // world authored before the profile existed meshes bit for bit.
            if (ra - rb).abs() <= 1e-4 {
                mods.push(Box::new(CapsuleMod::new(
                    format!("{label}:{i}"),
                    centre(i),
                    centre(i + 1),
                    ra,
                    ModOp::Subtract,
                )));
            } else {
                mods.push(Box::new(RoundConeMod::new(
                    format!("{label}:{i}"),
                    centre(i),
                    centre(i + 1),
                    ra,
                    rb,
                    ModOp::Subtract,
                )));
            }
        }

        for (i, room) in self.chambers.iter().enumerate() {
            if room.radius <= 0.0 || room.height <= 0.0 {
                continue;
            }
            let depth = room.depth.unwrap_or(self.depth);
            let ground = base.sample(room.at.x, room.at.y);
            let name = room
                .name
                .clone()
                .unwrap_or_else(|| format!("{label}:chamber:{i}"));
            mods.push(Box::new(EllipsoidMod::new(
                name,
                Vec3::new(room.at.x, ground - depth, room.at.y),
                Vec3::new(room.radius, room.height * 0.5, room.radius),
                0.0,
                ModOp::Subtract,
            )));
        }

        for (i, shaft) in self.shafts.iter().enumerate() {
            if shaft.radius <= 0.0 {
                continue;
            }
            let depth = shaft.depth.unwrap_or(self.depth);
            let ground = base.sample(shaft.at.x, shaft.at.y);
            let name = shaft
                .name
                .clone()
                .unwrap_or_else(|| format!("{label}:shaft:{i}"));
            // Overshoot the surface: a chimney that stops exactly at ground
            // level leaves a skin of rock over it at the mesher's resolution,
            // and the shaft is a sealed pipe nobody can see into.
            mods.push(Box::new(CapsuleMod::new(
                name,
                Vec3::new(shaft.at.x, ground - depth, shaft.at.y),
                Vec3::new(shaft.at.x, ground + SHAFT_OVERSHOOT, shaft.at.y),
                shaft.radius,
                ModOp::Subtract,
            )));
        }
        mods
    }
}

/// Cumulative arc-length fraction of every station, 0 at the first and 1 at
/// the last.
fn arc_fractions(stations: &[Vec2]) -> Vec<f32> {
    let mut acc = Vec::with_capacity(stations.len());
    let mut run = 0.0_f32;
    acc.push(0.0);
    for pair in stations.windows(2) {
        run += pair[0].distance(pair[1]);
        acc.push(run);
    }
    if run <= 1e-6 {
        return vec![0.0; stations.len()];
    }
    acc.iter().map(|d| d / run).collect()
}

/// A value sampled from a profile at fraction `t`: empty is 0, one value is
/// constant, N values interpolate evenly along the path.
fn profile_at(values: &[f32], t: f32) -> f32 {
    match values.len() {
        0 => 0.0,
        1 => values[0],
        n => {
            let f = t.clamp(0.0, 1.0) * (n - 1) as f32;
            let i = (f.floor() as usize).min(n - 2);
            let frac = f - i as f32;
            values[i] + (values[i + 1] - values[i]) * frac
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Debug)]
    struct Flat(f32);

    impl HeightField for Flat {
        fn sample(&self, _x: f32, _z: f32) -> f32 {
            self.0
        }
        fn sample_normal(&self, _x: f32, _z: f32, _e: f32) -> Vec3 {
            Vec3::Y
        }
        fn max_height(&self) -> f32 {
            self.0
        }
    }

    fn spec() -> CaveSpec {
        CaveSpec {
            name: Some("mina".into()),
            path: vec![
                Vec2::new(-40.0, 0.0),
                Vec2::new(0.0, 0.0),
                Vec2::new(40.0, 0.0),
            ],
            radius: vec![3.0],
            depth: 10.0,
            open_ends: true,
            ..CaveSpec::default()
        }
    }

    #[test]
    fn test_a_constant_radius_still_builds_plain_capsules() {
        // The profile is opt-in: a world authored before it existed must emit
        // the same mods it always did.
        let mods = spec().build(&Flat(50.0));
        assert!(
            mods.iter()
                .all(|m| format!("{m:?}").starts_with("CapsuleMod"))
        );
    }

    #[test]
    fn test_a_radius_profile_tapers_the_tube() {
        let mut s = spec();
        s.radius = vec![2.0, 6.0, 2.0];
        assert_eq!(s.radius_min(), 2.0);
        assert_eq!(s.radius_max(), 6.0);
        let mods = s.build(&Flat(50.0));
        assert!(
            mods.iter()
                .any(|m| format!("{m:?}").starts_with("RoundConeMod")),
            "a varying radius must switch to the tapered primitive"
        );
        // The tube is 6 m wide at the middle of the path — twice the 3 m the
        // flat-radius spec would have given. Ground 50, depth 10, so the
        // centre line runs at y = 40 there.
        let field = super::super::field::VoxelField::new(mods, 256.0, 64.0);
        assert!(
            field.density(&Flat(50.0), Vec3::new(0.0, 40.0, 5.0)) > 0.0,
            "5 m off the line must be inside the gallery"
        );
        assert!(
            field.density(&Flat(50.0), Vec3::new(0.0, 40.0, 7.0)) < 0.0,
            "7 m off the line must still be rock"
        );
    }

    #[test]
    fn test_a_chamber_is_a_separate_room_and_a_shaft_reaches_daylight() {
        let mut s = spec();
        s.chambers.push(CaveChamberSpec {
            name: Some("salao".into()),
            at: Vec2::ZERO,
            radius: 8.0,
            height: 6.0,
            depth: None,
        });
        s.shafts.push(CaveShaftSpec {
            name: Some("chamine".into()),
            at: Vec2::new(6.0, 0.0),
            radius: 1.5,
            depth: None,
        });
        let mods = s.build(&Flat(50.0));
        assert!(mods.iter().any(|m| m.label() == "salao"));
        assert!(mods.iter().any(|m| m.label() == "chamine"));
        assert!(mods.iter().all(|m| m.op() == ModOp::Subtract));

        let field = super::super::field::VoxelField::new(s.build(&Flat(50.0)), 256.0, 64.0);
        // The room is wider than the 3 m tube: 6 m off the line is still air.
        assert!(
            field.density(&Flat(50.0), Vec3::new(0.0, 40.0, 6.0)) > 0.0,
            "the chamber never opened"
        );
        // The chimney breaks the surface, so the top of the world there is the
        // tunnel floor rather than the flat 50.
        assert!(
            field.surface_top(&Flat(50.0), 6.0, 0.0) < 49.0,
            "the shaft did not reach daylight"
        );
    }

    #[test]
    fn test_a_degenerate_chamber_or_shaft_is_skipped_not_built() {
        let mut s = spec();
        s.chambers.push(CaveChamberSpec {
            radius: 0.0,
            ..CaveChamberSpec::default()
        });
        s.shafts.push(CaveShaftSpec {
            radius: -1.0,
            ..CaveShaftSpec::default()
        });
        let plain = spec().build(&Flat(50.0)).len();
        assert_eq!(s.build(&Flat(50.0)).len(), plain);
    }

    #[test]
    fn test_a_short_or_degenerate_path_builds_nothing() {
        let mut s = spec();
        s.path = vec![Vec2::ZERO];
        assert!(s.build(&Flat(50.0)).is_empty());
        let mut s = spec();
        s.radius = vec![0.0];
        assert!(s.build(&Flat(50.0)).is_empty());
    }

    #[test]
    fn test_the_tunnel_is_subtractive_and_follows_the_path() {
        let mods = spec().build(&Flat(50.0));
        assert!(mods.len() > 10, "80 m at 4 m spacing, got {}", mods.len());
        assert!(mods.iter().all(|m| m.op() == ModOp::Subtract));
        // The whole chain stays inside the authored XZ corridor.
        for m in &mods {
            let b = m.bounds();
            assert!(b.min.x >= -50.0 && b.max.x <= 50.0, "strayed: {b:?}");
        }
    }

    #[test]
    fn test_open_ends_bring_the_mouths_up_to_the_surface() {
        let mods = spec().build(&Flat(50.0));
        let first = mods.first().unwrap().bounds();
        let last = mods.last().unwrap().bounds();
        // A mouth reaches the surface (ground 50, radius 3): its box must
        // clear the ground, otherwise the cave is a sealed bubble.
        assert!(
            first.max.y >= 50.0,
            "entrance is buried: {first:?} — the cave would be unreachable"
        );
        assert!(last.max.y >= 50.0, "exit is buried: {last:?}");
    }

    #[test]
    fn test_the_middle_of_the_cave_stays_at_the_authored_depth() {
        let s = spec();
        assert!((s.depth_at(0.5) - 10.0).abs() < 1e-6);
        assert_eq!(s.depth_at(0.0), 0.0, "the mouth is at the surface");
        assert_eq!(s.depth_at(1.0), 0.0);
        // Monotone ramp out of the mouth.
        assert!(s.depth_at(0.05) < s.depth_at(0.12));
    }

    #[test]
    fn test_closed_ends_keep_the_full_depth_everywhere() {
        let mut s = spec();
        s.open_ends = false;
        assert_eq!(s.depth_at(0.0), 10.0);
        assert_eq!(s.depth_at(0.5), 10.0);
        let mods = s.build(&Flat(50.0));
        let first = mods.first().unwrap().bounds();
        assert!(
            first.max.y < 50.0,
            "a closed cave must stay buried, got {first:?}"
        );
    }

    #[test]
    fn test_build_is_deterministic() {
        let a = spec().build(&Flat(50.0));
        let b = spec().build(&Flat(50.0));
        let bounds = |m: &Vec<Box<dyn VoxelMod>>| m.iter().map(|x| x.bounds()).collect::<Vec<_>>();
        assert_eq!(bounds(&a), bounds(&b));
    }
}
