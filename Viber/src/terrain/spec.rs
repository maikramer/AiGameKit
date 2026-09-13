//! Declarative terrain specification — the shared contract between the XML
//! parser ([`crate::recipes`]) and the runtime terrain plugin ([`crate::terrain::plugin`]).
//!
//! These types are pure data (no Bevy ECS): `recipes` fills them from XML
//! attributes, `spawn` inserts them as components, and the terrain plugin
//! consumes them to build chunk meshes. All lengths are meters, all angles
//! follow the Viber convention (translation/scale meters, euler degrees).

use bevy::color::Color;
use bevy::math::Vec2;

/// Default XZ world span of a `<Terrain>` (meters).
pub const DEFAULT_WORLD_SIZE: f32 = 256.0;
/// Default peak height of the terrain (meters).
pub const DEFAULT_MAX_HEIGHT: f32 = 50.0;
/// Default chunk XZ size (meters). The terrain grid is `ceil(world_size / chunk_size)²` chunks.
pub const DEFAULT_CHUNK_SIZE: f32 = 64.0;
/// Number of LOD levels (0 = full resolution, `levels - 1` = coarsest).
pub const DEFAULT_LEVELS: u8 = 3;
/// Chunks switch to the next LOD when `distance > chunk_size * LOD_DISTANCE_RATIO`.
pub const DEFAULT_LOD_DISTANCE_RATIO: f32 = 2.0;
/// Hysteresis factor applied when switching back to a finer LOD (prevents flicker at the boundary).
pub const DEFAULT_LOD_HYSTERESIS: f32 = 1.2;
/// Camera must move this far (meters, as a fraction of `chunk_size`) before LODs are re-evaluated.
pub const DEFAULT_LOD_RESELECT_DISTANCE: f32 = 6.0;
/// `0.0` = bilinear sampling, `1.0` = monotone Catmull-Rom (C1, no ringing).
pub const DEFAULT_HEIGHT_SMOOTHING: f32 = 1.0;
/// Collider heightfield resolution per chunk edge (0 disables collider generation).
pub const DEFAULT_COLLISION_RESOLUTION: u32 = 64;
/// Mesh vertices per chunk edge at LOD 0. The effective grid step is
/// `chunk_size / resolution` rounded to whole meters (the mesh builder works
/// on integer steps); values finer than 1 m/vertex clamp to 1.
pub const DEFAULT_RESOLUTION: u32 = 64;
/// Cliff trigger angle (degrees): raw terrain steeper than this slope counts
/// as cliff for the peak-preserving LOD and the wall shading.
pub const DEFAULT_CLIFF_ANGLE: f32 = 50.0;
/// Default trigger angle (degrees) of the opt-in sharpen pass.
pub const DEFAULT_SHARPEN_ANGLE: f32 = 35.0;
/// Region filter of the cliff mask: minimum component area (m²).
pub const DEFAULT_CLIFF_MIN_AREA: f32 = 120.0;
/// Region filter: minimum total drop inside the component (meters).
pub const DEFAULT_CLIFF_MIN_DROP: f32 = 4.0;
/// Region filter: minimum bbox extent of the component (meters).
pub const DEFAULT_CLIFF_MIN_EXTENT: f32 = 8.0;
/// Runoff streak strength on cliff walls (`cliff-streaks`): 0 = clean rock.
pub const DEFAULT_CLIFF_STREAKS: f32 = 0.5;
/// Procedural moss on cliff shoulders/ledges (`cliff-moss`): 0 = bare rock.
pub const DEFAULT_CLIFF_MOSS: f32 = 0.35;
/// Mesh chunks rebuilt per frame after the initial load (frame budget).
pub const DEFAULT_MAX_MESH_BUILDS_PER_FRAME: u32 = 4;
/// Chunks kept resident around the camera when the world does not author a
/// `render-distance`. `None` used to mean "draw everything", which on a 4 km
/// world is ~4 000 live chunk entities (and 15 625 when the heightmap file
/// widened the world behind the author's back) — every one of them paying
/// transform propagation, visibility and a draw call every frame. The budget
/// is a chunk *count* rather than a distance so it scales with `chunk_size`
/// instead of being tuned per world.
pub const DEFAULT_RESIDENT_CHUNK_BUDGET: f32 = 2048.0;
/// Sanity ceiling on the heightfield edge derived from a spec
/// (`chunk_rows() × samples per chunk edge`). A tiny `chunk-size` (e.g. 0.01)
/// passes the `> 0` attribute check but would otherwise ask the procedural
/// generator and the chunk spawner for a grid millions of samples per edge —
/// gigabyte allocations / abort. [`TerrainSpec::validate`] refuses such specs
/// with a clear error; [`TerrainSpec::heightfield_edge`] degrades gracefully.
pub const MAX_GRID_EDGE_VERTS: usize = 8192;

/// Declarative terrain description parsed from a `<Terrain>` tag.
#[derive(Debug, Clone, PartialEq)]
pub struct TerrainSpec {
    /// Path of the heightmap image (16-bit grayscale PNG preferred; 8-bit accepted).
    /// Relative paths resolve against the world XML directory. When `None`, a
    /// deterministic procedural heightfield is generated from [`TerrainSpec::seed`].
    pub heightmap: Option<String>,
    /// World span on X and Z (meters).
    pub world_size: f32,
    /// Height of a fully-white heightmap sample (meters).
    pub max_height: f32,
    /// `world-size` / `max-height` came from the world XML rather than from
    /// [`TerrainSpec::default`]. A heightmap file carries its own coverage
    /// metadata, but an authored value has to win over it: the XML is what the
    /// pads, lakes, rivers, roads and biome polygons were laid out against.
    pub extent_authored: bool,
    /// Chunk edge length (meters).
    pub chunk_size: f32,
    /// Number of LOD levels per chunk (minimum 1).
    pub levels: u8,
    /// LOD boundary factor: distance threshold is `chunk_size * lod_distance_ratio`.
    pub lod_distance_ratio: f32,
    /// Hysteresis factor for switching back to a finer LOD (must be >= 1.0 to have effect).
    pub lod_hysteresis: f32,
    /// Chunks farther than this from the camera (meters) are despawned. `None` = render everything.
    pub render_distance: Option<f32>,
    /// Height smoothing: `0.0` bilinear, `1.0` monotone Catmull-Rom.
    pub height_smoothing: f32,
    /// Interruptor de colisão de terreno: `0` desliga os colliders; qualquer
    /// outro valor liga. (Já NÃO define geometria: o collider é o trimesh
    /// transvoxel da coluna ao passo do LOD — ver `physics.rs`.)
    pub collision_resolution: u32,
    /// Célula voxel do LOD 0: o passo LOD-0 é `chunk_size / resolution`
    /// arredondado a metros inteiros (o ladder voxel duplica por nível —
    /// 1→2→4 m no default). Ver [`DEFAULT_RESOLUTION`].
    pub resolution: u32,
    /// Optional tiled diffuse texture applied over the whole terrain.
    /// Legacy path — when `layers` is non-empty the layer blend material
    /// replaces it (and the height/slope tint) entirely.
    pub texture: Option<String>,
    /// Texture tile size in meters; `0.0` = auto (keeps texel density constant across LODs).
    pub texture_tile_size: f32,
    /// Terrain layer blend (`layers="grass vale_grass dirt …"`): pool aliases
    /// (see [`crate::terrain::splat::DEFAULT_LAYERS`]) or raw texture paths,
    /// bound to the 12 shader slots in order. Empty keeps the legacy
    /// single-texture + height-tint path.
    pub layers: Vec<String>,
    /// Shore band width (meters): sand fades into the terrain textures over
    /// this distance outside the lake/river waterline (`shore-width`).
    pub shore_width: f32,
    /// Splat map texel size in meters; `0.0` = auto (`world_size/2048`,
    /// floor 1 m) (`splat-texel`).
    pub splat_texel: f32,
    /// Height/slope color tinting applied as vertex colors. Consumed by the
    /// LEGACY path only — with `layers` active the vertex colors carry just
    /// `base_color` and the splat map drives the ground look.
    pub tint: TerrainTint,
    /// Seed for the procedural heightfield (ignored when `heightmap` is set).
    pub seed: u64,
    /// Climas do chão que a paleta do splat usa. Não vem do XML: é declarado
    /// pelo próprio heightmap (bloco `biomes` do meta do `.ahgt`), porque o
    /// campo de biomas e o campo de alturas TÊM de ser autorados juntos —
    /// uma cunha de deserto sobre uma bacia húmida não é um mundo.
    pub biomes: crate::terrain::splat::BiomeField,
    /// Cliff trigger angle (degrees, `cliff-angle`): raw terrain steeper than
    /// this slope reads as cliff — peak-preserving LOD and wall shading.
    pub cliff_angle: f32,
    /// Opt-in sharpen pass (`sharpen`): rewrite smooth steep ramps of the
    /// final field into terraced cliff bands. Off by default — it changes
    /// heights, so colliders, spawner placement and quest geometry move.
    pub sharpen: bool,
    /// Ramps steeper than this angle (degrees, `sharpen-angle`) are terraced.
    pub sharpen_angle: f32,
    /// Seed for the sharpen dither (`sharpen-seed`); `0` derives from `seed`.
    pub sharpen_seed: u64,
    /// Region filter: a slope component is a cliff only above this area (m²,
    /// `cliff-min-area`).
    pub cliff_min_area: f32,
    /// Region filter: minimum total drop of the component (meters,
    /// `cliff-min-drop`).
    pub cliff_min_drop: f32,
    /// Region filter: minimum bbox extent of the component (meters,
    /// `cliff-min-extent`).
    pub cliff_min_extent: f32,
    /// Runoff streak strength on cliff walls (0..1, `cliff-streaks`) —
    /// dark vertical water stains below drainage points.
    pub cliff_streaks: f32,
    /// Procedural moss on cliff shoulders and ledges (0..1, `cliff-moss`).
    pub cliff_moss: f32,
}

impl Default for TerrainSpec {
    fn default() -> Self {
        Self {
            heightmap: None,
            world_size: DEFAULT_WORLD_SIZE,
            max_height: DEFAULT_MAX_HEIGHT,
            extent_authored: false,
            chunk_size: DEFAULT_CHUNK_SIZE,
            levels: DEFAULT_LEVELS,
            lod_distance_ratio: DEFAULT_LOD_DISTANCE_RATIO,
            lod_hysteresis: DEFAULT_LOD_HYSTERESIS,
            render_distance: None,
            height_smoothing: DEFAULT_HEIGHT_SMOOTHING,
            collision_resolution: DEFAULT_COLLISION_RESOLUTION,
            resolution: DEFAULT_RESOLUTION,
            texture: None,
            texture_tile_size: 0.0,
            layers: Vec::new(),
            shore_width: 5.0,
            splat_texel: 0.0,
            tint: TerrainTint::default(),
            seed: 0,
            biomes: crate::terrain::splat::BiomeField::default(),
            cliff_angle: DEFAULT_CLIFF_ANGLE,
            sharpen: false,
            sharpen_angle: DEFAULT_SHARPEN_ANGLE,
            sharpen_seed: 0,
            cliff_min_area: DEFAULT_CLIFF_MIN_AREA,
            cliff_min_drop: DEFAULT_CLIFF_MIN_DROP,
            cliff_min_extent: DEFAULT_CLIFF_MIN_EXTENT,
            cliff_streaks: DEFAULT_CLIFF_STREAKS,
            cliff_moss: DEFAULT_CLIFF_MOSS,
        }
    }
}

impl TerrainSpec {
    /// Number of chunks along each axis (at least 1).
    pub fn chunk_rows(&self) -> u32 {
        (self.world_size / self.chunk_size).ceil().max(1.0) as u32
    }

    /// Heightfield samples per world edge for `samples_per_chunk_edge` samples
    /// per chunk edge, capped at [`MAX_GRID_EDGE_VERTS`] with saturating
    /// arithmetic — a tiny `chunk-size` saturates `chunk_rows()` and must not
    /// overflow the product or explode the procedural allocation.
    pub fn heightfield_edge(&self, samples_per_chunk_edge: usize) -> usize {
        (self.chunk_rows() as usize)
            .saturating_mul(samples_per_chunk_edge.max(1))
            .clamp(1, MAX_GRID_EDGE_VERTS)
    }

    /// Parse-time sanity checks for the derived terrain geometry (shared by
    /// the XML parser and the runtime).
    ///
    /// # Errors
    /// Fails when the extent metrics are not finite/positive or when the
    /// derived heightfield edge exceeds [`MAX_GRID_EDGE_VERTS`] — i.e. the
    /// authored values themselves are unreasonable (raise `chunk-size` or
    /// lower `resolution`).
    pub fn validate(&self) -> Result<(), String> {
        if !self.world_size.is_finite() || self.world_size <= 0.0 {
            return Err(format!(
                "terrain world-size must be finite and positive, got {}",
                self.world_size
            ));
        }
        if !self.max_height.is_finite() || self.max_height <= 0.0 {
            return Err(format!(
                "terrain max-height must be finite and positive, got {}",
                self.max_height
            ));
        }
        if !self.chunk_size.is_finite() || self.chunk_size <= 0.0 {
            return Err(format!(
                "terrain chunk-size must be finite and positive, got {}",
                self.chunk_size
            ));
        }
        let edge = (self.chunk_rows() as usize).saturating_mul(self.resolution.max(1) as usize);
        if edge > MAX_GRID_EDGE_VERTS {
            return Err(format!(
                "terrain grid too dense: chunk_rows ({}) × resolution ({}) = {edge} samples \
                 per edge exceeds the cap of {MAX_GRID_EDGE_VERTS} — raise chunk-size or \
                 lower resolution",
                self.chunk_rows(),
                self.resolution
            ));
        }
        Ok(())
    }

    /// LOD distance threshold in meters for a chunk of this terrain.
    pub fn lod_distance(&self) -> f32 {
        self.chunk_size * self.lod_distance_ratio
    }

    /// Tint folded into chunk vertex colors: the full height/slope banding
    /// on the legacy single-texture path; with `layers` the splat blend
    /// replaces the banding entirely and the authored `base-color` is
    /// IGNORED — the vertex colors carry wall/region data for
    /// `terrain_chunk.wgsl` and the only global tint is the day/night
    /// `day_tint` uniform. The bootstrap and the LOD rebuilds must agree on
    /// this or neighboring chunks disagree at their shared border.
    pub fn chunk_tint(&self) -> crate::terrain::mesh::TintParams {
        let mut tint = crate::terrain::mesh::TintParams::from(&self.tint);
        if !self.layers.is_empty() {
            tint.height_blend_strength = 0.0;
        }
        tint
    }

    /// Radius (meters) beyond which chunks are culled.
    ///
    /// An authored `render-distance` wins. Otherwise the radius is the one
    /// that keeps [`DEFAULT_RESIDENT_CHUNK_BUDGET`] chunks inside the disc
    /// (`π r² = budget · chunk_size²`). Worlds smaller than that radius are
    /// unaffected — the whole field stays resident, as before.
    pub fn effective_render_distance(&self) -> f32 {
        if let Some(distance) = self.render_distance {
            return distance;
        }
        let chunk = self.chunk_size.max(1.0);
        (DEFAULT_RESIDENT_CHUNK_BUDGET / std::f32::consts::PI).sqrt() * chunk
    }

    /// Runtime override of the streaming/cull radius: `VIBER_RENDER_DISTANCE`
    /// (finite f32 > 0) wins over the authored `render-distance`. Applied once
    /// at the terrain bootstrap — a runtime knob for small-VRAM GPUs; headless
    /// `analyze` never runs the bootstrap and keeps reading the XML value.
    pub fn apply_env_render_distance(&mut self) {
        let Some(distance) = env_render_distance() else {
            return;
        };
        bevy::log::info!(
            "terrain: render-distance sobreposto por env: {distance} (XML tinha {})",
            self.render_distance
                .map_or("auto".to_string(), |authored| authored.to_string())
        );
        self.render_distance = Some(distance);
    }
}

/// `VIBER_RENDER_DISTANCE` value for [`TerrainSpec::apply_env_render_distance`]:
/// valid when set and parsing as a finite f32 > 0; anything else ("abc", "0",
/// negative, "inf", "NaN") is ignored so a typo never breaks a boot.
fn env_render_distance() -> Option<f32> {
    std::env::var("VIBER_RENDER_DISTANCE")
        .ok()
        .and_then(|raw| raw.parse::<f32>().ok())
        .filter(|distance| distance.is_finite() && *distance > 0.0)
}

/// Height/slope color tinting, ported from the VibeGame terrain shader
/// (`colorLow/Mid/High/Rock`, `snowHeight`, `slopeThreshold`) but evaluated
/// CPU-side into vertex colors — no custom WGSL needed.
#[derive(Debug, Clone, PartialEq)]
pub struct TerrainTint {
    /// Base color multiplied into every vertex.
    pub base_color: Color,
    /// Valley / low altitude color.
    pub color_low: Color,
    /// Mid altitude color.
    pub color_mid: Color,
    /// High altitude color.
    pub color_high: Color,
    /// Steep slope color (cliffs).
    pub color_rock: Color,
    /// Normalized altitude (0..1) above which the high color fades to snow white.
    pub snow_height: f32,
    /// Slope (0..1, 1 = vertical) at which the rock color fully takes over.
    pub slope_threshold: f32,
    /// Softness of the slope -> rock transition.
    pub slope_softness: f32,
    /// Blend strength between altitude bands (0..1).
    pub height_blend_strength: f32,
}

impl Default for TerrainTint {
    fn default() -> Self {
        Self {
            base_color: Color::srgb(1.0, 1.0, 1.0),
            color_low: Color::srgb(0.30, 0.42, 0.23),
            color_mid: Color::srgb(0.43, 0.53, 0.30),
            color_high: Color::srgb(0.55, 0.55, 0.48),
            color_rock: Color::srgb(0.42, 0.40, 0.38),
            snow_height: 0.75,
            slope_threshold: 0.55,
            slope_softness: 0.10,
            height_blend_strength: 0.35,
        }
    }
}

/// Declarative ground-flattening pad parsed from a `<TerrainPad>` tag.
///
/// Flattens the terrain inside a rounded rectangle (`at` center, `size` full
/// extents) to `height`. When [`TerrainPadSpec::height`] is `None` the height
/// is sampled at the pad center (`auto` mode) and written back after
/// application, so structures anchored to the pad always agree with the ground.
#[derive(Debug, Clone, PartialEq)]
pub struct TerrainPadSpec {
    /// Pad center in world XZ (written from the `at` attribute / `translation`).
    pub at: Vec2,
    /// Full extents of the flat core (meters).
    pub size: Vec2,
    /// Width of the smoothstep falloff ring around the core (meters).
    pub falloff: f32,
    /// Corner rounding radius of the flat core (meters).
    pub corner_radius: f32,
    /// Target height (meters); `None` = sample the terrain at the pad center.
    pub height: Option<f32>,
}

impl Default for TerrainPadSpec {
    fn default() -> Self {
        Self {
            at: Vec2::ZERO,
            size: Vec2::splat(10.0),
            falloff: 8.0,
            corner_radius: 4.0,
            height: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_spec_defaults_match_documented_values() {
        let spec = TerrainSpec::default();
        assert_eq!(spec.world_size, 256.0);
        assert_eq!(spec.max_height, 50.0);
        assert_eq!(spec.chunk_size, 64.0);
        assert_eq!(spec.levels, 3);
        assert_eq!(spec.collision_resolution, 64);
        assert_eq!(spec.seed, 0);
    }

    #[test]
    fn test_effective_render_distance_defaults_to_the_chunk_budget() {
        let mut spec = TerrainSpec::default();
        // An authored value always wins.
        spec.render_distance = Some(700.0);
        assert_eq!(spec.effective_render_distance(), 700.0);

        // Without one, the radius is the disc that holds the chunk budget.
        spec.render_distance = None;
        spec.chunk_size = 64.0;
        let r = spec.effective_render_distance();
        let chunks_inside = std::f32::consts::PI * (r / spec.chunk_size).powi(2);
        assert!(
            (chunks_inside - DEFAULT_RESIDENT_CHUNK_BUDGET).abs() < 1.0,
            "radius {r} holds {chunks_inside} chunks, budget is {DEFAULT_RESIDENT_CHUNK_BUDGET}"
        );

        // It scales with `chunk_size`: bigger chunks, fewer of them per disc,
        // so the same budget reaches further.
        spec.chunk_size = 128.0;
        assert!(spec.effective_render_distance() > r);
    }

    #[test]
    fn test_extent_authored_is_off_by_default() {
        // The flag is what lets a heightmap file fill in the extent; it must
        // start clear so a spec that never saw XML still takes the file's.
        assert!(!TerrainSpec::default().extent_authored);
    }

    #[test]
    fn test_chunk_rows_rounds_up() {
        let mut spec = TerrainSpec::default();
        assert_eq!(spec.chunk_rows(), 4);
        spec.world_size = 65.0;
        assert_eq!(spec.chunk_rows(), 2);
        spec.world_size = 10.0;
        assert_eq!(spec.chunk_rows(), 1);
    }

    #[test]
    fn test_validate_rejects_tiny_chunk_size() {
        let mut spec = TerrainSpec::default();
        assert!(spec.validate().is_ok(), "defaults validate");
        // Passes the parser's `> 0` attribute check but derives a 25600-row
        // grid — refused with a clear error, and the runtime path degrades to
        // the capped edge instead of allocating it.
        spec.chunk_size = 0.01;
        assert!(spec.validate().is_err(), "grid too dense must be refused");
        assert_eq!(spec.heightfield_edge(64), MAX_GRID_EDGE_VERTS);
        spec.chunk_size = f32::NAN;
        assert!(spec.validate().is_err(), "non-finite chunk-size is refused");
    }

    #[test]
    fn test_heightfield_edge_caps_and_saturates() {
        let mut spec = TerrainSpec::default(); // 4 chunk rows
        assert_eq!(spec.heightfield_edge(64), 4 * 64);
        // Saturated rows (chunk_size -> 0) must not overflow the product.
        spec.chunk_size = 1e-9;
        assert_eq!(spec.heightfield_edge(64), MAX_GRID_EDGE_VERTS);
    }

    #[test]
    fn test_lod_distance_is_ratio_times_chunk() {
        let spec = TerrainSpec::default();
        assert!((spec.lod_distance() - 128.0).abs() < f32::EPSILON);
    }

    #[test]
    fn test_env_render_distance_override() {
        let mut spec = TerrainSpec::default();
        spec.render_distance = Some(950.0);

        // Env válido: o override ganha ao valor do XML.
        unsafe { std::env::set_var("VIBER_RENDER_DISTANCE", "450") };
        spec.apply_env_render_distance();
        assert_eq!(spec.render_distance, Some(450.0));
        assert_eq!(spec.effective_render_distance(), 450.0);

        // Env inválido: fica o valor do XML, sem panic.
        for raw in ["abc", "0", "-120", "inf", "NaN"] {
            unsafe { std::env::set_var("VIBER_RENDER_DISTANCE", raw) };
            spec.render_distance = Some(950.0);
            spec.apply_env_render_distance();
            assert_eq!(spec.render_distance, Some(950.0), "raw={raw}");
        }

        // Env por definir: fica o valor do XML.
        unsafe { std::env::remove_var("VIBER_RENDER_DISTANCE") };
        spec.apply_env_render_distance();
        assert_eq!(spec.render_distance, Some(950.0));

        // Sem valor autorado o override também se aplica (sai do orçamento
        // de chunks e usa o raio do env).
        unsafe { std::env::set_var("VIBER_RENDER_DISTANCE", "300") };
        spec.render_distance = None;
        spec.apply_env_render_distance();
        assert_eq!(spec.effective_render_distance(), 300.0);

        unsafe { std::env::remove_var("VIBER_RENDER_DISTANCE") };
    }

    #[test]
    fn test_pad_defaults() {
        let pad = TerrainPadSpec::default();
        assert_eq!(pad.height, None, "absent height attribute = auto mode");
        assert_eq!(pad.falloff, 8.0);
    }
}
