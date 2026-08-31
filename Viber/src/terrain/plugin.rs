//! Terrain runtime plugin (filled by task C).
//!
//! Owns the chunk lifecycle: heightmap load state machine, pad application,
//! LOD selection with hysteresis, chunk spawn/despawn diffing, asynchronous
//! mesh building (with the upstream poll-once orphan-task bug fixed), and
//! distance culling. Chunks are `ChildOf` the terrain entity; the sampler is
//! the shape authority consumed via [`super::mesh::HeightField`].

use bevy::app::App;

/// Terrain plugin — registers all terrain resources and systems.
///
/// Works headless (no render plugins): mesh building only needs `Assets<Mesh>`
/// plus the task pools. With render plugins enabled, `Mesh3d`/`MeshMaterial3d`
/// components make chunks visible automatically.
#[derive(Default)]
pub struct TerrainPlugin;

impl bevy::app::Plugin for TerrainPlugin {
    fn build(&self, _app: &mut App) {
        // filled by task C
    }
}
