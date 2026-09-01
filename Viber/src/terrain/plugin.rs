//! Terrain chunk lifecycle — the core LOD plugin.
//!
//! Complements [`super::runtime::TerrainFeaturesPlugin`] (which bootstraps
//! the carved world and spawns LOD-0 chunk meshes at startup): this plugin
//! owns the **runtime** half of the chunk lifecycle:
//!
//! 1. **Adopt** — one-shot scan of the terrain root for the `chunk {cz}-{cx}`
//!    meshes spawned by the bootstrap, tagging them with [`TerrainChunk`].
//! 2. **Select** — LOD per chunk from the camera distance: the flat
//!    `2^lod`-step scheme of `bevy_mesh_terrain`, corrected with the VibeGame
//!    hysteresis + camera-move reselect gate so crossing a boundary never
//!    thrashes rebuilds.
//! 3. **Rebuild** — meshes rebuild inline with a per-frame budget
//!    ([`super::spec::DEFAULT_MAX_MESH_BUILDS_PER_FRAME`]). Deliberately *no*
//!    async tasks: the upstream crate lost chunks forever to a poll-once
//!    orphan-task bug (`finish_chunk_build_tasks`); small heightfield grids
//!    build in well under a frame, so inline + budget eliminates the whole
//!    bug class.
//! 4. **Cull** — chunks beyond `render_distance` are despawned and respawned
//!    (at LOD 0) when the camera approaches, matching VibeGame behavior.
//!
//! Works headless: only `Assets<Mesh>` and the camera transform are needed;
//! `analyze` never runs this (no Update schedule).

use std::collections::HashMap;

use bevy::asset::RenderAssetUsages;
use bevy::prelude::*;
use bevy::render::mesh::{Indices, PrimitiveTopology};

use super::brush::BrushGrid;
use super::mesh::{ChunkMeshData, ChunkMeshParams, build_chunk_mesh};
use super::runtime::TerrainRuntime;
use super::spec::{
    DEFAULT_LOD_HYSTERESIS, DEFAULT_LOD_RESELECT_DISTANCE, DEFAULT_MAX_MESH_BUILDS_PER_FRAME,
    TerrainSpec,
};

/// Tag on every terrain chunk mesh entity managed by this plugin.
#[derive(Component, Debug, Clone, Copy, PartialEq)]
pub struct TerrainChunk {
    /// Chunk grid coordinates (x, z), 0-based from the world's -X/-Z corner.
    pub coords: UVec2,
    /// LOD the chunk *should* render at (selection output).
    pub lod: u8,
    /// LOD of the mesh currently attached (rebuild output).
    pub built_lod: u8,
}

/// Per-terrain tracking state for the LOD plugin.
#[derive(Resource, Debug, Default)]
pub struct ChunkLodState {
    /// `true` after the one-shot adopt scan.
    adopted: bool,
    /// Terrain root entity (chunks attach here on respawn).
    root: Option<Entity>,
    /// Material captured from an adopted chunk, reused for respawns.
    material: Option<Handle<StandardMaterial>>,
    /// Camera XZ at the last full LOD evaluation (reselect gate).
    last_cam: Option<Vec2>,
    /// Work remained when the frame budget ran out (keeps draining).
    pending: bool,
    /// Live chunk index by grid coords (despawn removes the entry).
    chunks: HashMap<UVec2, Entity>,
}

impl ChunkLodState {
    /// Number of chunks currently tracked.
    pub fn tracked_chunks(&self) -> usize {
        self.chunks.len()
    }

    /// Entity of the chunk at `coords`, if tracked.
    pub fn chunk_entity(&self, coords: UVec2) -> Option<Entity> {
        self.chunks.get(&coords).copied()
    }
}

/// Terrain chunk LOD plugin — see module docs.
///
/// Does **not** register [`super::runtime::TerrainFeaturesPlugin`]; the CLI
/// wires both explicitly (double registration would panic).
#[derive(Default)]
pub struct TerrainPlugin;

impl bevy::app::Plugin for TerrainPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.init_resource::<ChunkLodState>()
            .add_systems(bevy::app::Update, (adopt_chunks, update_chunk_lods).chain());
    }
}

/// Name prefix used by the bootstrap for chunk entities (`chunk {cz}-{cx}`).
const CHUNK_NAME_PREFIX: &str = "chunk ";

/// Entity name for a chunk, mirroring the bootstrap's naming scheme.
pub fn chunk_name(coords: UVec2) -> String {
    format!("chunk {}-{}", coords.y, coords.x)
}

/// Parses `chunk {cz}-{cx}` into `(cx, cz)` grid coords.
fn parse_chunk_name(name: &str) -> Option<UVec2> {
    let rest = name.strip_prefix(CHUNK_NAME_PREFIX)?;
    let (cz, cx) = rest.split_once('-')?;
    let (cx, cz) = (cx.trim().parse().ok()?, cz.trim().parse().ok()?);
    Some(UVec2::new(cx, cz))
}

/// One-shot: tag the bootstrap's chunk meshes with [`TerrainChunk`], capture
/// the terrain root and the shared chunk material.
type ChunkCandidate = (
    Entity,
    &'static Name,
    Option<&'static MeshMaterial3d<StandardMaterial>>,
);

fn adopt_chunks(
    mut state: ResMut<ChunkLodState>,
    roots: Query<(Entity, &Name, &Children)>,
    tagged: Query<(), With<TerrainChunk>>,
    chunk_candidates: Query<ChunkCandidate, With<Mesh3d>>,
    mut commands: Commands,
) {
    if state.adopted {
        return;
    }
    state.adopted = true;
    for (root, name, children) in &roots {
        if name.as_str() != "terrain" {
            continue;
        }
        state.root = Some(root);
        for child in children.iter() {
            let Ok((entity, child_name, material)) = chunk_candidates.get(child) else {
                continue;
            };
            let Some(coords) = parse_chunk_name(child_name.as_str()) else {
                continue;
            };
            // Capture the shared material before the tagged check: the
            // bootstrap tags its own chunks (it builds them at the LOD their
            // distance implies), and respawns still need this handle.
            if let Some(mat) = material {
                state.material.get_or_insert(mat.0.clone());
            }
            if tagged.get(entity).is_ok() {
                continue;
            }
            commands.entity(entity).insert(TerrainChunk {
                coords,
                lod: 0,
                built_lod: 0,
            });
        }
    }
}

/// Per-frame LOD pass: reselect (gated by camera movement), rebuild within
/// the frame budget, and render-distance culling/respawn.
fn update_chunk_lods(
    mut state: ResMut<ChunkLodState>,
    runtime: Option<Res<TerrainRuntime>>,
    cameras: Query<&GlobalTransform, With<Camera3d>>,
    mut chunks: Query<(Entity, &Transform, &mut TerrainChunk, &mut Mesh3d)>,
    mut meshes: ResMut<Assets<Mesh>>,
    mut commands: Commands,
) {
    let Some(runtime) = runtime else {
        return;
    };
    if !state.adopted {
        return;
    }

    // Sync the chunk index with reality (adoptions and despawns).
    let dead: Vec<UVec2> = state
        .chunks
        .iter()
        .filter(|(_, e)| chunks.get(**e).is_err())
        .map(|(c, _)| *c)
        .collect();
    for coords in dead {
        state.chunks.remove(&coords);
    }
    for (entity, _, chunk, _) in chunks.iter() {
        state.chunks.insert(chunk.coords, entity);
    }

    let Ok(cam) = cameras.single() else {
        return;
    };
    let t = cam.translation();
    let cam_xz = Vec2::new(t.x, t.z);

    // Reselect gate (VibeGame `LOD_RESELECT_DISTANCE`): a full pass happens
    // only when the camera moved, or when the previous pass ran out of
    // budget with work remaining.
    let moved = state
        .last_cam
        .is_none_or(|last| last.distance(cam_xz) > DEFAULT_LOD_RESELECT_DISTANCE);
    if !moved && !state.pending {
        return;
    }
    if moved {
        state.last_cam = Some(cam_xz);
    }
    let mut budget = DEFAULT_MAX_MESH_BUILDS_PER_FRAME;
    state.pending = false;

    let spec = &runtime.spec;
    let edge = chunk_edge(spec);
    let half = spec.world_size * 0.5;
    let rows = (spec.world_size / edge).ceil().max(1.0) as u32;
    let max_lod = max_lod_for(spec, edge);
    let margin = hysteresis_margin(spec);
    let grid = &runtime.grid;

    // 1. Cull + re-evaluate tracked chunks.
    for (entity, transform, mut chunk, mut mesh) in chunks.iter_mut() {
        let tr = transform.translation;
        let dist = Vec2::new(tr.x, tr.z).distance(cam_xz);
        if let Some(render_distance) = spec.render_distance {
            if dist > render_distance {
                state.chunks.remove(&chunk.coords);
                commands.entity(entity).despawn();
                continue;
            }
        }
        chunk.lod = select_lod(dist, spec.lod_distance(), chunk.built_lod, max_lod, margin);
        if chunk.lod == chunk.built_lod || budget == 0 {
            if chunk.lod != chunk.built_lod {
                state.pending = true;
            }
            continue;
        }
        match rebuild(grid, spec, chunk.coords, edge, chunk.lod) {
            Some(data) => {
                *mesh = Mesh3d(meshes.add(to_bevy_mesh(&data)));
                chunk.built_lod = chunk.lod;
                budget -= 1;
            }
            // Step does not divide the edge exactly — stay at the coarser LOD.
            None => chunk.lod = chunk.built_lod,
        }
    }
    if budget == 0 {
        state.pending = true;
    }

    // 2. Respawn missing chunks (culled earlier, or skipped by the
    //    bootstrap) once they come within render distance.
    let Some(root) = state.root else {
        return;
    };
    let Some(material) = state.material.clone() else {
        return;
    };
    for cz in 0..rows {
        for cx in 0..rows {
            if budget == 0 {
                state.pending = true;
                return;
            }
            let coords = UVec2::new(cx, cz);
            if state.chunks.contains_key(&coords) {
                continue;
            }
            let center = Vec2::new(
                -half + cx as f32 * edge + edge * 0.5,
                -half + cz as f32 * edge + edge * 0.5,
            );
            if let Some(render_distance) = spec.render_distance {
                if center.distance(cam_xz) > render_distance {
                    continue;
                }
            }
            let Some(data) = rebuild(grid, spec, coords, edge, 0) else {
                continue;
            };
            let handle = meshes.add(to_bevy_mesh(&data));
            let entity = commands
                .spawn((
                    Name::new(chunk_name(coords)),
                    Transform::from_translation(Vec3::new(center.x, 0.0, center.y)),
                    Visibility::Inherited,
                    Mesh3d(handle),
                    MeshMaterial3d(material.clone()),
                    ChildOf(root),
                ))
                .id();
            commands.entity(entity).insert(TerrainChunk {
                coords,
                lod: 0,
                built_lod: 0,
            });
            state.chunks.insert(coords, entity);
            budget -= 1;
        }
    }
}

/// Rebuilds one chunk mesh at `lod`; `None` when the LOD step does not divide
/// the chunk edge exactly (the coarser mesh keeps rendering).
fn rebuild(
    grid: &BrushGrid,
    spec: &TerrainSpec,
    coords: UVec2,
    edge: f32,
    lod: u8,
) -> Option<ChunkMeshData> {
    let step = lod0_step(spec) << lod;
    let half = spec.world_size * 0.5;
    let origin = Vec3::new(
        -half + coords.x as f32 * edge,
        0.0,
        -half + coords.y as f32 * edge,
    );
    let params = ChunkMeshParams {
        origin,
        size: edge,
        lod_step: step,
        skirt_depth: spec.skirt_depth_meters(),
        normal_epsilon: grid.texel(),
        texture_tile_size: spec.texture_tile_size,
        levels: spec.levels,
        world_size: spec.world_size,
    };
    build_chunk_mesh(grid, &params).ok().flatten()
}

/// LOD selection with hysteresis: boundaries sit at `lod_distance * 2^(l-1)`;
/// coarsening requires crossing `boundary * margin`, refining
/// `boundary / margin`.
pub(crate) fn select_lod(
    dist: f32,
    lod_distance: f32,
    current: u8,
    max_lod: u8,
    margin: f32,
) -> u8 {
    if lod_distance <= 0.0 || !dist.is_finite() {
        return 0;
    }
    let raw = (((dist / lod_distance).log2() + 1.0)
        .floor()
        .clamp(0.0, f32::from(max_lod))) as u8;
    if raw == current {
        return current;
    }
    let margin = if margin.is_finite() && margin >= 1.0 {
        margin
    } else {
        1.0
    };
    if raw > current {
        let boundary = lod_distance * 2f32.powi(i32::from(current));
        if dist > boundary * margin {
            raw
        } else {
            current
        }
    } else {
        let boundary = lod_distance * 2f32.powi(i32::from(raw.saturating_sub(1)));
        if dist < boundary / margin {
            raw
        } else {
            current
        }
    }
}

/// `sqrt(hysteresis)` — a factor 1.2 shifts each boundary by ~±9.5%, giving
/// symmetric dead-zones around it.
pub(crate) fn hysteresis_margin(spec: &TerrainSpec) -> f32 {
    let h = if spec.lod_hysteresis >= 1.0 {
        spec.lod_hysteresis
    } else {
        DEFAULT_LOD_HYSTERESIS
    };
    h.sqrt()
}

/// Coarsest LOD whose integer grid step still divides the chunk edge evenly.
pub(crate) fn max_lod_for(spec: &TerrainSpec, edge: f32) -> u8 {
    let base_segments = (edge / lod0_step(spec) as f32).round() as u32;
    let mut lod = 0u8;
    while lod + 1 < spec.levels && base_segments.is_multiple_of(1 << (lod + 1)) {
        lod += 1;
    }
    lod
}

/// Full chunk edge in meters (the bootstrap's `edge`, which falls back to
/// whole meters when `resolution` does not divide `chunk_size` exactly).
pub(crate) fn chunk_edge(spec: &TerrainSpec) -> f32 {
    let step = lod0_step(spec);
    (spec.chunk_size / step as f32).round() * step as f32
}

/// LOD-0 grid step — mirrors `runtime::lod0_step` (kept local to avoid
/// touching the features module; a `pub(crate)` dedupe is a follow-up).
pub(crate) fn lod0_step(spec: &TerrainSpec) -> usize {
    let ideal = spec.chunk_size / spec.resolution.max(1) as f32;
    let step = ideal.round().max(1.0) as usize;
    if (spec.chunk_size / step as f32).abs().fract() > 1e-3 {
        1
    } else {
        step
    }
}

/// Converts pure [`ChunkMeshData`] buffers into a Bevy mesh (CPU-resident) —
/// mirrors `runtime::to_bevy_mesh` so LOD swaps keep the same conventions.
fn to_bevy_mesh(data: &ChunkMeshData) -> Mesh {
    let mut mesh = Mesh::new(
        PrimitiveTopology::TriangleList,
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    );
    mesh.insert_attribute(Mesh::ATTRIBUTE_POSITION, data.positions.clone());
    mesh.insert_attribute(Mesh::ATTRIBUTE_NORMAL, data.normals.clone());
    mesh.insert_attribute(Mesh::ATTRIBUTE_UV_0, data.uvs.clone());
    // Only surfaces that actually use vertex colours get the attribute:
    // roads carry their edge alpha there, terrain chunks carry nothing.
    if !data.colors.is_empty() {
        mesh.insert_attribute(Mesh::ATTRIBUTE_COLOR, data.colors.clone());
    }
    mesh.insert_indices(Indices::U32(data.indices.clone()));
    mesh
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::terrain::brush::BrushGrid;
    use crate::terrain::heightmap::HeightMapU16;

    fn spec() -> TerrainSpec {
        TerrainSpec {
            world_size: 32.0,
            chunk_size: 16.0,
            levels: 3,
            ..TerrainSpec::default()
        }
    }

    #[test]
    fn test_chunk_name_roundtrip() {
        let coords = UVec2::new(3, 5);
        assert_eq!(parse_chunk_name(&chunk_name(coords)), Some(coords));
        assert_eq!(parse_chunk_name("chunk 0-0"), Some(UVec2::new(0, 0)));
        assert_eq!(parse_chunk_name("lake 1"), None);
        assert_eq!(parse_chunk_name("chunk x-0"), None);
    }

    #[test]
    fn test_select_lod_flat_scheme() {
        // lod_distance = 32 (chunk 16 × ratio 2): boundaries at 32 and 64.
        let (ld, margin) = (32.0, 1.0);
        assert_eq!(select_lod(10.0, ld, 0, 2, margin), 0);
        assert_eq!(select_lod(40.0, ld, 0, 2, margin), 1);
        assert_eq!(select_lod(100.0, ld, 0, 2, margin), 2);
        assert_eq!(select_lod(100.0, ld, 1, 2, margin), 2);
        // Beyond max_lod clamps.
        assert_eq!(select_lod(10_000.0, ld, 0, 2, margin), 2);
        // Degenerate distance metric.
        assert_eq!(select_lod(10.0, 0.0, 0, 2, margin), 0);
    }

    #[test]
    fn test_select_lod_hysteresis_dead_zone() {
        // Boundary at 32; margin ~1.095 for hysteresis 1.2 → dead zone
        // (32/1.095, 32×1.095) ≈ (29.2, 35.0): no switch inside it.
        let margin = 1.2f32.sqrt();
        assert_eq!(select_lod(33.0, 32.0, 0, 2, margin), 0, "stays coarse");
        assert_eq!(select_lod(31.0, 32.0, 1, 2, margin), 1, "stays fine");
        assert_eq!(select_lod(36.0, 32.0, 0, 2, margin), 1, "coarsens");
        assert_eq!(select_lod(28.0, 32.0, 1, 2, margin), 0, "refines");
    }

    #[test]
    fn test_max_lod_respects_grid_divisibility() {
        // step 1 → 16 segments: divisible by 4 → max LOD 2 with 3 levels.
        let edge = chunk_edge(&spec());
        assert_eq!(max_lod_for(&spec(), edge), 2);
        // Odd segment counts stop earlier.
        let mut odd = spec();
        odd.chunk_size = 15.0;
        let edge = chunk_edge(&odd);
        assert_eq!(edge, 15.0);
        assert_eq!(
            max_lod_for(&odd, edge),
            0,
            "15 segments: not even divisible"
        );
    }

    #[test]
    fn test_chunk_edge_falls_back_to_whole_meters() {
        let mut coarse = spec();
        coarse.resolution = 32;
        coarse.chunk_size = 16.0;
        assert!((chunk_edge(&coarse) - 16.0).abs() < 1e-4);
        let mut inexact = spec();
        inexact.chunk_size = 10.0; // 10/64 rounds to step 1 → edge 10
        inexact.resolution = 64;
        assert!((chunk_edge(&inexact) - 10.0).abs() < 1e-4);
    }

    /// Integration: adopt → LOD switch → render-distance cull/respawn, all
    /// headless over a real `BrushGrid`.
    #[test]
    fn test_plugin_adopts_switches_lod_and_culls() {
        let mut app = bevy::app::App::new();
        app.add_plugins(bevy::MinimalPlugins)
            .add_plugins(bevy::transform::TransformPlugin)
            .add_plugins(TerrainPlugin);
        app.init_resource::<Assets<Mesh>>();
        app.init_resource::<Assets<StandardMaterial>>();

        let spec = spec();
        let map = HeightMapU16 {
            width: 33,
            depth: 33,
            data: (0..33 * 33).map(|i| (i % 1000) as u16).collect(),
        };
        let grid = BrushGrid::from_height_map(&map, spec.world_size, spec.max_height, 1.0)
            .expect("grid from heightmap");
        app.insert_resource(TerrainRuntime {
            spec: spec.clone(),
            grid,
            water: Vec::new(),
            roads: Vec::new(),
            pads: Vec::new(),
        });

        let root = app
            .world_mut()
            .spawn((
                Name::new("terrain"),
                Transform::default(),
                Visibility::Inherited,
            ))
            .id();
        let material = app
            .world_mut()
            .resource_mut::<Assets<StandardMaterial>>()
            .add(StandardMaterial::default());
        let edge = chunk_edge(&spec);
        let half = spec.world_size * 0.5;
        let mesh0 = app
            .world_mut()
            .resource_mut::<Assets<Mesh>>()
            .add(Mesh::from(bevy::math::primitives::Cuboid::new(
                1.0, 1.0, 1.0,
            )));
        for cz in 0..2u32 {
            for cx in 0..2u32 {
                let center = Vec3::new(
                    -half + cx as f32 * edge + edge * 0.5,
                    0.0,
                    -half + cz as f32 * edge + edge * 0.5,
                );
                app.world_mut().spawn((
                    Name::new(chunk_name(UVec2::new(cx, cz))),
                    Transform::from_translation(center),
                    Visibility::Inherited,
                    Mesh3d(mesh0.clone()),
                    MeshMaterial3d(material.clone()),
                    ChildOf(root),
                ));
            }
        }
        app.world_mut().spawn((
            Camera3d::default(),
            Transform::from_xyz(half, 20.0, half),
            GlobalTransform::default(),
        ));

        app.update();
        app.update();
        {
            let state = app.world().resource::<ChunkLodState>();
            assert_eq!(state.tracked_chunks(), 4, "all chunks adopted");
            assert!(state.material.is_some(), "material captured");
        }
        let lod = |app: &mut bevy::app::App| {
            let mut q = app.world_mut().query::<&TerrainChunk>();
            let mut lods: Vec<u8> = q.iter(app.world()).map(|c| c.built_lod).collect();
            lods.sort_unstable();
            lods
        };
        assert_eq!(lod(&mut app), vec![0, 0, 0, 0], "camera at center: LOD 0");

        // Fly far away → coarser LOD everywhere (budget 4 covers 4 chunks).
        let mut cam = app
            .world_mut()
            .query::<(&Camera3d, &mut Transform)>()
            .single_mut(app.world_mut())
            .expect("one camera");
        cam.1.translation = Vec3::new(half + 500.0, 20.0, half);
        // Two updates: the first syncs Transform -> GlobalTransform (PostUpdate),
        // the second runs the LOD pass against the new camera position.
        app.update();
        app.update();
        assert_eq!(lod(&mut app), vec![2, 2, 2, 2], "far camera: coarsest LOD");

        // Render distance: cull everything, then respawn on approach.
        let mut runtime = app.world_mut().resource_mut::<TerrainRuntime>();
        runtime.spec.render_distance = Some(100.0);
        app.update();
        {
            let state = app.world().resource::<ChunkLodState>();
            assert_eq!(state.tracked_chunks(), 0, "all chunks culled");
        }
        let count = |app: &mut bevy::app::App| {
            let mut q = app.world_mut().query::<(&Mesh3d, &TerrainChunk)>();
            q.iter(app.world()).count()
        };
        assert_eq!(count(&mut app), 0);
        let mut cam = app
            .world_mut()
            .query::<(&Camera3d, &mut Transform)>()
            .single_mut(app.world_mut())
            .expect("one camera");
        cam.1.translation = Vec3::new(half, 20.0, half);
        app.update();
        app.update();
        assert_eq!(count(&mut app), 4, "chunks respawned near the camera");
        assert_eq!(lod(&mut app), vec![0, 0, 0, 0]);
    }

    #[test]
    fn test_plugin_is_idle_without_runtime() {
        let mut app = bevy::app::App::new();
        app.add_plugins(bevy::MinimalPlugins)
            .add_plugins(bevy::transform::TransformPlugin)
            .add_plugins(TerrainPlugin);
        app.init_resource::<Assets<Mesh>>();
        app.init_resource::<Assets<StandardMaterial>>();
        app.world_mut()
            .spawn((Name::new("terrain"), Transform::default()));
        app.world_mut().spawn((
            Camera3d::default(),
            Transform::default(),
            GlobalTransform::default(),
        ));
        for _ in 0..3 {
            app.update();
        }
        assert_eq!(
            app.world().resource::<ChunkLodState>().tracked_chunks(),
            0,
            "no terrain runtime: nothing tracked, no panics"
        );
    }
}
