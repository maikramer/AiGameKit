//! Chunk mesh generation from a heightfield — pure geometry, no Bevy ECS.
//!
//! Ported from the VibeGame terrain plugin (`chunk-geometry.ts`) and the
//! `bevy_mesh_terrain` crate (MIT, ethereumdegen), with the known upstream
//! bugs fixed: borders are sealed with vertical **skirts** instead of the
//! broken neighbor-stitching ("THIS IS BUSTED" in `compute_stitch_data`), and
//! **frontier normals** sample the shared heightfield with a constant
//! terrain-wide epsilon so lighting is seamless across chunks and LODs.

use bevy::math::Vec3;

/// Read-only height queries used by mesh/collider building.
///
/// Implemented by [`crate::terrain::sampler::HeightSampler`]. All coordinates
/// are world-space meters; heights are meters (`normalized * max_height`).
pub trait HeightField {
    /// Terrain height at a world XZ position (smoothed per the sampler mode).
    fn sample(&self, world_x: f32, world_z: f32) -> f32;
    /// Surface normal via central differences with the given world epsilon.
    /// Callers pass a terrain-wide constant epsilon (not per-chunk/per-LOD)
    /// so normals agree on shared chunk borders.
    fn sample_normal(&self, world_x: f32, world_z: f32, epsilon: f32) -> Vec3;
    /// Peak height of the heightfield (meters).
    fn max_height(&self) -> f32;
}

/// Vertex/index buffers for one terrain chunk mesh.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ChunkMeshData {
    /// Vertex positions, world-origin relative to the chunk entity
    /// (x/z relative to the chunk center, y absolute meters).
    pub positions: Vec<[f32; 3]>,
    /// Smooth normals.
    pub normals: Vec<[f32; 3]>,
    /// UVs (world-space tiled, see [`build_chunk_mesh`]).
    pub uvs: Vec<[f32; 2]>,
    /// RGBA vertex colors from the height/slope tint.
    pub colors: Vec<[f32; 4]>,
    /// Triangle indices.
    pub indices: Vec<u32>,
}

/// Low-poly heightfield collision mesh for one chunk (Phase 3 physics ready).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct TerrainColliderData {
    /// Vertex positions (world XZ, absolute Y meters).
    pub positions: Vec<[f32; 3]>,
    /// Triangle indices.
    pub indices: Vec<u32>,
}

/// Color banding parameters for [`build_chunk_mesh`] (subset of the tint spec).
#[derive(Debug, Clone)]
pub struct TintParams {
    pub base_color: [f32; 4],
    pub color_low: [f32; 4],
    pub color_mid: [f32; 4],
    pub color_high: [f32; 4],
    pub color_rock: [f32; 4],
    pub snow_height: f32,
    pub slope_threshold: f32,
    pub slope_softness: f32,
    pub height_blend_strength: f32,
}

impl From<&crate::terrain::spec::TerrainTint> for TintParams {
    fn from(t: &crate::terrain::spec::TerrainTint) -> Self {
        let conv = |c: bevy::color::Color| {
            let s = c.to_srgba();
            [s.red, s.green, s.blue, s.alpha]
        };
        Self {
            base_color: conv(t.base_color),
            color_low: conv(t.color_low),
            color_mid: conv(t.color_mid),
            color_high: conv(t.color_high),
            color_rock: conv(t.color_rock),
            snow_height: t.snow_height,
            slope_threshold: t.slope_threshold,
            slope_softness: t.slope_softness,
            height_blend_strength: t.height_blend_strength,
        }
    }
}

/// Options for [`build_chunk_mesh`].
#[derive(Debug, Clone)]
pub struct ChunkMeshParams {
    /// World XZ of the chunk's minimum corner (meters).
    pub origin: Vec3,
    /// Chunk edge length on X and Z (meters).
    pub size: f32,
    /// Grid step over the heightfield: `1 << lod`. `1` = full resolution.
    pub lod_step: usize,
    /// Vertical skirt depth in meters (0 disables skirts).
    pub skirt_depth: f32,
    /// Texture tile size in meters; `0.0` = auto (see [`auto_texture_tile_size`]).
    pub texture_tile_size: f32,
    /// Height/slope tint parameters.
    pub tint: TintParams,
    /// Terrain-wide world size, used by the auto tile-size rule.
    pub world_size: f32,
}

/// Auto texture tile size: keeps texel density constant between LODs and
/// continuous across chunks (`world_size / 2^(levels-1) / 32`), ported from
/// VibeGame's `textureTileSize = 0` auto rule.
pub fn auto_texture_tile_size(world_size: f32, levels: u8) -> f32 {
    let levels = levels.max(1);
    world_size / (1u32 << (levels - 1)) as f32 / 32.0
}

/// Builds one terrain chunk mesh from the heightfield.
///
/// Returns `Ok(None)` when the chunk contains no vertices at the requested
/// step (degenerate request, e.g. `lod_step` larger than the chunk grid).
pub fn build_chunk_mesh(
    field: &impl HeightField,
    params: &ChunkMeshParams,
) -> anyhow::Result<Option<ChunkMeshData>> {
    let _ = (field, params);
    anyhow::bail!("terrain::mesh::build_chunk_mesh is implemented by task B")
}

/// Builds a regular-grid collision heightfield for one chunk at the given
/// resolution (samples per edge). Decoupled from the render LOD so collision
/// fidelity is tunable independently (VibeGame `collision-resolution`).
pub fn build_chunk_collider(
    field: &impl HeightField,
    origin: Vec3,
    size: f32,
    resolution: u32,
) -> anyhow::Result<TerrainColliderData> {
    let _ = (field, origin, size, resolution);
    anyhow::bail!("terrain::mesh::build_chunk_collider is implemented by task B")
}
