//! Terrain runtime — turns the declarative specs into a carved world.
//!
//! Owns the one-shot startup pipeline (exclusive systems, so no archetype
//! churn in the frame loop):
//!
//! 1. **Grid** — heightmap file (PNG, 8/16-bit grayscale; blocking read is
//!    fine at startup) or the deterministic procedural field. `.ahgt` (the
//!    VibeGame packed format) is not decoded natively yet and falls back to
//!    procedural with a warning.
//! 2. **Features** — [`apply_features`] runs pads → water → roads on the
//!    [`BrushGrid`] (the VibeGame order), producing the query registries.
//! 3. **Entities** — chunk meshes (LOD 0, integer grid step; dynamic LOD
//!    selection arrives with the core plugin), water mirrors/ribbons and road
//!    ribbons; registries land as resources for gameplay queries
//!    (`avoid-water`, `isPointOnRoad`, ground height sampling).
//!
//! Headless `analyze` never runs this — it only parses and validates.

use std::path::{Path, PathBuf};

use bevy::asset::RenderAssetUsages;
use bevy::prelude::*;
use bevy::render::mesh::{Indices, PrimitiveTopology};

use super::brush::BrushGrid;
use super::features::{FeatureResult, apply_features};
use super::heightmap::HeightMapU16;
use super::mesh::{ChunkMeshParams, TintParams, build_chunk_mesh};
use super::roads::RoadPath;
use super::sampler::ResolvedPad;
use super::spec::TerrainSpec;
use super::water::{WaterBody, lake_water_mesh, river_water_mesh};
use crate::recipes::spawn::PendingTerrain;

/// Carved world state, published after the bootstrap for gameplay queries.
#[derive(Resource)]
pub struct TerrainRuntime {
    pub spec: TerrainSpec,
    pub grid: BrushGrid,
    pub water: Vec<WaterBody>,
    pub roads: Vec<RoadPath>,
    pub pads: Vec<ResolvedPad>,
}

impl TerrainRuntime {
    /// Ground height at a world XZ position (meters).
    pub fn sample(&self, x: f32, z: f32) -> f32 {
        self.grid.sample(x, z)
    }

    /// Point is inside a water carve zone (`avoid-water`).
    pub fn in_water(&self, x: f32, z: f32) -> bool {
        self.water.iter().any(|w| w.contains(Vec2::new(x, z)))
    }

    /// Point is on a road ribbon (`isPointOnRoad`).
    pub fn on_road(&self, x: f32, z: f32) -> bool {
        self.roads.iter().any(|r| r.is_on_road(Vec2::new(x, z)))
    }
}

/// Terrain feature plugin: consumes [`PendingTerrain`] at startup and builds
/// the carved world. Works headless (no render plugins) — meshes land in
/// `Assets<Mesh>` regardless; visibility only appears with render plugins.
#[derive(Default)]
pub struct TerrainFeaturesPlugin;

impl bevy::app::Plugin for TerrainFeaturesPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.add_systems(
            bevy::app::Startup,
            bootstrap.after(crate::recipes::spawn::startup),
        );
    }
}

/// Exclusive startup: PendingTerrain → grid → carve → entities → registries.
pub fn bootstrap(world: &mut World) {
    let Some(pending) = world.remove_resource::<PendingTerrain>() else {
        return;
    };
    let Some(spec) = pending.terrain.clone() else {
        return;
    };

    // 1. Height grid.
    let map = match &spec.heightmap {
        Some(path) => match load_heightmap(pending.base_dir.as_deref(), path) {
            Ok(map) => map,
            Err(error) => {
                warn!("heightmap `{path}` unavailable ({error:#}); using the procedural field");
                HeightMapU16::procedural(&spec, spec.resolution.max(1) as usize)
            }
        },
        None => HeightMapU16::procedural(&spec, spec.resolution.max(1) as usize),
    };
    let mut grid = match BrushGrid::from_height_map(
        &map,
        spec.world_size,
        spec.max_height,
        spec.height_smoothing,
    ) {
        Ok(grid) => grid,
        Err(error) => {
            error!("terrain grid rejected its heightmap: {error:#}");
            return;
        }
    };

    // 2. Features (pads → water → roads).
    let result = apply_features(&mut grid, &pending.features);

    // 3. Entities. Assets are removed/reinserted to avoid aliasing `&mut World`
    //    (same pattern as `spawn::startup`).
    let mut meshes = world
        .remove_resource::<Assets<Mesh>>()
        .expect("Assets<Mesh> exists before startup systems run");
    let mut materials = world
        .remove_resource::<Assets<StandardMaterial>>()
        .expect("Assets<StandardMaterial> exists before startup systems run");
    let asset_server = world.get_resource::<AssetServer>().cloned();

    let root = world
        .spawn((
            Name::new("terrain"),
            Transform::default(),
            Visibility::Inherited,
        ))
        .id();
    spawn_chunks(
        world,
        &mut meshes,
        &mut materials,
        asset_server.as_ref(),
        root,
        &spec,
        &grid,
    );
    spawn_water(
        world,
        &mut meshes,
        &mut materials,
        root,
        &pending.features,
        &result,
    );
    spawn_roads(
        world,
        &mut meshes,
        &mut materials,
        asset_server.as_ref(),
        root,
        &grid,
        &result,
    );

    world.insert_resource(meshes);
    world.insert_resource(materials);
    world.insert_resource(TerrainRuntime {
        spec,
        grid,
        water: result.water,
        roads: result.roads,
        pads: result.pads,
    });
}

/// LOD 0 grid step in whole meters (the chunk builder works on integer
/// steps); `resolution` finer than 1 m/vertex clamps to 1.
fn lod0_step(spec: &TerrainSpec) -> usize {
    let ideal = spec.chunk_size / spec.resolution.max(1) as f32;
    let step = ideal.round().max(1.0) as usize;
    if (spec.chunk_size / step as f32).abs().fract() > 1e-3 {
        1
    } else {
        step
    }
}

fn spawn_chunks(
    world: &mut World,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    asset_server: Option<&AssetServer>,
    parent: Entity,
    spec: &TerrainSpec,
    grid: &BrushGrid,
) {
    let step = lod0_step(spec);
    let segments = (spec.chunk_size / step as f32).round() as usize;
    if segments == 0 {
        warn!("terrain chunk size is smaller than one grid step — no chunks");
        return;
    }
    let edge = segments as f32 * step as f32;
    let rows = (spec.world_size / edge).ceil().max(1.0) as u32;
    let tint = TintParams::from(&spec.tint);
    let epsilon = grid.texel();

    let mut material = StandardMaterial {
        base_color: spec.tint.base_color,
        metallic: 0.0,
        perceptual_roughness: 0.95,
        ..StandardMaterial::default()
    };
    if let (Some(server), Some(texture)) = (asset_server, spec.texture.as_deref()) {
        material.base_color_texture = Some(server.load(texture.to_string()));
    }
    let terrain_material = materials.add(material);

    let half = spec.world_size * 0.5;
    for cz in 0..rows {
        for cx in 0..rows {
            let origin = Vec3::new(-half + cx as f32 * edge, 0.0, -half + cz as f32 * edge);
            let params = ChunkMeshParams {
                origin,
                size: edge,
                lod_step: step,
                skirt_depth: spec.skirt_depth_meters(),
                normal_epsilon: epsilon,
                texture_tile_size: spec.texture_tile_size,
                levels: spec.levels,
                tint: tint.clone(),
                world_size: spec.world_size,
            };
            let data = match build_chunk_mesh(grid, &params) {
                Ok(Some(data)) => data,
                Ok(None) => continue,
                Err(error) => {
                    warn!("chunk ({cx},{cz}) failed to build: {error:#}");
                    continue;
                }
            };
            let handle = meshes.add(to_bevy_mesh(&data));
            world
                .spawn((
                    Name::new(format!("chunk {cz}-{cx}")),
                    // Mesh positions are chunk-center relative on XZ.
                    Transform::from_translation(Vec3::new(
                        origin.x + edge * 0.5,
                        0.0,
                        origin.z + edge * 0.5,
                    )),
                    Visibility::Inherited,
                    ChildOf(parent),
                ))
                .insert((Mesh3d(handle), MeshMaterial3d(terrain_material.clone())));
        }
    }
}

fn spawn_water(
    world: &mut World,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    parent: Entity,
    features: &super::features::TerrainFeatures,
    result: &FeatureResult,
) {
    if result.water.is_empty() {
        return;
    }
    let water_material = materials.add(StandardMaterial {
        base_color: Color::WHITE,
        metallic: 0.0,
        perceptual_roughness: 0.08,
        reflectance: 0.5,
        alpha_mode: bevy::material::AlphaMode::Blend,
        cull_mode: None,
        ..StandardMaterial::default()
    });
    let n_lakes = features.lakes.len();

    for (i, lake) in features.lakes.iter().enumerate() {
        let Some(body) = result.water.get(i) else {
            continue;
        };
        let mesh = lake_water_mesh(lake, body.water_y);
        if mesh.indices.is_empty() {
            continue;
        }
        let handle = meshes.add(to_bevy_mesh(&mesh));
        world
            .spawn((
                Name::new(format!("lake {i}")),
                Transform::default(),
                Visibility::Inherited,
                ChildOf(parent),
            ))
            .insert((Mesh3d(handle), MeshMaterial3d(water_material.clone())));
    }
    for (i, river) in features.rivers.iter().enumerate() {
        let Some(body) = result.water.get(n_lakes + i) else {
            continue;
        };
        let mesh = river_water_mesh(river, body);
        if mesh.indices.is_empty() {
            continue;
        }
        let handle = meshes.add(to_bevy_mesh(&mesh));
        world
            .spawn((
                Name::new(format!("river {i}")),
                Transform::default(),
                Visibility::Inherited,
                ChildOf(parent),
            ))
            .insert((Mesh3d(handle), MeshMaterial3d(water_material.clone())));
    }
}

fn spawn_roads(
    world: &mut World,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    asset_server: Option<&AssetServer>,
    parent: Entity,
    grid: &BrushGrid,
    result: &FeatureResult,
) {
    for (i, (path, spec)) in result.roads.iter().zip(&result.road_specs).enumerate() {
        let mesh = super::roads::road_ribbon_mesh(grid, path, spec);
        if mesh.indices.is_empty() {
            continue;
        }
        let mut material = StandardMaterial {
            base_color: Color::srgb(0.62, 0.60, 0.56), // stone fallback
            metallic: 0.0,
            perceptual_roughness: 0.9,
            alpha_mode: bevy::material::AlphaMode::Blend,
            ..StandardMaterial::default()
        };
        if let (Some(server), Some(texture)) = (asset_server, spec.texture.as_deref()) {
            material.base_color_texture = Some(server.load(texture.to_string()));
        }
        let handle = materials.add(material);
        let mesh_handle = meshes.add(to_bevy_mesh(&mesh));
        world
            .spawn((
                Name::new(path.name.clone().unwrap_or_else(|| format!("road {i}"))),
                Transform::default(),
                Visibility::Inherited,
                ChildOf(parent),
            ))
            .insert((Mesh3d(mesh_handle), MeshMaterial3d(handle)));
    }
}

/// Converts pure [`super::mesh::ChunkMeshData`] buffers into a Bevy mesh
/// (CPU-resident, so tests/tools can inspect it).
fn to_bevy_mesh(data: &super::mesh::ChunkMeshData) -> Mesh {
    let mut mesh = Mesh::new(
        PrimitiveTopology::TriangleList,
        RenderAssetUsages::MAIN_WORLD,
    );
    mesh.insert_attribute(Mesh::ATTRIBUTE_POSITION, data.positions.clone());
    mesh.insert_attribute(Mesh::ATTRIBUTE_NORMAL, data.normals.clone());
    mesh.insert_attribute(Mesh::ATTRIBUTE_UV_0, data.uvs.clone());
    mesh.insert_attribute(Mesh::ATTRIBUTE_COLOR, data.colors.clone());
    mesh.insert_indices(Indices::U32(data.indices.clone()));
    mesh
}

/// Loads a PNG heightmap (8-bit grayscale is upscaled to the full 16-bit
/// range like the VibeGame loader). Relative paths resolve against the world
/// XML directory first, then the process CWD.
fn load_heightmap(base_dir: Option<&Path>, path: &str) -> anyhow::Result<HeightMapU16> {
    if path.to_ascii_lowercase().ends_with(".ahgt") {
        anyhow::bail!(
            "the packed `.ahgt` format is not decoded natively yet — export a 16-bit PNG heightmap"
        );
    }
    let resolved = match base_dir {
        Some(dir) if !path.starts_with('/') => {
            let candidate = dir.join(path);
            if candidate.exists() {
                candidate
            } else {
                PathBuf::from(path)
            }
        }
        _ => PathBuf::from(path),
    };
    let bytes =
        std::fs::read(&resolved).map_err(|e| anyhow::anyhow!("{}: {e}", resolved.display()))?;
    let img = image::load_from_memory(&bytes)
        .map_err(|e| anyhow::anyhow!("{}: {e}", resolved.display()))?;
    let (width, depth) = (img.width() as usize, img.height() as usize);
    let data = img.to_luma16().into_raw();
    Ok(HeightMapU16 { width, depth, data })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::terrain::spec::TerrainSpec;

    #[test]
    fn test_lod0_step_matches_chunk_division() {
        let mut spec = TerrainSpec {
            resolution: 64, // 64 m chunks
            ..Default::default()
        };
        assert_eq!(lod0_step(&spec), 1);
        spec.resolution = 32;
        assert_eq!(lod0_step(&spec), 2);
        spec.resolution = 128; // finer than 1 m: clamps to the 1 m step
        assert_eq!(lod0_step(&spec), 1);
        spec.chunk_size = 50.0;
        spec.resolution = 20;
        assert_eq!(lod0_step(&spec), 1, "50/2.5 is not an exact meter step");
    }

    /// End-to-end smoke: PendingTerrain (procedural terrain + all features)
    /// through the exclusive bootstrap — chunks, water and road entities land
    /// with registries, fully headless.
    #[test]
    fn test_bootstrap_builds_a_carved_world_headless() {
        use crate::recipes::spawn::PendingTerrain;
        use bevy::math::Vec2;

        let mut app = bevy::app::App::new();
        app.add_plugins(bevy::MinimalPlugins)
            .add_plugins(bevy::transform::TransformPlugin)
            .init_resource::<Assets<Mesh>>()
            .init_resource::<Assets<StandardMaterial>>();

        let features = super::super::features::TerrainFeatures {
            pads: vec![crate::terrain::TerrainPadSpec {
                at: Vec2::ZERO,
                size: Vec2::splat(24.0),
                falloff: 8.0,
                corner_radius: 4.0,
                height: None,
            }],
            lakes: vec![crate::terrain::LakeSpec {
                at: Vec2::new(-30.0, 30.0),
                radius: 10.0,
                ..crate::terrain::LakeSpec::default()
            }],
            rivers: vec![crate::terrain::RiverSpec {
                path: vec![Vec2::new(10.0, -40.0), Vec2::new(50.0, -40.0)],
                ..crate::terrain::RiverSpec::default()
            }],
            roads: vec![],
            networks: vec![crate::terrain::RoadNetworkSpec {
                ways: vec![
                    crate::terrain::WaySpec {
                        id: "a".into(),
                        at: Vec2::new(-30.0, 20.0),
                        width: None,
                    },
                    crate::terrain::WaySpec {
                        id: "b".into(),
                        at: Vec2::new(30.0, 20.0),
                        width: None,
                    },
                ],
                segments: vec![crate::terrain::SegmentSpec {
                    a: "a".into(),
                    b: "b".into(),
                    via: Vec::new(),
                    width: None,
                    profile: None,
                }],
                ..crate::terrain::RoadNetworkSpec::default()
            }],
        };
        app.insert_resource(PendingTerrain {
            base_dir: None,
            terrain: Some(TerrainSpec {
                world_size: 128.0,
                max_height: 40.0,
                chunk_size: 64.0,
                seed: 3,
                ..TerrainSpec::default()
            }),
            features,
        });

        app.add_systems(bevy::app::Startup, bootstrap);
        app.update(); // MinimalPlugins Startup runs on the first update

        let runtime = app
            .world()
            .get_resource::<TerrainRuntime>()
            .expect("terrain built");
        assert_eq!(runtime.water.len(), 2, "lake + river registered");
        assert_eq!(runtime.roads.len(), 1, "network expanded into a road");
        assert_eq!(runtime.pads.len(), 1);
        // Pad core is flat.
        assert!((runtime.grid.sample(0.0, 0.0) - runtime.pads[0].height).abs() < 0.05);
        // Lake bowl is below its mirror.
        assert!(runtime.sample(-30.0, 30.0) < runtime.water[0].water_y);
        assert!(runtime.in_water(-30.0, 30.0));
        assert!(runtime.on_road(0.0, 20.0));
        assert!(!runtime.on_road(0.0, 60.0));

        // Entities: terrain root + chunks + lake/river/road visuals.
        let world = app.world_mut();
        let names: Vec<String> = world
            .query::<&Name>()
            .iter(world)
            .map(|n| n.to_string())
            .collect();
        assert!(names.iter().any(|n| n.starts_with("chunk ")), "{names:?}");
        assert!(names.iter().any(|n| n == "lake 0"), "{names:?}");
        assert!(names.iter().any(|n| n == "river 0"), "{names:?}");
        assert!(names.iter().any(|n| n.contains("net/a-b")), "{names:?}");
    }
}
