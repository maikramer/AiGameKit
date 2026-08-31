//! Water features — lakes and rivers: lower-only terrain carve plus water
//! surface geometry and the [`WaterBody`] query registry.
//!
//! Ported from the VibeGame water plugin (`water/carve.ts`, `river-channel.ts`,
//! `lake-bowl.ts`). Contracts kept from the original:
//!
//! * **Lower-only carve** — water never raises terrain, so overlapping bodies
//!   and pre-existing valleys are always safe (brush [`BrushMode::Lower`];
//!   banks are the one intentional raise, applied first and capped).
//! * **Lakes** — an organic contour (`lake_shape_radius`: ±28% of the radius
//!   across three sine harmonics with position-seeded phase) is sampled on a
//!   32-ray ring to find the `rim`; the bowl floor is
//!   `rim − depth · (1 − t²)^1.5` (C1 at the rim), carved out to
//!   `carveR = radius · 1.25`. The water mirror sits at `rim − water_offset`.
//! * **Rivers** — the path is Chaikin-smoothed ×2 and resampled to 3 m
//!   stations; axis heights are sampled from the **post-pad, pre-water** grid
//!   and box-smoothed (±2 stations); the surface is a **descending prefix
//!   min** (`surf[i] = min(surf[i−1], axis[i] − water_offset)`) so water never
//!   flows uphill. Carving runs per station: banks raise first (capped
//!   profile), then the channel + bank cut lower — a bank from a neighbouring
//!   station never ends up inside the channel.
//! * **Registry** — every carve returns a [`WaterBody`]; gameplay (spawner
//!   `avoid-water` / `near-water`) queries it without touching the grid.

use bevy::math::Vec2;

use super::brush::{BrushGrid, BrushMode, BrushRequest, min_effective, smoothstep01};
use super::mesh::ChunkMeshData;
use super::paths::{chaikin_smooth, distance_to_path, nearest_on_path, resample, station_lerp};
use super::roads::ADAPTIVE_FALLOFF_FACTOR;

/// Lake contour: number of rim rays sampled (VibeGame `rimY` ring).
const RIM_RAYS: usize = 32;
/// Lake contour: harmonic amplitudes of `shapeRadius` (±28% total).
const SHAPE_AMPLITUDES: [f32; 3] = [0.12, 0.10, 0.06];
/// Lake carve margin over the design radius (VibeGame `carveMargin`).
pub const CARVE_MARGIN: f32 = 1.25;
/// River station spacing (meters, VibeGame `STATION_SPACING`).
pub const RIVER_STATION_SPACING: f32 = 3.0;
/// River: **minimum** falloff band outside the banks (meters). The band grows
/// with the local cut depth (see [`ADAPTIVE_FALLOFF_FACTOR`]) — a fixed band
/// turns every deep cut into a vertical wall.
pub const FEATHER_WIDTH: f32 = 2.5;
/// River: maximum bank raise over the **pre-carve axis height** (meters) —
/// deeper cuts read as a cascade instead of a levee wall.
pub const MAX_BANK_RAISE: f32 = 2.0;
/// River channel: minimum cut below the water surface (meters).
const MIN_CHANNEL_DEPTH: f32 = 0.2;
/// Water surface alpha fades to 0 over the outer fraction of the width.
const WATER_EDGE_FADE: f32 = 0.25;
/// Lake fan segments around the contour (VibeGame 72-segment mirror).
const LAKE_FAN_SEGMENTS: usize = 72;

/// Declarative lake (`<Lake at radius depth water-offset color opacity>`).
#[derive(Debug, Clone, PartialEq)]
pub struct LakeSpec {
    /// Lake center in world XZ.
    pub at: Vec2,
    /// Design radius of the water mirror (meters).
    pub radius: f32,
    /// Maximum bowl depth below the rim (meters).
    pub depth: f32,
    /// Water surface drop below the rim (meters).
    pub water_offset: f32,
    /// Water body color (sRGB 0..1).
    pub color: [f32; 3],
    /// Water surface opacity (0..1).
    pub opacity: f32,
    /// Ripple strength (reserved for the animated water material).
    pub ripple: f32,
}

impl Default for LakeSpec {
    fn default() -> Self {
        Self {
            at: Vec2::ZERO,
            radius: 6.0,
            depth: 1.5,
            water_offset: 0.5,
            color: [0.184, 0.478, 0.604], // #2f7a9a
            opacity: 0.78,
            ripple: 0.6,
        }
    }
}

/// Declarative river (`<River path width depth water-offset bank-width …>`).
#[derive(Debug, Clone, PartialEq)]
pub struct RiverSpec {
    /// Centerline polyline in world XZ (`"x z x z …"`).
    pub path: Vec<Vec2>,
    /// Full water width (meters).
    pub width: f32,
    /// Channel depth below the water surface (meters).
    pub depth: f32,
    /// Water surface drop below the smoothed axis (meters).
    pub water_offset: f32,
    /// Bank band width outside the water (meters).
    pub bank_width: f32,
    /// Bank raise height (meters).
    pub bank_height: f32,
    /// Water body color (sRGB 0..1).
    pub color: [f32; 3],
    /// Water surface opacity (0..1).
    pub opacity: f32,
}

impl Default for RiverSpec {
    fn default() -> Self {
        Self {
            path: Vec::new(),
            width: 6.0,
            depth: 1.5,
            water_offset: 0.3,
            bank_width: 2.0,
            bank_height: 0.9,
            color: [0.165, 0.4, 0.522], // #2a6685
            opacity: 0.85,
        }
    }
}

/// Kind of a registered [`WaterBody`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WaterKind {
    Lake,
    River,
}

/// Query registry entry produced by every water carve.
#[derive(Debug, Clone, PartialEq)]
pub struct WaterBody {
    pub kind: WaterKind,
    /// Anchor point (lake center; river centroid).
    pub at: Vec2,
    /// Lake: design water radius. River: `0.0`.
    pub radius: f32,
    /// Avoid-zone radius (lake: carve radius; river: half carve width).
    pub carve_radius: f32,
    /// Lake mirror height (rivers: mean surface height).
    pub water_y: f32,
    /// River stations in world XZ (empty for lakes).
    pub stations: Vec<Vec2>,
    /// River surface height per station (empty for lakes).
    pub surface_y: Vec<f32>,
    /// River full water width (lakes: `0.0`).
    pub water_width: f32,
}

impl WaterBody {
    /// Point is inside the carve zone (spawner `avoid-water`).
    pub fn contains(&self, p: Vec2) -> bool {
        match self.kind {
            WaterKind::Lake => p.distance(self.at) <= self.carve_radius,
            WaterKind::River => {
                !self.stations.is_empty()
                    && distance_to_path(&self.stations, p) <= self.carve_radius
            }
        }
    }

    /// Point is inside the carve zone plus `margin` (spawner `near-water`).
    pub fn is_near(&self, p: Vec2, margin: f32) -> bool {
        match self.kind {
            WaterKind::Lake => p.distance(self.at) <= self.carve_radius + margin,
            WaterKind::River => {
                !self.stations.is_empty()
                    && distance_to_path(&self.stations, p) <= self.carve_radius + margin
            }
        }
    }

    /// Water surface height at `p` when the point is over the water ribbon
    /// (rivers: nearest station surface; lakes: flat mirror when inside the
    /// design radius). `None` when the point is on dry land.
    pub fn surface_y_at(&self, p: Vec2) -> Option<f32> {
        match self.kind {
            WaterKind::Lake => {
                let d = p.distance(self.at);
                (d <= self.radius.max(self.carve_radius)).then_some(self.water_y)
            }
            WaterKind::River => {
                let hit = nearest_on_path(&self.stations, p)?;
                let d = hit.point.distance(p);
                (d <= self.water_width * 0.5).then(|| {
                    if self.surface_y.is_empty() {
                        self.water_y
                    } else {
                        station_lerp(&self.surface_y, &hit)
                    }
                })
            }
        }
    }
}

/// Deterministic harmonic phases for a lake contour, seeded by position.
fn shape_phases(at: Vec2) -> [f32; 3] {
    // Classic deterministic hash — precision beyond f32 is meaningless here.
    let s = at.x * 12.989_8_f32 + at.y * 78.233_f32;
    let base = (s.sin() * 43_758.55_f32).fract() * std::f32::consts::TAU;
    [base, base * 1.7 + 2.1, base * 0.6 + 4.4]
}

/// Organic lake contour: `radius · (1 + Σ aₖ·sin(kθ + φₖ))` (±28%).
pub fn lake_shape_radius(radius: f32, theta: f32, phases: [f32; 3]) -> f32 {
    let mut r = 1.0;
    for (k, a) in SHAPE_AMPLITUDES.iter().enumerate() {
        let harmonic = [2.0_f32, 3.0, 5.0][k]; // sin(2θ), sin(3θ), sin(5θ)
        r += a * (harmonic * theta + phases[k]).sin();
    }
    radius * r
}

/// Lake mirror height: the 32-ray rim minimum minus `water_offset`.
pub fn lake_water_height(grid: &BrushGrid, spec: &LakeSpec) -> f32 {
    let phases = shape_phases(spec.at);
    let mut rim = f32::INFINITY;
    for i in 0..RIM_RAYS {
        let theta = i as f32 / RIM_RAYS as f32 * std::f32::consts::TAU;
        let r = lake_shape_radius(spec.radius, theta, phases);
        let p = spec.at + Vec2::new(theta.cos(), theta.sin()) * r;
        rim = rim.min(grid.sample(p.x, p.y));
    }
    rim - spec.water_offset
}

/// Carves a lake bowl (lower-only) and returns its registry body.
/// Returns `None` when the radius/depth are degenerate.
pub fn carve_lake(grid: &mut BrushGrid, spec: &LakeSpec, index: usize) -> Option<WaterBody> {
    if spec.radius <= 0.0 || spec.depth <= 0.0 {
        return None;
    }
    let texel = grid.texel();
    let phases = shape_phases(spec.at);
    let water_y = lake_water_height(grid, spec);
    let carve_r = spec.radius * CARVE_MARGIN;

    // Bowl floor: C1 at the contour rim (`(1 − t²)^1.5` has zero derivative).
    let bowl = |p: Vec2| -> f32 {
        let d = p.distance(spec.at);
        let theta = (p.y - spec.at.y).atan2(p.x - spec.at.x);
        let r = lake_shape_radius(spec.radius, theta, phases) * CARVE_MARGIN;
        if d >= r {
            return water_y + spec.water_offset;
        }
        let t = d / r;
        (water_y + spec.water_offset) - spec.depth * (1.0 - t * t).max(0.0).powf(1.5)
    };

    // Shore band, per rim ray. The contour used to be a hard 0/1 mask, so a
    // lake dropped into a slope cut a cylinder: everything inside the contour
    // went down to the water surface and the uphill side became a sheer wall
    // as tall as the hillside. Grading the rim over a band that widens with
    // the local cut (the rule `roads` and the river bank already use) turns
    // that wall into a shore.
    let surface = water_y + spec.water_offset;
    let feather_base = min_effective(FEATHER_WIDTH, texel);
    let shore: Vec<f32> = (0..RIM_RAYS)
        .map(|i| {
            let theta = i as f32 / RIM_RAYS as f32 * std::f32::consts::TAU;
            let r = lake_shape_radius(spec.radius, theta, phases) * CARVE_MARGIN;
            let p = spec.at + Vec2::new(theta.cos(), theta.sin()) * r;
            let cut = (grid.sample(p.x, p.y) - surface).max(0.0);
            feather_base.max(ADAPTIVE_FALLOFF_FACTOR * cut)
        })
        .collect();
    let shore_max = shore.iter().copied().fold(feather_base, f32::max);
    // Shore width at an arbitrary angle: linear blend of the two rim rays.
    let shore_at = move |theta: f32| -> f32 {
        let tau = std::f32::consts::TAU;
        let f = (theta.rem_euclid(tau) / tau) * RIM_RAYS as f32;
        let i = (f.floor() as usize) % RIM_RAYS;
        let j = (i + 1) % RIM_RAYS;
        let frac = f - f.floor();
        shore[i] + (shore[j] - shore[i]) * frac
    };

    let owner = format!("lake:{index}");
    grid.begin_stroke(&owner);
    let mut weight = |p: Vec2| {
        let d = p.distance(spec.at);
        let theta = (p.y - spec.at.y).atan2(p.x - spec.at.x);
        let r = lake_shape_radius(spec.radius, theta, phases) * CARVE_MARGIN;
        if d < r {
            return 1.0;
        }
        let band = shore_at(theta);
        if d >= r + band {
            return 0.0;
        }
        1.0 - smoothstep01((d - r) / band.max(1e-4))
    };
    let mut target = bowl;
    // No guard: outside the contour `bowl` is just the water surface, not a
    // design — clamping the unweighted ring onto it is what cut a slot around
    // the shore (see the river carve for the same trap).
    let extent = carve_r + shore_max + texel * 2.0;
    grid.apply(BrushRequest {
        mode: BrushMode::Lower,
        min_x: spec.at.x - extent,
        min_z: spec.at.y - extent,
        max_x: spec.at.x + extent,
        max_z: spec.at.y + extent,
        target: &mut target,
        weight: &mut weight,
    });
    grid.commit_stroke();

    Some(WaterBody {
        kind: WaterKind::Lake,
        at: spec.at,
        radius: spec.radius,
        carve_radius: carve_r,
        water_y,
        stations: Vec::new(),
        surface_y: Vec::new(),
        water_width: 0.0,
    })
}

/// Carves a river (banks then channel) and returns its registry body.
/// Returns `None` for degenerate paths.
pub fn carve_river(grid: &mut BrushGrid, spec: &RiverSpec, index: usize) -> Option<WaterBody> {
    if spec.path.len() < 2 || spec.width <= 0.0 {
        return None;
    }
    let texel = grid.texel();
    let width = min_effective(spec.width, texel);
    let half = width * 0.5;
    let bank = min_effective(spec.bank_width, texel);
    let bank_height = spec.bank_height;
    let feather_base = min_effective(FEATHER_WIDTH, texel);

    // Design profile: smooth the path, sample the pre-water axis heights and
    // build the descending surface (water never flows uphill).
    let smoothed = chaikin_smooth(&spec.path, 2, false);
    let stations = resample(&smoothed, RIVER_STATION_SPACING.max(texel * 0.5));
    if stations.len() < 2 {
        return None;
    }
    let mut axis: Vec<f32> = stations.iter().map(|p| grid.sample(p.x, p.y)).collect();
    box_smooth(&mut axis, 2);
    let mut surface = Vec::with_capacity(axis.len());
    let mut running = f32::INFINITY;
    for &h in &axis {
        running = running.min(h - spec.water_offset);
        surface.push(running);
    }

    // Adaptive outer feather, per station. At the outer bank rim the design
    // profile is the water surface, so the hillside there is cut by
    // `axis - surface` meters. A fixed feather would spread that whole cut
    // over 2.5 m — a ~33 m cliff where the path crosses a ridge, which is
    // exactly the vertical fin that used to line the banks. Grading the band
    // with the cut (the same rule `roads` already uses for its falloff) turns
    // the gorge walls into slopes.
    let feathers: Vec<f32> = axis
        .iter()
        .zip(surface.iter())
        .map(|(&a, &s)| feather_base.max(ADAPTIVE_FALLOFF_FACTOR * (a - s).max(0.0)))
        .collect();
    let feather_max = feathers.iter().copied().fold(feather_base, f32::max);

    // The registry radius stays the *water* reach (the `avoid-water` zone):
    // the graded slopes beyond the bank are ordinary terrain, not river.
    let reach = half + bank + feather_base;
    let extent = half + bank + feather_max + texel * 2.0;
    let owner = format!("river:{index}");

    // Bank + channel profile at a point: the pass-2 design surface. The bank
    // band rises to `surf + bank_height` (capped against the axis height so
    // deep valleys stay cascades), the channel bowls under the surface.
    let channel_profile = {
        let stations = &stations;
        let surface = &surface;
        let axis = &axis;
        move |p: Vec2| -> f32 {
            let hit = nearest_on_path(stations, p).expect("stations >= 2");
            let d = hit.point.distance(p);
            // Interpolated along the path: reading `surface[seg]` directly
            // steps by a whole station at every segment boundary, and the
            // lower-only pass then cuts that step into a vertical fin.
            let surf = station_lerp(surface, &hit);
            let axis_h = station_lerp(axis, &hit);
            if d <= half {
                let t = d / half;
                surf - spec.depth.max(MIN_CHANNEL_DEPTH) * (1.0 - t * t).max(0.0).powf(1.5)
            } else {
                let band = ((d - half) / bank.max(1e-4)).clamp(0.0, 1.0);
                let raised = surf + bank_height * smoothstep01(1.0 - band);
                raised.min(axis_h + MAX_BANK_RAISE)
            }
        }
    };

    // Pass 1 — banks (raise): fills the near bank band up to the profile.
    grid.begin_stroke(&format!("{owner}:banks"));
    let mut bank_weight = |p: Vec2| {
        let hit = nearest_on_path(&stations, p).expect("stations >= 2");
        let d = hit.point.distance(p);
        let feather = station_lerp(&feathers, &hit);
        if d <= half || d > half + bank + feather {
            return 0.0;
        }
        let band = ((d - half) / bank.max(1e-4)).clamp(0.0, 1.0);
        let mut w = smoothstep01(1.0 - band);
        if d > half + bank {
            w *= 1.0 - smoothstep01((d - half - bank) / feather);
        }
        w
    };
    let mut bank_target = |p: Vec2| channel_profile(p);
    river_apply(
        grid,
        &stations,
        extent,
        &mut bank_target,
        &mut bank_weight,
        BrushMode::Raise,
    );
    grid.commit_stroke();

    // Pass 2 — channel + bank cut (lower-only): bowls the channel through the
    // water surface and cuts the hillside down to the bank profile.
    grid.begin_stroke(&owner);
    let mut channel_weight = |p: Vec2| {
        let hit = nearest_on_path(&stations, p).expect("stations >= 2");
        let d = hit.point.distance(p);
        let feather = station_lerp(&feathers, &hit);
        if d > half + bank + feather {
            return 0.0;
        }
        if d > half + bank {
            return 1.0 - smoothstep01((d - half - bank) / feather);
        }
        1.0
    };
    let mut channel_target = |p: Vec2| channel_profile(p);
    // Deliberately **no guard**. The guard clamp is a flat-design device: it
    // exists so a pad/road bed has no bilinear-stencil lip just outside its
    // falloff, where the design surface is still meaningful. A river has no
    // design surface out there — past the bank band `channel_profile` decays
    // to the bare water surface, tens of meters under the hillside it is
    // crossing. Since the guard only ever visits texels the main pass left
    // unweighted (everything beyond `half + bank + feather`), wiring it up
    // here did nothing *except* stamp that water height into a two-texel
    // column at the footprint edge — the vertical fins along every bank.
    river_apply(
        grid,
        &stations,
        extent,
        &mut channel_target,
        &mut channel_weight,
        BrushMode::Lower,
    );
    grid.commit_stroke();

    let water_y = surface.iter().sum::<f32>() / surface.len() as f32;
    Some(WaterBody {
        kind: WaterKind::River,
        at: centroid(&stations),
        radius: 0.0,
        carve_radius: reach,
        water_y,
        stations,
        surface_y: surface,
        water_width: width,
    })
}

/// One river carve pass: AABB over all stations; the closures spatially
/// filter (nearest-on-path distance).
fn river_apply(
    grid: &mut BrushGrid,
    stations: &[Vec2],
    extent: f32,
    target: &mut dyn FnMut(Vec2) -> f32,
    weight: &mut dyn FnMut(Vec2) -> f32,
    mode: BrushMode,
) {
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
        mode,
        min_x: min_x - extent,
        min_z: min_z - extent,
        max_x: max_x + extent,
        max_z: max_z + extent,
        target,
        weight,
    });
}

fn centroid(points: &[Vec2]) -> Vec2 {
    points.iter().sum::<Vec2>() / points.len().max(1) as f32
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

/// Builds the lake water surface mesh: a 72-segment two-ring disc following
/// the organic contour, alpha faded to 0 at the shore ring. Positions are
/// **world space**. `water_y` is the mirror height resolved by the carve
/// ([`WaterBody::water_y`]) so the mesh always matches the registry.
pub fn lake_water_mesh(spec: &LakeSpec, water_y: f32) -> ChunkMeshData {
    let phases = shape_phases(spec.at);
    let y = water_y;
    let push = |mesh: &mut ChunkMeshData, p: Vec2, radial: f32, alpha: f32| {
        mesh.positions.push([p.x, y, p.y]);
        mesh.normals.push([0.0, 1.0, 0.0]);
        mesh.uvs.push([0.5, radial]);
        mesh.colors
            .push([spec.color[0], spec.color[1], spec.color[2], alpha]);
    };

    let mut mesh = ChunkMeshData::default();
    // Center vertex.
    push(&mut mesh, spec.at, 0.5, spec.opacity);
    // Two rings: inner at full opacity, outer faded to the shore.
    let ring_count = 2;
    for ring in 0..ring_count {
        let radial = (ring + 1) as f32 / ring_count as f32; // 0.5, 1.0
        for i in 0..LAKE_FAN_SEGMENTS {
            let theta = i as f32 / LAKE_FAN_SEGMENTS as f32 * std::f32::consts::TAU;
            let r = lake_shape_radius(spec.radius, theta, phases) * radial;
            let p = spec.at + Vec2::new(theta.cos(), theta.sin()) * r;
            let alpha = spec.opacity * (1.0 - smoothstep01((radial - 0.75) / 0.25));
            push(&mut mesh, p, radial, alpha);
        }
    }
    // Center fan.
    let seg = LAKE_FAN_SEGMENTS as u32;
    let inner0 = 1u32;
    for i in 0..LAKE_FAN_SEGMENTS as u32 {
        let a = inner0 + i;
        let b = inner0 + (i + 1) % seg;
        mesh.indices.extend_from_slice(&[0, b, a]);
    }
    // Ring band: inner ring [1..73), outer ring [73..145).
    let outer0 = 1 + LAKE_FAN_SEGMENTS as u32;
    for i in 0..LAKE_FAN_SEGMENTS as u32 {
        let a = inner0 + i;
        let b = inner0 + (i + 1) % seg;
        let c = outer0 + i;
        let d = outer0 + (i + 1) % seg;
        mesh.indices.extend_from_slice(&[a, b, c, b, d, c]);
    }
    mesh
}

/// Builds the river water ribbon: two vertices per station (left/right of the
/// centerline), `y` per station surface, alpha fading over the outer quarter
/// of the width. Positions are **world space**.
pub fn river_water_mesh(spec: &RiverSpec, body: &WaterBody) -> ChunkMeshData {
    let mut mesh = ChunkMeshData::default();
    let n = body.stations.len();
    if n < 2 {
        return mesh;
    }
    let half = body.water_width * 0.5;
    let mut arc = 0.0;
    // Three vertices per station (left, center, right): the alpha fade lives
    // on the outer quarter of each half, so the center stays fully opaque.
    let alpha_at = |v: f32| -> f32 {
        let edge = (((v - 0.5).abs() * 2.0 - (1.0 - WATER_EDGE_FADE * 2.0)).max(0.0)
            / (WATER_EDGE_FADE * 2.0))
            .clamp(0.0, 1.0);
        spec.opacity * (1.0 - edge)
    };
    for (i, st) in body.stations.iter().enumerate() {
        if i > 0 {
            arc += st.distance(body.stations[i - 1]);
        }
        let next = body.stations[(i + 1).min(n - 1)];
        let prev = body.stations[i.saturating_sub(1)];
        let dir = (next - prev).normalize_or_zero();
        let perp = Vec2::new(-dir.y, dir.x);
        let y = body.surface_y[i];
        for v in [0.0_f32, 0.5, 1.0] {
            let p = *st + perp * (half * (v * 2.0 - 1.0));
            mesh.positions.push([p.x, y, p.y]);
            mesh.normals.push([0.0, 1.0, 0.0]);
            mesh.uvs.push([arc, v]);
            mesh.colors
                .push([spec.color[0], spec.color[1], spec.color[2], alpha_at(v)]);
        }
    }
    for i in 0..(n - 1) {
        // Vertices: 3 per station (l, c, r). Four CCW triangles per segment.
        let l0 = (i * 3) as u32;
        let c0 = l0 + 1;
        let r0 = l0 + 2;
        let l1 = ((i + 1) * 3) as u32;
        let c1 = l1 + 1;
        let r1 = l1 + 2;
        mesh.indices.extend_from_slice(&[
            l0, l1, c0, c0, l1, c1, // left half
            c0, c1, r0, r0, c1, r1, // right half
        ]);
    }
    mesh
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 96x96 grid, 96 m world, rolling hill profile.
    fn test_grid() -> BrushGrid {
        let mut grid = BrushGrid::new(vec![0; 96 * 96], 96, 96, 96.0, 50.0, 0.0).expect("grid");
        grid.begin_stroke("base");
        for z in 0..96 {
            for x in 0..96 {
                let p = grid.cell_center(x, z);
                let h = 12.0 + 4.0 * (p.x * 0.05).sin() + 3.0 * (p.y * 0.07).cos();
                grid.set_cell_height(x, z, h);
            }
        }
        grid.commit_stroke();
        grid
    }

    /// Flat 96x96 grid at 8 m for predictable carves.
    fn flat_grid() -> BrushGrid {
        let mut grid = BrushGrid::new(vec![0; 96 * 96], 96, 96, 96.0, 50.0, 0.0).expect("grid");
        grid.begin_stroke("flat");
        for i in 0..96 * 96 {
            grid.set_cell_height(i % 96, i / 96, 8.0);
        }
        grid.commit_stroke();
        grid
    }

    #[test]
    fn test_defaults_match_vibegame() {
        let lake = LakeSpec::default();
        assert_eq!(lake.radius, 6.0);
        assert_eq!(lake.depth, 1.5);
        assert_eq!(lake.water_offset, 0.5);
        assert!((lake.opacity - 0.78).abs() < 1e-6);
        let river = RiverSpec::default();
        assert_eq!(river.width, 6.0);
        assert_eq!(river.bank_width, 2.0);
        assert!((river.bank_height - 0.9).abs() < 1e-6);
    }

    #[test]
    fn test_lake_shape_radius_varies_and_is_deterministic() {
        let phases = shape_phases(Vec2::new(3.0, -7.0));
        assert_eq!(
            lake_shape_radius(10.0, 0.3, phases),
            lake_shape_radius(10.0, 0.3, phases),
            "same position -> same contour"
        );
        let mut min = f32::INFINITY;
        let mut max = f32::NEG_INFINITY;
        for i in 0..64 {
            let r = lake_shape_radius(10.0, i as f32 / 64.0 * std::f32::consts::TAU, phases);
            min = min.min(r);
            max = max.max(r);
        }
        assert!(
            min < 10.0 * 0.85 && max > 10.0 * 1.15,
            "±28% contour: {min}..{max}"
        );
        assert!(min > 10.0 * 0.6 && max < 10.0 * 1.4, "bounded variation");
    }

    #[test]
    fn test_carve_lake_lowers_inside_leaves_outside() {
        let mut grid = test_grid();
        let before = grid.sample(-40.0, -40.0);
        let spec = LakeSpec {
            at: Vec2::new(30.0, 30.0),
            radius: 12.0,
            depth: 3.0,
            water_offset: 0.5,
            ..LakeSpec::default()
        };
        let body = carve_lake(&mut grid, &spec, 0).expect("lake");
        let center = grid.sample(30.0, 30.0);
        assert!(
            center < body.water_y,
            "bowl floor sits below the mirror: {center} vs {}",
            body.water_y
        );
        assert!(
            (before - grid.sample(-40.0, -40.0)).abs() < 1e-3,
            "terrain outside the carve is untouched"
        );
        assert!(body.carve_radius > spec.radius, "carve margin applied");
        // After the carve the rim ring sits on discretized texels slightly
        // below the pre-carve rim (bowl C1 tail); tolerate the artifact.
        // Depth lands in the bowl between the mirror and rim + offset.
        let depth_reached = body.water_y + spec.water_offset - grid.sample(30.0, 30.0);
        assert!(
            (depth_reached - spec.depth).abs() < 0.15,
            "bowl depth at center: {depth_reached}"
        );
    }

    #[test]
    fn test_carve_lake_is_lower_only() {
        // Overlap the lake with an existing deep valley: the valley must not
        // be filled in.
        let mut grid = test_grid();
        grid.begin_stroke("valley");
        for z in 0..96 {
            for x in 0..96 {
                let p = grid.cell_center(x, z);
                if p.distance(Vec2::new(20.0, 20.0)) < 8.0 {
                    grid.set_cell_height(x, z, 1.0);
                }
            }
        }
        grid.commit_stroke();
        let spec = LakeSpec {
            at: Vec2::new(20.0, 20.0),
            radius: 12.0,
            depth: 2.0,
            ..LakeSpec::default()
        };
        let _ = carve_lake(&mut grid, &spec, 0).expect("lake");
        let valley = grid.sample(20.0, 20.0);
        assert!(
            valley < 2.0,
            "pre-existing valley is never raised: {valley}"
        );
    }

    #[test]
    fn test_carve_lake_degenerate_is_none() {
        let mut grid = test_grid();
        let spec = LakeSpec {
            radius: 0.0,
            ..LakeSpec::default()
        };
        assert!(carve_lake(&mut grid, &spec, 0).is_none());
        let spec = LakeSpec {
            depth: 0.0,
            ..LakeSpec::default()
        };
        assert!(carve_lake(&mut grid, &spec, 0).is_none());
    }

    fn river_spec() -> RiverSpec {
        RiverSpec {
            path: vec![Vec2::new(5.0, 0.0), Vec2::new(90.0, 0.0)],
            width: 6.0,
            depth: 2.0,
            water_offset: 0.4,
            bank_width: 2.5,
            bank_height: 0.8,
            color: [0.2, 0.4, 0.5],
            opacity: 0.85,
        }
    }

    #[test]
    fn test_carve_river_creates_a_channel_below_banks() {
        // Flat ground keeps the axis heights predictable (a rolling grid plus
        // the descending prefix-min would carve to the global minimum).
        let mut grid = flat_grid();
        let spec = river_spec();
        let body = carve_river(&mut grid, &spec, 0).expect("river");
        let channel = grid.sample(48.0, 0.0);
        let bank = grid.sample(48.0, body.water_width * 0.5 + 1.0);
        assert!(
            channel < body.water_y,
            "channel floor below the surface: {channel} vs {}",
            body.water_y
        );
        assert!(
            bank > channel,
            "bank sits above the channel floor: {bank} vs {channel}"
        );
        // Far from the river the base terrain survives.
        let far = grid.sample(48.0, -32.0);
        assert!((far - 8.0).abs() < 0.05, "far terrain untouched: {far}");
    }

    #[test]
    fn test_river_surface_never_rises_downstream() {
        let mut grid = test_grid();
        let spec = RiverSpec {
            path: vec![Vec2::new(5.0, 10.0), Vec2::new(90.0, -20.0)],
            ..river_spec()
        };
        let body = carve_river(&mut grid, &spec, 0).expect("river");
        for w in body.surface_y.windows(2) {
            assert!(
                w[1] <= w[0] + 1e-4,
                "descending prefix min: {} -> {}",
                w[0],
                w[1]
            );
        }
    }

    #[test]
    fn test_carve_river_degenerate_is_none() {
        let mut grid = test_grid();
        let spec = RiverSpec {
            path: vec![Vec2::ZERO],
            ..river_spec()
        };
        assert!(carve_river(&mut grid, &spec, 0).is_none());
    }

    #[test]
    fn test_water_body_queries() {
        let mut grid = test_grid();
        let lake = carve_lake(
            &mut grid,
            &LakeSpec {
                at: Vec2::new(30.0, 30.0),
                radius: 10.0,
                ..LakeSpec::default()
            },
            0,
        )
        .expect("lake");
        assert!(lake.contains(Vec2::new(32.0, 30.0)), "inside the carve");
        assert!(!lake.contains(Vec2::new(46.0, 30.0)));
        assert!(
            lake.is_near(Vec2::new(46.0, 30.0), 20.0),
            "margin reaches past the carve edge"
        );
        assert!(!lake.is_near(Vec2::new(44.0, -44.0), 5.0));
        assert!(lake.surface_y_at(Vec2::new(31.0, 30.0)).is_some());
        assert!(lake.surface_y_at(Vec2::new(44.0, -44.0)).is_none());

        let spec = river_spec();
        let river = carve_river(&mut grid, &spec, 1).expect("river");
        assert!(river.contains(Vec2::new(48.0, 1.0)), "on the river");
        assert!(!river.contains(Vec2::new(48.0, 22.0)), "off the river");
        assert!(river.surface_y_at(Vec2::new(48.0, 0.5)).is_some());
        assert!(river.surface_y_at(Vec2::new(48.0, 22.0)).is_none());
    }

    #[test]
    fn test_lake_water_mesh_rings_and_fade() {
        let mut grid = test_grid();
        let spec = LakeSpec {
            at: Vec2::new(40.0, 40.0),
            radius: 10.0,
            ..LakeSpec::default()
        };
        let body = carve_lake(&mut grid, &spec, 0).expect("lake");
        let mesh = lake_water_mesh(&spec, body.water_y);
        let expected = 1 + LAKE_FAN_SEGMENTS * 2;
        assert_eq!(mesh.positions.len(), expected, "center + two rings");
        assert_eq!(mesh.indices.len(), LAKE_FAN_SEGMENTS * 9, "fan + band");
        let center = mesh.positions[0];
        assert!(
            (center[0] - 40.0).abs() < 1e-4 && (center[2] - 40.0).abs() < 1e-4,
            "fan is centered on the lake"
        );
        assert!(
            (center[1] - body.water_y).abs() < 1e-3,
            "mirror at the rim offset"
        );
        // Outer ring follows the contour radius and fades out.
        let outer0 = 1 + LAKE_FAN_SEGMENTS;
        let mut min = f32::INFINITY;
        let mut max = f32::NEG_INFINITY;
        for p in &mesh.positions[outer0..] {
            let d = ((p[0] - 40.0).powi(2) + (p[2] - 40.0).powi(2)).sqrt();
            min = min.min(d);
            max = max.max(d);
        }
        assert!(min < 10.0 && max > 10.0, "organic contour: {min}..{max}");
        let center_alpha = mesh.colors[0][3];
        let outer_alpha = mesh.colors[outer0][3];
        assert!(
            outer_alpha < center_alpha * 0.2,
            "shore fades to ~0: {outer_alpha} vs {center_alpha}"
        );
    }

    #[test]
    fn test_river_water_mesh_ribbon() {
        let mut grid = test_grid();
        let spec = river_spec();
        let body = carve_river(&mut grid, &spec, 0).expect("river");
        let mesh = river_water_mesh(&spec, &body);
        assert_eq!(mesh.positions.len(), body.stations.len() * 3, "l/c/r");
        assert_eq!(mesh.indices.len(), (body.stations.len() - 1) * 12);
        for (i, p) in mesh.positions.iter().enumerate() {
            let st = body.stations[i / 3];
            let side = match i % 3 {
                0 | 2 => body.water_width * 0.5,
                _ => 0.0, // center vertex rides the surface
            };
            let d = ((p[0] - st.x).powi(2) + (p[2] - st.y).powi(2)).sqrt();
            assert!((d - side).abs() < 1e-3, "vertex {i} at expected offset");
        }
        let edge = mesh.colors[0][3];
        let mid = mesh.colors[1][3];
        assert!(edge < mid, "edge fades: {edge} vs {mid}");
        assert!((mid - spec.opacity).abs() < 1e-4, "center opaque: {mid}");
    }

    #[test]
    fn test_min_effective_grows_tiny_rivers() {
        let grid = test_grid();
        let width = min_effective(0.1, grid.texel());
        assert!(width >= 1.5 * grid.texel(), "width never below 1.5 texels");
    }
}
