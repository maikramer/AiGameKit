//! `<Arch>` — a free-standing rock portal (`src/terrain/voxel/mods.rs::
//! ArchMod`).
//!
//! Like `<Cave>`, this never touches the heightfield: the arch is a union
//! solid in the voxel field, which is the only representation that can hold
//! rock above and air below at the same XZ. The whole point of the tag is
//! the walk-through: a column under the opening resolves to TWO spans
//! (ground + the arch band), and gameplay asks
//! [`super::field::VoxelField::surface_below`] which of them to stand on.

use bevy::math::{Vec2, Vec3};

use super::super::mesh::HeightField;
use super::super::paths::{chaikin_smooth, path_length};
use super::mods::{ArchMod, ModOp, RoundConeMod, VoxelMod};

/// Default clear span of the opening (meters).
pub const DEFAULT_ARCH_SPAN: f32 = 8.0;
/// Default clear opening height at the crown (meters).
pub const DEFAULT_ARCH_HEIGHT: f32 = 6.0;
/// Default leg thickness beyond the opening on each side (meters).
pub const DEFAULT_ARCH_THICKNESS: f32 = 2.5;
/// Default block depth along the arch axis (meters).
pub const DEFAULT_ARCH_DEPTH: f32 = 4.0;

/// How an `<Arch>` is shaped.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ArchProfile {
    /// A cut block: straight jambs, rounded crown, flat faces. Reads as built.
    #[default]
    Portal,
    /// A rock band curving from foot to foot, fat at the ground and thin at
    /// the crown. Reads as eroded.
    Natural,
}

impl ArchProfile {
    pub fn parse(name: &str) -> Option<Self> {
        match name.trim().to_ascii_lowercase().as_str() {
            "portal" | "block" => Some(Self::Portal),
            "natural" | "rock" => Some(Self::Natural),
            _ => None,
        }
    }

    pub const NAMES: &'static str = "portal, natural";
}

/// Stations used to draw a natural arch band. More is smoother and costs one
/// mod each; 12 reads as a curve at walking distance.
const NATURAL_SEGMENTS: usize = 12;

/// Declarative `<Arch>` spec.
#[derive(Debug, Clone, PartialEq)]
pub struct ArchSpec {
    pub name: Option<String>,
    /// Centre of the block footprint, world XZ. Ignored when `path` is set.
    pub at: Vec2,
    /// Two or more world XZ points the arch follows. The alternative to `at`:
    /// each leg then lands on the ground under its own foot, which is what
    /// makes an arch across a slope read as natural instead of floating.
    pub path: Vec<Vec2>,
    /// Clear opening width (meters). Unset derives it: [`DEFAULT_ARCH_SPAN`]
    /// for an `at` arch, the pitch minus the legs for a `path` one.
    pub span: Option<f32>,
    /// Clear opening height at the crown (meters).
    pub height: f32,
    /// Leg thickness beyond the opening on each side (meters).
    pub thickness: f32,
    /// Block depth along the arch axis (meters).
    pub depth: f32,
    /// Rotation of the arch axis about the centre, degrees. Ignored with
    /// `path` — the path's own tangent wins.
    pub yaw: f32,
    /// Number of openings spread along `path`: 1 is an arch, N is a viaduct.
    pub spans: u32,
    pub profile: ArchProfile,
}

impl Default for ArchSpec {
    fn default() -> Self {
        Self {
            name: None,
            at: Vec2::ZERO,
            path: Vec::new(),
            span: None,
            height: DEFAULT_ARCH_HEIGHT,
            thickness: DEFAULT_ARCH_THICKNESS,
            depth: DEFAULT_ARCH_DEPTH,
            yaw: 0.0,
            spans: 1,
            profile: ArchProfile::Portal,
        }
    }
}

/// One opening, resolved against the terrain: where it stands, which way it
/// faces, how wide it is, and what the ground does under each of its feet.
#[derive(Debug, Clone, Copy)]
struct ArchInstance {
    at: Vec2,
    yaw: f32,
    span: f32,
    /// Ground under the left and right feet (local -X and +X).
    foot_l: f32,
    foot_r: f32,
}

impl ArchSpec {
    /// Every parameter finite and positive — the gate every build goes through.
    fn is_valid(&self) -> bool {
        let span_ok = self.span.is_none_or(|s| s > 0.0 && s.is_finite());
        span_ok
            && self.height > 0.0
            && self.thickness > 0.0
            && self.depth > 0.0
            && self.height.is_finite()
            && self.thickness.is_finite()
            && self.depth.is_finite()
            && self.spans >= 1
    }

    /// Resolves the openings this spec asks for against the height field.
    fn instances(&self, base: &dyn HeightField) -> Vec<ArchInstance> {
        let foot = |at: Vec2, yaw: f32, span: f32| {
            // The two feet sit half a clear span plus half a leg out from the
            // centre, along the arch axis.
            let reach = span * 0.5 + self.thickness * 0.5;
            let (s, c) = yaw.sin_cos();
            // Inverse of `yaw_local`: local +X back out into the world.
            let axis = Vec2::new(c, -s);
            let l = at - axis * reach;
            let r = at + axis * reach;
            (base.sample(l.x, l.y), base.sample(r.x, r.y))
        };
        if self.path.len() < 2 {
            let yaw = self.yaw.to_radians();
            let span = self.span.unwrap_or(DEFAULT_ARCH_SPAN);
            let (foot_l, foot_r) = foot(self.at, yaw, span);
            return vec![ArchInstance {
                at: self.at,
                yaw,
                span,
                foot_l,
                foot_r,
            }];
        }
        let smoothed = chaikin_smooth(&self.path, 2, false);
        let total = path_length(&smoothed);
        if total <= 1e-3 {
            return Vec::new();
        }
        let n = self.spans.max(1);
        let pitch = total / n as f32;
        // Default clear span: the pitch minus a leg on each side, floored so a
        // tight viaduct still has an opening rather than a solid wall.
        let span = self
            .span
            .unwrap_or_else(|| (pitch - 2.0 * self.thickness).max(pitch * 0.4));
        (0..n)
            .filter_map(|k| {
                let t = (k as f32 + 0.5) / n as f32;
                let (at, tangent) = along(&smoothed, t)?;
                let yaw = -tangent.y.atan2(tangent.x);
                let (foot_l, foot_r) = foot(at, yaw, span);
                Some(ArchInstance {
                    at,
                    yaw,
                    span,
                    foot_l,
                    foot_r,
                })
            })
            .collect()
    }

    /// Builds the union mods for this arch against the carved terrain.
    ///
    /// The base comes from the heightfield under the arch's feet minus a metre
    /// of embed, exactly the treatment every ground-anchored feature gets —
    /// and like every mod build, it reads the grid up front and never again.
    pub fn build(&self, base: &dyn HeightField) -> Vec<Box<dyn VoxelMod>> {
        if !self.is_valid() {
            return Vec::new();
        }
        let label = self.name.clone().unwrap_or_else(|| "arch".to_string());
        let instances = self.instances(base);
        let mut out: Vec<Box<dyn VoxelMod>> = Vec::new();
        for (k, inst) in instances.iter().enumerate() {
            let name = if instances.len() == 1 {
                label.clone()
            } else {
                format!("{label}:{k}")
            };
            match self.profile {
                ArchProfile::Portal => self.push_portal(&mut out, inst, name),
                ArchProfile::Natural => self.push_natural(&mut out, inst, name),
            }
        }
        out
    }

    /// The cut block: one [`ArchMod`], the shape this tag shipped with.
    fn push_portal(&self, out: &mut Vec<Box<dyn VoxelMod>>, inst: &ArchInstance, label: String) {
        let span_half = inst.span * 0.5;
        // Jamb height = total opening minus the rounded crown. A span wider
        // than the opening is tall turns the arch into a dome — two clamps
        // keep the contract: the jamb keeps at least a token straight section,
        // and the OPENING is clamped to 3/4 of the height so the crown's apex
        // (jamb + span half) lands at the authored `height` instead of poking
        // above it. Legs keep their full authored thickness either way.
        let jamb = (self.height - span_half).max(self.height * 0.25);
        let span_half_eff = span_half.min(self.height * 0.75);
        // Rock cap above the crown, proportionate to the legs.
        let cap = self.thickness * 0.8;
        // The LOWER of the two feet: anchoring on the higher one would leave
        // the downhill leg hanging in the air.
        let ground = inst.foot_l.min(inst.foot_r);
        out.push(Box::new(ArchMod::new(
            label,
            Vec3::new(inst.at.x, ground - 1.0, inst.at.y),
            span_half_eff,
            jamb,
            self.thickness,
            self.depth,
            cap,
            inst.yaw,
            ModOp::Union,
        )));
    }

    /// The eroded band: a chain of round cones from foot to foot, following an
    /// ellipse over the opening, fat where it meets the ground and thin at the
    /// crown. Each foot sits on its own ground, so a slope tilts the arch
    /// instead of leaving one leg in mid-air.
    fn push_natural(&self, out: &mut Vec<Box<dyn VoxelMod>>, inst: &ArchInstance, label: String) {
        let reach = inst.span * 0.5 + self.thickness * 0.5;
        let (s, c) = inst.yaw.sin_cos();
        let axis = Vec2::new(c, -s);
        let half_th = self.thickness * 0.5;
        // Centre-line of the band at parameter u in [-1, 1].
        let point = |u: f32| -> (Vec3, f32) {
            let xz = inst.at + axis * (u * reach);
            let foot = inst.foot_l + (inst.foot_r - inst.foot_l) * (u + 1.0) * 0.5 - 1.0;
            let lift = self.height * (1.0 - u * u).max(0.0).sqrt();
            // Fat at the feet, thin at the crown — the profile water leaves.
            let radius = half_th * (0.85 + 1.3 * u * u);
            (Vec3::new(xz.x, foot + lift + half_th, xz.y), radius)
        };
        for i in 0..NATURAL_SEGMENTS {
            let u0 = -1.0 + 2.0 * i as f32 / NATURAL_SEGMENTS as f32;
            let u1 = -1.0 + 2.0 * (i + 1) as f32 / NATURAL_SEGMENTS as f32;
            let (a, ra) = point(u0);
            let (b, rb) = point(u1);
            out.push(Box::new(RoundConeMod::new(
                format!("{label}:{i}"),
                a,
                b,
                ra,
                rb,
                ModOp::Union,
            )));
        }
    }
}

/// Point and unit tangent at fraction `t` of a polyline's length.
fn along(points: &[Vec2], t: f32) -> Option<(Vec2, Vec2)> {
    if points.len() < 2 {
        return None;
    }
    let total = path_length(points);
    if total <= 1e-6 {
        return None;
    }
    let mut want = t.clamp(0.0, 1.0) * total;
    for pair in points.windows(2) {
        let (a, b) = (pair[0], pair[1]);
        let d = b - a;
        let len = d.length();
        if len <= 1e-6 {
            continue;
        }
        if want <= len {
            let dir = d / len;
            return Some((a + dir * want, dir));
        }
        want -= len;
    }
    let a = points[points.len() - 2];
    let b = points[points.len() - 1];
    let dir = (b - a).try_normalize().unwrap_or(Vec2::X);
    Some((b, dir))
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Flat;

    impl HeightField for Flat {
        fn sample(&self, _x: f32, _z: f32) -> f32 {
            10.0
        }
        fn sample_normal(&self, _x: f32, _z: f32, _e: f32) -> Vec3 {
            Vec3::Y
        }
        fn max_height(&self) -> f32 {
            10.0
        }
    }

    #[derive(Debug)]
    struct Slope;

    impl HeightField for Slope {
        fn sample(&self, x: f32, _z: f32) -> f32 {
            // 10 m at x = 0, climbing 0.5 m per metre eastwards.
            10.0 + 0.5 * x
        }
        fn sample_normal(&self, _x: f32, _z: f32, _e: f32) -> Vec3 {
            Vec3::Y
        }
        fn max_height(&self) -> f32 {
            60.0
        }
    }

    #[test]
    fn test_a_path_arch_lands_both_feet_on_their_own_ground() {
        // Across a slope: the naive build anchors on the centre height and
        // leaves the downhill leg in mid-air. The block has to reach the LOWER
        // foot, so its base sits under the ground at the western foot.
        let spec = ArchSpec {
            path: vec![Vec2::new(-6.0, 0.0), Vec2::new(6.0, 0.0)],
            height: 6.0,
            ..ArchSpec::default()
        };
        let mods = spec.build(&Slope);
        assert_eq!(mods.len(), 1);
        let b = mods[0].bounds();
        let west_foot = Slope.sample(-6.0, 0.0);
        assert!(
            b.min.y <= west_foot,
            "block bottom {:.2} must reach the downhill ground {west_foot:.2}",
            b.min.y
        );
    }

    #[test]
    fn test_spans_makes_a_viaduct_of_separate_openings() {
        let spec = ArchSpec {
            path: vec![Vec2::new(-40.0, 0.0), Vec2::new(40.0, 0.0)],
            spans: 4,
            height: 10.0,
            ..ArchSpec::default()
        };
        let mods = spec.build(&Flat);
        assert_eq!(mods.len(), 4, "one block per opening");
        // The four blocks are spread along the path, not stacked on one point.
        let mut centres: Vec<f32> = mods
            .iter()
            .map(|m| (m.bounds().min.x + m.bounds().max.x) * 0.5)
            .collect();
        centres.sort_by(f32::total_cmp);
        for pair in centres.windows(2) {
            assert!(
                pair[1] - pair[0] > 10.0,
                "openings must be a pitch apart, got {centres:?}"
            );
        }
    }

    #[test]
    fn test_a_natural_arch_is_a_band_with_air_under_it() {
        let spec = ArchSpec {
            path: vec![Vec2::new(-9.0, 0.0), Vec2::new(9.0, 0.0)],
            profile: ArchProfile::Natural,
            height: 7.0,
            thickness: 2.0,
            ..ArchSpec::default()
        };
        let mods = spec.build(&Flat);
        assert_eq!(mods.len(), NATURAL_SEGMENTS);
        assert!(mods.iter().all(|m| m.op() == ModOp::Union));
        let field = super::super::field::VoxelField::new(mods, 256.0, 64.0);
        // Under the crown: air, then the flat ground under the walker.
        let top = field.surface_top(&Flat, 0.0, 0.0);
        assert!(
            top > 14.0,
            "the band must stand well over the ground: {top}"
        );
        let floor = field
            .surface_below(&Flat, 0.0, 0.0, top - 1.0)
            .expect("ground under the span");
        assert!(floor < 11.0, "the walker keeps the ground: {floor}");
        assert!(
            field.density(&Flat, Vec3::new(0.0, 13.0, 0.0)) > 0.0,
            "mid-height under the crown must be air"
        );
    }

    #[test]
    fn test_arch_builds_one_union_mod_on_the_ground() {
        let spec = ArchSpec::default();
        let mods = spec.build(&Flat);
        assert_eq!(mods.len(), 1);
        assert_eq!(mods[0].op(), ModOp::Union);
        let b = mods[0].bounds();
        // Base sits 1 m under ground (10), block ~9 m tall: bounds straddle.
        assert!(b.min.y < 10.0 && b.max.y > 10.0);
    }

    #[test]
    fn test_invalid_specs_build_nothing() {
        for span in [0.0, f32::NAN, -3.0] {
            let spec = ArchSpec {
                span: Some(span),
                ..ArchSpec::default()
            };
            assert!(
                spec.build(&Flat).is_empty(),
                "span {span} must build nothing"
            );
        }
        let no_height = ArchSpec {
            height: 0.0,
            ..ArchSpec::default()
        };
        assert!(no_height.build(&Flat).is_empty());
        let no_spans = ArchSpec {
            spans: 0,
            path: vec![Vec2::ZERO, Vec2::new(10.0, 0.0)],
            ..ArchSpec::default()
        };
        assert!(no_spans.build(&Flat).is_empty());
    }

    #[test]
    fn test_a_span_taller_than_the_arch_stays_under_the_authored_height() {
        // span 10 vs height 6: the naive apex (jamb 1.5 + span half 5) would
        // poke 6.5 m above the base — above the authored `height`. The
        // opening is clamped to 3/4 of the height so the crown lands exactly
        // at 6.
        let spec = ArchSpec {
            span: Some(10.0),
            height: 6.0,
            ..ArchSpec::default()
        };
        let mods = spec.build(&Flat);
        let field = super::super::field::VoxelField::new(mods, 256.0, 64.0);
        // Base sits at ground 10 − 1 = 9; crown apex = 9 + jamb 1.5 + 4.5 = 15.
        let under_crown = field.density(&Flat, Vec3::new(0.0, 14.7, 0.0));
        assert!(under_crown > 0.0, "just under the crown must still be air");
        let above_crown = field.density(&Flat, Vec3::new(0.0, 15.3, 0.0));
        assert!(
            above_crown < 0.0,
            "rock must start at the authored height (apex 15) — the opening \
             poked above it ({under_crown} at 14.7, {above_crown} at 15.3)"
        );
    }

    #[test]
    fn test_opening_column_has_two_spans_of_air_between() {
        let spec = ArchSpec::default();
        let mods = spec.build(&Flat);
        let field = super::super::field::VoxelField::new(mods, 256.0, 64.0);
        let at = spec.at;
        // Through the opening: ground at 10, arch band above. Top of the
        // column is the arch crown, NOT the flat 10.
        let top = field.surface_top(&Flat, at.x, at.y);
        assert!(top > 12.0, "crown must stand above the flat ground: {top}");
        // And the ground right below the walker is still the flat 10.
        let ground = field
            .surface_below(&Flat, at.x, at.y, top - 0.5)
            .expect("solid below the crown");
        assert!(ground < 11.0, "walker's floor stays the ground: {ground}");
    }
}
