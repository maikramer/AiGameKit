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
use bevy::light::NotShadowCaster;
use bevy::prelude::*;
use bevy::render::mesh::{Indices, PrimitiveTopology};

use super::brush::BrushGrid;
use super::features::{FeatureResult, apply_features};
use super::heightmap::HeightMapU16;
use bevy::image::{ImageAddressMode, ImageSampler, ImageSamplerDescriptor};

use super::mesh::{ChunkMeshParams, build_chunk_mesh};
use super::roads::RoadPath;
use super::sampler::ResolvedPad;
use super::spec::TerrainSpec;
use super::water::{WaterBody, lake_water_mesh, river_water_mesh};
use crate::recipes::spawn::PendingTerrain;

/// Materials whose `base_color_texture` is still loading, so a texture that
/// never arrives can be dropped instead of blanking what it was painting.
///
/// Bevy will not prepare a `StandardMaterial` until every texture it
/// references is resident, and a mesh with an unprepared material is simply
/// not drawn. A single missing PNG therefore makes the whole terrain
/// disappear — which is exactly what `simple-rpg` hit: it asks for
/// `vale_grass.png`, the file is not in the pool, and the entire 4000 m world
/// rendered as empty sky.
#[derive(Resource, Default)]
pub struct PendingTerrainTextures {
    /// `(material, texture)` pairs still waiting on their image.
    pub watched: Vec<(Handle<StandardMaterial>, Handle<Image>)>,
}

/// Sampler para texturas com UV em unidades de mundo (terrain `world/tile`,
/// road/river `arc/scale`): sem REPEAT os UVs >> 1.0 clampeiam na borda e a
/// textura estica numa mancha única — a ribbon da estrada ficava "quebrada".
pub fn world_tiled_sampler() -> ImageSampler {
    ImageSampler::Descriptor(ImageSamplerDescriptor {
        address_mode_u: ImageAddressMode::Repeat,
        address_mode_v: ImageAddressMode::Repeat,
        ..ImageSamplerDescriptor::default()
    })
}

/// Drops textures that failed to load from the materials referencing them.
///
/// The material then falls back to its vertex-color tint (terrain) or flat
/// base color (roads) — a texture-less surface instead of no surface.
pub fn drop_failed_terrain_textures(
    server: Res<AssetServer>,
    mut pending: ResMut<PendingTerrainTextures>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut images: ResMut<Assets<Image>>,
) {
    if pending.watched.is_empty() {
        return;
    }
    pending.watched.retain(|(material, texture)| {
        match server.get_load_state(texture) {
            Some(bevy::asset::LoadState::Failed(error)) => {
                warn!(
                    "terrain texture failed to load ({error}); rendering untextured \
                     instead of hiding the surface"
                );
                if let Some(mut material) = materials.get_mut(material) {
                    material.base_color_texture = None;
                }
                false
            }
            Some(bevy::asset::LoadState::Loaded) => {
                // Toda textura watched usa UV em metros — repete em vez de
                // clampear (mutar o asset re-dispara o upload da GPU).
                if let Some(mut image) = images.get_mut(texture) {
                    image.sampler = world_tiled_sampler();
                }
                false
            }
            _ => true,
        }
    });
}

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
        app.init_resource::<PendingTerrainTextures>()
            .add_systems(
                bevy::app::Startup,
                bootstrap.after(crate::recipes::spawn::startup),
            )
            .add_systems(bevy::app::Update, drop_failed_terrain_textures);
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
    let mut spec = spec.clone();
    let map = match &spec.heightmap {
        Some(path) => match load_heightmap(pending.base_dir.as_deref(), path) {
            Ok(loaded) => {
                // The heightmap file describes its own coverage, but the world
                // XML wins when it states one: pads, lakes, rivers, roads and
                // biome polygons are all authored in the XML's coordinate
                // space. `simple-rpg` asks for 4000 m while its `.ahgt` header
                // claims 8000 — taking the file stretched the whole world to
                // double scale and quadrupled the chunk grid (63² → 125²).
                let authored = spec.extent_authored;
                for (label, from_file, target) in [
                    ("world-size", loaded.world_size, &mut spec.world_size),
                    ("max-height", loaded.max_height, &mut spec.max_height),
                ] {
                    let Some(from_file) = from_file else { continue };
                    if !authored {
                        *target = from_file;
                    } else if (from_file - *target).abs() > 1e-3 {
                        warn!(
                            "heightmap `{path}` declares {label} {from_file}, the world XML says \
                             {} — keeping the authored value",
                            *target
                        );
                    }
                }
                loaded.map
            }
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

    let mut watched: Vec<(Handle<StandardMaterial>, Handle<Image>)> = Vec::new();

    // Chunk LODs are picked against the camera the world spawned, so a large
    // world does not materialise at full detail (see `spawn_chunks`).
    let camera_xz = world
        .query_filtered::<&Transform, With<Camera3d>>()
        .iter(world)
        .next()
        .map(|t| Vec2::new(t.translation.x, t.translation.z));

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
        camera_xz,
        &mut watched,
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
        &mut watched,
    );

    world.insert_resource(PendingTerrainTextures { watched });
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

/// Spawns the terrain's chunk meshes.
///
/// Each chunk is built at the LOD its distance from `camera_xz` implies — the
/// same selection [`super::plugin`] applies every frame — and chunks past
/// `render_distance` are left for the plugin to spawn on approach. Building
/// the whole field at LOD 0 is not viable: `simple-rpg` is a 4000 m world with
/// the default 64 m chunk, i.e. 3969 chunks, and at LOD 0 that is ~18 M
/// vertices of resident mesh — the host ran out of memory before the window
/// ever opened. Picking LODs up front keeps distant chunks at a few hundred
/// vertices each.
#[allow(clippy::too_many_arguments)]
fn spawn_chunks(
    world: &mut World,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    asset_server: Option<&AssetServer>,
    parent: Entity,
    spec: &TerrainSpec,
    grid: &BrushGrid,
    camera_xz: Option<Vec2>,
    watched: &mut Vec<(Handle<StandardMaterial>, Handle<Image>)>,
) {
    let step = lod0_step(spec);
    let segments = (spec.chunk_size / step as f32).round() as usize;
    if segments == 0 {
        warn!("terrain chunk size is smaller than one grid step — no chunks");
        return;
    }
    let edge = segments as f32 * step as f32;
    let rows = (spec.world_size / edge).ceil().max(1.0) as u32;
    let epsilon = grid.texel();

    let mut material = StandardMaterial {
        // White: the authored `base-color` is already folded into the chunk
        // vertex colours by `tint_vertex_color`, and the PBR shader multiplies
        // those in. Putting it here as well would square it.
        base_color: Color::WHITE,
        metallic: 0.0,
        perceptual_roughness: 0.95,
        ..StandardMaterial::default()
    };
    let mut texture_handle = None;
    if let (Some(server), Some(texture)) = (asset_server, spec.texture.as_deref()) {
        // strip leading '/' — bevy treats root-absolute asset paths as unapproved
        let handle: Handle<Image> = server.load(texture.trim_start_matches('/').to_string());
        texture_handle = Some(handle.clone());
        material.base_color_texture = Some(handle);
    }
    let terrain_material = materials.add(material);
    if let Some(texture) = texture_handle {
        watched.push((terrain_material.clone(), texture));
    }

    let half = spec.world_size * 0.5;
    let max_lod = super::plugin::max_lod_for(spec, edge);
    let margin = super::plugin::hysteresis_margin(spec);
    for cz in 0..rows {
        for cx in 0..rows {
            let origin = Vec3::new(-half + cx as f32 * edge, 0.0, -half + cz as f32 * edge);
            let center = Vec2::new(origin.x + edge * 0.5, origin.z + edge * 0.5);
            let distance = camera_xz.map(|cam| cam.distance(center));
            if let Some(distance) = distance {
                // The plugin spawns these at LOD 0 once the camera approaches.
                if distance > spec.effective_render_distance() {
                    continue;
                }
            }
            // Without a camera every chunk is "near"; that only happens in
            // headless setups with no view, where LOD 0 is the safe answer.
            let lod = distance
                .map(|d| super::plugin::select_lod(d, spec.lod_distance(), 0, max_lod, margin))
                .unwrap_or(0);
            let params = ChunkMeshParams {
                origin,
                size: edge,
                lod_step: step << lod,
                skirt_depth: spec.skirt_depth_meters(),
                normal_epsilon: epsilon,
                texture_tile_size: spec.texture_tile_size,
                levels: spec.levels,
                world_size: spec.world_size,
                tint: (&spec.tint).into(),
            };
            // A LOD step that does not divide the chunk edge yields no mesh;
            // fall back to LOD 0, which always does.
            let data = match build_chunk_mesh(grid, &params) {
                Ok(Some(data)) => data,
                Ok(None) | Err(_) if lod > 0 => {
                    let params = ChunkMeshParams {
                        lod_step: step,
                        ..params
                    };
                    match build_chunk_mesh(grid, &params) {
                        Ok(Some(data)) => data,
                        _ => continue,
                    }
                }
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

#[allow(clippy::too_many_arguments)]
fn spawn_roads(
    world: &mut World,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    asset_server: Option<&AssetServer>,
    parent: Entity,
    grid: &BrushGrid,
    result: &FeatureResult,
    watched: &mut Vec<(Handle<StandardMaterial>, Handle<Image>)>,
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
        let mut texture_handle = None;
        if let (Some(server), Some(texture)) = (asset_server, spec.texture.as_deref()) {
            // strip leading '/' — bevy treats root-absolute asset paths as unapproved
            let image: Handle<Image> = server.load(texture.trim_start_matches('/').to_string());
            texture_handle = Some(image.clone());
            material.base_color_texture = Some(image);
        }
        let handle = materials.add(material);
        if let Some(texture) = texture_handle {
            watched.push((handle.clone(), texture));
        }
        let mesh_handle = meshes.add(to_bevy_mesh(&mesh));
        world
            .spawn((
                Name::new(path.name.clone().unwrap_or_else(|| format!("road {i}"))),
                Transform::default(),
                Visibility::Inherited,
                // estrada não projeta sombra dura — a sombra própria
                // escurecia as bordas na relva (polish loop 6)
                NotShadowCaster,
                ChildOf(parent),
            ))
            .insert((Mesh3d(mesh_handle), MeshMaterial3d(handle)));
    }

    // Fusion discs (VibeGame junctions.ts): círculo opaco nas junções multi-braço
    // cobrindo as costuras das ribbons.
    for (i, junction) in result.road_junctions.iter().enumerate() {
        let mesh = super::roads::junction_disc_mesh(grid, junction);
        if mesh.indices.is_empty() {
            continue;
        }
        let mut material = StandardMaterial {
            base_color: Color::srgb(0.62, 0.60, 0.56),
            metallic: 0.0,
            perceptual_roughness: 0.9,
            alpha_mode: bevy::material::AlphaMode::Blend,
            ..StandardMaterial::default()
        };
        let mut texture_handle = None;
        if let (Some(server), Some(texture)) = (asset_server, junction.texture.as_deref()) {
            let image: Handle<Image> = server.load(texture.trim_start_matches('/').to_string());
            texture_handle = Some(image.clone());
            material.base_color_texture = Some(image);
        }
        let handle = materials.add(material);
        if let Some(texture) = texture_handle {
            watched.push((handle.clone(), texture));
        }
        let mesh_handle = meshes.add(to_bevy_mesh(&mesh));
        world
            .spawn((
                Name::new(format!("junction {i}")),
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
        RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
    );
    mesh.insert_attribute(Mesh::ATTRIBUTE_POSITION, data.positions.clone());
    mesh.insert_attribute(Mesh::ATTRIBUTE_NORMAL, data.normals.clone());
    mesh.insert_attribute(Mesh::ATTRIBUTE_UV_0, data.uvs.clone());
    // Only surfaces that actually use vertex colours get the attribute:
    // terrain chunks carry the height/slope tint, roads their edge alpha.
    if !data.colors.is_empty() {
        mesh.insert_attribute(Mesh::ATTRIBUTE_COLOR, data.colors.clone());
    }
    mesh.insert_indices(Indices::U32(data.indices.clone()));
    mesh
}

/// Loads a PNG heightmap (8-bit grayscale is upscaled to the full 16-bit
/// range like the VibeGame loader). Relative paths resolve against the world
/// XML directory first, then the process CWD.
/// Loaded heightmap plus the world size / max height its own metadata
/// declares. These only fill the spec when the world XML did not state them
/// (`TerrainSpec::extent_authored`); an authored extent always wins.
pub struct LoadedHeightmap {
    pub map: HeightMapU16,
    pub world_size: Option<f32>,
    pub max_height: Option<f32>,
}

fn load_heightmap(base_dir: Option<&Path>, path: &str) -> anyhow::Result<LoadedHeightmap> {
    // `/assets/…`-style paths are site-root relative: resolve against the
    // world dir (the folder that contains `assets/`).
    let rel = path.trim_start_matches('/');
    let resolved = match base_dir {
        Some(dir) => {
            let candidate = dir.join(rel);
            if candidate.exists() {
                candidate
            } else {
                PathBuf::from(path)
            }
        }
        None => PathBuf::from(path),
    };
    let bytes =
        std::fs::read(&resolved).map_err(|e| anyhow::anyhow!("{}: {e}", resolved.display()))?;
    if path.to_ascii_lowercase().ends_with(".ahgt") {
        let (map, world_size, max_height) = HeightMapU16::from_ahgt(&bytes)
            .map_err(|e| anyhow::anyhow!("{}: {e}", resolved.display()))?;
        return Ok(LoadedHeightmap {
            map,
            world_size: Some(world_size),
            max_height: Some(max_height),
        });
    }
    let img = image::load_from_memory(&bytes)
        .map_err(|e| anyhow::anyhow!("{}: {e}", resolved.display()))?;
    let (width, depth) = (img.width() as usize, img.height() as usize);
    let data = img.to_luma16().into_raw();
    Ok(LoadedHeightmap {
        map: HeightMapU16 { width, depth, data },
        world_size: None,
        max_height: None,
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn test_world_tiled_sampler_repeats() {
        let sampler = world_tiled_sampler();
        let bevy::image::ImageSampler::Descriptor(desc) = sampler else {
            panic!("expected a concrete sampler descriptor");
        };
        assert_eq!(desc.address_mode_u, bevy::image::ImageAddressMode::Repeat);
        assert_eq!(desc.address_mode_v, bevy::image::ImageAddressMode::Repeat);
    }

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
