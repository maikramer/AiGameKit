//! Declarative terrain for Viber — Bevy 0.19 port of the ideas from
//! [`bevy_mesh_terrain`](https://github.com/ethereumdegen/bevy_mesh_terrain)
//! (MIT) corrected and merged with the feature contracts of the VibeGame
//! terrain plugin (`VibeGame/src/plugins/terrain/`).
//!
//! # Design contracts (ported from VibeGame)
//!
//! * **Sampler CPU único** — [`sampler::HeightSampler`] is the only shape
//!   authority; mesh, colliders, pads and gameplay queries read the same grid.
//! * **Skirts + frontier normals** instead of neighbor stitching — no cracks
//!   and no seam lighting between chunks/LODs.
//! * **LOD com histerese** and a camera-move reselect gate to avoid thrashing.
//! * **Pads** flatten the heightfield with a rounded-rect SDF + smoothstep
//!   falloff and write the resolved height back (auto mode).
//! * **Ground look** — two paths: the legacy height/slope tint in vertex
//!   colors (no custom WGSL), or the `layers` splat blend
//!   ([`splat`] + [`layer_material`], 12 pool textures incl. the shore
//!   sand/mud bands), which replaces the tint entirely.
//! * **Collider heightfields** are generated per chunk at an independent
//!   `collision-resolution`, ready for the Phase 3 physics integration.
//!
//! # XML
//!
//! ```xml
//! <Terrain heightmap="terrain/height.png" world-size="256" max-height="50"
//!          chunk-size="64" levels="3" collision-resolution="64"
//!          base-color="#ffffff" color-rock="#6b6560" />
//! <!-- ou o blend de camadas (substitui o tint; areia nas margens): -->
//! <Terrain layers="grass vale_grass dirt forest_floor gravel
//!          mountain_stone sand snow_peak" shore-width="6" />
//! <TerrainPad at="20 -10" size="24 16" falloff="8" corner-radius="4" />
//! ```

pub mod brush;
pub mod cliffs;
pub mod decal;
pub mod features;
pub mod heightmap;
pub mod layer_material;
pub mod mesh;
pub mod paths;
pub mod plugin;
pub mod roads;
pub mod cut;
pub mod delta;
pub mod runtime;
pub mod plateau;
pub mod sampler;
pub mod shore_rocks;
pub mod spec;
pub mod splat;
pub mod voxel;
pub mod water;
pub mod water_fx;
pub mod water_material;

pub use brush::{BrushGrid, BrushMode};
pub use cliffs::{CliffMask, CliffProfile, CliffSide, CliffSpec, sharpen_terrain};
pub use cut::CutSpec;
pub use delta::{DeltaGrid, EditedBase, TerrainEdit, TerrainEditQueue};
pub use decal::{GroundDecalSpec, ground_decal_mesh};
pub use heightmap::HeightMapU16;
pub use layer_material::TerrainChunkMaterial;
pub use mesh::{ChunkMeshData, HeightField};
pub use paths::{chaikin_smooth, resample};
pub use plateau::PlateauSpec;
pub use plugin::TerrainPlugin;
pub use roads::{RoadNetworkSpec, RoadPath, RoadProfile, RoadSpec, SegmentSpec, WaySpec};
pub use runtime::TerrainFeaturesPlugin;
pub use sampler::{HeightSampler, ResolvedPad};
pub use spec::{TerrainPadSpec, TerrainSpec, TerrainTint};
pub use voxel::{
    Bounds3, BoxMod, CapsuleMod, ChunkClass, ModIndex, ModOp, Span, VoxelChunkParams, VoxelField,
    VoxelMod, build_voxel_mesh,
};
pub use water::{LakeSpec, RiverSpec, WaterBody, WaterKind};
pub use water_fx::WaterFxPlugin;
pub use water_material::{WaterExtension, WaterMaterial, WaterSurfaceConfig};
