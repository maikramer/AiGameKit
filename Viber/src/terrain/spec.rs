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
/// Skirt depth as a fraction of `max_height` — hides T-junction cracks between LODs.
pub const DEFAULT_SKIRT_WIDTH: f32 = 0.015625;
/// Multiplier applied on top of the skirt width.
pub const DEFAULT_SKIRT_DEPTH: f32 = 1.0;
/// `0.0` = bilinear sampling, `1.0` = monotone Catmull-Rom (C1, no ringing).
pub const DEFAULT_HEIGHT_SMOOTHING: f32 = 1.0;
/// Collider heightfield resolution per chunk edge (0 disables collider generation).
pub const DEFAULT_COLLISION_RESOLUTION: u32 = 64;
/// Mesh vertices per chunk edge at LOD 0. The effective grid step is
/// `chunk_size / resolution` rounded to whole meters (the mesh builder works
/// on integer steps); values finer than 1 m/vertex clamp to 1.
pub const DEFAULT_RESOLUTION: u32 = 64;
/// Mesh chunks rebuilt per frame after the initial load (frame budget).
pub const DEFAULT_MAX_MESH_BUILDS_PER_FRAME: u32 = 4;

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
    /// Vertical skirt depth as a fraction of `max_height` (hides LOD cracks).
    pub skirt_width: f32,
    /// Multiplier on the skirt depth.
    pub skirt_depth: f32,
    /// Height smoothing: `0.0` bilinear, `1.0` monotone Catmull-Rom.
    pub height_smoothing: f32,
    /// Collider heightfield resolution per chunk edge; `0` disables collider generation.
    pub collision_resolution: u32,
    /// Mesh vertices per chunk edge at LOD 0 (see [`DEFAULT_RESOLUTION`]).
    pub resolution: u32,
    /// Optional tiled diffuse texture applied over the whole terrain.
    pub texture: Option<String>,
    /// Texture tile size in meters; `0.0` = auto (keeps texel density constant across LODs).
    pub texture_tile_size: f32,
    /// Height/slope color tinting applied as vertex colors.
    pub tint: TerrainTint,
    /// Seed for the procedural heightfield (ignored when `heightmap` is set).
    pub seed: u64,
}

impl Default for TerrainSpec {
    fn default() -> Self {
        Self {
            heightmap: None,
            world_size: DEFAULT_WORLD_SIZE,
            max_height: DEFAULT_MAX_HEIGHT,
            chunk_size: DEFAULT_CHUNK_SIZE,
            levels: DEFAULT_LEVELS,
            lod_distance_ratio: DEFAULT_LOD_DISTANCE_RATIO,
            lod_hysteresis: DEFAULT_LOD_HYSTERESIS,
            render_distance: None,
            skirt_width: DEFAULT_SKIRT_WIDTH,
            skirt_depth: DEFAULT_SKIRT_DEPTH,
            height_smoothing: DEFAULT_HEIGHT_SMOOTHING,
            collision_resolution: DEFAULT_COLLISION_RESOLUTION,
            resolution: DEFAULT_RESOLUTION,
            texture: None,
            texture_tile_size: 0.0,
            tint: TerrainTint::default(),
            seed: 0,
        }
    }
}

impl TerrainSpec {
    /// Number of chunks along each axis (at least 1).
    pub fn chunk_rows(&self) -> u32 {
        (self.world_size / self.chunk_size).ceil().max(1.0) as u32
    }

    /// LOD distance threshold in meters for a chunk of this terrain.
    pub fn lod_distance(&self) -> f32 {
        self.chunk_size * self.lod_distance_ratio
    }

    /// Skirt depth in meters for this terrain.
    pub fn skirt_depth_meters(&self) -> f32 {
        self.max_height * self.skirt_width * self.skirt_depth
    }
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
    fn test_chunk_rows_rounds_up() {
        let mut spec = TerrainSpec::default();
        assert_eq!(spec.chunk_rows(), 4);
        spec.world_size = 65.0;
        assert_eq!(spec.chunk_rows(), 2);
        spec.world_size = 10.0;
        assert_eq!(spec.chunk_rows(), 1);
    }

    #[test]
    fn test_lod_distance_is_ratio_times_chunk() {
        let spec = TerrainSpec::default();
        assert!((spec.lod_distance() - 128.0).abs() < f32::EPSILON);
    }

    #[test]
    fn test_skirt_depth_meters() {
        let spec = TerrainSpec::default();
        assert!((spec.skirt_depth_meters() - 50.0 * 0.015625).abs() < 1e-4);
    }

    #[test]
    fn test_pad_defaults() {
        let pad = TerrainPadSpec::default();
        assert_eq!(pad.height, None, "absent height attribute = auto mode");
        assert_eq!(pad.falloff, 8.0);
    }
}
