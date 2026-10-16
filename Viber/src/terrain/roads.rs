//! Roads — corridor carving, `<RoadNetwork>` expansion (Way/Segment), road
//! ribbons and the gameplay query registry.
//!
//! Ported from the original road plugin (`road/carve.ts`, `road/network.ts`,
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
//! Known deviations from the reference (documented): station spacing is 1 m
//! (vs 0.35 m); berms and cross-slope banking are not implemented.
//!
//! **Racing extensions** (simple-racer port, 2026-10-15): `<Road>` accepts
//! authored per-node channels — `y="…"` (driving-surface elevations) and
//! `widths="…"` — that ride the same chaikin+resample as the geometry, plus
//! `viaduct-clearance="6"`: spans floating ≥ clearance above the ground skip
//! the carve (the valley stays) and render a suspended deck with pylons.
//! Closed roads close their ring explicitly, so the start/finish seam is
//! queryable ([`RoadPath::progress`]/[`RoadPath::surface_y_at`]) and meshed.
//! `TerrainRuntime::road_surface` is the vehicle-facing query.

use std::collections::HashMap;

use bevy::math::Vec2;

use super::brush::{BrushGrid, BrushMode, BrushRequest, min_effective, smootherstep01};
use super::mesh::ChunkMeshData;
use super::paths::{
    PathHit, chaikin_smooth_channels, nearest_on_path, path_length, resample_channels, station_lerp,
};
use super::water::WaterBody;

/// Road station spacing for the design profile (meters). The original uses
/// 0.35 m; 1 m keeps native carving cheap with identical visual results at
/// terrain texel sizes ≥ 1 m.
pub const STATION_SPACING: f32 = 1.0;
/// Alternations of "pin pad plazas / re-limit the grade" while resolving a
/// road's design profile (see `carve_road`); converges well inside this.
const PAD_PIN_ITERATIONS: usize = 4;
/// Extra bed overhang beyond the ribbon (meters, `ROADBED_OVERHANG`).
pub const ROADBED_OVERHANG: f32 = 2.0;
/// Adaptive falloff slope: `falloff = max(falloff, 1.875 · cutDepth)` gives a
/// ~45° cut slope (`DEFAULT_CORRIDOR_MAX_CUT_SLOPE = 1.0`).
pub const ADAPTIVE_FALLOFF_FACTOR: f32 = 1.875;
/// Junction flare multiplier (crossing flare ×1.45).
pub const CROSSING_FLARE: f32 = 1.45;
/// Lift of the road ribbon above the carved bed (meters) — avoids z-fighting.
/// Altura da ribbon sobre o terreno esculpido. 6 cm era sub-pixel a médias
/// distâncias (far plane do domo = 4000 m) e a ribbon brigava no depth buffer
/// com o terreno — listras verde/branco (terreno/estrada) nas artérias.
/// Ribbon: sub-elevação sobre o leito — o drape segue o terreno e as
/// bordas descem em *skirt*, escondendo o gap nas encostas (loop de polish:
/// antes era 0.2 fixo, que fazia a ribbon flutuar sobre declives).
pub const RIBBON_LIFT: f32 = 0.06;
/// Profundidade das saias laterais — rasa de propósito: saia alta aparece
/// como borda escura vista de ângulo raso e perfura ribbons cruzadas. Hoje as
/// saias vão a **alpha 0** em qualquer caso (desenhavam um risco na berma) —
/// a constante só continua a dar-lhes o offset em Y. Ver `road_ribbon_mesh`.
pub const RIBBON_SKIRT_DEPTH: f32 = 0.12;
/// Lift of a junction fusion disc. Deliberately **above** [`RIBBON_LIFT`]:
/// the disc's whole job is to hide the seam where several ribbon tips
/// overlap, so it has to win the depth test against them rather than
/// z-fight. Ground decals sit below both ([`super::decal::DECAL_LIFT`]), so
/// the three transparent layers stack in a fixed order.
pub const JUNCTION_LIFT: f32 = 0.10;
/// Rim wobble of a junction disc (fraction of the radius) — a perfect circle
/// of cobble on grass reads as a stamped decal, not as a worn crossing.
pub const JUNCTION_NOISE: f32 = 0.13;
/// Clearance kept between the widest ribbon reaching a junction and the disc
/// rim (meters).
pub const JUNCTION_MARGIN: f32 = 0.6;
/// Passo máximo entre estações refinadas (m) — subdivisão para o noise.
pub const RIBBON_SUBDIV: f32 = 2.5;
/// Amplitude do noise lateral orgânico (m).
pub const RIBBON_NOISE_AMP: f32 = 0.28;
/// Wobble da largura (fração da meia-largura).
pub const RIBBON_WOBBLE: f32 = 0.08;

/// Cap on the miter scale at a corner (`ROAD_MITER_LIMIT`). Sem o
/// limite um hairpin atirava a borda externa ao infinito; 3 mantém junções
/// de 90° quase quadradas.
pub const ROAD_MITER_LIMIT: f32 = 3.0;

/// Road profiles (`road/profiles.ts`).
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
    /// Authored driving-surface elevation per path node (`y="…"`), meters.
    /// `None` keeps the classic behaviour: survey the natural terrain
    /// profile. Authored roads grade the terrain to the given profile
    /// (grade-limited) instead of following the ground.
    pub elevations: Option<Vec<f32>>,
    /// Authored ribbon width per path node (`widths="…"`), meters. `None` =
    /// `width` everywhere.
    pub widths: Option<Vec<f32>>,
    /// Viaduct threshold (m, `viaduct-clearance`): spans where the
    /// authored elevation floats ≥ this far above the natural ground stop the
    /// corridor carve — the valley below stays — and the ribbon renders as a
    /// suspended deck with pylons. `0.0` disables. One number drives both the
    /// carve skip and the deck: set them apart and you get either a graded
    /// scar under a bridge or a span with nothing holding it up.
    pub viaduct_clearance: f32,
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
            elevations: None,
            widths: None,
            viaduct_clearance: 0.0,
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
#[derive(Debug, Clone, PartialEq, Default)]
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

    /// Fusion-disc points: ways touched by ≥3 segments (T/cruz/praça).
    ///
    /// The radius has to cover exactly what the ribbons actually draw at the
    /// junction and no more: the widest half-width touching the way, times
    /// the crossing flare, times the width wobble, plus a small clearance.
    /// The old formula floored the radius at `default_width × 2` — 8 m for a
    /// 4 m network, so a 4 m road grew a **16 m** disc of cobble. That is the
    /// large circle that appeared inside the north gate when the junctions
    /// were introduced; it is more than twice the geometry it was meant to
    /// cover.
    pub fn junction_points(&self) -> Vec<RoadJunction> {
        let mut degree: HashMap<&str, usize> = HashMap::new();
        let mut max_width: HashMap<&str, f32> = HashMap::new();
        for seg in &self.segments {
            for id in [&seg.a, &seg.b] {
                *degree.entry(id.as_str()).or_default() += 1;
                let w = seg
                    .width
                    .unwrap_or_else(|| self.way_width(id).unwrap_or(self.default_width));
                let slot = max_width.entry(id.as_str()).or_insert(0.0);
                *slot = (*slot).max(w);
            }
        }
        self.ways
            .iter()
            .filter_map(|w| {
                let d = degree.get(w.id.as_str()).copied().unwrap_or(0);
                if d < 3 {
                    return None;
                }
                let max_w = max_width
                    .get(w.id.as_str())
                    .copied()
                    .unwrap_or(self.default_width);
                let flare = if self.crossing_flare {
                    CROSSING_FLARE
                } else {
                    1.0
                };
                Some(RoadJunction {
                    at: w.at,
                    radius: max_w * 0.5 * flare * (1.0 + RIBBON_WOBBLE) + JUNCTION_MARGIN,
                    // Wide enough to dissolve into the grass; 1.2 m ended the
                    // cobble on a visible ring.
                    feather: 2.2,
                    texture: self.texture.clone(),
                    texture_scale: self.texture_scale,
                })
            })
            .collect()
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
                // O extremo distante acompanha-se EXPLICITAMENTE (head_end/
                // tail_end): re-derivar de `segments[head].b` partia a cadeia
                // após um merge reverso — o extremo ficava no lado já
                // atravessado e o próximo segmento grau-2 nunca fundia.
                let mut head_end = seg.b.clone();
                let mut head = i;
                loop {
                    let last = head_end.clone();
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
                    // Orientação: o próximo segmento pode SAIR do way
                    // (s2.a == last, caso natural) ou CHEGAR a ele
                    // (s2.b == last, autoria head-to-head — antes o ramo
                    // era consumido ao contrário e a estrada perdida).
                    if s2.a == last {
                        let Some(at) = way_at_checked(&self.ways, &s2.b) else {
                            break;
                        };
                        used[j] = true;
                        points.extend_from_slice(&s2.via);
                        points.push(at);
                        last_id = s2.b.clone();
                        head_end = s2.b.clone();
                        head = j;
                    } else if s2.b == last {
                        let Some(at) = way_at_checked(&self.ways, &s2.a) else {
                            break;
                        };
                        used[j] = true;
                        points.extend(s2.via.iter().rev().copied());
                        points.push(at);
                        last_id = s2.a.clone();
                        head_end = s2.a.clone();
                        head = j;
                    } else {
                        break;
                    }
                }
                let mut tail_end = seg.a.clone();
                let mut tail = i;
                loop {
                    let first = tail_end.clone();
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
                    if s2.b == first {
                        // Natural: s2.a → via → first. Prepend [way_a, via…]
                        // — as via inserem-se em 1 (não 1+k) para preservar
                        // a ordem autoral (1+k invertia ≥2 vias).
                        let Some(at) = way_at_checked(&self.ways, &s2.a) else {
                            break;
                        };
                        used[j] = true;
                        points.insert(0, at);
                        for v in s2.via.iter().rev() {
                            points.insert(1, *v);
                        }
                        first_id = s2.a.clone();
                        tail_end = s2.a.clone();
                        tail = j;
                    } else if s2.a == first {
                        // Reverso: s2.b → via.rev → first.
                        let Some(at) = way_at_checked(&self.ways, &s2.b) else {
                            break;
                        };
                        used[j] = true;
                        points.insert(0, at);
                        for v in s2.via.iter() {
                            points.insert(1, *v);
                        }
                        first_id = s2.b.clone();
                        tail_end = s2.b.clone();
                        tail = j;
                    } else {
                        break;
                    }
                }
            }

            // extendPathEnds: uma ribbon que parava exactamente na
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
                    // Networks são 2D hoje (vias urbanas) — sem canais
                    // autorados nem viadutos.
                    elevations: None,
                    widths: None,
                    viaduct_clearance: 0.0,
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
/// (`at_end = true`) path point along the end tangent (`extendPathEnds`).
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

/// Fusion disc at a multi-arm junction (`junctions.ts`): um círculo
/// opaco de cobble centrado na junção cobre as emendas das ribbons que se
/// cruzam — sem ele, pontas com alpha feather sobrepostas deixam costuras
/// escuras e cunhas no cruzamento.
#[derive(Debug, Clone, PartialEq)]
pub struct RoadJunction {
    /// World XZ center (the shared way position).
    pub at: Vec2,
    /// Opaque core radius (m) — cobre a maior meia-largura + folga.
    pub radius: f32,
    /// Outer alpha fade beyond `radius` (m).
    pub feather: f32,
    pub texture: Option<String>,
    pub texture_scale: f32,
}

/// Surface info for racing queries ([`TerrainRuntime::road_surface`]).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RoadSurface {
    /// Driving surface Y at the query point: the deck height on suspended
    /// spans, the (carved) terrain heightfield elsewhere.
    pub y: f32,
    /// The point sits on a suspended deck span (viaduct/bridge).
    pub deck: bool,
    /// Index into the roads registry.
    pub road: usize,
    /// Arc position as a fractional station index — lap/progress signal.
    pub s: f32,
    /// Half width of the road at the query point (logical wall distance).
    pub half_width: f32,
    /// Signed lateral offset from the centerline (m, positivo à esquerda
    /// da tangente) — rampas/pads de corrida assentam por aqui.
    pub lateral: f32,
}

/// Registry entry for one carved road (queries + ribbon generation).
#[derive(Debug, Clone, PartialEq)]
pub struct RoadPath {
    pub name: Option<String>,
    /// Smoothed stations (world XZ). Closed roads end with an explicit copy
    /// of the first station so the seam segment is queryable and meshed.
    pub stations: Vec<Vec2>,
    /// Half width per station (meters, flare applied).
    pub half_width: Vec<f32>,
    pub profile: RoadProfile,
    /// Driving/deck surface elevation per station (meters): the authored,
    /// grade-limited profile for authored roads; the carved design profile
    /// otherwise; the flat deck for `bridge`.
    pub surface: Vec<f32>,
    /// Station sits on a suspended deck span (viaduct): no carve below, the
    /// ribbon renders at [`RoadPath::surface`] and pylons reach the ground.
    pub deck: Vec<bool>,
    /// Bridge roads: no carve, flat deck at `deck_y`.
    pub bridge: bool,
    pub deck_y: Option<f32>,
}

impl RoadPath {
    /// Signed "distance onto the road": ≤ 0 when on the ribbon, else meters
    /// to the nearest edge (`distanceToRoadAt`).
    pub fn distance_to_road(&self, p: Vec2) -> f32 {
        let Some(hit) = nearest_on_path(&self.stations, p) else {
            return f32::INFINITY;
        };
        // Interpolated: a per-segment half-width makes the ribbon edge (and
        // every `is_on_road` query against it) step at each station.
        let hw = station_lerp(&self.half_width, &hit);
        hit.point.distance(p) - hw
    }

    /// Point is on the road ribbon (`isPointOnRoad`).
    pub fn is_on_road(&self, p: Vec2) -> bool {
        self.distance_to_road(p) <= 0.0
    }

    pub(crate) fn deck_weight(&self, hit: &PathHit) -> f32 {
        if self.bridge {
            return 1.0;
        }
        if self.deck.len() != self.stations.len() {
            return 0.0;
        }
        // station_lerp sem alocar: flags 0/1 interpoladas no hit.
        let n = self.deck.len();
        let s = hit.station(n);
        let i = (s.floor() as usize).min(n - 1);
        let j = (i + 1).min(n - 1);
        let frac = s - i as f32;
        let a = if self.deck[i] { 1.0 } else { 0.0 };
        let b = if self.deck[j] { 1.0 } else { 0.0 };
        a + (b - a) * frac
    }

    /// The point sits on a suspended deck span (viaduct or bridge road).
    pub fn is_deck_at(&self, p: Vec2) -> bool {
        let Some(hit) = nearest_on_path(&self.stations, p) else {
            return false;
        };
        self.distance_to_road(p) <= 0.0 && self.deck_weight(&hit) > 0.5
    }

    /// Driving surface elevation for a point **on the road**: deck spans
    /// return their authored deck height; ground roads return `None` — the
    /// caller samples the (carved) terrain heightfield instead.
    pub fn surface_y_at(&self, p: Vec2) -> Option<f32> {
        if self.distance_to_road(p) > 0.0 {
            return None;
        }
        let hit = nearest_on_path(&self.stations, p)?;
        if self.deck_weight(&hit) > 0.5 {
            Some(station_lerp(&self.surface, &hit))
        } else {
            None
        }
    }

    /// Arc position along the road as a fractional station index — the
    /// progress signal lap counting and racing AI build on.
    pub fn progress(&self, p: Vec2) -> Option<f32> {
        nearest_on_path(&self.stations, p).map(|hit| hit.station(self.stations.len()))
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
    // Spacing REAL das estações. `limit_grade` e a janela de suavização têm de
    // usar o MESMO passo: assumir 1 m com heightmaps grossos (texel > 2 m)
    // convertia flatten-max-grade em grade/16 e a janela de 56 m em ~900 m —
    // estradas sobre-planadas.
    let spacing = STATION_SPACING.max(texel * 0.5);

    // Canais autorados (elevação/largura por nó) viajam COM a geometria pelo
    // chaikin + resample — projectar os valores depois sobre a polilinha
    // acabada saltaria entre os dois braços de um loop que se cruza a si
    // próprio (a pista do racer passa POR BAIXO do próprio viaduto).
    let mut elev_ch: Vec<f32> = spec.elevations.clone().unwrap_or_default();
    let mut width_ch: Vec<f32> = spec.widths.clone().unwrap_or_default();
    let mut smoothed = chaikin_smooth_channels(
        &spec.path,
        &mut [&mut elev_ch, &mut width_ch],
        spec.smoothing,
        spec.closed,
    );
    // Pistas fechadas: fechar o anel EXPLICITAMENTE (primeiro ponto no fim).
    // O `resample`/`nearest_on_path` só andam para a frente — sem o fecho, a
    // reta da meta não existia para queries nem para a mesh.
    if spec.closed && !smoothed.is_empty() {
        smoothed.push(smoothed[0]);
        if elev_ch.len() == smoothed.len() - 1 {
            elev_ch.push(elev_ch[0]);
        }
        if width_ch.len() == smoothed.len() - 1 {
            width_ch.push(width_ch[0]);
        }
    }
    let mut stations = resample_channels(&smoothed, &mut [&mut elev_ch, &mut width_ch], spacing);
    // E fechar TAMBÉM as estações: o pin do fim é condicional (≤ 0,25·espaço
    // do primeiro ponto) — sem isto a ribbon ficava com um vão até 25 cm na
    // costura da meta.
    if spec.closed && !stations.is_empty() {
        stations.push(stations[0]);
        if elev_ch.len() == stations.len() - 1 {
            elev_ch.push(elev_ch[0]);
        }
        if width_ch.len() == stations.len() - 1 {
            width_ch.push(width_ch[0]);
        }
    }
    if stations.len() < 2 {
        return None;
    }
    let authored_elev =
        (spec.elevations.is_some() && elev_ch.len() == stations.len()).then(|| elev_ch.clone());
    let per_node_width = spec.widths.is_some() && width_ch.len() == stations.len();

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

    // Half width per station (authored widths ride the channel, uniform
    // `width` otherwise), flare included — the bed is derived from it.
    let half_widths: Vec<f32> = (0..stations.len())
        .map(|i| {
            let base = if per_node_width {
                width_ch[i] * 0.5
            } else {
                spec.width * 0.5
            };
            base.max(0.05) * flare_at(stations[i])
        })
        .collect();
    let bed_stations: Vec<f32> = half_widths.iter().map(|h| h + ROADBED_OVERHANG).collect();

    // Bridge: no carve; flat deck at the higher end (GLB decks come later).
    if spec.profile == RoadProfile::Bridge || !spec.flatten {
        let y0 = grid.sample(stations[0].x, stations[0].y);
        let y1 = grid.sample(
            stations[stations.len() - 1].x,
            stations[stations.len() - 1].y,
        );
        let deck_y = (spec.profile == RoadProfile::Bridge).then(|| y0.max(y1));
        let surface: Vec<f32> = match (&authored_elev, deck_y) {
            (Some(e), _) => e.clone(),
            (None, Some(y)) => vec![y; stations.len()],
            (None, None) => natural_profile(grid, &stations),
        };
        let deck = vec![spec.profile == RoadProfile::Bridge; stations.len()];
        return Some(RoadPath {
            name: spec.name.clone(),
            half_width: half_widths,
            stations,
            profile: spec.profile,
            surface,
            deck,
            bridge: spec.profile == RoadProfile::Bridge,
            deck_y,
        });
    }

    let sink = spec.platform_sink;
    let natural = natural_profile(grid, &stations);

    // Spans de viaduto: estações autoradas ≥ `viaduct-clearance` acima do
    // solo natural. Sem carve aí — o vale fica intacto (a floresta cresce por
    // baixo) — e a ribbon desenha o tabuleiro à cota autoriada com pilares.
    // Um número só governa os dois lados (carve skip + deck), como no
    // original: afasta-os e tens ou uma cicatriz gradada debaixo de ponte ou
    // um vão sem nada a segurá-lo.
    let deck: Vec<bool> = match (&authored_elev, spec.viaduct_clearance > 0.0) {
        (Some(e), true) => e
            .iter()
            .zip(&natural)
            .map(|(&d, &n)| d - n >= spec.viaduct_clearance)
            .collect(),
        _ => vec![false; stations.len()],
    };

    // 1. Design profile: authored (hand-tuned, no window smoothing — only the
    //    grade limit) or the classic natural survey + 3 box passes.
    let mut design: Vec<f32> = match &authored_elev {
        Some(e) => e.clone(),
        None => {
            let mut d = natural.clone();
            let window = (spec.flatten_window / spacing).round();
            let half_window = (window as usize).max(1);
            for _ in 0..3 {
                box_smooth(&mut d, half_window);
            }
            d
        }
    };
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
        limit_grade(&mut design, spec.flatten_max_grade, spacing);
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
    // O AABB tem de cobrir o falloff ADAPTATIVO máximo: o peso corta até
    // `inner + fall` com `fall = max(falloff, 1.875·cut)` por estação — usar
    // só `falloff_base` (o mínimo) truncava o ramp no limite do AABB e
    // fabricava um degrau/falésia ao lado do leito em cortes fundos. Mesmo
    // padrão do `feather_max` do carve de rios (water.rs).
    let falloff_max = falloff.iter().copied().fold(falloff_base, f32::max);
    let bed_max = bed_stations.iter().copied().fold(0.0_f32, f32::max);
    let extent = bed_max * CROSSING_FLARE + shoulder + falloff_max + texel * 2.0;

    let owner = format!("road:{index}");
    grid.begin_stroke(&owner);
    let stations_ref = &stations;
    let deck_f: Vec<f32> = deck.iter().map(|&d| if d { 1.0 } else { 0.0 }).collect();
    let mut weight = |p: Vec2| {
        if guards.blocked(p) {
            return 0.0;
        }
        let Some(hit) = nearest_on_path(stations_ref, p) else {
            return 0.0;
        };
        // Vão suspenso: o carve não toca no vale (peso 0).
        if station_lerp(&deck_f, &hit) > 0.5 {
            return 0.0;
        }
        let d = hit.point.distance(p);
        // Both the bed half-width and the falloff are evaluated at the
        // projected point / interpolated station: sampling them per segment
        // steps the corridor width and terraces its slope.
        let hw = station_lerp(&bed_stations, &hit);
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
        half_width: half_widths,
        stations,
        profile: spec.profile,
        surface: design,
        deck,
        bridge: false,
        deck_y: None,
    })
}

/// Natural terrain height sampled at every station.
fn natural_profile(grid: &BrushGrid, stations: &[Vec2]) -> Vec<f32> {
    stations.iter().map(|p| grid.sample(p.x, p.y)).collect()
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

/// Value noise 1D determinístico (hash de inteiro + smoothstep): oscilação
/// orgânica reproduzível sem crate de RNG.
fn organic_noise(seed: u32, t: f32) -> f32 {
    fn hash(i: u32) -> f32 {
        let mut x = i.wrapping_mul(0x9E37_79B9);
        x ^= x >> 15;
        x = x.wrapping_mul(0x85EB_CA6B);
        x ^= x >> 13;
        (x & 0xFFFF) as f32 / 65535.0 * 2.0 - 1.0
    }
    let smooth = |f: f32| f * f * (3.0 - 2.0 * f);
    let t = t.max(0.0);
    let i = t.floor() as u32;
    let f = smooth(t - t.floor());
    hash(seed.wrapping_add(i)) * (1.0 - f) + hash(seed.wrapping_add(i + 1)) * f
}

/// Refina o caminho para o toque orgânico do polish: subdivisão em passos
/// curtos, suavização dos cantos (média dos vizinhos) e noise lateral de
/// baixa frequência + wobble da largura — determinístico por estrada
/// (seed derivado da primeira estação). Transporta os canais de superfície
/// (cota do tabuleiro) e deck em lockstep com os pontos refinados; o noise
/// só desloca XZ, a cota não mexe.
fn refine_organic(path: &RoadPath) -> (Vec<Vec2>, Vec<f32>, Vec<f32>, Vec<bool>) {
    let has_surface = path.surface.len() == path.stations.len();
    let has_deck = path.deck.len() == path.stations.len();
    // 1) subdivisão em passos curtos
    let mut pts: Vec<Vec2> = Vec::new();
    let mut widths: Vec<f32> = Vec::new();
    let mut surface: Vec<f32> = Vec::new();
    let mut deck: Vec<bool> = Vec::new();
    for (i, st) in path.stations.iter().enumerate() {
        if i > 0 {
            let prev = path.stations[i - 1];
            let d = st.distance(prev);
            let steps = (d / RIBBON_SUBDIV).ceil().max(1.0) as usize;
            for k in 1..=steps {
                let t = k as f32 / steps as f32;
                pts.push(prev.lerp(*st, t));
                widths.push(
                    path.half_width[i - 1] + (path.half_width[i] - path.half_width[i - 1]) * t,
                );
                if has_surface {
                    surface.push(path.surface[i - 1] + (path.surface[i] - path.surface[i - 1]) * t);
                }
                if has_deck {
                    let a = path.deck[i - 1];
                    let b = path.deck[i];
                    deck.push(if a == b {
                        a
                    } else {
                        let fa = if a { 1.0 } else { 0.0 };
                        let fb = if b { 1.0 } else { 0.0 };
                        fa + (fb - fa) * t > 0.5
                    });
                }
            }
        } else {
            pts.push(*st);
            widths.push(path.half_width[0]);
            if has_surface {
                surface.push(path.surface[0]);
            }
            if has_deck {
                deck.push(path.deck[0]);
            }
        }
    }
    // 2) suavização dos cantos (2 passes de média dos vizinhos, endpoints fixos)
    for _ in 0..2 {
        let mut smoothed = pts.clone();
        for i in 1..pts.len() - 1 {
            smoothed[i] = (pts[i - 1] + pts[i] * 2.0 + pts[i + 1]) / 4.0;
        }
        pts = smoothed;
    }
    // 3) noise lateral + wobble da largura (seed = primeira estação)
    let seed = (pts[0].x.to_bits() ^ pts[0].y.to_bits()) & 0xFFFF;
    let dirs: Vec<Vec2> = pts
        .windows(2)
        .map(|w| (w[1] - w[0]).normalize_or_zero())
        .chain(std::iter::once(Vec2::ONE))
        .collect();
    for (i, p) in pts.iter_mut().enumerate() {
        let arc_t = i as f32 / 2.0; // ~2 m por passo
        let offset = organic_noise(seed, arc_t * 0.35) * RIBBON_NOISE_AMP;
        let dir = dirs.get(i).copied().unwrap_or(Vec2::ONE);
        let perp = Vec2::new(-dir.y, dir.x);
        *p += perp * offset;
        widths[i] *= 1.0 + organic_noise(seed.wrapping_add(0x5111), arc_t * 0.25) * RIBBON_WOBBLE;
    }
    if !has_surface {
        surface.clear();
    }
    if !has_deck {
        deck.clear();
    }
    (pts, widths, surface, deck)
}

/// Pilar de tabuleiro (viaduto): espaçamento ao longo do vão (m) —
/// `pylon-spacing` = 30 m.
pub const DECK_PYLON_SPACING: f32 = 30.0;
/// Meia secção do pilar (m) — quadrado de 1,6 m.
pub const DECK_PYLON_HALF: f32 = 0.8;
/// Vãos com menos folga que esta ficam sem pilar — um aterro lê-se melhor do
/// que tocos de coluna.
pub const DECK_PYLON_MIN_CLEARANCE: f32 = 2.0;
/// Enterramento da base do pilar sob o solo (m).
pub const DECK_PYLON_BURIAL: f32 = 1.0;
/// Pilares não nascem a menos distância disto da borda de OUTRA estrada (m) —
/// a passagem inferior da pista tem de ficar limpa.
pub const DECK_PYLON_GUARD: f32 = 3.0;
/// Recuo do topo do pilar sob o tabuleiro (m).
pub const DECK_PYLON_TUCK: f32 = 0.35;

/// Builds the road ribbon draped on the carved terrain (world-space
/// positions, edge alpha feather, UVs em metros/scale — world-space, a
/// textura fica fixa no mundo). Spans de viaduto (`path.deck`) e pontes
/// (`profile="bridge"`) desenham o tabuleiro à cota de [`RoadPath::surface`]
/// com pilares até ao solo — `others` (as restantes estradas do mundo) só
/// serve para os pilares não nascerem dentro do corredor de uma passagem
/// inferior.
/// Loop de polish (2026-09-01): caminho refinado orgânico (subdivisão +
/// suavização de cantos + noise lateral/wobble determinísticos) e saias
/// laterais que descem até o leito — a estrada funde com o terreno em vez
/// de flutuar a 0.2 m fixo.
pub fn road_ribbon_mesh(
    grid: &BrushGrid,
    path: &RoadPath,
    spec: &RoadSpec,
    others: &[RoadPath],
    self_index: usize,
) -> ChunkMeshData {
    let mut mesh = ChunkMeshData::default();
    let n = path.stations.len();
    if n < 2 {
        return mesh;
    }
    let (stations, half_widths, surfaces, decks) = refine_organic(path);
    let n = stations.len();
    let has_surface = !surfaces.is_empty();
    let feather = spec.edge_feather.max(0.0); // metros (edgeFeather)
    let scale = if spec.texture_scale > 0.0 {
        spec.texture_scale
    } else {
        1.0
    };
    let feather_eff = feather.max(0.001);
    // Per-station feather wobble. A constant feather draws the alpha ramp as
    // two lines exactly parallel to the centreline — the eye reads that as a
    // painted stripe even after the centreline itself was made organic. Let
    // the fade width breathe (±45%) and the edge dissolves irregularly into
    // the grass. Deterministic: same seed family as `refine_organic`.
    let feather_seed =
        (stations[0].x.to_bits() ^ stations[0].y.to_bits()).wrapping_add(0x2f13) & 0xFFFF;
    // Seis vértices por estação: [skirtL, edgeL, coreL, coreR, edgeR,
    // skirtR]. O deck (edge/core) mantém o feather da referência; as saias
    // descem RIBBON_SKIRT_DEPTH a partir das bordas e ficam a alpha 0 (ver
    // `alphas` abaixo) — o que desenha é só o deck.
    for (i, st) in stations.iter().enumerate() {
        let seg_normal = |d: Vec2| Vec2::new(-d.y, d.x);
        let in_n = if i > 0 {
            seg_normal((*st - stations[i - 1]).normalize_or_zero())
        } else {
            Vec2::ZERO
        };
        let out_n = if i + 1 < n {
            seg_normal((stations[i + 1] - *st).normalize_or_zero())
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
                (in_n, in_n)
            }
        };
        let cos_half = perp.dot(seg_n).abs();
        let miter = if cos_half > 1e-3 {
            (1.0 / cos_half).min(ROAD_MITER_LIMIT)
        } else {
            1.0
        };
        let hw = half_widths[i].max(0.05);
        let outer_l = -hw;
        let outer_r = hw;
        // Independent wobble per side, so the two edges do not fade in
        // lockstep (that just moves the stripe, it does not break it).
        let arc_t = i as f32 / 2.0;
        let fl = feather_eff * (1.0 + 0.45 * organic_noise(feather_seed, arc_t * 0.3));
        let fr = feather_eff
            * (1.0 + 0.45 * organic_noise(feather_seed.wrapping_add(0x77), arc_t * 0.3));
        let core_l = (outer_l + fl.clamp(0.05, hw * 0.9)).min(-0.02);
        let core_r = (outer_r - fr.clamp(0.05, hw * 0.9)).max(0.02);

        let laterals = [outer_l, outer_l, core_l, core_r, outer_r, outer_r];
        // As saias laterais NÃO se desenham (alpha 0). Não há corte para
        // tapar: a borda externa do deck já desvanece a alpha 0, e a
        // cortina era pior que o problema — partilha a lateral do anel
        // externo, partilha a UV do vértice de cima (parede vertical com
        // coordenada constante = textura esticada num só texel) e era OPACA
        // em baixo, exactamente onde o deck já é transparente. Como o deck
        // vai `RIBBON_LIFT` acima do chão, ~6 cm dessa cortina ficava SEMPRE
        // acima da superfície: em ângulo raso desenhava um risco contínuo ao
        // longo da berma (bug reportado). No tabuleiro de ponte a saia nunca
        // chegou a existir — o ramo `deck aqui em baixo` ignora `lift`, por
        // isso os dois anéis coincidem em Y e o quad é degenerado.
        let alphas = [0.0, 0.0, 1.0, 1.0, 0.0, 0.0];
        // Vão suspenso: a cota vem do perfil autoriado (tabuleiro), não do
        // terreno lá em baixo.
        let on_deck = path.bridge || (has_surface && decks.get(i).copied().unwrap_or(false));
        let deck_y = if has_surface {
            surfaces[i]
        } else {
            path.deck_y.unwrap_or(0.0)
        };
        for (k, lat) in laterals.iter().enumerate() {
            let p = *st + perp * (*lat * miter);
            let lift = if k == 0 || k == 5 {
                RIBBON_SKIRT_DEPTH
            } else {
                0.0
            };
            let y = if on_deck {
                deck_y + RIBBON_LIFT
            } else {
                grid.sample(p.x, p.y) + RIBBON_LIFT - lift
            };
            mesh.positions.push([p.x, y, p.y]);
            mesh.normals.push(if on_deck {
                [0.0, 1.0, 0.0]
            } else {
                grid.sample_normal(p.x, p.y, grid.texel()).to_array()
            });
            // UV world-space (posição / scale): a textura fica FIXA no
            // mundo — a praça (decal Plane) partilha a mesma fórmula e o
            // mesmo divisor, por isso o cobblestone bate na junção e nas
            // margens.
            mesh.uvs.push([p.x / scale, p.y / scale]);
            mesh.colors.push([1.0, 1.0, 1.0, alphas[k]]);
        }
    }
    // Deck quads: 3 por segmento (edge/core) usando o stride de 6; depois os
    // quads de saia (skirtL↔edgeL e edgeR↔skirtR), opacos de lado.
    let stride = 6_usize;
    for i in 0..(n - 1) {
        let a = (i * stride) as u32;
        let b = ((i + 1) * stride) as u32;
        for k in 1..4_u32 {
            mesh.indices
                .extend_from_slice(&[a + k, a + k + 1, b + k, a + k + 1, b + k + 1, b + k]);
        }
        // saia esquerda: verts 0 (skirt) ↔ 1 (edge)
        mesh.indices
            .extend_from_slice(&[a, b, b + 1, a, b + 1, a + 1]);
        // saia direita: verts 4 (edge) ↔ 5 (skirt)
        mesh.indices
            .extend_from_slice(&[a + 4, b + 4, b + 5, a + 4, b + 5, a + 5]);
    }

    // Pilares dos vãos suspensos: colunas quadradas (4 lados, topo enfiado
    // sob o tabuleiro, base enterrada) a cada `DECK_PYLON_SPACING` de arco.
    // Determinístico — percorre as estações refinadas acumulando o arco.
    if has_surface {
        let mut since_pylon = f32::INFINITY;
        for i in 0..n {
            if i > 0 {
                since_pylon += stations[i].distance(stations[i - 1]);
            }
            let on_deck = path.bridge || decks.get(i).copied().unwrap_or(false);
            if !on_deck {
                since_pylon = 0.0;
                continue;
            }
            if since_pylon < DECK_PYLON_SPACING {
                continue;
            }
            since_pylon = 0.0;
            let center = stations[i];
            let top_y = surfaces[i] - DECK_PYLON_TUCK;
            let ground_y = grid.sample(center.x, center.y);
            if top_y - ground_y < DECK_PYLON_MIN_CLEARANCE {
                continue;
            }
            // Passagem inferior: não nascer dentro do corredor de outra estrada.
            if others
                .iter()
                .enumerate()
                .any(|(oi, o)| oi != self_index && o.distance_to_road(center) < DECK_PYLON_GUARD)
            {
                continue;
            }
            emit_deck_pylon(
                &mut mesh,
                center,
                top_y,
                ground_y - DECK_PYLON_BURIAL,
                scale,
            );
        }
    }
    mesh
}

/// Anexa uma coluna quadrada (4 lados, um quad por lado) ao mesh. Winding e
/// normais para FORA: para o lado a→b, `(b_a − t_a) × (b_b − t_a)` dá a
/// normal horizontal exterior qualquer que seja o lado — por isso um único
/// padrão de índices serve os 4 lados.
fn emit_deck_pylon(mesh: &mut ChunkMeshData, center: Vec2, top_y: f32, bottom_y: f32, scale: f32) {
    let h = DECK_PYLON_HALF;
    // Cantos em ordem (XZ): os 4 lados fecham o anel.
    let corners = [
        Vec2::new(center.x + h, center.y + h),
        Vec2::new(center.x + h, center.y - h),
        Vec2::new(center.x - h, center.y - h),
        Vec2::new(center.x - h, center.y + h),
    ];
    for i in 0..4 {
        let a = corners[i];
        let b = corners[(i + 1) % 4];
        // Exterior do lado a→b: perpendicular horizontal de (b−a).
        let d = b - a;
        let n = Vec2::new(-d.y, d.x).normalize_or_zero();
        let base = mesh.positions.len() as u32;
        for (p, y) in [(a, top_y), (a, bottom_y), (b, bottom_y), (b, top_y)] {
            mesh.positions.push([p.x, y, p.y]);
            mesh.normals.push([n.x, 0.0, n.y]);
            mesh.uvs.push([p.x / scale, y / scale]);
            mesh.colors.push([1.0, 1.0, 1.0, 1.0]);
        }
        let (t_a, b_a, b_b, t_b) = (base, base + 1, base + 2, base + 3);
        mesh.indices
            .extend_from_slice(&[t_a, b_a, b_b, t_a, b_b, t_b]);
    }
}

/// Fusion-disc mesh for a junction.
///
/// Delegates to the shared ground-decal generator so a junction gets the same
/// treatment as a plaza floor: concentric rings that drape on the terrain
/// (the old centre fan was planar between hub and rim, so a 16 m disc cut
/// through any slope), a wobbled rim instead of a stamped circle, and a
/// smootherstep alpha ramp out to zero.
pub fn junction_disc_mesh(grid: &BrushGrid, j: &RoadJunction) -> ChunkMeshData {
    super::decal::ground_decal_mesh(grid, &j.decal_spec())
}

impl RoadJunction {
    /// The decal this junction renders as.
    pub fn decal_spec(&self) -> super::decal::GroundDecalSpec {
        super::decal::GroundDecalSpec {
            name: None,
            at: self.at,
            half_extent: Vec2::splat(self.radius),
            feather: self.feather,
            noise: JUNCTION_NOISE,
            // Seed from the position so every junction wobbles differently
            // but reproducibly.
            seed: self.at.x.to_bits() ^ self.at.y.to_bits().rotate_left(16),
            texture: self.texture.clone(),
            texture_scale: self.texture_scale,
            lift: JUNCTION_LIFT,
            ..super::decal::GroundDecalSpec::default()
        }
    }
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
    fn test_road_defaults_match_reference() {
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
    fn test_network_merges_head_to_head_segments() {
        // Autoria head-to-head: os DOIS segmentos apontam PARA o way b
        // (a→b e d→b, ambos terminando no canto). Antes a fusão assumia
        // tail-to-head, consumia o segmento reverso, duplicava o ponto b
        // e PERDIA a perna d—b sem aviso nenhum.
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
                    id: "d".into(),
                    at: Vec2::new(20.0, 0.0),
                    width: None,
                },
            ],
            segments: vec![
                SegmentSpec {
                    a: "a".into(),
                    b: "b".into(),
                    ..SegmentSpec::default()
                },
                SegmentSpec {
                    a: "d".into(),
                    b: "b".into(),
                    ..SegmentSpec::default()
                },
            ],
            ..RoadNetworkSpec::default()
        };
        let roads = net.expand();
        assert_eq!(roads.len(), 1, "head-to-head chain collapses into one path");
        assert_eq!(
            roads[0].path,
            vec![
                Vec2::new(0.0, 20.0),
                Vec2::new(20.0, 20.0),
                Vec2::new(20.0, 0.0)
            ]
        );
    }

    #[test]
    fn test_network_merges_chain_through_reversed_segment() {
        // Cadeia com um merge REVERSO a meio: a→b, d→b (reverso), d→e. O
        // extremo da cabeça passa a `d` — se for re-derivado do `.b` do
        // último segmento fundido (b), o d→e nunca entra e nasce uma
        // segunda ribbon d-b com duplo carve.
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
                    id: "d".into(),
                    at: Vec2::new(20.0, 0.0),
                    width: None,
                },
                WaySpec {
                    id: "e".into(),
                    at: Vec2::new(40.0, 0.0),
                    width: None,
                },
            ],
            segments: vec![
                SegmentSpec {
                    a: "a".into(),
                    b: "b".into(),
                    ..SegmentSpec::default()
                },
                SegmentSpec {
                    a: "d".into(),
                    b: "b".into(),
                    ..SegmentSpec::default()
                },
                SegmentSpec {
                    a: "d".into(),
                    b: "e".into(),
                    ..SegmentSpec::default()
                },
            ],
            ..RoadNetworkSpec::default()
        };
        let roads = net.expand();
        assert_eq!(
            roads.len(),
            1,
            "chain through a reversed merge stays one path"
        );
        assert_eq!(
            roads[0].path,
            vec![
                Vec2::new(0.0, 20.0),
                Vec2::new(20.0, 20.0),
                Vec2::new(20.0, 0.0),
                Vec2::new(40.0, 0.0),
            ]
        );
    }

    #[test]
    fn test_network_merge_keeps_via_order_across_degree2_way() {
        // ≥2 pontos de via no segmento da cauda: a fusão preserva a ordem
        // autoral das vias (a ordem invertida curvava o rio/estrada ao
        // contrário através do canto).
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
                    at: Vec2::new(0.0, 30.0),
                    width: None,
                },
                WaySpec {
                    id: "c".into(),
                    at: Vec2::new(30.0, 30.0),
                    width: None,
                },
            ],
            segments: vec![
                SegmentSpec {
                    a: "c".into(),
                    b: "b".into(),
                    via: vec![Vec2::new(20.0, 30.0), Vec2::new(10.0, 30.0)],
                    width: None,
                    profile: None,
                },
                SegmentSpec {
                    a: "b".into(),
                    b: "a".into(),
                    ..SegmentSpec::default()
                },
            ],
            ..RoadNetworkSpec::default()
        };
        let roads = net.expand();
        assert_eq!(roads.len(), 1);
        assert_eq!(
            roads[0].path,
            vec![
                Vec2::new(30.0, 30.0),
                Vec2::new(20.0, 30.0),
                Vec2::new(10.0, 30.0),
                Vec2::new(0.0, 30.0),
                Vec2::new(0.0, 0.0),
            ]
        );
    }

    #[test]
    fn test_network_extends_ends_at_shared_junction() {
        // T: B tem grau 3 — cada ribbon estende para lá do B (extendPathEnds)
        // para não deixar cunha de chão nu no canto externo.
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

    /// The north-gate regression: a 4 m network used to floor every junction
    /// disc at `default_width × 2` = 8 m radius — a 16 m circle of cobble for
    /// a road whose flared ribbon is only ~3.1 m wide on each side. The disc
    /// must track the geometry it covers, not a constant.
    #[test]
    fn test_junction_disc_tracks_the_flared_ribbon() {
        let net = RoadNetworkSpec {
            default_width: 4.0,
            crossing_flare: true,
            ways: vec![
                WaySpec {
                    id: "hub".into(),
                    at: Vec2::ZERO,
                    width: None,
                },
                WaySpec {
                    id: "n".into(),
                    at: Vec2::new(0.0, 30.0),
                    width: None,
                },
                WaySpec {
                    id: "e".into(),
                    at: Vec2::new(30.0, 0.0),
                    width: None,
                },
                WaySpec {
                    id: "s".into(),
                    at: Vec2::new(0.0, -30.0),
                    width: None,
                },
            ],
            segments: vec![
                SegmentSpec {
                    a: "hub".into(),
                    b: "n".into(),
                    ..SegmentSpec::default()
                },
                SegmentSpec {
                    a: "hub".into(),
                    b: "e".into(),
                    ..SegmentSpec::default()
                },
                SegmentSpec {
                    a: "hub".into(),
                    b: "s".into(),
                    ..SegmentSpec::default()
                },
            ],
            ..RoadNetworkSpec::default()
        };
        let junctions = net.junction_points();
        assert_eq!(junctions.len(), 1, "only the degree-3 hub gets a disc");
        let j = &junctions[0];
        // Widest ribbon half-width reaching the hub, flare and wobble included.
        let ribbon_reach = 4.0 * 0.5 * CROSSING_FLARE * (1.0 + RIBBON_WOBBLE);
        assert!(
            j.radius >= ribbon_reach,
            "disc {} must still cover the flared ribbon {ribbon_reach}",
            j.radius
        );
        assert!(
            j.radius <= ribbon_reach + JUNCTION_MARGIN + 1e-4,
            "disc {} overshoots the ribbon it covers (old floor was 8.0)",
            j.radius
        );
        assert!(j.radius < 5.0, "a 4 m road must not grow a 16 m circle");
    }

    /// The disc sits above the ribbons it hides and both sit above a ground
    /// decal — a fixed stacking order, so the three transparent cobble layers
    /// cannot z-fight.
    #[test]
    fn test_decal_layers_stack_in_a_fixed_order() {
        const { assert!(super::super::decal::DECAL_LIFT < RIBBON_LIFT) };
        const { assert!(RIBBON_LIFT < JUNCTION_LIFT) };
    }

    /// The disc mesh must drape: every vertex on the ground, not a planar fan.
    #[test]
    fn test_junction_disc_drapes_and_feathers() {
        let grid = test_grid();
        let j = RoadJunction {
            at: Vec2::new(40.0, 32.0),
            radius: 4.0,
            feather: 2.2,
            texture: None,
            texture_scale: 9.0,
        };
        let mesh = junction_disc_mesh(&grid, &j);
        assert!(!mesh.positions.is_empty());
        for p in &mesh.positions {
            let expected = grid.sample(p[0], p[2]) + JUNCTION_LIFT;
            assert!(
                (p[1] - expected).abs() < 1e-3,
                "junction vertex {p:?} left the ground"
            );
        }
        let min_alpha = mesh
            .colors
            .iter()
            .map(|c| c[3])
            .fold(f32::INFINITY, f32::min);
        assert!(
            min_alpha.abs() < 1e-6,
            "rim must fade to 0, got {min_alpha}"
        );
    }

    /// Feather wobble: the ribbon edges must not be two lines exactly
    /// parallel to the centreline.
    #[test]
    fn test_ribbon_edge_feather_wobbles() {
        let grid = test_grid();
        let spec = road_spec();
        let path = {
            let mut g = grid.clone();
            carve_road(&mut g, &spec, 0, &RoadGuards::default()).expect("road")
        };
        let mesh = road_ribbon_mesh(&grid, &path, &spec, &[], 0);
        // Stride 6: [skirtL, edgeL, coreL, coreR, edgeR, skirtR]. The feather
        // width on the left is |coreL - edgeL| in XZ.
        let widths: Vec<f32> = mesh
            .positions
            .chunks(6)
            .map(|c| {
                let e = Vec2::new(c[1][0], c[1][2]);
                let k = Vec2::new(c[2][0], c[2][2]);
                e.distance(k)
            })
            .collect();
        let lo = widths.iter().cloned().fold(f32::INFINITY, f32::min);
        let hi = widths.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
        assert!(
            hi - lo > 0.1,
            "feather width should breathe along the road (lo {lo}, hi {hi})"
        );
    }

    /// REGRESSÃO "risco na berma": a saia lateral partilha a lateral do anel
    /// externo (alpha 0) e o seu topo fica `RIBBON_LIFT` acima do chão, por
    /// isso uma saia OPACA desenhava uma linha contínua ao longo da borda da
    /// estrada. Tem de ficar invisível — só o deck (edge/core) desenha.
    #[test]
    fn test_ribbon_skirt_never_draws() {
        let grid = test_grid();
        let spec = road_spec();
        let path = {
            let mut g = grid.clone();
            carve_road(&mut g, &spec, 0, &RoadGuards::default()).expect("road")
        };
        // Stride 6: [skirtL, edgeL, coreL, coreR, edgeR, skirtR].
        let mut bridge_path = path.clone();
        bridge_path.bridge = true;
        bridge_path.deck_y = Some(6.0);
        for (label, p) in [("chão", &path), ("ponte", &bridge_path)] {
            let mesh = road_ribbon_mesh(&grid, p, &spec, &[], 0);
            for (i, c) in mesh.colors.chunks(6).enumerate() {
                assert!(
                    c[0][3].abs() < 1e-6 && c[5][3].abs() < 1e-6,
                    "{label}, estação {i}: saia tem de ser transparente, \
                     got L={} R={}",
                    c[0][3],
                    c[5][3]
                );
            }
            // O núcleo continua opaco e a borda continua a desvanecer.
            assert!(
                (mesh.colors[2][3] - 1.0).abs() < 1e-6,
                "{label}: núcleo opaco"
            );
            assert!(mesh.colors[1][3].abs() < 1e-6, "{label}: borda desvanece");
        }
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
            &[],
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
    fn test_junction_disc_mesh_layout() {
        let grid = test_grid();
        let j = RoadJunction {
            at: Vec2::new(64.0, 64.0),
            radius: 8.0,
            feather: 1.2,
            texture: None,
            texture_scale: 4.0,
        };
        let mesh = junction_disc_mesh(&grid, &j);
        // Hub + anéis concêntricos (o disco delega no gerador de decals: já
        // não é um leque de 2 anéis, que era planar entre o centro e a
        // borda e cortava o terreno em declive).
        assert!(mesh.positions.len() > 1 + 2 * 33);
        assert_eq!(mesh.positions.len(), mesh.colors.len());
        assert_eq!(mesh.indices.len() % 3, 0);
        assert!((mesh.indices.iter().copied().max().unwrap() as usize) < mesh.positions.len());
        // Winding do leque: o primeiro triângulo (centro, a1, a0) → +Y.
        let v = |i: u32| bevy::math::Vec3::from_array(mesh.positions[i as usize]);
        let (c, a1, a0) = (v(0), v(mesh.indices[1]), v(mesh.indices[2]));
        let n = (a1 - c).cross(a0 - c);
        assert!(n.y > 0.0, "fan winding must face up: {n}");
        // Alpha: hub e núcleo opacos, anel exterior a 0.
        assert!((mesh.colors[0][3] - 1.0).abs() < 1e-5);
        assert!((mesh.colors[1][3] - 1.0).abs() < 1e-5);
        assert_eq!(
            mesh.colors.last().expect("colors")[3],
            0.0,
            "o anel exterior desvanece a zero"
        );
    }

    #[test]
    fn test_junction_points_from_ways() {
        // T: b tem grau 3 → um disco; a/c/d grau 1 → nada.
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
        let j = net.junction_points();
        assert_eq!(j.len(), 1, "only the degree-3 way gets a disc");
        assert_eq!(j[0].at, Vec2::new(10.0, 0.0));
        // Sem crossing-flare o disco cobre só a meia-largura (2 m) + wobble
        // + folga. O antigo piso `default_width · 2` = 8 m dava um círculo
        // de 16 m para uma estrada de 4 m — a regressão do portão norte.
        let expect = 4.0 * 0.5 * (1.0 + RIBBON_WOBBLE) + JUNCTION_MARGIN;
        assert!(
            (j[0].radius - expect).abs() < 1e-4,
            "raio segue a ribbon ({expect}): {j:?}"
        );
    }

    #[test]
    fn test_road_ribbon_winding_faces_up() {
        // Estrada +X: o primeiro triângulo do DECK (edgeL, coreL, edgeL do
        // próximo passo) tem de dar normal +Y — winding invertido escondia
        // a ribbon atrás do FrontSide cull.
        let grid = test_grid();
        let path = RoadPath {
            name: None,
            profile: RoadProfile::Artery,
            bridge: false,
            deck_y: None,
            stations: vec![Vec2::new(30.0, 64.0), Vec2::new(40.0, 64.0)],
            half_width: vec![2.0, 2.0],
            surface: vec![6.0, 6.0],
            deck: vec![false, false],
        };
        let spec = road_spec();
        let mesh = road_ribbon_mesh(&grid, &path, &spec, &[], 0);
        // O 1º segmento escreve 18 índices de deck antes das saias.
        let v = |i: u32| bevy::math::Vec3::from_array(mesh.positions[i as usize]);
        let (a, b, c) = (v(mesh.indices[0]), v(mesh.indices[1]), v(mesh.indices[2]));
        let normal = (b - a).cross(c - a);
        assert!(normal.y > 0.0, "ribbon winding must face up: {normal}");
        // Determinismo: mesma entrada → mesma malha.
        let again = road_ribbon_mesh(&grid, &path, &spec, &[], 0);
        assert_eq!(mesh.positions, again.positions, "noise determinístico");
        // Refine orgânico: 10 m com passo ≤ 2.5 m → ≥ 4 estações refinadas
        // (2 de entrada), 6 vértices cada (deck 4 + saias 2).
        assert!(mesh.positions.len() >= 4 * 6, "refine subdividiu");
        assert_eq!(mesh.positions.len() % 6, 0);
    }

    #[test]
    fn test_road_ribbon_corner_keeps_constant_width() {
        // L de 90°: o refine orgânico (subdivisão + suavização) arredonda o
        // canto; o miter mantém a largura local perto da nominal em TODAS
        // as estações (nem pinch de 45° nem esticões).
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
            surface: vec![6.0; 5],
            deck: vec![false; 5],
        };
        let spec = road_spec();
        let mesh = road_ribbon_mesh(&grid, &path, &spec, &[], 0);
        assert_eq!(mesh.positions.len() % 6, 0, "6 verts por estação");
        let stations = mesh.positions.len() / 6;
        assert!(stations >= 20, "subdivisão refiniu a L: {stations}");
        for s in 0..stations {
            // deck: [skirtL, edgeL, coreL, coreR, edgeR, skirtR] — a largura
            // nominal mede-se edge↔edge (1↔4; os skirts repetem a lateral
            // descendo)
            let left = bevy::math::Vec3::from_array(mesh.positions[s * 6 + 1]);
            let right = bevy::math::Vec3::from_array(mesh.positions[s * 6 + 4]);
            let width = left.distance(right);
            assert!(
                (3.4..=5.2).contains(&width),
                "largura local {width} fora da faixa (estação {s})"
            );
        }
    }

    #[test]
    fn test_road_ribbon_mesh_drapes_the_bed() {
        let mut grid = test_grid();
        let spec = road_spec();
        let guards = RoadGuards::default();
        let path = carve_road(&mut grid, &spec, 0, &guards).expect("road");
        let mesh = road_ribbon_mesh(&grid, &path, &spec, &[], 0);
        assert_eq!(mesh.positions.len() % 6, 0, "deck 4 + saias 2");
        // 18 índices de deck + 12 de saias por segmento.
        let refined = mesh.positions.len() / 6;
        assert_eq!(mesh.indices.len(), (refined - 1) * 30);
        // Ribbon sits just above the bed (vértice 1 = edgeL; o 0 é o skirt
        // que desce RIBBON_SKIRT_DEPTH).
        let first = &mesh.positions[1];
        let bed = grid.sample(first[0], first[2]);
        assert!(
            first[1] - bed >= 0.0 && first[1] - bed < 0.2,
            "ribbon drapes the carve: {} vs {bed}",
            first[1]
        );
        // UV world-space: a última estação tem uv = (x, z) / texture_scale.
        let last_uv = &mesh.uvs[mesh.uvs.len() - 1];
        let last_pos = &mesh.positions[mesh.positions.len() - 1];
        let expected = [
            last_pos[0] / spec.texture_scale,
            last_pos[2] / spec.texture_scale,
        ];
        assert!(
            (last_uv[0] - expected[0]).abs() < 1e-4 && (last_uv[1] - expected[1]).abs() < 1e-4,
            "uv é world/scale: {last_uv:?} vs {expected:?}"
        );
        // Edge alpha feather; the center line stays opaque.
        assert!(mesh.colors[1][3] < 1.0, "edges feather");
        assert!((mesh.colors[2][3] - 1.0).abs() < 1e-4, "center opaque");
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

    /// Grelha plana a 6 m — cenário limpo para pistas autoradas.
    fn flat_grid() -> BrushGrid {
        let mut grid =
            BrushGrid::new(vec![0; 128 * 128], 128, 128, 128.0, 50.0, 0.0).expect("grid");
        grid.begin_stroke("base");
        for z in 0..128 {
            for x in 0..128 {
                let p = grid.cell_center(x, z);
                grid.set_cell_height(x, z, 6.0 + 0.05 * p.x);
            }
        }
        grid.commit_stroke();
        grid
    }

    /// Pista autorada com vão de viaduto: o terreno sob o deck fica INTACTO
    /// (o vale não é gradado), as spans de solo são carvadas ao perfil, o
    /// RoadPath marca deck/surface e as queries respondem em conformidade.
    #[test]
    fn test_authored_road_skips_carve_over_viaduct() {
        let mut grid = flat_grid();
        let spec = RoadSpec {
            path: vec![
                Vec2::new(10.0, 64.0),
                Vec2::new(40.0, 64.0),
                Vec2::new(64.0, 64.0),
                Vec2::new(90.0, 64.0),
                Vec2::new(118.0, 64.0),
            ],
            // solo 6 → tabuleiro 16 no meio: folga 10 ≥ clearance 6.
            elevations: Some(vec![6.0, 6.0, 16.0, 16.0, 6.0]),
            widths: Some(vec![8.0, 8.0, 10.0, 10.0, 8.0]),
            viaduct_clearance: 6.0,
            width: 8.0,
            flatten_shoulder: 2.0,
            // Perfil autoriado livre de clamp — o limite de gradiente tem
            // teste próprio (`test_limit_grade_clamps_steep_profiles`).
            flatten_max_grade: 1.5,
            ..road_spec()
        };
        let path = carve_road(&mut grid, &spec, 0, &RoadGuards::default()).expect("carved");
        assert!(path.deck.iter().any(|&d| d), "mid span flagged as deck");
        assert!(!path.deck[0], "ground span not deck");
        assert_eq!(path.deck.len(), path.stations.len());
        assert_eq!(path.surface.len(), path.stations.len());

        // Sob o tabuleiro (x=64): terreno intacto, sem carve.
        let natural_center = 6.0 + 0.05 * 64.0;
        let under_deck = grid.sample(64.0, 64.0);
        assert!(
            (under_deck - natural_center).abs() < 0.35,
            "valley under the deck stays: {under_deck} vs {natural_center}"
        );
        // No span de solo (x=12): carve assentou no perfil autoriado.
        let on_ground = grid.sample(12.0, 64.0);
        assert!(
            (on_ground - (6.0 - spec.platform_sink)).abs() < 0.3,
            "ground span carved to authored profile: {on_ground}"
        );

        // Query de superfície: deck devolve a cota autoriada; span de solo
        // devolve None (o chamador usa o heightfield).
        let deck_hit = path.surface_y_at(Vec2::new(64.0, 64.0));
        assert!(
            matches!(deck_hit, Some(y) if (y - 16.0).abs() < 0.6),
            "deck surface is authored: {deck_hit:?}"
        );
        assert!(
            path.surface_y_at(Vec2::new(12.0, 64.0)).is_none(),
            "ground span has no deck surface"
        );
        assert!(path.is_deck_at(Vec2::new(64.0, 64.0)));
        assert!(!path.is_deck_at(Vec2::new(12.0, 64.0)));
        // Progresso varia ao longo da pista.
        let s0 = path.progress(Vec2::new(12.0, 64.0)).expect("progress");
        let s1 = path.progress(Vec2::new(64.0, 64.0)).expect("progress");
        assert!(s1 > s0 + 5.0, "progress advances: {s0} -> {s1}");
    }

    /// Larguras por nó: a half_width do RoadPath segue o canal `widths`.
    #[test]
    fn test_authored_widths_reach_the_road_path() {
        let mut grid = flat_grid();
        let spec = RoadSpec {
            path: vec![Vec2::new(10.0, 64.0), Vec2::new(118.0, 64.0)],
            elevations: Some(vec![6.0, 6.0]),
            widths: Some(vec![6.0, 18.0]),
            width: 6.0,
            ..road_spec()
        };
        let path = carve_road(&mut grid, &spec, 0, &RoadGuards::default()).expect("carved");
        let hw0 = path.half_width[0];
        let hw1 = *path.half_width.last().expect("non-empty");
        assert!(
            (hw0 - 3.0).abs() < 0.2 && (hw1 - 9.0).abs() < 0.2,
            "half widths follow the channel: {hw0} .. {hw1}"
        );
    }

    /// Pista fechada: o anel fica explícito (primeira estação repetida no
    /// fim) — a reta da meta é queryável e a ribbon fecha sem vão.
    #[test]
    fn test_closed_road_ring_is_explicit() {
        let mut grid = flat_grid();
        let spec = RoadSpec {
            path: vec![
                Vec2::new(44.0, 44.0),
                Vec2::new(84.0, 44.0),
                Vec2::new(84.0, 84.0),
                Vec2::new(44.0, 84.0),
            ],
            closed: true,
            width: 8.0,
            ..road_spec()
        };
        let path = carve_road(&mut grid, &spec, 0, &RoadGuards::default()).expect("carved");
        assert_eq!(path.stations.len(), path.half_width.len());
        assert_eq!(path.stations.len(), path.surface.len());
        assert_eq!(path.stations.len(), path.deck.len());
        assert!(
            path.stations.first() == path.stations.last(),
            "ring closed with an explicit copy of the first station"
        );
        // Reta de meta (meio da aresta de topo): em pista e a superfície é a
        // do terreno carvado (query devolve None — é span de solo).
        assert!(path.is_on_road(Vec2::new(64.0, 44.0)));
        assert!(path.surface_y_at(Vec2::new(64.0, 44.0)).is_none());
        // O meio do vão de fechamento (costura) também é pista.
        assert!(path.is_on_road(Vec2::new(44.0, 64.0)));
    }

    /// Ribbon em vão suspenso: vértices à cota autoriada (+RIBBON_LIFT),
    /// normal +Y, e pilares anexados ao mesmo mesh.
    #[test]
    fn test_ribbon_deck_renders_at_authored_height_with_pylons() {
        let grid = flat_grid();
        let path = RoadPath {
            name: None,
            profile: RoadProfile::Artery,
            bridge: false,
            deck_y: None,
            stations: vec![Vec2::new(30.0, 64.0), Vec2::new(90.0, 64.0)],
            half_width: vec![4.0, 4.0],
            surface: vec![16.0, 16.0],
            deck: vec![true, true],
        };
        let spec = road_spec();
        let mesh = road_ribbon_mesh(&grid, &path, &spec, &[], 0);
        assert!(!mesh.positions.is_empty());
        // Vértices de ribbon (a cota do deck, 16 + RIBBON_LIFT): o refine
        // subdividiu 60 m em passos ≤ 2,5 m → ≥ 24 estações × 6 verts. Os
        // pilares ficam a outras cotas (topo entalado a 16 − TUCK), por isso
        // a contagem exata de vértices de ribbon é a contagem no deck.
        let deck_verts = mesh
            .positions
            .iter()
            .filter(|p| (p[1] - (16.0 + RIBBON_LIFT)).abs() < 1e-4)
            .count();
        assert!(
            deck_verts >= 24 * 6 && deck_verts % 6 == 0,
            "ribbon subdivided on the deck: {deck_verts}"
        );
        // Pilares: vértices fora da ribbon (a ribbon é múltiplo de 6; cada
        // pilar anexa 16 vértices = 4 lados × 4). As bases descem bem abaixo
        // do tabuleiro.
        let pylon_verts = mesh.positions.len() - deck_verts;
        assert!(
            pylon_verts >= 16 && pylon_verts % 16 == 0,
            "whole pylons on a 60 m span: {pylon_verts}"
        );
        let pylon_min_y = mesh.positions[deck_verts..]
            .iter()
            .map(|p| p[1])
            .fold(f32::INFINITY, f32::min);
        assert!(
            pylon_min_y < 16.0 - DECK_PYLON_MIN_CLEARANCE,
            "pylons descend well below the deck: {pylon_min_y}"
        );
        // Winding do deck continua a virar para cima.
        let v = |i: u32| bevy::math::Vec3::from_array(mesh.positions[i as usize]);
        let (a, b, c) = (v(mesh.indices[0]), v(mesh.indices[1]), v(mesh.indices[2]));
        let normal = (b - a).cross(c - a);
        assert!(normal.y > 0.0, "deck winding must face up: {normal}");
    }

    /// Pilares não nascem dentro do corredor de outra estrada (passagem
    /// inferior da pista fica limpa).
    #[test]
    fn test_deck_pylons_skip_crossing_roads() {
        let grid = flat_grid();
        // Deck +X a 16 m, vão de 90 m → pilares a x=20, 50, 80, 110; a
        // estrada de solo em X=60 (meia-largura 10) limpa o pilar de x=50
        // (distância à borda = 0 < guarda).
        let path = RoadPath {
            name: None,
            profile: RoadProfile::Artery,
            bridge: false,
            deck_y: None,
            stations: vec![Vec2::new(20.0, 64.0), Vec2::new(110.0, 64.0)],
            half_width: vec![4.0, 4.0],
            surface: vec![16.0, 16.0],
            deck: vec![true, true],
        };
        let under = RoadPath {
            name: None,
            profile: RoadProfile::Artery,
            bridge: false,
            deck_y: None,
            stations: vec![Vec2::new(60.0, 24.0), Vec2::new(60.0, 104.0)],
            half_width: vec![10.0, 10.0],
            surface: vec![6.0, 6.0],
            deck: vec![false, false],
        };
        let spec = road_spec();
        // Contrato do runtime: `others` = TODAS as estradas do mundo,
        // `self_index` = índice da estrada em mesh nesse conjunto.
        let all = vec![path.clone(), under];
        let with_guard = road_ribbon_mesh(&grid, &path, &spec, &all, 0);
        let without = road_ribbon_mesh(&grid, &path, &spec, &[], 0);
        // A ribbon é idêntica; a diferença são pilares (16 vértices cada,
        // fora dos vértices de ribbon à cota do deck).
        let deck_verts = |m: &ChunkMeshData| {
            m.positions
                .iter()
                .filter(|p| (p[1] - (16.0 + RIBBON_LIFT)).abs() < 1e-4)
                .count()
        };
        let pylons = |m: &ChunkMeshData| (m.positions.len() - deck_verts(m)) / 16;
        assert!(
            pylons(&without) >= 2,
            "baseline: pylons exist on the 90 m span: {}",
            pylons(&without)
        );
        assert_eq!(
            pylons(&without) - pylons(&with_guard),
            1,
            "exactly the underpass pylon is removed ({} -> {})",
            pylons(&without),
            pylons(&with_guard)
        );
    }
}
