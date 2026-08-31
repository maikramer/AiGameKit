//! Heightmap loading, encoding and procedural generation.
//!
//! Fixes two upstream `bevy_mesh_terrain` bugs: the asymmetric PNG decoding
//! (read little-endian, write big-endian) and the main-thread PNG decode.
//! Decoding happens from a Bevy [`bevy::image::Image`] (already loaded by the
//! asset system); encoding is only used by tools/tests via the `image` dev
//! dependency.

use super::spec::TerrainSpec;

/// 16-bit height grid, row-major `[z][x]` (compatible with the upstream
/// `HeightMapU16` on-disk layout: one PNG per chunk).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HeightMapU16 {
    pub width: usize,
    pub depth: usize,
    pub data: Vec<u16>,
}

impl HeightMapU16 {
    /// Allocates a flat map filled with `value`.
    pub fn filled(width: usize, depth: usize, value: u16) -> Self {
        Self {
            width,
            depth,
            data: vec![value; width * depth],
        }
    }

    /// Decodes a Bevy `Image` into a height grid.
    ///
    /// Accepts 16-bit grayscale (`R16Uint` / `R16Unorm` / `R16Float`),
    /// 8-bit grayscale (`R8Unorm`) and RGBA8 (red channel, VibeGame
    /// "R=high/G=low" packing also works because red == high byte).
    /// 8-bit values are expanded to the full 16-bit range (`v * 257`).
    ///
    /// # Errors
    /// Fails on unsupported texture formats or non-2D images.
    pub fn from_image(image: &bevy::image::Image) -> anyhow::Result<Self> {
        let _ = image;
        anyhow::bail!("terrain::heightmap::from_image is implemented by task A")
    }

    /// Generates a deterministic procedural heightfield (seeded value-noise
    /// FBM, no external RNG dependency) in `[0, 65535]`. Same seed + dims
    /// always produce the same grid (first-command-works / reproducibility).
    pub fn procedural(spec: &TerrainSpec, samples_per_chunk_edge: usize) -> Self {
        let _ = (spec, samples_per_chunk_edge);
        Self::filled(1, 1, 0) // implemented by task A
    }

    /// Raw height at grid coordinates (no bounds check beyond a panic-safe clamp).
    pub fn get(&self, x: usize, z: usize) -> u16 {
        let _ = (x, z);
        0 // implemented by task A
    }
}
