//! CPU height sampling — the single source of truth for terrain shape.
//!
//! Ported from the VibeGame `HeightSampler` (`height-sampler.ts`): mesh,
//! colliders, pads and gameplay queries all read the same grid, and any
//! mutation bumps [`HeightSampler::revision`] so caches can invalidate
//! cheaply (VibeGame `getGroundRevision`).

use bevy::math::Vec2;
use bevy::math::Vec3;

use super::mesh::HeightField;
use super::spec::TerrainPadSpec;

/// Resolved pad: the declarative spec with the auto height filled in.
#[derive(Debug, Clone, PartialEq)]
pub struct ResolvedPad {
    pub at: Vec2,
    pub size: Vec2,
    pub falloff: f32,
    pub corner_radius: f32,
    /// Target height in meters (auto pads have it sampled from the center).
    pub height: f32,
}

/// Smoothed heightfield over a `u16` grid (row-major, `[z * width + x]`).
///
/// Heights in the grid are raw `u16`; world height is
/// `(raw / 65535) * max_height`. World XZ `(-world_size/2, +world_size/2)`
/// maps to the grid; positions outside are clamped.
#[derive(Debug, Clone, PartialEq)]
pub struct HeightSampler {
    grid: Vec<u16>,
    width: usize,
    depth: usize,
    world_size: f32,
    max_height: f32,
    /// 0.0 = bilinear, 1.0 = monotone Catmull-Rom; values in between lerp.
    smoothing: f32,
    /// Bumped on every mutation (pads, reloads) — cache invalidation counter.
    revision: u64,
}

impl HeightSampler {
    /// Builds a sampler from a raw `u16` grid.
    ///
    /// # Errors
    /// Fails when the grid is empty, not `width * depth`, or dimensions/metrics are not finite and positive.
    pub fn from_grid(
        grid: Vec<u16>,
        width: usize,
        depth: usize,
        world_size: f32,
        max_height: f32,
        smoothing: f32,
    ) -> anyhow::Result<Self> {
        let _ = (grid, width, depth, world_size, max_height, smoothing);
        anyhow::bail!("terrain::sampler::from_grid is implemented by task A")
    }

    /// Grid width (samples along X).
    pub fn width(&self) -> usize {
        self.width
    }

    /// Grid depth (samples along Z).
    pub fn depth(&self) -> usize {
        self.depth
    }

    /// World span on X and Z (meters).
    pub fn world_size(&self) -> f32 {
        self.world_size
    }

    /// Peak height (meters) of a fully-white sample.
    pub fn max_height(&self) -> f32 {
        self.max_height
    }

    /// Height of a raw grid cell (meters).
    pub fn cell_height(&self, x: usize, z: usize) -> f32 {
        let _ = (x, z);
        unimplemented!("terrain::sampler::cell_height is implemented by task A")
    }

    /// Cache invalidation counter; bumped on every mutation.
    pub fn revision(&self) -> u64 {
        self.revision
    }

    /// Applies pads in order and returns the resolved pads (auto heights
    /// filled in). The flattening algorithm is ported from VibeGame
    /// `flatten.ts`: rounded-rect SDF core kept exactly flat, smoothstep
    /// falloff ring, and a lower-only guard that clamps the first falloff
    /// texel to the plane (prevents the bilinear-stencil "lip").
    ///
    /// Bumps [`Self::revision`] when any pad changed the grid.
    pub fn apply_pads(&mut self, pads: &[TerrainPadSpec]) -> Vec<ResolvedPad> {
        let _ = pads;
        Vec::new() // implemented by task A
    }
}

impl HeightField for HeightSampler {
    fn sample(&self, world_x: f32, world_z: f32) -> f32 {
        let _ = (world_x, world_z);
        0.0 // implemented by task A
    }

    fn sample_normal(&self, world_x: f32, world_z: f32, epsilon: f32) -> Vec3 {
        let _ = (world_x, world_z, epsilon);
        Vec3::Y // implemented by task A
    }

    fn max_height(&self) -> f32 {
        self.max_height
    }
}
