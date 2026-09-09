//! Procedural cliffs — the one carve that *wants* the vertical step.
//!
//! Every other ground feature (roads, rivers, lakes) feathers its edges with
//! the adaptive falloff because a stray one-texel wall reads as a rendering
//! artifact. A cliff is the opposite: the step IS the feature. The u16 grid
//! represents near-vertical walls faithfully (a 30 m drop between adjacent
//! ~1 m texels reads ~86°) and the monotone Catmull-Rom sampler preserves
//! the step without ringing, so cliffs live in the heightfield like every
//! other feature — colliders, spawners and `sample_mesh_surface` stay in
//! sync for free.
//!
//! # Geometry
//!
//! The authored `path` is the **crest line**; the face band extends one-sided
//! on the drop side, `[0, width]` meters from the path. Per station the
//! carve samples the terrain just outside both ends of the band:
//!
//! * `top_y` — the crest height (top side, untouched by the carve);
//! * `bot_y` — the natural toe height (just beyond the band).
//!
//! Inside the band the design surface lerps `top_y → bot_y` through the
//! profile curve, so an auto drop (`height` absent) reshapes whatever
//! elevation difference the place already has — the wall adapts to the
//! terrain, not the other way round. An authored `height` overrides the toe:
//! `bot_y = min(natural, top_y − height)`, which caps drops that are too
//! tall and digs a quarry-style pit into flat ground.
//!
//! # Profiles
//!
//! * [`CliffProfile::Vertical`] — smoothstep S-curve across the band: natural
//!   shoulders, the steepest face in the middle.
//! * [`CliffProfile::Concave`] — `t^0.4`: vertical at the crest, leaning
//!   back toward the toe — the quarry/cirque wall, as close to an undercut
//!   as a 2.5D heightfield gets.
//! * [`CliffProfile::Terraced`] — quantized ledges (~3 m tall) with
//!   deterministic per-step jitter: mesas and treads.
//!
//! Determinism: all samples are taken **before** the stroke opens and every
//! variation (band wobble, step jitter) comes from `seed` + position hashes,
//! never from RNG or mid-carve reads — same seed, same world.

use bevy::math::Vec2;

use super::brush::BrushGrid;
use super::paths::{nearest_on_path, station_lerp};

/// Face profile of a carved cliff.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum CliffProfile {
    /// Smoothstep S-curve: flat shoulders top and bottom, steep face centered.
    #[default]
    Vertical,
    /// `t^0.4` — vertical at the crest, leaning back toward the toe.
    Concave,
    /// `t^2.2` — rounded brow at the crest, steepest at the toe: the face
    /// bulges outward like a dome/outcrop when seen from below.
    Convex,
    /// Columnar basalt look (the wargame-reference wall): the crest line
    /// breaks into straight per-column plan-view offsets (protruding fronts,
    /// recessed slots), each column a piecewise-linear stack of planar slabs
    /// ("straight cuts") with a jittered, chunky brow.
    Columnar,
    /// Quantized ledges with per-step jitter.
    Terraced,
    /// Deliberate overhang: the face leans OUT over its own foot, so the brow
    /// shades a usable recess underneath. Voxel-only by nature — a heightfield
    /// cannot hold two surfaces over one point, which is why this profile
    /// could not exist before (`src/terrain/voxel/cliff.rs`).
    Overhang,
    /// Natural rock arch: the contour stays plumb and ONE arched window is
    /// bored through the wall at the band's middle (`src/terrain/voxel/
    /// cliff.rs`) — the void is the feature, and only the voxel field can
    /// hold rock above, air and rock below at the same XZ.
    Arch,
}

impl CliffProfile {
    /// Parses the `profile` attribute
    /// (`vertical|concave|convex|columnar|terraced|overhang|arch`).
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "vertical" => Some(Self::Vertical),
            "concave" => Some(Self::Concave),
            "convex" => Some(Self::Convex),
            "columnar" | "columns" | "cortes" => Some(Self::Columnar),
            "terraced" => Some(Self::Terraced),
            "overhang" | "saliencia" | "saliência" => Some(Self::Overhang),
            "arch" | "arco" => Some(Self::Arch),
            _ => None,
        }
    }
}

/// Which side of the path the face drops toward.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum CliffSide {
    /// The side opposite the higher crest probe (mean over all stations) —
    /// the wall keeps the high ground and drops toward the low side.
    #[default]
    Auto,
    /// Left of the path direction (first → last station), map view.
    Left,
    /// Right of the path direction.
    Right,
}

impl CliffSide {
    /// Parses the `side` attribute (`auto|left|right`).
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "auto" => Some(Self::Auto),
            "left" => Some(Self::Left),
            "right" => Some(Self::Right),
            _ => None,
        }
    }
}

/// Declarative cliff parsed from a `<Cliff>` tag.
#[derive(Debug, Clone, PartialEq)]
pub struct CliffSpec {
    /// Crest polyline in world XZ (`"x z x z …"`); the face hangs on the
    /// drop side of it.
    pub path: Vec<Vec2>,
    /// Horizontal run of the face (meters) — `width`. Ignored when both
    /// `height` and `angle` are authored (then `width = height / tan(angle)`).
    pub width: f32,
    /// Total drop (meters). `None` = auto: the natural elevation difference
    /// between crest and toe (the wall adapts to the place).
    pub height: Option<f32>,
    /// Face slope in degrees — with `height` authored, derives the band width
    /// (`height / tan(angle)`); 90° would be zero-width, so the useful range
    /// is ~50–85°.
    pub angle: Option<f32>,
    pub profile: CliffProfile,
    pub side: CliffSide,
    /// Organic band-edge wobble as a fraction of `width` (0 = ruler-straight).
    pub noise: f32,
    /// Erosion gullies (`gullies`): one-sided recesses of the face as a
    /// fraction of `width` (0 = off, useful 0.15–0.4). High-frequency
    /// grooves cut the wall back between buttresses that stay on the
    /// nominal face line — the vertical ribbing of real rock faces.
    pub gullies: f32,
    /// Crest notches (`notches`): one-sided dips of the crest line as a
    /// fraction of the LOCAL drop (0 = off, useful 0.1–0.3). Low-frequency
    /// cols in the silhouette, scaled by the drop so gentle stretches stay.
    pub notches: f32,
    /// Debris apron at the toe (`talus`): an angle-of-repose scree cone
    /// rising from the natural ground to swallow the wall base — almost
    /// every real cliff sheds one.
    pub talus: bool,
    /// Repose angle of the talus surface in degrees (`talus-angle`, 36).
    /// Sets how far the apron spreads: `run ≈ 0.55 · drop / tan(angle)`.
    pub talus_angle: f32,
    /// Seed for the wobble/step jitter (deterministic).
    pub seed: u64,
}

impl Default for CliffSpec {
    fn default() -> Self {
        Self {
            path: Vec::new(),
            width: 6.0,
            height: None,
            angle: None,
            profile: CliffProfile::default(),
            side: CliffSide::default(),
            noise: 0.15,
            gullies: 0.0,
            notches: 0.0,
            talus: false,
            talus_angle: 36.0,
            seed: 0,
        }
    }
}

/// Terraced ledges aim for this tread RUN (meters of band per step), 2..=8
/// steps — the flat benches that catch the grass splat (wargame reference).
pub(crate) const TERRACE_TREAD: f32 = 2.5;
/// Dilation (texels) of the public cliff-query layer: grass/spawners skip
/// the wall AND this verge.
pub const CLIFF_MASK_MARGIN_TEXELS: usize = 2;
/// Half-window (texels) of the wall-space estimate in
/// [`CliffMask::compute_wall`]: the local min/max search around a marked
/// texel. 8 texels ≈ the band width of a mid-size wall, enough to catch
/// both brow and toe without reaching over to the neighboring landform.
const WALL_WINDOW_TEXELS: usize = 8;
/// Neutral wall-space byte (flat/terraced context — no weathering gradient).
const WALL_NEUTRAL: u8 = 128;

/// 24-bit deterministic hash → `[0, 1)`.
pub(crate) fn hash01(seed: u64, a: u64, b: u64) -> f32 {
    let mut x =
        seed ^ a.wrapping_mul(0x9E37_79B9_7F4A_7C15) ^ b.wrapping_mul(0xBF58_476D_1CE4_E5B9);
    x ^= x >> 30;
    x = x.wrapping_mul(0xBF58_476D_1CE4_E5B9);
    x ^= x >> 27;
    x = x.wrapping_mul(0x94D0_49BB_1331_11EB);
    x ^= x >> 31;
    (x >> 40) as f32 / (1u64 << 24) as f32
}

/// Left normal of the segment starting at `i` (map view: looking along the
/// path from the first to the last station).
pub(crate) fn segment_left(stations: &[Vec2], i: usize) -> Vec2 {
    let a = stations[i];
    let b = stations[(i + 1).min(stations.len() - 1)];
    let dir = (b - a).normalize_or_zero();
    Vec2::new(-dir.y, dir.x)
}

/// Band width at an arc position: three seeded harmonics, ±`noise` of the
/// base width (0 noise = constant).
pub(crate) fn wave(arc: f32, p1: f32, p2: f32, p3: f32, noise: f32, base: f32) -> f32 {
    let w = 0.5 * (arc * 0.11 + p1).sin()
        + 0.3 * (arc * 0.27 + p2).sin()
        + 0.2 * (arc * 0.53 + p3).sin();
    base * (1.0 + noise.clamp(0.0, 1.0) * w)
}

/// One column of a [`CliffProfile::Columnar`] face. Every parameter is
/// hash-derived from `(seed, index)` — deterministic, and discontinuous at
/// the column boundary (the 1-texel jump renders as the dark slit between
/// columns).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Column {
    /// Arc range covered by the column (meters along the crest polyline).
    pub start: f32,
    pub end: f32,
    /// Plan-view crest offset along the drop axis as a FRACTION of the local
    /// band width: positive = recessed (crest behind the path, the notch
    /// bites into the plateau edge), negative = protruding front (the face
    /// starts at the path and eats into the lower ground).
    pub off: f32,
    /// Crest height jitter (meters) — the chunky, bouldery brow.
    pub jit: f32,
    /// Piecewise-linear facet kinks (`s` at the 0.42 / 0.72 band fractions):
    /// the straight-cut slab breaks, varied per column.
    pub k1: f32,
    pub k2: f32,
}

impl Column {
    pub fn new(seed: u64, index: u64, start: f32, end: f32) -> Self {
        Self {
            start,
            end,
            off: (hash01(seed, index, 101) - 0.5) * 0.56,
            jit: (hash01(seed, index, 211) - 0.5) * 1.6,
            k1: 0.28 + hash01(seed, index, 307) * 0.16,
            k2: 0.62 + hash01(seed, index, 409) * 0.16,
        }
    }
}

/// Post-carve cliff regions — the INTELLIGENT trigger. A raw slope scan
/// alone cannot tell a cliff from a spurious bump: any single 1-texel jump
/// would pass. So `build` runs a pipeline with neighborhood awareness:
///
/// 1. raw scan (neighbor drop steeper than `cliff-angle`);
/// 2. morphological opening (erode + dilate, kills 1-texel speckle);
/// 3. connected-component labeling (deterministic BFS);
/// 4. per-component stats (area, drop, bbox extent) and a FILTER: a
///    component is a cliff only if area ≥ `min_area` m², extent ≥
///    `min_extent` m, and the drop (own range or the range of its ~4 m
///    context) ≥ `min_drop` m — terraced riser lines carry zero internal
///    drop, their context carries the real one;
/// 5. two layers: **core** (accepted components) and **dilated** (core grown
///    by `margin_texels` — the query layer with the margin baked in, so
///    grass/spawners skip the wall AND its verge).
///
/// Consumers: the mesh bakes `factor()` into vertex color alpha (the wall
/// shader multiplies its triplanar gate by it — spurious bumps get 0), the
/// splat paints solid stone over the core, sharpen only terraces the core,
/// the LOD peak-preserving snaps only inside regions, grass avoids the
/// dilated layer. Built once over the FINAL field.
#[derive(Debug, Clone, Default, bevy::ecs::prelude::Resource)]
pub struct CliffMask {
    /// Accepted cliff components (the filter output).
    core: Vec<u64>,
    /// Core dilated by the margin — the public query layer.
    bits: Vec<u64>,
    /// Authored talus aprons (rasterized from the carve registry) — painted
    /// as gravel by the splat, avoided by grass/spawners via `bits`.
    talus: Vec<u64>,
    /// Wall space per texel of the public layer, `0..=255`: the height
    /// fraction inside the local drop window (0 = brow, 255 = toe, 128 =
    /// neutral). Baked into vertex color R by the mesh builder — the wall
    /// shader reads it for the weathering gradient, the toe contact shadow
    /// and the streak/moss distribution.
    wall: Vec<u8>,
    width: usize,
    depth: usize,
    world_size: f32,
}

/// Raw steep scan of `grid` (neighbor drop steeper than `angle_deg`).
fn scan_steep(grid: &BrushGrid, angle_deg: f32, bits: &mut [u64]) {
    let (width, depth) = (grid.width(), grid.depth());
    bits.iter_mut().for_each(|w| *w = 0);
    if !angle_deg.is_finite() || angle_deg <= 0.0 {
        return;
    }
    let limit = angle_deg.to_radians().tan() * grid.texel();
    for z in 0..depth {
        for x in 0..width {
            let h = grid.cell_height(x, z);
            let steep_x = x + 1 < width && (grid.cell_height(x + 1, z) - h).abs() > limit;
            let steep_z = z + 1 < depth && (grid.cell_height(x, z + 1) - h).abs() > limit;
            if steep_x || steep_z {
                set_bit(bits, width, x, z);
            }
        }
    }
}

fn get_bit(bits: &[u64], width: usize, x: usize, z: usize) -> bool {
    let idx = z * width + x;
    bits[idx / 64] & (1 << (idx % 64)) != 0
}

fn set_bit(bits: &mut [u64], width: usize, x: usize, z: usize) {
    let idx = z * width + x;
    bits[idx / 64] |= 1 << (idx % 64);
}

/// One dilation pass (4-neighborhood + self) over a bitset.
fn dilate_once(bits: &mut [u64], width: usize, depth: usize) {
    let src = bits.to_vec();
    for z in 0..depth {
        for x in 0..width {
            if get_bit(&src, width, x, z) {
                continue;
            }
            let any = (x > 0 && get_bit(&src, width, x - 1, z))
                || (x + 1 < width && get_bit(&src, width, x + 1, z))
                || (z > 0 && get_bit(&src, width, x, z - 1))
                || (z + 1 < depth && get_bit(&src, width, x, z + 1));
            if any {
                set_bit(bits, width, x, z);
            }
        }
    }
}

/// One erosion pass: keep a texel only if it and its 4 neighbors are set —
/// removes 1-texel speckle before labeling. Out-of-bounds neighbors count as
/// clear (border texels erode), which is what makes the opening eat thin
/// needles.
fn erode_once(bits: &mut [u64], width: usize, depth: usize) {
    let src = bits.to_vec();
    let at = |x: isize, z: isize| -> bool {
        x >= 0
            && z >= 0
            && (x as usize) < width
            && (z as usize) < depth
            && get_bit(&src, width, x as usize, z as usize)
    };
    for z in 0..depth {
        for x in 0..width {
            let (xi, zi) = (x as isize, z as isize);
            let keep =
                at(xi, zi) && at(xi - 1, zi) && at(xi + 1, zi) && at(xi, zi - 1) && at(xi, zi + 1);
            if !keep {
                let idx = z * width + x;
                bits[idx / 64] &= !(1 << (idx % 64));
            }
        }
    }
}

impl CliffMask {
    /// Full pipeline over `grid` with the filter thresholds of the spec.
    pub fn build(grid: &BrushGrid, spec: &super::spec::TerrainSpec) -> Self {
        Self::build_with(
            grid,
            spec.cliff_angle,
            spec.cliff_min_area,
            spec.cliff_min_drop,
            spec.cliff_min_extent,
        )
    }

    /// Pipeline with explicit thresholds.
    pub fn build_with(
        grid: &BrushGrid,
        angle_deg: f32,
        min_area: f32,
        min_drop: f32,
        min_extent: f32,
    ) -> Self {
        let (width, depth) = (grid.width(), grid.depth());
        let mut mask = Self {
            core: vec![0; width.saturating_mul(depth).div_ceil(64)],
            bits: vec![0; width.saturating_mul(depth).div_ceil(64)],
            talus: vec![0; width.saturating_mul(depth).div_ceil(64)],
            wall: vec![WALL_NEUTRAL; width.saturating_mul(depth)],
            width,
            depth,
            world_size: grid.world_size(),
        };
        // 1. Raw scan + 2. opening.
        let mut raw = vec![0u64; width.saturating_mul(depth).div_ceil(64)];
        scan_steep(grid, angle_deg, &mut raw);
        erode_once(&mut raw, width, depth);
        dilate_once(&mut raw, width, depth);

        // 3+4. Connected components with stats + the region filter → core.
        Self::label_into_core(
            grid, &raw, width, depth, min_area, min_drop, min_extent, &mut mask,
        );
        // 5. Dilated query layer.
        mask.bits = mask.core.clone();
        for _ in 0..CLIFF_MASK_MARGIN_TEXELS {
            dilate_once(&mut mask.bits, width, depth);
        }
        // 6. Wall space (weathering gradient input for the wall shader).
        mask.compute_wall(grid, min_drop);
        mask
    }

    /// Height fraction inside the local drop per marked texel: a min/max
    /// window around each texel of the public layer spans brow→toe on a
    /// wall, so `(h − min) / (max − min)` reads "how far down the wall am
    /// I". Flat context (range under half the region's min drop — terrace
    /// treads, gentle verge) stays neutral.
    fn compute_wall(&mut self, grid: &BrushGrid, min_drop: f32) {
        let (width, depth) = (self.width, self.depth);
        let floor_drop = min_drop.max(0.5) * 0.5;
        for z in 0..depth {
            for x in 0..width {
                if !get_bit(&self.bits, width, x, z) {
                    continue;
                }
                let x0 = x.saturating_sub(WALL_WINDOW_TEXELS);
                let x1 = (x + WALL_WINDOW_TEXELS).min(width - 1);
                let z0 = z.saturating_sub(WALL_WINDOW_TEXELS);
                let z1 = (z + WALL_WINDOW_TEXELS).min(depth - 1);
                let (mut lo, mut hi) = (f32::INFINITY, f32::NEG_INFINITY);
                for zz in z0..=z1 {
                    for xx in x0..=x1 {
                        let h = grid.cell_height(xx, zz);
                        lo = lo.min(h);
                        hi = hi.max(h);
                    }
                }
                if hi - lo < floor_drop {
                    continue; // flat/terraced context keeps WALL_NEUTRAL
                }
                // 0 = brow (high), 1 = toe (low) — the shader's weathering
                // gradient and toe contact shadow read "how far down".
                let frac = ((hi - grid.cell_height(x, z)) / (hi - lo)).clamp(0.0, 1.0);
                self.wall[z * width + x] = (frac * 255.0).round() as u8;
            }
        }
    }

    /// Rasterizes authored talus aprons into the mask and rebuilds the
    /// public layer as `dilate(core ∪ talus)` — grass, spawners and the
    /// LOD gate inherit the apron exclusion for free. Talus texels keep a
    /// NEUTRAL wall value (the apron is a pile, not a face — no fake
    /// weathering gradient over it).
    /// Marks the footprint of the authored `<Cliff>` bands.
    ///
    /// The 2.5D system had to *find* its cliffs: the carve wrote a steeper
    /// patch of `u16` and `build_with` recovered the regions afterwards with
    /// erode/dilate/BFS and five tunable thresholds, because by then nothing
    /// remembered where the wall was.
    ///
    /// A band knows exactly. This marks its core and its apron directly, so
    /// the splat paints stone on the wall, the grass stays off it and the
    /// spawners keep their clearance — with no heuristic in the path and
    /// nothing for `cliff-min-area`/`-drop`/`-extent` to get wrong. The
    /// morphological scan stays for NATURAL steep ground coming out of the
    /// heightmap, which really does have to be discovered.
    pub fn add_authored_bands(&mut self, bands: &[super::voxel::CliffBand]) {
        if self.width < 2 || self.depth < 2 || self.world_size <= 0.0 {
            return;
        }
        let (width, depth) = (self.width, self.depth);
        let texel = self.texel_size();
        if texel <= 0.0 {
            return;
        }
        let half = self.world_size * 0.5;
        for band in bands {
            if band.stations.len() < 2 {
                continue;
            }
            let max_w = band.width.iter().copied().fold(0.0, f32::max);
            let max_run = band.talus_run_max();
            let reach = max_w + max_run + texel * 4.0;
            let (mut min_x, mut min_z) = (f32::INFINITY, f32::INFINITY);
            let (mut max_x, mut max_z) = (f32::NEG_INFINITY, f32::NEG_INFINITY);
            for st in &band.stations {
                min_x = min_x.min(st.x);
                min_z = min_z.min(st.y);
                max_x = max_x.max(st.x);
                max_z = max_z.max(st.y);
            }
            let clampx = |v: f32| v.clamp(0.0, (width - 1) as f32) as usize;
            let clampz = |v: f32| v.clamp(0.0, (depth - 1) as f32) as usize;
            let x0 = clampx(((min_x - reach) + half) / texel);
            let x1 = clampx((((max_x + reach) + half) / texel).ceil());
            let z0 = clampz(((min_z - reach) + half) / texel);
            let z1 = clampz((((max_z + reach) + half) / texel).ceil());

            for z in z0..=z1 {
                for x in x0..=x1 {
                    let p = Vec2::new(x as f32 * texel - half, z as f32 * texel - half);
                    let Some(hit) = nearest_on_path(&band.stations, p) else {
                        continue;
                    };
                    let n = band.drop_normal[hit.segment.min(band.drop_normal.len() - 1)];
                    let d = (p - hit.point).dot(n);
                    if d < -texel {
                        continue; // behind the crest: the plateau is not wall
                    }
                    let w = station_lerp(&band.width, &hit);
                    if d <= w {
                        set_bit(&mut self.core, width, x, z);
                        // Wall space straight from the geometry: 0 at the
                        // brow, 255 at the toe. The 2.5D path had to estimate
                        // this from a +/-8 texel min/max window over the
                        // finished heights.
                        let frac = (d / w.max(1e-3)).clamp(0.0, 1.0);
                        self.wall[z * width + x] = (frac * 255.0).round() as u8;
                    } else if max_run > 0.0 && d < w + band.talus_run_at(&hit) {
                        set_bit(&mut self.talus, width, x, z);
                        self.wall[z * width + x] = WALL_NEUTRAL;
                    }
                }
            }
        }
        self.bits = self.core.clone();
        for i in 0..self.bits.len() {
            self.bits[i] |= self.talus[i];
        }
        for _ in 0..CLIFF_MASK_MARGIN_TEXELS {
            dilate_once(&mut self.bits, width, depth);
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn label_into_core(
        grid: &BrushGrid,
        raw: &[u64],
        width: usize,
        depth: usize,
        min_area: f32,
        min_drop: f32,
        min_extent: f32,
        mask: &mut Self,
    ) {
        let texel = grid.texel();
        let total = width * depth;
        let mut visited = vec![false; total];
        let mut members: Vec<usize> = Vec::new();
        for start in 0..total {
            if visited[start] || !get_bit(raw, width, start % width, start / width) {
                continue;
            }
            visited[start] = true;
            members.clear();
            members.push(start);
            let mut cursor = 0;
            let (mut min_h, mut max_h) = (f32::INFINITY, f32::NEG_INFINITY);
            let (mut min_x, mut max_x) = (usize::MAX, 0usize);
            let (mut min_z, mut max_z) = (usize::MAX, 0usize);
            while cursor < members.len() {
                let idx = members[cursor];
                cursor += 1;
                let (x, z) = (idx % width, idx / width);
                let h = grid.cell_height(x, z);
                min_h = min_h.min(h);
                max_h = max_h.max(h);
                min_x = min_x.min(x);
                max_x = max_x.max(x);
                min_z = min_z.min(z);
                max_z = max_z.max(z);
                for (nx, nz) in [
                    (x.wrapping_sub(1), z),
                    (x + 1, z),
                    (x, z.wrapping_sub(1)),
                    (x, z + 1),
                ] {
                    if nx < width && nz < depth {
                        let n = nz * width + nx;
                        if !visited[n] && get_bit(raw, width, nx, nz) {
                            visited[n] = true;
                            members.push(n);
                        }
                    }
                }
            }
            let extent = (max_x - min_x).max(max_z - min_z) as f32 * texel;
            // Drop: the component's OWN range OR the range of its context
            // (bbox + ~4 m apron). Terraced fields are riser LINES with zero
            // internal drop — the context (the treads around them) carries
            // the real elevation change.
            let apron = (4.0 / texel).ceil() as usize;
            let cx0 = min_x.saturating_sub(apron);
            let cx1 = (max_x + apron).min(width - 1);
            let cz0 = min_z.saturating_sub(apron);
            let cz1 = (max_z + apron).min(depth - 1);
            let mut ctx_min = f32::INFINITY;
            for z in cz0..=cz1 {
                for x in cx0..=cx1 {
                    ctx_min = ctx_min.min(grid.cell_height(x, z));
                }
            }
            let accept = members.len() as f32 * texel * texel >= min_area
                && extent >= min_extent
                && (max_h - min_h >= min_drop || max_h - ctx_min >= min_drop);
            #[cfg(test)]
            if std::env::var("CLIFF_DEBUG").is_ok() {
                eprintln!(
                    "comp: texels {} area {:.0} m² extent {:.0} m own_drop {:.2} ctx_drop {:.2} → {}",
                    members.len(),
                    members.len() as f32 * texel * texel,
                    extent,
                    max_h - min_h,
                    max_h - ctx_min,
                    accept
                );
            }
            if accept {
                for &idx in &members {
                    set_bit(&mut mask.core, width, idx % width, idx / width);
                }
            }
        }
    }

    /// Texel `(x, z)` is INSIDE an accepted cliff component (no margin).
    pub fn is_core(&self, x: usize, z: usize) -> bool {
        if x >= self.width || z >= self.depth {
            return false;
        }
        get_bit(&self.core, self.width, x, z)
    }

    /// Texel `(x, z)` is cliff terrain or within the margin of one.
    pub fn is_cliff(&self, x: usize, z: usize) -> bool {
        if x >= self.width || z >= self.depth {
            return false;
        }
        get_bit(&self.bits, self.width, x, z)
    }

    /// Texel coordinates of a world position.
    fn texel_of(&self, p: Vec2) -> (f32, f32) {
        let half = self.world_size * 0.5;
        let step_x = self.world_size / (self.width - 1).max(1) as f32;
        let step_z = self.world_size / (self.depth - 1).max(1) as f32;
        ((p.x + half) / step_x, (p.y + half) / step_z)
    }

    /// World-space query — INSIDE an accepted component (no margin).
    pub fn is_core_at(&self, p: Vec2) -> bool {
        if self.width < 2 || self.world_size <= 0.0 {
            return false;
        }
        let (fx, fz) = self.texel_of(p);
        let x = fx.round().clamp(0.0, (self.width - 1) as f32) as usize;
        let z = fz.round().clamp(0.0, (self.depth - 1) as f32) as usize;
        self.is_core(x, z)
    }

    /// World-space query — the texel nearest to `p`, dilated layer.
    pub fn is_cliff_at(&self, p: Vec2) -> bool {
        if self.width < 2 || self.world_size <= 0.0 {
            return false;
        }
        let (fx, fz) = self.texel_of(p);
        let x = fx.round().clamp(0.0, (self.width - 1) as f32) as usize;
        let z = fz.round().clamp(0.0, (self.depth - 1) as f32) as usize;
        self.is_cliff(x, z)
    }

    /// World-space size of one mask texel (vertex convention of `texel_of`).
    pub fn texel_size(&self) -> f32 {
        if self.width < 2 || self.depth < 2 || self.world_size <= 0.0 {
            return 0.0;
        }
        self.world_size / (self.width - 1).max(1) as f32
    }

    /// Wall space at a world position, `0.0` (brow) → `1.0` (toe); `0.5`
    /// off-mask or on flat context. The mesh builder bakes this into vertex
    /// color R for the wall shader.
    pub fn wall_at(&self, p: Vec2) -> f32 {
        if self.wall.len() != self.width * self.depth || self.width < 2 {
            return 0.5;
        }
        let (fx, fz) = self.texel_of(p);
        let x = fx.round().clamp(0.0, (self.width - 1) as f32) as usize;
        let z = fz.round().clamp(0.0, (self.depth - 1) as f32) as usize;
        self.wall[z * self.width + x] as f32 / 255.0
    }

    /// Texel `(x, z)` is inside an authored talus apron.
    pub fn is_talus(&self, x: usize, z: usize) -> bool {
        if x >= self.width || z >= self.depth || self.talus.len() != self.core.len() {
            return false;
        }
        get_bit(&self.talus, self.width, x, z)
    }

    /// World-space query — inside an authored talus apron (the splat paints
    /// gravel over it).
    pub fn is_talus_at(&self, p: Vec2) -> bool {
        if self.width < 2 || self.world_size <= 0.0 {
            return false;
        }
        let (fx, fz) = self.texel_of(p);
        let x = fx.round().clamp(0.0, (self.width - 1) as f32) as usize;
        let z = fz.round().clamp(0.0, (self.depth - 1) as f32) as usize;
        self.is_talus(x, z)
    }

    /// World-space proximity query — cliff terrain (the dilated layer) within
    /// `meters` of `p`? Scans the texel window around the position, so spawn
    /// gates can keep props a REAL distance off the wall instead of the fixed
    /// 2-texels bake of `is_cliff_at`. Non-positive `meters` degenerates to
    /// `is_cliff_at`; the radius is capped so a pathological margin on a fine
    /// mask cannot stall the placement pass.
    pub fn is_cliff_within(&self, p: Vec2, meters: f32) -> bool {
        if self.width < 2 || self.depth < 2 || self.world_size <= 0.0 {
            return false;
        }
        let step_x = self.world_size / (self.width - 1).max(1) as f32;
        let step_z = self.world_size / (self.depth - 1).max(1) as f32;
        let (fx, fz) = self.texel_of(p);
        let r = (meters / step_x.min(step_z)).ceil().clamp(0.0, 128.0) as i64;
        let (cx, cz) = (fx.round() as i64, fz.round() as i64);
        let half = self.world_size * 0.5;
        // Metade-largura do texel: a distância mede ao RETÂNGULO do texel,
        // não ao seu centro — "a 2 m do cliff" mede à superfície da parede,
        // e o texel mais próximo conta sempre (meters 0 degenera para
        // `is_cliff_at`).
        let (hx, hz) = (step_x * 0.5, step_z * 0.5);
        let m2 = meters * meters;
        for dz in -r..=r {
            let z = cz + dz;
            if z < 0 || z as usize >= self.depth {
                continue;
            }
            let oz = ((z as f32 * step_z - half - p.y).abs() - hz).max(0.0);
            for dx in -r..=r {
                let x = cx + dx;
                if x < 0 || x as usize >= self.width {
                    continue;
                }
                let ox = ((x as f32 * step_x - half - p.x).abs() - hx).max(0.0);
                if ox * ox + oz * oz > m2 {
                    continue;
                }
                if get_bit(&self.bits, self.width, x as usize, z as usize) {
                    return true;
                }
            }
        }
        false
    }

    /// Bilinear cliff factor `0..1` at a world position over the DILATED
    /// layer — the soft edge baked into vertex color alpha. Off-mask
    /// terrain reads 0, region interior 1, the margin blends.
    pub fn factor(&self, p: Vec2) -> f32 {
        if self.width < 2 || self.depth < 2 || self.world_size <= 0.0 {
            return 0.0;
        }
        let (fx, fz) = self.texel_of(p);
        let x0 = fx.floor().clamp(0.0, (self.width - 1) as f32) as usize;
        let z0 = fz.floor().clamp(0.0, (self.depth - 1) as f32) as usize;
        let x1 = (x0 + 1).min(self.width - 1);
        let z1 = (z0 + 1).min(self.depth - 1);
        let tx = (fx - x0 as f32).clamp(0.0, 1.0);
        let tz = (fz - z0 as f32).clamp(0.0, 1.0);
        let v = |x: usize, z: usize| {
            if get_bit(&self.bits, self.width, x, z) {
                1.0
            } else {
                0.0
            }
        };
        let top = v(x0, z0) * (1.0 - tx) + v(x1, z0) * tx;
        let bot = v(x0, z1) * (1.0 - tx) + v(x1, z1) * tx;
        top * (1.0 - tz) + bot * tz
    }

    /// Number of marked texels in the dilated layer (debug/stats).
    pub fn marked(&self) -> usize {
        self.bits.iter().map(|w| w.count_ones() as usize).sum()
    }
}

/// Opt-in sharpen pass (`<Terrain sharpen="1">`): rewrites smooth steep
/// ramps of the FINAL field into terraced cliff bands. Texels flatter than
/// `sharpen_angle` are untouched; steep ones quantize to ~3 m treads whose
/// band edges dither with seeded noise (no machine-ruled contour lines).
/// Sub-water texels are skipped so lake/river bowls stay glassy, roads
/// never trigger (their grade is limited below the default angle anyway), and
/// only texels inside ACCEPTED cliff components of the [`CliffMask`] are
/// terraced — spurious ramps in open ground keep their smooth shape, and the
/// bands of authored `<Cliff>` walls keep their own profiles (never
/// re-quantized into stairs).
///
/// The journal owner `"sharpen"` keeps revert + re-carve idempotent.
/// Deterministic: the dither hashes texel position + `sharpen-seed`
/// (`0` derives from the terrain `seed`), never RNG.
pub fn sharpen_terrain(
    grid: &mut BrushGrid,
    spec: &super::spec::TerrainSpec,
    water: &[super::water::WaterBody],
    mask: &CliffMask,
) -> usize {
    let angle = if spec.sharpen_angle.is_finite() && spec.sharpen_angle > 0.0 {
        spec.sharpen_angle
    } else {
        super::spec::DEFAULT_SHARPEN_ANGLE
    };
    let limit = angle.to_radians().tan() * grid.texel();
    let seed = if spec.sharpen_seed != 0 {
        spec.sharpen_seed
    } else {
        spec.seed ^ 0xA11CE
    };

    let (width, depth) = (grid.width(), grid.depth());
    // Authored cliff bands are already shaped by their own profile — the
    // auto-terracer must not re-quantize them into stairs.
    let carved = vec![0u64; width.saturating_mul(depth).div_ceil(64)];
    grid.begin_stroke("sharpen");
    for z in 0..depth {
        for x in 0..width {
            let h = grid.cell_height(x, z);
            let steep_x = x + 1 < width && (grid.cell_height(x + 1, z) - h).abs() > limit;
            let steep_z = z + 1 < depth && (grid.cell_height(x, z + 1) - h).abs() > limit;
            if !steep_x && !steep_z {
                continue;
            }
            if get_bit(&carved, width, x, z) {
                continue;
            }
            let p = grid.cell_center(x, z);
            // Underwater ground keeps its carve: terraced bowls would
            // staircase through the water mirror.
            if water.iter().any(|w| w.contains(p) && h < w.water_y) {
                continue;
            }
            // Region gate: only terrace INSIDE accepted cliff components —
            // a spurious steep ramp in open ground keeps its smooth shape.
            if !mask.is_core(x, z) {
                continue;
            }
            let dither = (hash01(seed, p.x.to_bits() as u64, p.y.to_bits() as u64) - 0.5) * 0.6;
            let q = (h / TERRACE_TREAD + dither).floor() * TERRACE_TREAD;
            grid.set_cell_height(x, z, q);
        }
    }
    grid.commit_stroke()
}

/// Marcador: as cores deste mesh de caixa voxel já levaram o bake da máscara
/// de cliff (canal R = wall space, A = fator de região).
#[derive(Debug, bevy::prelude::Component)]
pub struct CliffBaked;

/// Assa a máscara regional nas vertex colors de UM mesh de caixa voxel:
/// R = wall space ([`CliffMask::wall_at`], 0 = brow → 1 = pé, 0.5 neutro) e
/// A = fator de região ([`CliffMask::factor`], borda suave da camada
/// dilatada) — o contrato que o `chunk.wgsl` lê para o gate do triplanar, a
/// meteorização brow→pé, o AO de contacto e a distribuição de
/// escorrimentos/musgo. Só o material de LAYERS usa estes canais: no caminho
/// do tint as cores SÃO o tint e valores de wall-space escureceriam cada
/// voxel (o chamador filtra pelo tipo de material).
///
/// `origin` é o XZ de MUNDO do canto mínimo da caixa (as posições do mesh
/// são relativas à caixa; o `Transform` da entidade carrega o offset).
/// Devolve quantos vértices foram escritos.
pub fn bake_cliff_colors(mask: &CliffMask, origin: Vec2, mesh: &mut bevy::render::mesh::Mesh) -> usize {
    use bevy::render::mesh::VertexAttributeValues;
    // Primeiro lê as posições (borrow imutável) e coleta o XZ de mundo — só
    // depois se pode pedir o attribute MUTÁVEL das cores.
    let mut xz: Vec<Vec2> = Vec::new();
    if let Some(VertexAttributeValues::Float32x3(pos)) = mesh.attribute(bevy::render::mesh::Mesh::ATTRIBUTE_POSITION) {
        xz.reserve(pos.len());
        for p in pos.iter() {
            xz.push(Vec2::new(origin.x + p[0], origin.y + p[2]));
        }
    } else {
        return 0;
    }
    let Some(VertexAttributeValues::Float32x4(colors)) = mesh.attribute_mut(bevy::render::mesh::Mesh::ATTRIBUTE_COLOR) else {
        return 0;
    };
    let mut baked = 0;
    for (vertex, p) in colors.iter_mut().zip(xz.iter()) {
        vertex[0] = mask.wall_at(*p);
        vertex[3] = mask.factor(*p);
        baked += 1;
    }
    baked
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cliff_mask_marks_steep_drops_only() {
        let mut grid =
            BrushGrid::new(vec![0u16; 128 * 128], 128, 128, 128.0, 50.0, 1.0).expect("grid");
        grid.begin_stroke("field");
        for z in 0..128 {
            for x in 0..128 {
                let cx = grid.cell_center(x, z).x;
                let h = if cx > 30.0 && cx <= 50.0 {
                    30.0 - (cx - 30.0) * 1.43 // ~55° face, ~20 texels wide
                } else if (cx - 62.5).abs() < 1.0 && (grid.cell_center(x, 70).y).abs() < 3.0 {
                    30.0 // SHORT needle (2×4 texels, ~8 m²) — speckle
                } else {
                    10.0 + cx.clamp(0.0, 40.0) * (12.0 / 40.0) // gentle ramp
                };
                grid.set_cell_height(x, z, h);
            }
        }
        grid.commit_stroke();
        let mask = CliffMask::build_with(&grid, 50.0, 120.0, 4.0, 8.0);
        // The wide face survives opening + the region filter (interior texels).
        assert!(
            mask.is_core(95, 64) && mask.is_core(103, 64) && mask.is_core(110, 64),
            "the wide face is a cliff region"
        );
        // A short needle is speckle: the AREA filter removes it (the opening
        // was removed — it would also erase terraced risers, which are
        // legitimate 1-texel features).
        assert!(
            !mask.is_core(126, 70) && !mask.is_cliff(126, 70),
            "short needle is speckle, not a cliff"
        );
        // Gentle ramp texels far from the wall are clear.
        assert!(!mask.is_cliff(80, 20), "12 m over 40 m is ~17°, not cliff");
        // World-space query maps back to the face region.
        assert!(mask.is_cliff_at(Vec2::new(40.0, 0.0)));
        assert!(!mask.is_cliff_at(Vec2::new(-30.0, 0.0)));
        // Angle 0 disables the scan entirely.
        assert_eq!(
            CliffMask::build_with(&grid, 0.0, 120.0, 4.0, 8.0).marked(),
            0
        );
    }

    /// `is_cliff_within` measures real meters off the wall — the spawn-gate
    /// margin — instead of the fixed 2-texels of `is_cliff_at`.
    #[test]
    fn test_is_cliff_within_measures_real_distance() {
        let mut grid =
            BrushGrid::new(vec![0u16; 128 * 128], 128, 128, 128.0, 50.0, 1.0).expect("grid");
        grid.begin_stroke("field");
        for z in 0..128 {
            for x in 0..128 {
                let cx = grid.cell_center(x, z).x;
                let h = if cx > 30.0 && cx <= 50.0 {
                    30.0 - (cx - 30.0) * 1.43 // ~55° face, ~20 texels wide
                } else {
                    10.0 + cx.clamp(0.0, 40.0) * (12.0 / 40.0) // gentle ramp
                };
                grid.set_cell_height(x, z, h);
            }
        }
        grid.commit_stroke();
        let mask = CliffMask::build_with(&grid, 50.0, 120.0, 4.0, 8.0);
        assert!(mask.marked() > 0, "the wide face registers as cliff");
        assert!(
            (mask.texel_size() - 128.0 / 127.0).abs() < 1e-3,
            "texel size follows the vertex convention, got {}",
            mask.texel_size()
        );
        // On the face every margin hits; zero meters matches `is_cliff_at`.
        let face = Vec2::new(40.0, 0.0);
        assert!(mask.is_cliff_within(face, 2.0));
        assert_eq!(mask.is_cliff_within(face, 0.0), mask.is_cliff_at(face));
        // Walk +x past the toe and find where the 2 m query flips.
        let mut flip = None;
        let mut x = 52.0;
        while x < 90.0 {
            if !mask.is_cliff_within(Vec2::new(x, 0.0), 2.0) {
                flip = Some(x);
                break;
            }
            x += 0.25;
        }
        let flip = flip.expect("the wall ends; the 2 m ring clears");
        assert!(!mask.is_cliff_at(Vec2::new(flip, 0.0)), "flip is off-mask");
        // 1.5 m back toward the wall must be inside the 2 m ring again.
        assert!(
            mask.is_cliff_within(Vec2::new(flip - 1.5, 0.0), 2.0),
            "just past the flip the wall is within 2 m"
        );
        // Margins scale: far ground only enters wide rings.
        let far = Vec2::new(-30.0, 0.0); // ~60 m from the face
        assert!(!mask.is_cliff_within(far, 40.0));
        assert!(mask.is_cliff_within(far, 200.0));
    }

    /// A steep but SMALL bump (under the min area) is filtered out: neither
    /// the mask marks it nor does the sharpen pass terrace it — the spurious
    /// "cliff in the middle of nowhere" the region filter exists for.
    #[test]
    fn test_small_steep_bump_is_filtered_everywhere() {
        let mut grid =
            BrushGrid::new(vec![0u16; 128 * 128], 128, 128, 128.0, 50.0, 1.0).expect("grid");
        grid.begin_stroke("field");
        for z in 0..128 {
            for x in 0..128 {
                grid.set_cell_height(x, z, 10.0);
            }
        }
        // A 3x3-texel, 20 m tall spike: steep everywhere, tiny area.
        for z in 63..=65 {
            for x in 63..=65 {
                grid.set_cell_height(x, z, 30.0);
            }
        }
        grid.commit_stroke();
        let before = grid.raw().to_vec();
        let spec = crate::terrain::TerrainSpec {
            sharpen: true,
            sharpen_angle: 35.0,
            sharpen_seed: 5,
            ..crate::terrain::TerrainSpec::default()
        };
        let mask = CliffMask::build(&grid, &spec);
        assert_eq!(mask.marked(), 0, "3x3 spike is speckle, not a cliff");
        let changed = sharpen_terrain(&mut grid, &spec, &[], &mask);
        assert_eq!(changed, 0, "sharpen respects the region gate");
        assert_eq!(grid.raw(), before);
    }

    /// The sharpen terraces a smooth 20 m ramp into ~3 m treads,
    /// leaves the flat plateau exact, and reverts byte-identically.
    #[test]
    fn test_sharpen_terraces_steep_ramps_only() {
        let mut grid =
            BrushGrid::new(vec![0u16; 128 * 128], 128, 128, 128.0, 50.0, 1.0).expect("grid");
        grid.begin_stroke("field");
        for z in 0..128 {
            for x in 0..128 {
                let cx = grid.cell_center(x, z).x;
                // Flat plateau up to x=0, then a 45° ramp for 20 m, then flat.
                let h = if cx <= 0.0 {
                    25.0
                } else {
                    25.0 - (cx.min(20.0)) * 1.0
                };
                grid.set_cell_height(x, z, h);
            }
        }
        grid.commit_stroke();
        let before = grid.raw().to_vec();
        let mut spec = crate::terrain::TerrainSpec::default();
        spec.sharpen = true;
        spec.sharpen_angle = 35.0;
        spec.sharpen_seed = 5;
        let pre_mask = CliffMask::build_with(&grid, 35.0, 120.0, 4.0, 8.0);
        let changed = sharpen_terrain(&mut grid, &spec, &[], &pre_mask);
        assert!(changed > 0, "the ramp is 45° — well over the trigger");
        // Plateau untouched (flat texels never trigger; 25 m is not a tread
        // multiple on purpose — it survives because nothing was steep).
        let plateau = grid.cell_height(20, 64);
        assert!(
            (plateau - 25.0).abs() < 0.01,
            "flat plateau keeps its height, got {plateau}"
        );
        // Ramp texels land on tread multiples (±dither never crosses a tread).
        for x in 70..80 {
            let h = grid.cell_height(x, 64);
            if h > 4.5 {
                let off = (h / TERRACE_TREAD) - (h / TERRACE_TREAD).floor();
                assert!(
                    off < 1e-3 || off > 0.999,
                    "ramp texel {x} sits mid-tread: {h}"
                );
            }
        }
        // Idempotency via the journal: revert → re-run lands the same bytes.
        assert!(grid.revert_last_stroke("sharpen"));
        assert_eq!(grid.raw(), before, "revert restores the field");
        let mask = CliffMask::build_with(&grid, 35.0, 120.0, 4.0, 8.0);
        sharpen_terrain(&mut grid, &spec, &[], &mask);
        // The pre-sharpen mask stays valid as the FINAL mask: sharpening
        // only adds steps inside its core (rebuilding over the terraced
        // field would fragment into riser slivers). The terraced walls
        // register through it.
        assert!(mask.marked() > 0, "terraced walls register as cliff");
    }

    /// Sub-water texels never terrace: lake bowls stay glassy.
    #[test]
    fn test_sharpen_skips_underwater_texels() {
        let mut grid =
            BrushGrid::new(vec![0u16; 128 * 128], 128, 128, 128.0, 50.0, 1.0).expect("grid");
        grid.begin_stroke("field");
        for z in 0..128 {
            for x in 0..128 {
                let cx = grid.cell_center(x, z).x;
                let h = if cx <= 0.0 { 25.0 } else { 25.0 - cx.min(20.0) };
                grid.set_cell_height(x, z, h);
            }
        }
        grid.commit_stroke();
        // A water body covering the whole ramp with its mirror above ground.
        let water = vec![super::super::water::WaterBody {
            kind: super::super::water::WaterKind::Lake,
            at: Vec2::new(10.0, 0.0),
            radius: 300.0,
            carve_radius: 300.0,
            water_y: 30.0,
            mirror_reach: 1.0,
            shape: super::super::water::LakeShape::default(),
            stations: Vec::new(),
            surface_y: Vec::new(),
            water_width: 0.0,
            half_width: Vec::new(),
            depths: Vec::new(),
            cascades: Vec::new(),
        }];
        let mut spec = crate::terrain::TerrainSpec::default();
        spec.sharpen = true;
        spec.sharpen_angle = 35.0;
        spec.sharpen_seed = 5;
        let pre_mask = CliffMask::build(&grid, &spec);
        let changed = sharpen_terrain(&mut grid, &spec, &water, &pre_mask);
        assert_eq!(changed, 0, "every steep texel is underwater here");
    }

    /// O bake escreve wall space no R e fator de região no A, e só onde a
    /// máscara tem região — fora dela fica neutro (0.5) / zero.
    #[test]
    fn test_bake_cliff_colors_writes_wall_and_region() {
        let mut grid =
            BrushGrid::new(vec![0u16; 128 * 128], 128, 128, 128.0, 50.0, 1.0).expect("grid");
        grid.begin_stroke("field");
        for z in 0..128 {
            for x in 0..128 {
                let cx = grid.cell_center(x, z).x;
                let h = if cx > 30.0 && cx <= 50.0 {
                    30.0 - (cx - 30.0) * 1.43 // ~55° face
                } else {
                    10.0 + cx.clamp(0.0, 40.0) * (12.0 / 40.0)
                };
                grid.set_cell_height(x, z, h);
            }
        }
        grid.commit_stroke();
        let mask = CliffMask::build_with(&grid, 50.0, 120.0, 4.0, 8.0);

        let mut mesh = bevy::render::mesh::Mesh::new(
            bevy::render::mesh::PrimitiveTopology::TriangleList,
            bevy::asset::RenderAssetUsages::MAIN_WORLD,
        );
        // Posições = mundo (origin ZERO): um vértice na face do cliff, um em
        // campo aberto.
        mesh.insert_attribute(
            bevy::render::mesh::Mesh::ATTRIBUTE_POSITION,
            vec![[40.0, 15.0, 0.0], [-30.0, 15.0, 0.0]],
        );
        mesh.insert_attribute(
            bevy::render::mesh::Mesh::ATTRIBUTE_COLOR,
            vec![[0.5, 0.0, 0.0, 1.0]; 2],
        );

        let baked = bake_cliff_colors(&mask, Vec2::ZERO, &mut mesh);
        assert_eq!(baked, 2, "both vertices written");
        let bevy::render::mesh::VertexAttributeValues::Float32x4(colors) = mesh
            .attribute(bevy::render::mesh::Mesh::ATTRIBUTE_COLOR)
            .expect("colors present")
        else {
            panic!("colors must be Float32x4");
        };
        let colors = colors.clone();
        // Dentro da face: fator de região forte e wall space = o da máscara.
        assert!(
            colors[0][3] > 0.5,
            "inside the face: region factor {:.2}",
            colors[0][3]
        );
        assert!(
            (colors[0][0] - mask.wall_at(Vec2::new(40.0, 0.0))).abs() < 1e-6,
            "R carries the mask wall space"
        );
        // Campo aberto: sem região; o R leva o wall space da MÁSCARA
        // (qualquer que seja fora da região — o shader só o lê × region).
        assert!(colors[1][3] <= 1e-4, "open ground: no region");
        assert!(
            (colors[1][0] - mask.wall_at(Vec2::new(-30.0, 0.0))).abs() < 1e-6,
            "R carries the mask wall space"
        );
    }
}
