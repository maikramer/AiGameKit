//! Roads — corridor carving, `<RoadNetwork>` expansion (Way/Segment), road
//! ribbons and the gameplay query registry.
//!
//! Ported from the VibeGame road plugin (`road/carve.ts`, `road/network.ts`,
//! `road/profiles.ts`). Contracts kept from the original:
//!
//! * **Profile first** — the design profile is surveyed from the **post-pad,
//!   post-water** grid, smoothed with a moving-average window
//!   (`flatten-window`, 3 passes) and then **grade-limited**
//!   (`flatten-max-grade`, forward+backward passes), so roads hug hillsides
//!   instead of tunnelling through them.
//! * **Corridor carve** — blend (cut **and** fill) toward
//!   `profile − platform_sink`, weight 1 inside `half + shoulder` and a C2
//!   `smootherstep` falloff outside; the falloff widens adaptively with the
//!   cut depth (`max(falloff, 1.875 · cut)`) so deep cuts get a ~45° slope
//!   instead of a trench.
//! * **Mutual guards** — roads skip pad cores (plaza stays flat) and water
//!   carve zones (river/lake are never filled back in). Bridges
//!   (`profile="bridge"`) skip the corridor carve entirely and only report a
//!   flat deck height for the ribbon.
//! * **Network expansion** — one road per `<Segment>`: endpoints `<Way>` plus
//!   `via` points, width lerped from the way widths, junction flare
//!   (`crossing-flare`, ×1.45) widening near ways with degree ≥ 3.
//!
//! Known deviations from VibeGame (documented, none affect simple-rpg):
//! station spacing is 1 m (vs 0.35 m); berms and cross-slope banking are not
//! implemented; bridge decks are flat ribbons (GLB decks arrive with glTF).

use std::collections::HashMap;

use bevy::math::Vec2;

use super::brush::{BrushGrid, BrushMode, BrushRequest, min_effective, smootherstep01};
use super::mesh::ChunkMeshData;
use super::paths::{chaikin_smooth, nearest_on_path, path_length, resample, station_lerp};
use super::water::WaterBody;

/// Road station spacing for the design profile (meters). VibeGame uses
/// 0.35 m; 1 m keeps native carving cheap with identical visual results at
/// terrain texel sizes ≥ 1 m.
pub const STATION_SPACING: f32 = 1.0;
/// Alternations of "pin pad plazas / re-limit the grade" while resolving a
/// road's design profile (see `carve_road`); converges well inside this.
const PAD_PIN_ITERATIONS: usize = 4;
/// Extra bed overhang beyond the ribbon (meters, VibeGame `ROADBED_OVERHANG`).
pub const ROADBED_OVERHANG: f32 = 2.0;
/// Adaptive falloff slope: `falloff = max(falloff, 1.875 · cutDepth)` gives a
/// ~45° cut slope (VibeGame `DEFAULT_CORRIDOR_MAX_CUT_SLOPE = 1.0`).
pub const ADAPTIVE_FALLOFF_FACTOR: f32 = 1.875;
/// Junction flare multiplier (VibeGame crossing flare ×1.45).
pub const CROSSING_FLARE: f32 = 1.45;
/// Lift of the road ribbon above the carved bed (meters) — avoids z-fighting.
/// Altura da ribbon sobre o terreno esculpido. 6 cm era sub-pixel a médias
/// distâncias (far plane do domo = 4000 m) e a ribbon brigava no depth buffer
/// com o terreno — listras verde/branco (terreno/estrada) nas artérias.
pub const RIBBON_LIFT: f32 = 0.2;

/// Cap on the miter scale at a corner (VibeGame `ROAD_MITER_LIMIT`). Sem o
/// limite um hairpin atirava a borda externa ao infinito; 3 mantém junções
/// de 90° quase quadradas.
pub const ROAD_MITER_LIMIT: f32 = 3.0;

/// Road profiles (VibeGame `road/profiles.ts`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum RoadProfile {
    /// Main streets: full flattening.
    #[default]
    Artery,
    /// Narrow trails: full flattening.
    Spur,
    /// Plazas: flattening without sink (flush with the pad).
    Plaza,
    /// Bridges: no corridor carve; flat deck ribbon only.
    Bridge,
}

impl RoadProfile {
    /// Parses a `profile="…"` / `default-profile="…"` attribute.
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "artery" => Some(Self::Artery),
            "spur" => Some(Self::Spur),
            "plaza" => Some(Self::Plaza),
            "bridge" => Some(Self::Bridge),
            _ => None,
        }
    }
}

/// Declarative road (`<Road path width flatten flatten-falloff …>`).
#[derive(Debug, Clone, PartialEq)]
pub struct RoadSpec {
    /// Optional display name.
    pub name: Option<String>,
    /// Centerline polyline in world XZ (`"x z x z …"`).
    pub path: Vec<Vec2>,
    /// Full ribbon width (meters).
    pub width: f32,
    pub profile: RoadProfile,
    /// `false` = decal-only trail (no terrain carve).
    pub flatten: bool,
    /// Falloff ring outside the bed (meters).
    pub flatten_falloff: f32,
    /// Profile smoothing window (meters).
    pub flatten_window: f32,
    /// Maximum design profile grade (rise/run).
    pub flatten_max_grade: f32,
    /// Extra flat shoulder around the bed (meters).
    pub flatten_shoulder: f32,
    /// Bed drop below the smoothed profile (meters).
    pub platform_sink: f32,
    /// Chaikin smoothing iterations on the authoring path.
    pub smoothing: u32,
    /// Loop road (`closed`).
    pub closed: bool,
    /// Ribbon texture path (world asset).
    pub texture: Option<String>,
    /// Meters of road length per texture tile.
    pub texture_scale: f32,
    /// Edge alpha fade (fraction of the half-width).
    pub edge_feather: f32,
    /// Junction flare annotations (network-internal, set by expansion).
    pub(crate) flare: Option<Flare>,
}

impl Default for RoadSpec {
    fn default() -> Self {
        Self {
            name: None,
            path: Vec::new(),
            width: 2.0,
            profile: RoadProfile::Artery,
            flatten: true,
            flatten_falloff: 8.0,
            flatten_window: 56.0,
            flatten_max_grade: 0.22,
            flatten_shoulder: 0.0,
            platform_sink: 0.12,
            smoothing: 2,
            closed: false,
            texture: None,
            texture_scale: 6.0,
            edge_feather: 1.0,
            flare: None,
        }
    }
}

/// Flare annotation (network-internal): widened near junction ways.
#[derive(Debug, Clone, PartialEq, Default)]
pub(crate) struct Flare {
    crossings: Vec<(Vec2, f32)>,
}

/// `<Way id xz [width]>` — a named network node.
#[derive(Debug, Clone, PartialEq)]
pub struct WaySpec {
    pub id: String,
    pub at: Vec2,
    pub width: Option<f32>,
}

/// `<Segment a b [via] [width] [profile]>` — one road between two ways.
#[derive(Debug, Clone, PartialEq)]
pub struct SegmentSpec {
    pub a: String,
    pub b: String,
    /// Intermediate points (`via="x z x z …"`).
    pub via: Vec<Vec2>,
    pub width: Option<f32>,
    pub profile: Option<RoadProfile>,
}

/// `<RoadNetwork …>` — ways + segments expanded into one road per segment.
#[derive(Debug, Clone, PartialEq)]
pub struct RoadNetworkSpec {
    pub name: Option<String>,
    pub default_profile: RoadProfile,
    pub default_width: f32,
    pub crossing_flare: bool,
    pub flatten: bool,
    pub flatten_falloff: f32,
    pub flatten_window: f32,
    pub flatten_max_grade: f32,
    pub texture: Option<String>,
    pub texture_scale: f32,
    pub ways: Vec<WaySpec>,
    pub segments: Vec<SegmentSpec>,
}

impl Default for RoadNetworkSpec {
    fn default() -> Self {
        Self {
            name: None,
            default_profile: RoadProfile::Artery,
            default_width: 4.0,
            crossing_flare: false,
            flatten: true,
            flatten_falloff: 8.0,
            flatten_window: 56.0,
            flatten_max_grade: 0.22,
            texture: None,
            texture_scale: 9.0,
            ways: Vec::new(),
            segments: Vec::new(),
        }
    }
}

impl RoadNetworkSpec {
    /// Way width override or the network default.
    fn way_width(&self, id: &str) -> Option<f32> {
        let way = self.ways.iter().find(|w| w.id == id)?;
        Some(way.width.unwrap_or(self.default_width))
    }

    /// Junction flare radius: ways referenced by 3+ segments get widened
    /// approaches.
    fn crossing_ways(&self) -> Vec<(Vec2, f32)> {
        let mut degree: Vec<(Vec2, usize)> = Vec::new();
        for seg in &self.segments {
            for id in [&seg.a, &seg.b] {
                if let Some(way) = self.ways.iter().find(|w| &w.id == id) {
                    match degree.iter_mut().find(|(at, _)| *at == way.at) {
                        Some((_, n)) => *n += 1,
                        None => degree.push((way.at, 1)),
                    }
                }
            }
        }
        degree
            .into_iter()
            .filter(|(_, n)| *n >= 3)
            .map(|(at, _)| (at, self.default_width * 2.0))
            .collect()
    }

    /// Expands the network into one [`RoadSpec`] per segment. Unknown way ids
    /// are skipped (parse-time validation already warns).
    pub fn expand(&self) -> Vec<RoadSpec> {
        let crossings = if self.crossing_flare {
            self.crossing_ways()
        } else {
            Vec::new()
        };
        let way_at = |id: &str| {
            self.ways
                .iter()
                .find(|w| w.id == id)
                .map(|w| w.at)
                .unwrap_or_default()
        };
        let resolved_profile = |seg: &SegmentSpec| seg.profile.unwrap_or(self.default_profile);
        let resolved_width = |seg: &SegmentSpec| -> Option<f32> {
            match (seg.width, self.way_width(&seg.a), self.way_width(&seg.b)) {
                (Some(w), _, _) => Some(w),
                (None, Some(wa), Some(wb)) => Some((wa + wb) * 0.5),
                _ => None,
            }
        };

        // Grau de cada way (nº de segmentos que a tocam) e adjacência.
        let mut degree: HashMap<&str, usize> = HashMap::new();
        let mut adjacency: HashMap<&str, Vec<usize>> = HashMap::new();
        for (i, seg) in self.segments.iter().enumerate() {
            *degree.entry(seg.a.as_str()).or_default() += 1;
            *degree.entry(seg.b.as_str()).or_default() += 1;
            adjacency.entry(seg.a.as_str()).or_default().push(i);
            adjacency.entry(seg.b.as_str()).or_default().push(i);
        }

        let mut used = vec![false; self.segments.len()];
        let mut out = Vec::with_capacity(self.segments.len());
        for (i, seg) in self.segments.iter().enumerate() {
            if used[i] {
                continue;
            }
            let (Some(_), Some(_)) = (self.way_width(&seg.a), self.way_width(&seg.b)) else {
                continue;
            };
            used[i] = true;
            let profile = resolved_profile(seg);
            let width = resolved_width(seg);
            let mut points: Vec<Vec2> = Vec::with_capacity(seg.via.len() + 2);
            points.push(way_at(&seg.a));
            points.extend_from_slice(&seg.via);
            points.push(way_at(&seg.b));
            let mut first_id = seg.a.clone();
            let mut last_id = seg.b.clone();

            // Funde cadeias através de ways de grau 2 (mesmo perfil/largura):
            // uma dobra que atravessa vários segmentos vira UM path e o
            // Chaikin do carve arredonda o canto — segmentos separados
            // deixavam junções de 90° duras no anel.
            if profile != RoadProfile::Bridge {
                let mut head = i;
                loop {
                    let last = self.segments[head].b.clone();
                    if degree.get(last.as_str()).copied().unwrap_or(0) != 2 {
                        break;
                    }
                    let next = adjacency
                        .get(last.as_str())
                        .and_then(|v| v.iter().copied().find(|&j| j != head && !used[j]));
                    let Some(j) = next else { break };
                    let s2 = &self.segments[j];
                    if resolved_profile(s2) != profile || resolved_width(s2) != width {
                        break;
                    }
                    let Some(at) = way_at_checked(&self.ways, &s2.b) else {
                        break;
                    };
                    used[j] = true;
                    points.extend_from_slice(&s2.via);
                    points.push(at);
                    last_id = s2.b.clone();
                    head = j;
                }
                let mut tail = i;
                loop {
                    let first = self.segments[tail].a.clone();
                    if degree.get(first.as_str()).copied().unwrap_or(0) != 2 {
                        break;
                    }
                    let next = adjacency
                        .get(first.as_str())
                        .and_then(|v| v.iter().copied().find(|&j| j != tail && !used[j]));
                    let Some(j) = next else { break };
                    let s2 = &self.segments[j];
                    if resolved_profile(s2) != profile || resolved_width(s2) != width {
                        break;
                    }
                    let Some(at) = way_at_checked(&self.ways, &s2.a) else {
                        break;
                    };
                    used[j] = true;
                    points.insert(0, at);
                    for (k, v) in s2.via.iter().rev().enumerate() {
                        points.insert(1 + k, *v);
                    }
                    first_id = s2.a.clone();
                    tail = j;
                }
            }

            // VibeGame extendPathEnds: uma ribbon que parava exactamente na
            // centreline da vizinha deixava uma cunha de chão nu no canto
            // externo da junção. Estende as pontas em ways partilhados
            // (grau ≥ 2) — as ribbons sobrepõem-se e o alpha feather funde.
            if profile != RoadProfile::Bridge {
                let ext = (width.unwrap_or(self.default_width) * 0.75).min(6.0);
                if degree.get(first_id.as_str()).copied().unwrap_or(0) >= 2 {
                    extend_path_end(&mut points, ext, false);
                }
                if degree.get(last_id.as_str()).copied().unwrap_or(0) >= 2 {
                    extend_path_end(&mut points, ext, true);
                }
            }

            out.push(
                RoadSpec {
                    name: Some(format!(
                        "{}/{}-{}",
                        self.name.as_deref().unwrap_or("net"),
                        first_id,
                        last_id
                    )),
                    path: points,
                    width: width.unwrap_or(self.default_width),
                    profile,
                    flatten: self.flatten && profile != RoadProfile::Bridge,
                    flatten_falloff: self.flatten_falloff,
                    flatten_window: self.flatten_window,
                    flatten_max_grade: self.flatten_max_grade,
                    flatten_shoulder: 0.0,
                    platform_sink: if profile == RoadProfile::Plaza {
                        0.0
                    } else {
                        0.12
                    },
                    smoothing: 2,
                    closed: false,
                    texture: self.texture.clone(),
                    texture_scale: self.texture_scale,
                    edge_feather: 1.0,
                    flare: None,
                }
                .with_flare(&crossings),
            );
        }
        out
    }
}

impl RoadSpec {
    /// Attaches junction flare annotations (network expansion only).
    fn with_flare(mut self, crossings: &[(Vec2, f32)]) -> Self {
        if !crossings.is_empty() {
            self.flare = Some(Flare {
                crossings: crossings.to_vec(),
            });
        }
        self
    }
}

/// Guards for the road carve: mutual exclusions against pads and water.
#[derive(Debug, Clone, Default)]
pub struct RoadGuards<'a> {
    /// Pad cores as `(center, half_extents, plane height)` — never carved by
    /// roads, and used to **anchor** the road grade so an approach ramps down
    /// onto the plaza instead of ending at a wall on its boundary.
    pub pad_cores: &'a [(Vec2, Vec2, f32)],
    /// Water bodies — carve zones are never filled back in.
    pub water: &'a [WaterBody],
}

impl<'a> RoadGuards<'a> {
    fn blocked(&self, p: Vec2) -> bool {
        self.pad_plane_at(p).is_some() || self.water.iter().any(|w| w.contains(p))
    }

    /// Resolved plane height of the pad core containing `p`, if any.
    fn pad_plane_at(&self, p: Vec2) -> Option<f32> {
        self.pad_cores
            .iter()
            .find(|(c, h, _)| {
                p.x >= c.x - h.x && p.x <= c.x + h.x && p.y >= c.y - h.y && p.y <= c.y + h.y
            })
            .map(|(_, _, plane)| *plane)
    }
}

/// Position of a way id, `None` when unknown (segments referencing ghosts
/// are skipped instead of collapsing onto the origin).
fn way_at_checked(ways: &[WaySpec], id: &str) -> Option<Vec2> {
    ways.iter().find(|w| w.id == id).map(|w| w.at)
}

/// Push one extrapolated point past the first (`at_end = false`) or last
/// (`at_end = true`) path point along the end tangent (VibeGame
/// `extendPathEnds`).
fn extend_path_end(points: &mut Vec<Vec2>, amount: f32, at_end: bool) {
    let n = points.len();
    if points.len() < 2 || amount <= 0.0 {
        return;
    }
    let (tip, prev) = if at_end {
        (points[n - 1], points[n - 2])
    } else {
        (points[0], points[1])
    };
    let d = tip - prev;
    let len = d.length();
    if len < 1e-4 {
        return;
    }
    let p = tip + d / len * amount;
    if at_end {
        points.push(p);
    } else {
        points.insert(0, p);
    }
}

/// Registry entry for one carved road (queries + ribbon generation).
#[derive(Debug, Clone, PartialEq)]
pub struct RoadPath {
    pub name: Option<String>,
    /// Smoothed stations (world XZ).
    pub stations: Vec<Vec2>,
    /// Half width per station (meters, flare applied).
    pub half_width: Vec<f32>,
    pub profile: RoadProfile,
    /// Bridge roads: no carve, flat deck at `deck_y`.
    pub bridge: bool,
    pub deck_y: Option<f32>,
}

impl RoadPath {
    /// Signed "distance onto the road": ≤ 0 when on the ribbon, else meters
    /// to the nearest edge (VibeGame `distanceToRoadAt`).
    pub fn distance_to_road(&self, p: Vec2) -> f32 {
        let Some(hit) = nearest_on_path(&self.stations, p) else {
            return f32::INFINITY;
        };
        // Interpolated: a per-segment half-width makes the ribbon edge (and
        // every `is_on_road` query against it) step at each station.
        let hw = station_lerp(&self.half_width, &hit);
        hit.point.distance(p) - hw
    }

    /// Point is on the road ribbon (VibeGame `isPointOnRoad`).
    pub fn is_on_road(&self, p: Vec2) -> bool {
        self.distance_to_road(p) <= 0.0
    }
}

/// Carves one road corridor and returns its registry path. Returns `None`
/// for degenerate paths.
pub fn carve_road(
    grid: &mut BrushGrid,
    spec: &RoadSpec,
    index: usize,
    guards: &RoadGuards,
) -> Option<RoadPath> {
    if spec.path.len() < 2 || spec.width <= 0.0 {
        return None;
    }
    let texel = grid.texel();
    let smoothed = chaikin_smooth(&spec.path, spec.smoothing, spec.closed);
    let stations = resample(&smoothed, STATION_SPACING.max(texel * 0.5));
    if stations.len() < 2 {
        return None;
    }
    let half_width = spec.width * 0.5;
    let bed_half = min_effective(half_width + ROADBED_OVERHANG, texel);

    // Flare profile: widen near crossing ways (network junctions).
    let flare_at = |p: Vec2| -> f32 {
        match &spec.flare {
            Some(flare) => flare
                .crossings
                .iter()
                .map(|(at, radius)| {
                    let d = p.distance(*at);
                    CROSSING_FLARE
                        + (1.0 - CROSSING_FLARE) * smootherstep01((d / radius.max(1e-3)).min(1.0))
                })
                .fold(f32::INFINITY, f32::min)
                .max(1.0),
            None => 1.0,
        }
    };

    // Bridge: no carve; flat deck at the higher end (GLB decks come later).
    if spec.profile == RoadProfile::Bridge || !spec.flatten {
        let y0 = grid.sample(stations[0].x, stations[0].y);
        let y1 = grid.sample(
            stations[stations.len() - 1].x,
            stations[stations.len() - 1].y,
        );
        return Some(RoadPath {
            name: spec.name.clone(),
            half_width: (0..stations.len())
                .map(|i| half_width * flare_at(stations[i]))
                .collect(),
            stations,
            profile: spec.profile,
            bridge: spec.profile == RoadProfile::Bridge,
            deck_y: (spec.profile == RoadProfile::Bridge).then(|| y0.max(y1)),
        });
    }

    let sink = spec.platform_sink;

    // 1. Survey the natural profile, then smooth it (window, 3 box passes).
    let mut design: Vec<f32> = stations.iter().map(|p| grid.sample(p.x, p.y)).collect();
    let window = (spec.flatten_window / STATION_SPACING.max(1e-3)).round();
    let half_window = (window as usize).max(1);
    for _ in 0..3 {
        box_smooth(&mut design, half_window);
    }
    // 2. Pin the pad plazas, then limit the grade.
    //
    // The survey window is wide (`flatten_window`, smoothed three times), so a
    // plaza's flat plane is averaged away and the design drifts back onto the
    // surrounding hillside. Roads do not carve pad cores, so that drift used
    // to surface as a sheer wall on the pad boundary — 18 m around the demo
    // world's plaza. Pinning the stations that sit on a pad to its plane and
    // re-running the grade limit makes the approach ramp down to meet it; the
    // limit can pull a pinned station, so pin and limit alternate to a fixed
    // point.
    let pins: Vec<Option<f32>> = stations
        .iter()
        .map(|p| guards.pad_plane_at(*p).map(|plane| plane + sink))
        .collect();
    for _ in 0..PAD_PIN_ITERATIONS {
        for (d, pin) in design.iter_mut().zip(&pins) {
            if let Some(plane) = pin {
                *d = *plane;
            }
        }
        limit_grade(&mut design, spec.flatten_max_grade, STATION_SPACING);
    }
    for (d, pin) in design.iter_mut().zip(&pins) {
        if let Some(plane) = pin {
            *d = *plane;
        }
    }

    // 3. Adaptive falloff per station: deep cuts get wide slopes.
    let falloff_base = min_effective(spec.flatten_falloff, texel);
    let falloff: Vec<f32> = design
        .iter()
        .zip(stations.iter())
        .map(|(&d, p)| {
            let natural = grid.sample(p.x, p.y);
            let cut = (natural - d).max(0.0);
            falloff_base.max(ADAPTIVE_FALLOFF_FACTOR * cut)
        })
        .collect();

    let shoulder = spec.flatten_shoulder;
    let extent = bed_half * CROSSING_FLARE + falloff_base + texel * 2.0;

    let owner = format!("road:{index}");
    grid.begin_stroke(&owner);
    let stations_ref = &stations;
    let mut weight = |p: Vec2| {
        if guards.blocked(p) {
            return 0.0;
        }
        let Some(hit) = nearest_on_path(stations_ref, p) else {
            return 0.0;
        };
        let d = hit.point.distance(p);
        // Both the bed half-width and the falloff are evaluated at the
        // projected point / interpolated station: sampling them per segment
        // steps the corridor width and terraces its slope.
        let hw = bed_half * flare_at(hit.point);
        let inner = hw + shoulder;
        let fall = station_lerp(&falloff, &hit);
        let outer = inner + fall;
        if d > outer {
            return 0.0;
        }
        if d <= inner {
            return 1.0;
        }
        1.0 - smootherstep01((d - inner) / (outer - inner).max(1e-3))
    };
    let mut target = |p: Vec2| match nearest_on_path(stations_ref, p) {
        Some(hit) => station_lerp(&design, &hit) - sink,
        None => -sink,
    };
    // No guard clamp. It only ever visits texels the falloff left unweighted,
    // i.e. the ring just outside `inner + fall`, and there it pulled the
    // hillside all the way down to the road bed — a ~18 m drop beside a deep
    // cut in the demo world. A guard cannot remove a discontinuity, it moves
    // it one texel outward; the adaptive falloff above is what actually grades
    // the transition. Same trap as the pad and river carves.
    let (min_x, min_z, max_x, max_z) = stations.iter().fold(
        (
            f32::INFINITY,
            f32::INFINITY,
            f32::NEG_INFINITY,
            f32::NEG_INFINITY,
        ),
        |(x0, z0, x1, z1), p| (x0.min(p.x), z0.min(p.y), x1.max(p.x), z1.max(p.y)),
    );
    grid.apply(BrushRequest {
        mode: BrushMode::Blend,
        min_x: min_x - extent,
        min_z: min_z - extent,
        max_x: max_x + extent,
        max_z: max_z + extent,
        target: &mut target,
        weight: &mut weight,
    });
    grid.commit_stroke();

    Some(RoadPath {
        name: spec.name.clone(),
        half_width: (0..stations.len())
            .map(|i| half_width * flare_at(stations[i]))
            .collect(),
        stations,
        profile: spec.profile,
        bridge: false,
        deck_y: None,
    })
}

/// Forward+backward grade clamp: `|ys[i+1] − ys[i]| ≤ max_grade · ds`.
fn limit_grade(ys: &mut [f32], max_grade: f32, ds: f32) {
    if ys.len() < 2 || max_grade <= 0.0 {
        return;
    }
    let step = max_grade * ds.max(1e-3);
    for i in 0..ys.len() - 1 {
        ys[i + 1] = ys[i + 1].clamp(ys[i] - step, ys[i] + step);
    }
    for i in (0..ys.len() - 1).rev() {
        ys[i] = ys[i].clamp(ys[i + 1] - step, ys[i + 1] + step);
    }
}

/// In-place moving average over a window of `±half` entries (edges clamp).
fn box_smooth(values: &mut [f32], half: usize) {
    if half == 0 || values.len() < 3 {
        return;
    }
    let n = values.len();
    let smoothed: Vec<f32> = (0..n)
        .map(|i| {
            let a = i.saturating_sub(half);
            let b = (i + half + 1).min(n);
            values[a..b].iter().sum::<f32>() / (b - a) as f32
        })
        .collect();
    values.copy_from_slice(&smoothed);
}

/// Builds the road ribbon draped on the carved terrain (world-space
/// positions, edge alpha feather, `v` = arc length / texture scale).
/// Bridge roads render as a flat deck at `deck_y`.
pub fn road_ribbon_mesh(grid: &BrushGrid, path: &RoadPath, spec: &RoadSpec) -> ChunkMeshData {
    let mut mesh = ChunkMeshData::default();
    let n = path.stations.len();
    if n < 2 {
        return mesh;
    }
    let feather = spec.edge_feather.max(0.0); // metros (VibeGame edgeFeather)
    let scale = if spec.texture_scale > 0.0 {
        spec.texture_scale
    } else {
        1.0
    };
    let mut arc = 0.0;
    // Four vertices per station (bordaEsq, núcleoEsq, núcleoDir, bordaDir,
    // alpha [0,1,1,0] — VibeGame makeRoadGeometry): o núcleo opaco tem
    // `feather` metros de folga de cada lado e só a borda faz fade. Com a
    // secção antiga de 3 vértices e feather interpretado como FRAÇÃO da
    // meia-largura, o núcleo opaco encolhia até à linha central e a estrada
    // ficava quase invisível.
    let feather_eff = feather.max(0.001);
    for (i, st) in path.stations.iter().enumerate() {
        if i > 0 {
            arc += st.distance(path.stations[i - 1]);
        }
        // Bisector normal + miter scale (VibeGame road geometry): offsetar
        // por `hw` ao longo da média das normais dos segmentos vizinhos
        // estreita a ribbon para `hw·cos(θ/2)` medido perpendicular a cada
        // segmento — os cantos apertam e a estrada "quebra" onde curva.
        // Dividir por cos(θ/2) (com cap) restaura largura constante.
        let seg_normal = |d: Vec2| Vec2::new(-d.y, d.x);
        let in_n = if i > 0 {
            seg_normal((*st - path.stations[i - 1]).normalize_or_zero())
        } else {
            Vec2::ZERO
        };
        let out_n = if i + 1 < n {
            seg_normal((path.stations[i + 1] - *st).normalize_or_zero())
        } else {
            Vec2::ZERO
        };
        let (perp, seg_n) = if in_n == Vec2::ZERO {
            (out_n, out_n)
        } else if out_n == Vec2::ZERO {
            (in_n, in_n)
        } else {
            let bisector = in_n + out_n;
            if bisector.length_squared() > 1e-8 {
                (bisector.normalize(), in_n)
            } else {
                (in_n, in_n) // reversão a 180°: usa a normal de entrada
            }
        };
        let cos_half = perp.dot(seg_n).abs();
        let miter = if cos_half > 1e-3 {
            (1.0 / cos_half).min(ROAD_MITER_LIMIT)
        } else {
            1.0
        };
        let hw = path.half_width[i];
        let outer_l = -hw;
        let outer_r = hw;
        // Núcleo opaco a `feather` metros da borda (clamp para não cruzar em
        // estradas estreitas — VibeGame coreL/coreR).
        let core_l = (outer_l + feather_eff).min(-0.02);
        let core_r = (outer_r - feather_eff).max(0.02);

        let laterals = [outer_l, core_l, core_r, outer_r];
        let alphas = [0.0, 1.0, 1.0, 0.0];
        for (k, lat) in laterals.iter().enumerate() {
            let p = *st + perp * (*lat * miter);
            let y = if path.bridge {
                path.deck_y.unwrap_or(0.0) + RIBBON_LIFT
            } else {
                grid.sample(p.x, p.y) + RIBBON_LIFT
            };
            mesh.positions.push([p.x, y, p.y]);
            // Bridge decks read as flat; ground ribbons follow the carve.
            mesh.normals.push(if path.bridge {
                [0.0, 1.0, 0.0]
            } else {
                grid.sample_normal(p.x, p.y, grid.texel()).to_array()
            });
            // Tile across the ribbon in world space, like the length already
            // does. A normalized `u` stretches exactly one texture tile over
            // the full width, so a narrow trail looked anisotropic and a wide
            // plaza came out as flat colour with no stones at all.
            mesh.uvs.push([lat / scale, arc / scale]);
            mesh.colors.push([1.0, 1.0, 1.0, alphas[k]]);
        }
    }
    for i in 0..(n - 1) {
        // Vertices: 4 per station (ol, cl, cr, or). Three quads per pair
        // (VibeGame). Winding CCW visto de CIMA — (l0, c0, l1) dá normal +Y;
        // a ordem (l0, l1, c0) invertia o normal (−Y) e o FrontSide cull
        // escondia a ribbon inteira vista de cima.
        let a = (i * 4) as u32;
        let b = ((i + 1) * 4) as u32;
        for k in 0..3u32 {
            mesh.indices
                .extend_from_slice(&[a + k, a + k + 1, b + k, a + k + 1, b + k + 1, b + k]);
        }
    }
    mesh
}

/// Total centerline length (meters) — used by tests and tooling.
pub fn road_length(path: &RoadPath) -> f32 {
    path_length(&path.stations)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::terrain::water::{LakeSpec, RiverSpec, carve_lake, carve_river};

    /// 128x128 grid, 128 m world, a central hill along X.
    fn test_grid() -> BrushGrid {
        let mut grid =
            BrushGrid::new(vec![0; 128 * 128], 128, 128, 128.0, 50.0, 0.0).expect("grid");
        grid.begin_stroke("base");
        for z in 0..128 {
            for x in 0..128 {
                let p = grid.cell_center(x, z);
                let h = 6.0 + 14.0 * (-(p.y * p.y) / 400.0).exp() + 2.0 * (p.x * 0.03).sin();
                grid.set_cell_height(x, z, h);
            }
        }
        grid.commit_stroke();
        grid
    }

    fn road_spec() -> RoadSpec {
        RoadSpec {
            name: Some("test-road".into()),
            path: vec![Vec2::new(5.0, 32.0), Vec2::new(122.0, 32.0)],
            width: 4.0,
            ..RoadSpec::default()
        }
    }

    #[test]
    fn test_road_defaults_match_vibegame() {
        let road = RoadSpec::default();
        assert_eq!(road.width, 2.0);
        assert_eq!(road.flatten_falloff, 8.0);
        assert_eq!(road.flatten_window, 56.0);
        assert!((road.flatten_max_grade - 0.22).abs() < 1e-6);
        assert!((road.platform_sink - 0.12).abs() < 1e-6);
        assert!(road.flatten, "roads flatten by default");
        let net = RoadNetworkSpec::default();
        assert_eq!(net.default_width, 4.0);
        assert!(matches!(net.default_profile, RoadProfile::Artery));
    }

    #[test]
    fn test_profile_parse() {
        assert!(matches!(
            RoadProfile::parse("artery"),
            Some(RoadProfile::Artery)
        ));
        assert!(matches!(
            RoadProfile::parse("Plaza"),
            Some(RoadProfile::Plaza)
        ));
        assert!(matches!(
            RoadProfile::parse("bridge"),
            Some(RoadProfile::Bridge)
        ));
        assert!(matches!(
            RoadProfile::parse("spur"),
            Some(RoadProfile::Spur)
        ));
        assert!(RoadProfile::parse("highway").is_none());
    }

    #[test]
    fn test_network_expansion() {
        let net = RoadNetworkSpec {
            name: Some("paths".into()),
            default_width: 4.0,
            ways: vec![
                WaySpec {
                    id: "plaza".into(),
                    at: Vec2::ZERO,
                    width: Some(4.8),
                },
                WaySpec {
                    id: "north".into(),
                    at: Vec2::new(0.0, 60.0),
                    width: None,
                },
                WaySpec {
                    id: "east".into(),
                    at: Vec2::new(60.0, 0.0),
                    width: None,
                },
            ],
            segments: vec![
                SegmentSpec {
                    a: "plaza".into(),
                    b: "north".into(),
                    via: vec![Vec2::new(0.0, 30.0)],
                    width: None,
                    profile: Some(RoadProfile::Bridge),
                },
                SegmentSpec {
                    a: "plaza".into(),
                    b: "east".into(),
                    via: Vec::new(),
                    width: Some(6.0),
                    profile: None,
                },
                SegmentSpec {
                    a: "plaza".into(),
                    b: "ghost".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                },
            ],
            ..RoadNetworkSpec::default()
        };
        let roads = net.expand();
        assert_eq!(roads.len(), 2, "unknown way ids are skipped");
        let bridge = &roads[0];
        assert!(matches!(bridge.profile, RoadProfile::Bridge));
        assert!(!bridge.flatten, "bridge segments never carve");
        assert_eq!(
            bridge.path,
            vec![Vec2::ZERO, Vec2::new(0.0, 30.0), Vec2::new(0.0, 60.0)]
        );
        assert!(
            (bridge.width - (4.8 + 4.0) * 0.5).abs() < 1e-5,
            "width lerp"
        );
        let artery = &roads[1];
        assert!((artery.width - 6.0).abs() < 1e-5, "explicit width wins");
        assert!(artery.flatten, "artery carves");
        assert!(
            artery
                .name
                .as_deref()
                .is_some_and(|n| n.contains("plaza-east")),
            "segment names carry the way ids: {:?}",
            artery.name
        );
    }

    #[test]
    fn test_network_merges_chain_through_degree2_way() {
        // Anel: mid → ring → mid com o way do canto a grau 2 — funde num
        // ÚNICO path A→B→C para o Chaikin arredondar a dobra (junções de 90°
        // duras eram o bug visual).
        let net = RoadNetworkSpec {
            default_width: 4.0,
            ways: vec![
                WaySpec {
                    id: "a".into(),
                    at: Vec2::new(0.0, 20.0),
                    width: None,
                },
                WaySpec {
                    id: "b".into(),
                    at: Vec2::new(20.0, 20.0),
                    width: None,
                },
                WaySpec {
                    id: "c".into(),
                    at: Vec2::new(20.0, 0.0),
                    width: None,
                },
            ],
            segments: vec![
                SegmentSpec {
                    a: "a".into(),
                    b: "b".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                },
                SegmentSpec {
                    a: "b".into(),
                    b: "c".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                },
            ],
            ..RoadNetworkSpec::default()
        };
        let roads = net.expand();
        assert_eq!(roads.len(), 1, "degree-2 chain collapses into one path");
        assert_eq!(
            roads[0].path,
            vec![
                Vec2::new(0.0, 20.0),
                Vec2::new(20.0, 20.0),
                Vec2::new(20.0, 0.0)
            ]
        );
        // Pontas em ways de grau 1 não estendem.
        assert_eq!(roads[0].path.len(), 3);
    }

    #[test]
    fn test_network_extends_ends_at_shared_junction() {
        // T: B tem grau 3 — cada ribbon estende para lá do B (VibeGame
        // extendPathEnds) para não deixar cunha de chão nu no canto externo.
        let net = RoadNetworkSpec {
            default_width: 4.0,
            ways: vec![
                WaySpec {
                    id: "a".into(),
                    at: Vec2::new(0.0, 0.0),
                    width: None,
                },
                WaySpec {
                    id: "b".into(),
                    at: Vec2::new(10.0, 0.0),
                    width: None,
                },
                WaySpec {
                    id: "c".into(),
                    at: Vec2::new(20.0, 0.0),
                    width: None,
                },
                WaySpec {
                    id: "d".into(),
                    at: Vec2::new(10.0, 10.0),
                    width: None,
                },
            ],
            segments: vec![
                SegmentSpec {
                    a: "a".into(),
                    b: "b".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                },
                SegmentSpec {
                    a: "c".into(),
                    b: "b".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                },
                SegmentSpec {
                    a: "d".into(),
                    b: "b".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                },
            ],
            ..RoadNetworkSpec::default()
        };
        let roads = net.expand();
        assert_eq!(roads.len(), 3, "T junction keeps 3 ribbons");
        // Ribbon a→b: estende para ALÉM de b (grau 3), ~width·0.75 = 3 m.
        let ab = roads
            .iter()
            .find(|r| r.name.as_deref().is_some_and(|n| n.contains("a-b")))
            .unwrap();
        let last = *ab.path.last().unwrap();
        assert!(
            last.x > 12.5 && last.x < 13.5 && last.y.abs() < 1e-4,
            "extended past the junction: {last:?}"
        );
        // Ponta em a (grau 1) intacta.
        assert_eq!(ab.path.first().unwrap().x, 0.0);
    }

    #[test]
    fn test_network_crossing_flare_widens_junctions() {
        let net = RoadNetworkSpec {
            crossing_flare: true,
            default_width: 4.0,
            ways: vec![
                WaySpec {
                    id: "c".into(),
                    at: Vec2::ZERO,
                    width: None,
                },
                WaySpec {
                    id: "n".into(),
                    at: Vec2::new(0.0, 40.0),
                    width: None,
                },
                WaySpec {
                    id: "s".into(),
                    at: Vec2::new(0.0, -40.0),
                    width: None,
                },
                WaySpec {
                    id: "e".into(),
                    at: Vec2::new(40.0, 0.0),
                    width: None,
                },
                WaySpec {
                    id: "w".into(),
                    at: Vec2::new(-40.0, 0.0),
                    width: None,
                },
            ],
            segments: vec![
                SegmentSpec {
                    a: "n".into(),
                    b: "s".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                },
                SegmentSpec {
                    a: "e".into(),
                    b: "w".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                },
            ],
            ..RoadNetworkSpec::default()
        };
        // The crossing way "c" is referenced by 0 segments directly — flare
        // keys on way degree, so "c" is not a crossing; both segment ends have
        // degree 1 and stay unflared. Build a real crossing instead:
        let net = RoadNetworkSpec {
            segments: vec![
                SegmentSpec {
                    a: "c".into(),
                    b: "n".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                },
                SegmentSpec {
                    a: "c".into(),
                    b: "s".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                },
                SegmentSpec {
                    a: "c".into(),
                    b: "e".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                },
            ],
            ..net
        };
        let roads = net.expand();
        assert_eq!(roads.len(), 3);
        // Flare shows up in the carved registry path's half widths.
        let mut grid = test_grid();
        let n_road =
            carve_road(&mut grid, &roads[0], 0, &RoadGuards::default()).expect("carved segment");
        assert!(
            n_road.half_width[0] > n_road.half_width[n_road.half_width.len() - 1],
            "junction end flared: {} > {}",
            n_road.half_width[0],
            n_road.half_width[n_road.half_width.len() - 1]
        );
    }

    #[test]
    fn test_carve_road_cuts_a_grade_limited_bed() {
        let mut grid = test_grid();
        let spec = road_spec();
        let guards = RoadGuards::default();
        let path = carve_road(&mut grid, &spec, 0, &guards).expect("road");
        assert!(!path.bridge);
        // On the bed: flat-ish; on the hill above: cut down to the road.
        let bed = grid.sample(64.0, 32.0);
        let hill = grid.sample(64.0, 24.0); // ~8 m north: still falloff zone
        assert!(
            hill < bed + 1.5,
            "the hill shoulder is cut toward the bed: {hill} vs {bed}"
        );
        // The road profile respects max grade: sample along the centerline.
        let mut grades = Vec::new();
        for w in path.stations.windows(2) {
            let a = grid.sample(w[0].x, w[0].y);
            let b = grid.sample(w[1].x, w[1].y);
            grades.push((b - a).abs() / w[0].distance(w[1]).max(1e-3));
        }
        // Carve + quantization noise; the design bound is 0.22, allow slack.
        let max_grade = grades.iter().cloned().fold(0.0_f32, f32::max);
        assert!(max_grade < 0.6, "grade-limited bed: {max_grade}");
        // Registry queries.
        assert!(path.is_on_road(Vec2::new(64.0, 32.0)));
        assert!(!path.is_on_road(Vec2::new(64.0, 48.0)));
        assert!(path.distance_to_road(Vec2::new(64.0, 38.0)) > 0.0);
    }

    #[test]
    fn test_road_skips_pad_cores() {
        let mut grid = test_grid();
        // Flatten a pad where the road will pass.
        grid.flatten_rect(
            Vec2::new(64.0, 32.0),
            Vec2::splat(20.0),
            6.0,
            3.0,
            None,
            "pad:0",
        );
        let pad_height = grid.sample(64.0, 32.0);
        let spec = road_spec();
        let guards = RoadGuards {
            pad_cores: &[(Vec2::new(64.0, 32.0), Vec2::splat(10.0), 0.0)],
            water: &[],
        };
        let _ = carve_road(&mut grid, &spec, 0, &guards).expect("road");
        let after = grid.sample(64.0, 32.0);
        assert!(
            (after - pad_height).abs() < 0.05,
            "pad core stays flat: {after} vs {pad_height}"
        );
    }

    #[test]
    fn test_road_skips_water_zones() {
        let mut grid = test_grid();
        let lake = carve_lake(
            &mut grid,
            &LakeSpec {
                at: Vec2::new(64.0, 32.0),
                radius: 14.0,
                depth: 3.0,
                ..LakeSpec::default()
            },
            0,
        )
        .expect("lake");
        let floor = grid.sample(64.0, 32.0);
        let spec = road_spec();
        let guards = RoadGuards {
            pad_cores: &[],
            water: std::slice::from_ref(&lake),
        };
        let _ = carve_road(&mut grid, &spec, 0, &guards).expect("road");
        let after = grid.sample(64.0, 32.0);
        assert!(
            after <= floor + 0.05,
            "the lake is never filled back in: {after} vs {floor}"
        );
        let river = carve_river(
            &mut grid,
            &RiverSpec {
                path: vec![Vec2::new(64.0, -30.0), Vec2::new(64.0, 60.0)],
                ..RiverSpec::default()
            },
            1,
        )
        .expect("river");
        let channel = grid.sample(64.0, 32.0);
        let guards = RoadGuards {
            pad_cores: &[],
            water: &[river],
        };
        let _ = carve_road(&mut grid, &spec, 0, &guards).expect("road");
        let after = grid.sample(64.0, 32.0);
        assert!(
            after <= channel + 0.05,
            "the river channel survives the road: {after} vs {channel}"
        );
    }

    #[test]
    fn test_bridge_road_reports_deck_height() {
        let mut grid = test_grid();
        let spec = RoadSpec {
            profile: RoadProfile::Bridge,
            flatten: false,
            ..road_spec()
        };
        let guards = RoadGuards::default();
        let path = carve_road(&mut grid, &spec, 0, &guards).expect("bridge");
        assert!(path.bridge);
        assert!(path.deck_y.is_some());
        let before = grid.sample(64.0, 32.0);
        // No carve happened (grid unchanged under the deck).
        let spec2 = road_spec(); // flatten=true for comparison
        let _ = carve_road(&mut grid, &spec2, 1, &guards).expect("road");
        let carved = grid.sample(64.0, 32.0);
        assert!(carved < before, "the flatten road cut; the bridge did not");
    }

    #[test]
    fn test_decal_road_skips_carve() {
        let mut grid = test_grid();
        let before = grid.raw().to_vec();
        let spec = RoadSpec {
            flatten: false,
            ..road_spec()
        };
        let guards = RoadGuards::default();
        let path = carve_road(&mut grid, &spec, 0, &guards).expect("decal road");
        assert!(!path.bridge, "decals are not bridges");
        assert_eq!(grid.raw(), before, "decal roads never touch the grid");
    }

    #[test]
    fn test_road_ribbon_winding_faces_up() {
        // Estrada +X: o 1º triângulo (l0, c0, l1) tem de dar normal +Y —
        // winding invertido escondia a ribbon inteira atrás do FrontSide
        // cull (visível só de baixo).
        let grid = test_grid();
        let path = RoadPath {
            name: None,
            profile: RoadProfile::Artery,
            bridge: false,
            deck_y: None,
            stations: vec![Vec2::new(30.0, 64.0), Vec2::new(40.0, 64.0)],
            half_width: vec![2.0, 2.0],
        };
        let spec = road_spec();
        let mesh = road_ribbon_mesh(&grid, &path, &spec);
        // Usa os ÍNDICES reais do primeiro triângulo (o que a GPU vê).
        let v = |i: u32| bevy::math::Vec3::from_array(mesh.positions[i as usize]);
        let (a, b, c) = (v(mesh.indices[0]), v(mesh.indices[1]), v(mesh.indices[2]));
        let normal = (b - a).cross(c - a);
        assert!(normal.y > 0.0, "ribbon winding must face up: {normal}");
    }

    #[test]
    fn test_road_ribbon_corner_keeps_constant_width() {
        // L de 90°: sem miter, a largura perpendicular no canto aperta para
        // hw·cos(45°) ≈ 1.41 m; com miter (= 1/cos45 ≈ 1.414) cada borda
        // fica a hw da linha de centro de AMBOS os segmentos.
        let grid = test_grid();
        let path = RoadPath {
            name: None,
            profile: RoadProfile::Artery,
            bridge: false,
            deck_y: None,
            stations: vec![
                Vec2::new(20.0, 64.0),
                Vec2::new(50.0, 64.0),
                Vec2::new(64.0, 64.0),
                Vec2::new(64.0, 78.0),
                Vec2::new(64.0, 108.0),
            ],
            half_width: vec![2.0; 5],
        };
        let spec = road_spec();
        let mesh = road_ribbon_mesh(&grid, &path, &spec);
        // Estação do canto (índice 2): 4 vértices (ol, cl, cr, or).
        let corner = 2 * 4;
        let left = bevy::math::Vec3::from_array(mesh.positions[corner]);
        let right = bevy::math::Vec3::from_array(mesh.positions[corner + 3]);
        // Borda esquerda do canto: afastada `hw` da linha z=64 (segmento de
        // entrada) e `hw` da linha x=64 (segmento de saída). Vec3 = (x, y
        // altura, z) — o plano XZ é x/z.
        assert!(
            ((left.x - 64.0).abs() - 2.0).abs() < 0.1,
            "left edge respects out-segment width: {left:?}"
        );
        assert!(
            ((left.z - 64.0).abs() - 2.0).abs() < 0.1,
            "left edge respects in-segment width: {left:?}"
        );
        // Sem miter esta distância seria ~1.41 (pinch de 45°).
        assert!(
            ((right.x - 64.0).abs() - 2.0).abs() < 0.1,
            "right edge respects out-segment width: {right:?}"
        );
        assert!(
            ((right.z - 64.0).abs() - 2.0).abs() < 0.1,
            "right edge respects in-segment width: {right:?}"
        );
        // A largura total no canto excede a nominal (offsets esticados pelo
        // miter cobrem a dobra externa, sem gap).
        assert!(
            left.distance(right) > 4.0,
            "miter widens the outer fold: {}",
            left.distance(right)
        );
    }

    #[test]
    fn test_road_ribbon_mesh_drapes_the_bed() {
        let mut grid = test_grid();
        let spec = road_spec();
        let guards = RoadGuards::default();
        let path = carve_road(&mut grid, &spec, 0, &guards).expect("road");
        let mesh = road_ribbon_mesh(&grid, &path, &spec);
        assert_eq!(mesh.positions.len(), path.stations.len() * 4, "ol/cl/cr/or");
        assert_eq!(mesh.indices.len(), (path.stations.len() - 1) * 18);
        // Ribbon sits just above the bed at the centerline.
        let first = &mesh.positions[0];
        let bed = grid.sample(first[0], first[2]);
        assert!(
            first[1] - bed >= 0.0 && first[1] - bed < 0.2,
            "ribbon drapes the carve: {} vs {bed}",
            first[1]
        );
        // v coordinate follows arc length / texture scale.
        let last_uv = &mesh.uvs[mesh.uvs.len() - 1];
        let total = road_length(&path);
        assert!(
            (last_uv[1] - total / spec.texture_scale).abs() < 0.5,
            "v is arc length: {} vs {}",
            last_uv[1],
            total / spec.texture_scale
        );
        // Edge alpha feather; the center line stays opaque.
        assert!(mesh.colors[0][3] < 1.0, "edges feather");
        assert!((mesh.colors[1][3] - 1.0).abs() < 1e-4, "center opaque");
    }

    #[test]
    fn test_degenerate_road_is_none() {
        let mut grid = test_grid();
        let spec = RoadSpec {
            path: vec![Vec2::ZERO],
            ..road_spec()
        };
        assert!(carve_road(&mut grid, &spec, 0, &RoadGuards::default()).is_none());
        let spec = RoadSpec {
            width: 0.0,
            ..road_spec()
        };
        assert!(carve_road(&mut grid, &spec, 0, &RoadGuards::default()).is_none());
    }

    #[test]
    fn test_limit_grade_clamps_steep_profiles() {
        let mut ys = vec![0.0, 5.0, 10.0, 15.0];
        limit_grade(&mut ys, 0.5, 1.0);
        for w in ys.windows(2) {
            assert!(
                (w[1] - w[0]).abs() <= 0.5 + 1e-4,
                "grade limited: {} -> {}",
                w[0],
                w[1]
            );
        }
        // The start is kept (the forward pass clamps the climb, the backward
        // pass does not invent new height at the head).
        assert!((ys[0] - 0.0).abs() < 1e-4, "head kept: {:?}", ys);
        assert!(ys[3] < 5.0, "tail pulled down by the clamp: {:?}", ys);
    }
}
