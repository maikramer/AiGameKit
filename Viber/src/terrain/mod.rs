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
//! * **Tint por altura/inclinação** evaluated CPU-side into vertex colors —
//!   no custom WGSL, stable across Bevy versions.
//! * **Collider heightfields** are generated per chunk at an independent
//!   `collision-resolution`, ready for the Phase 3 physics integration.
//!
//! # XML
//!
//! ```xml
//! <Terrain heightmap="terrain/height.png" world-size="256" max-height="50"
//!          chunk-size="64" levels="3" collision-resolution="64"
//!          base-color="#ffffff" color-rock="#6b6560" />
//! <TerrainPad at="20 -10" size="24 16" falloff="8" corner-radius="4" />
//! ```

pub mod heightmap;
pub mod mesh;
pub mod plugin;
pub mod sampler;
pub mod spec;

pub use heightmap::HeightMapU16;
pub use mesh::{ChunkMeshData, HeightField, TerrainColliderData};
pub use plugin::TerrainPlugin;
pub use sampler::{HeightSampler, ResolvedPad};
pub use spec::{TerrainPadSpec, TerrainSpec, TerrainTint};
