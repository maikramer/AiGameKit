//! Spawning: entity IR → Bevy entities, meshes, lights and cameras.

use std::path::PathBuf;

use bevy::asset::LoadState;

use bevy::gltf::Gltf;
use bevy::light::NotShadowCaster;
use bevy::math::primitives::{Capsule3d, Cuboid, Cylinder, Plane3d, Sphere};
use bevy::prelude::*;
use bevy::world_serialization::WorldAssetRoot;

use super::{EntityKind, EntitySpec, MaterialSpec, ParsedWorld, Shape, TransformSpec};
use crate::terrain::TerrainSpec;
use crate::terrain::cliffs::CliffSpec;
use crate::terrain::decal::GroundDecalSpec;
use crate::terrain::features::TerrainFeatures;
use crate::terrain::roads::{RoadNetworkSpec, RoadSpec, SegmentSpec, WaySpec};
use crate::terrain::spec::TerrainPadSpec;
use crate::terrain::voxel::ArchSpec;
use crate::terrain::voxel::BridgeSpec;
use crate::terrain::voxel::CaveSpec;
use crate::terrain::voxel::RockFeaturesSpec;
use crate::terrain::voxel::cave::{CaveChamberSpec, CaveShaftSpec};
use crate::terrain::water::{LakeSpec, RiverSpec};

/// Marker for `<OrbitCamera>`: keeps its offset from a named target.
///
/// Target-less cameras in worlds with a `<PlayerGLTF>` are third-person
/// cameras: [`crate::camera::third_person_camera`] drives them (decoupled
/// follow, terrain collision), and [`crate::player::player_movement`] steers
/// their yaw with A/D. Cameras with a `target` stay on the rigid follow.
#[derive(Debug, Component)]
pub struct OrbitCamera {
    pub target: Option<String>,
    pub distance: f32,
    pub height: f32,
    /// `Some` only when the world set `pitch` explicitly; it overrides
    /// `height` as the ring seed (see [`orbit_camera_follow`]).
    pub pitch_deg: Option<f32>,
    /// Live orbit pitch (degrees) — seeded from `pitch_deg` or from
    /// `atan(height/distance)`.
    pub pitch_state_deg: f32,
    /// Steered orbit yaw (degrees) — A/D in [`crate::player::player_movement`].
    pub yaw_deg: f32,
    /// Degrees per pixel of mouse drag (parsed for XML parity; the runtime
    /// is gamepad-style and never reads the mouse).
    pub mouse_sensitivity: f32,
    pub min_distance: f32,
    pub max_distance: f32,
    // --- third-person smoothing config (VibeGame ThirdPersonCamera) ---
    /// Position follow time constant in seconds (larger = more lag on
    /// sprints).
    pub follow_lag: f32,
    /// Yaw follow time constant in seconds (larger = the camera turns
    /// slower/later than the steering).
    pub turn_lag: f32,
    /// Minimum clearance above the terrain (0 disables camera collision).
    pub min_terrain_distance: f32,
    // --- third-person smoothing state ---
    /// Smoothed follow point the camera orbits and looks at; decoupled from
    /// the raw character transform so view jitter can't accumulate.
    pub follow_point: Vec3,
    /// Lagged yaw the camera actually orbits at; trails [`Self::yaw_deg`].
    pub smooth_yaw_deg: f32,
    /// Current (post-collision, micro-smoothed) camera position.
    pub current_pos: Vec3,
    /// Whether the smoothed state has been seeded onto the target.
    pub initialized: bool,
}

impl OrbitCamera {
    /// Height above the target the camera aims at (meters).
    ///
    /// `height` doubles as the pitch seed when the world gives no explicit
    /// `pitch`; either way it describes how far above the target's origin the
    /// framing sits, so it is the pivot too. Falls back to chest height for
    /// worlds that set neither.
    pub fn pivot_height(&self) -> f32 {
        if self.height > 0.0 {
            self.height
        } else {
            DEFAULT_ORBIT_PIVOT_HEIGHT
        }
    }
}

/// Look-at height used when a world sets no `height` (meters).
pub const DEFAULT_ORBIT_PIVOT_HEIGHT: f32 = 1.2;

/// Camera spawned automatically when the world has none: slow orbit at origin.
#[derive(Debug, Component)]
pub struct AutoOrbit {
    pub yaw: f32,
    pub radius: f32,
    pub height: f32,
}

/// World XML held as a resource until the startup system spawns it.
/// `base_dir` is the world file's directory (heightmap/asset path base).
#[derive(Resource)]
pub struct PendingWorld {
    pub world: ParsedWorld,
    pub base_dir: Option<PathBuf>,
}

/// `<DialogueNPC>` marker: this entity is a dialogue target with the given
/// quest/dialogue id (dialogue UI lands with the HUD phase).
#[derive(Debug, Component)]
pub struct DialogueNpc {
    pub dialogue_id: String,
}

/// Pending `<GltfScene>`: the handle loads async; [`gltf_scene_spawner`]
/// swaps it for a `SceneRoot` once loaded (and drops it on failure).
#[derive(Component)]
pub struct GltfScenePending {
    pub handle: Handle<Gltf>,
}

/// Declarative terrain collected from the entity tree, consumed by the
/// terrain runtime ([`crate::terrain::runtime`]) at startup.
#[derive(Resource, Default)]
pub struct PendingTerrain {
    pub base_dir: Option<PathBuf>,
    pub terrain: Option<TerrainSpec>,
    pub features: TerrainFeatures,
}

fn is_ground_feature(kind: &EntityKind) -> bool {
    matches!(
        kind,
        EntityKind::TerrainPad { .. }
            | EntityKind::Lake { .. }
            | EntityKind::River { .. }
            | EntityKind::Cliff { .. }
            | EntityKind::Cave { .. }
            | EntityKind::Arch { .. }
            | EntityKind::Bridge { .. }
            | EntityKind::RockFeatures { .. }
            | EntityKind::Road { .. }
            | EntityKind::RoadNetwork { .. }
            | EntityKind::GroundDecal { .. }
            | EntityKind::StaticSpawner { .. }
            // Consumidos como specs (grupos de spawn / recurso de
            // exclusões / spec de vegetação) — nunca spawna entidade.
            | EntityKind::DynamicSpawner { .. }
            | EntityKind::SpawnExclusion { .. }
            | EntityKind::Vegetation { .. }
    )
}

/// Collects the terrain spec and every ground feature out of a parsed entity
/// tree into `out` — the same pass `startup` runs, exposed so headless tools
/// and tests can carve a world without booting the app.
pub fn collect_terrain(specs: &[EntitySpec], out: &mut PendingTerrain) {
    collect_walk(specs, Vec2::ZERO, out);
}

/// Walks the entity tree accumulating group translations (groups are
/// transform-only containers). `offset` is the accumulated **ancestor** XZ
/// translation; feature coordinates (`at`, paths) are local to the parent, so
/// the world position is `offset + coordinate` (VibeGame `at` semantics).
fn collect_walk(specs: &[EntitySpec], offset: Vec2, out: &mut PendingTerrain) {
    for spec in specs {
        let child_offset =
            offset + Vec2::new(spec.transform.translation[0], spec.transform.translation[2]);
        match &spec.kind {
            EntityKind::Terrain { spec: terrain } => {
                if spec.transform.translation != [0.0, 0.0, 0.0] {
                    // Native terrain centers the heightfield at the origin;
                    // the heightmap itself defines the alignment.
                    bevy::log::warn!(
                        "<Terrain translation>: ignored — native terrain centers at the origin"
                    );
                }
                out.terrain = Some(terrain.clone());
            }
            EntityKind::TerrainPad { spec: pad } => {
                out.features.pads.push(TerrainPadSpec {
                    at: pad.at + offset,
                    ..pad.clone()
                });
            }
            EntityKind::GroundDecal { spec: dec } => {
                out.features.decals.push(GroundDecalSpec {
                    at: dec.at + offset,
                    ..dec.clone()
                });
            }
            EntityKind::Lake { spec: lake } => {
                out.features.lakes.push(LakeSpec {
                    at: lake.at + offset,
                    ..lake.clone()
                });
            }
            EntityKind::River { spec: river } => {
                out.features.rivers.push(RiverSpec {
                    path: river.path.iter().map(|p| *p + offset).collect(),
                    ..river.clone()
                });
            }
            EntityKind::Cliff { spec: cliff } => {
                out.features.cliffs.push(CliffSpec {
                    path: cliff.path.iter().map(|p| *p + offset).collect(),
                    ..cliff.clone()
                });
            }
            EntityKind::Cave { spec: cave } => {
                // Rooms and chimneys ride the same group offset as the tunnel;
                // shifting only the path would leave them behind in the world.
                out.features.caves.push(CaveSpec {
                    path: cave.path.iter().map(|p| *p + offset).collect(),
                    chambers: cave
                        .chambers
                        .iter()
                        .map(|c| CaveChamberSpec {
                            at: c.at + offset,
                            ..c.clone()
                        })
                        .collect(),
                    shafts: cave
                        .shafts
                        .iter()
                        .map(|c| CaveShaftSpec {
                            at: c.at + offset,
                            ..c.clone()
                        })
                        .collect(),
                    ..cave.clone()
                });
            }
            EntityKind::RockFeatures { spec: rocks } => {
                out.features.rock_fields.push(RockFeaturesSpec {
                    min: rocks.min + offset,
                    max: rocks.max + offset,
                    ..rocks.clone()
                });
            }
            EntityKind::Bridge { spec: bridge } => {
                out.features.bridges.push(BridgeSpec {
                    path: bridge.path.iter().map(|p| *p + offset).collect(),
                    ..bridge.clone()
                });
            }
            EntityKind::Arch { spec: arch } => {
                out.features.arches.push(ArchSpec {
                    at: arch.at + offset,
                    ..arch.clone()
                });
            }
            EntityKind::Road { spec: road } => {
                out.features.roads.push(RoadSpec {
                    path: road.path.iter().map(|p| *p + offset).collect(),
                    ..road.clone()
                });
            }
            EntityKind::RoadNetwork { spec: net } => {
                out.features.networks.push(RoadNetworkSpec {
                    ways: net
                        .ways
                        .iter()
                        .map(|w| WaySpec {
                            id: w.id.clone(),
                            at: w.at + offset,
                            width: w.width,
                        })
                        .collect(),
                    segments: net
                        .segments
                        .iter()
                        .map(|seg| SegmentSpec {
                            via: seg.via.iter().map(|p| *p + offset).collect(),
                            ..seg.clone()
                        })
                        .collect(),
                    ..net.clone()
                });
            }
            _ => {}
        }
        collect_walk(&spec.children, child_offset, out);
    }
}

/// Stats from a world spawn.
#[derive(Debug, Default)]
pub struct SpawnStats {
    pub entities: usize,
    pub has_camera: bool,
}

/// Exclusive startup system: consumes [`PendingWorld`] and builds the scene.
pub fn startup(world: &mut World) {
    let Some(pending) = world.remove_resource::<PendingWorld>() else {
        return;
    };
    let parsed = pending.world;
    let world_dir = pending.base_dir.clone();
    // Ground features are collected (not spawned) for the terrain runtime.
    let mut pending_terrain = PendingTerrain {
        base_dir: pending.base_dir.clone(),
        ..PendingTerrain::default()
    };
    collect_terrain(&parsed.entities, &mut pending_terrain);
    world.insert_resource(pending_terrain);
    // O save por-mundo precisa da pasta do world.xml DEPOIS de o bootstrap
    // do terreno remover `PendingTerrain` (save.rs::WorldBaseDir).
    world.insert_resource(crate::save::WorldBaseDir(pending.base_dir));
    if let Some([r, g, b]) = parsed.clear_color {
        world.insert_resource(ClearColor(Color::srgb(r, g, b)));
    }
    // Assets are removed/reinserted so spawning and handle creation never
    // alias `&mut World`.
    let mut meshes = world
        .remove_resource::<Assets<Mesh>>()
        .expect("Assets<Mesh> exists before startup systems run");
    let mut materials = world
        .remove_resource::<Assets<StandardMaterial>>()
        .expect("Assets<StandardMaterial> exists before startup systems run");
    let mut sky_mats = world
        .remove_resource::<Assets<crate::sky::SkyMaterial>>()
        .expect("Assets<SkyMaterial> exists before startup systems run");
    let asset_server = world
        .remove_resource::<AssetServer>()
        .expect("AssetServer exists before startup systems run");
    let mut tiled = world
        .remove_resource::<crate::textures::WorldTiledTextures>()
        .expect("WorldTiledTextures exists before startup systems run");
    // `<StaticSpawner>` groups: collect specs and start their template loads;
    // `spawner::instantiate_spawn_groups` places instances once the terrain
    // runtime and the assets are ready.
    let mut spawn_groups = Vec::new();
    let mut exclusions = Vec::new();
    collect_spawn_groups(
        &parsed.entities,
        &asset_server,
        &mut spawn_groups,
        &mut exclusions,
    );
    world.insert_resource(crate::spawner::PendingSpawnGroups {
        groups: spawn_groups,
        exclusions,
        occupancy: crate::spawner::SpawnOccupancy::new(),
        age: 0.0,
    });
    let mut stats = SpawnStats::default();
    let mut ambient: Option<GlobalAmbientLight> = None;
    let (
        chips,
        hud_elements,
        ui_styles,
        ui_trees,
        ui_tree_sheets,
        mixer_settings,
        pending_worldsys,
        _sky_request,
    ) = {
        let mut ctx = SpawnCtx {
            meshes: &mut meshes,
            materials: &mut materials,
            sky_mats: &mut sky_mats,
            asset_server: &asset_server,
            game_config: world.get_resource::<crate::config::GameConfig>().cloned(),
            tiled: &mut tiled,
            mesh_cache: std::collections::HashMap::new(),
            material_cache: std::collections::HashMap::new(),
            chip_counter: std::cell::Cell::new(0),
            mixer: std::cell::RefCell::new(None),
            chips: std::cell::RefCell::new(Vec::new()),
            hud: std::cell::RefCell::new(Vec::new()),
            ui_styles: std::cell::RefCell::new(Vec::new()),
            ui_pending_sheets: std::cell::RefCell::new(Vec::new()),
            ui_tree_sheets: std::cell::RefCell::new(Vec::new()),
            ui_trees: std::cell::RefCell::new(Vec::new()),
            worldsys: Default::default(),
            sky_request: std::cell::RefCell::new(None),
        };
        for spec in &parsed.entities {
            if is_ground_feature(&spec.kind) {
                continue;
            }
            spawn_entity(world, &mut ctx, spec, None, &mut stats, &mut ambient);
        }
        // `<Sky>`: domo procedural (o shader especializado com a config do
        // mundo é escrito no world dir pelo run()).
        if ctx.sky_request.take().is_some() {
            crate::sky::build_sky(world, ctx.meshes, ctx.sky_mats);
        }
        let hud_elements = ctx.hud.into_inner();
        let ui_styles = ctx.ui_styles.into_inner();
        let ui_tree_sheets = ctx.ui_tree_sheets.into_inner();
        let ui_trees = ctx.ui_trees.into_inner();
        let mixer = ctx.mixer.into_inner();
        let pending_worldsys = ctx.worldsys.consume();
        let sky_request = ctx.sky_request.into_inner();
        (
            ctx.chips.into_inner(),
            hud_elements,
            ui_styles,
            ui_trees,
            ui_tree_sheets,
            mixer,
            pending_worldsys,
            sky_request,
        )
    };
    if let Some(light) = ambient {
        world.insert_resource(light);
    }
    for (index, resource) in chips {
        crate::hud::spawn_resource_chip(world, index, &resource);
    }
    for (tag, attrs) in &hud_elements {
        crate::hud::spawn_hud(world, tag, attrs);
    }
    build_declarative_ui(
        world,
        &ui_styles,
        &ui_trees,
        &ui_tree_sheets,
        world_dir.as_deref(),
        &asset_server,
    );
    if let Some(day) = pending_worldsys.day_cycle {
        world.insert_resource(day);
    }
    if let Some(weather) = pending_worldsys.weather {
        world.insert_resource(weather);
    }
    if let Some(border) = pending_worldsys.border {
        world.insert_resource(border);
    }
    if let Some(scene) = pending_worldsys.interior_scene {
        world.insert_resource(scene);
    }
    if !pending_worldsys.biomes.is_empty() {
        world.insert_resource(crate::worldsys::BiomeRegions {
            list: pending_worldsys.biomes.clone(),
        });
    }
    for config in pending_worldsys.configs {
        world.insert_resource(config);
    }
    // Always present, `<AudioMixer>` or not — systems take it as `ResMut`.
    world.insert_resource(mixer_settings.unwrap_or_default());
    world.insert_resource(meshes);
    world.insert_resource(materials);
    world.insert_resource(sky_mats);
    world.insert_resource(asset_server);
    world.insert_resource(tiled);
    if !stats.has_camera {
        world.spawn((
            Camera3d::default(),
            Transform::default(),
            AutoOrbit {
                yaw: 0.0,
                radius: 12.0,
                height: 6.0,
            },
        ));
    }
}

/// Raw attributes of one HUD element.
pub type HudAttrs = Vec<(String, String)>;
/// Deferred HUD requests: (tag, attrs).
pub type HudList = Vec<(String, HudAttrs)>;

/// Chave de cache de malha de primitiva: a forma + dimensões (bits de f32
/// — duas primitivas "iguais" no XML são o mesmo asset).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum MeshKey {
    Cuboid([u32; 3]),
    Sphere(u32),
    Cylinder(u32, u32),
    Plane([u32; 2]),
    Capsule(u32, u32),
}

fn mesh_key(shape: &Shape) -> MeshKey {
    let bits = |v: f32| v.to_bits();
    match shape {
        Shape::Cuboid { half_size } => MeshKey::Cuboid(half_size.map(bits)),
        Shape::Sphere { radius } => MeshKey::Sphere(bits(*radius)),
        Shape::Cylinder {
            half_height,
            radius,
        } => MeshKey::Cylinder(bits(*half_height), bits(*radius)),
        Shape::Plane { half_size } => MeshKey::Plane(half_size.map(bits)),
        Shape::Capsule {
            radius,
            half_height,
        } => MeshKey::Capsule(bits(*radius), bits(*half_height)),
    }
}

/// Chave de cache de materiais: a spec canonizada em bits (f32 não é
/// `Eq`/`Hash`) + a flag de UV world-space, que muda a textura carregada.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct MaterialKey {
    base_color: Option<[u32; 3]>,
    metallic: Option<u32>,
    roughness: Option<u32>,
    opacity: Option<u32>,
    emissive: Option<[u32; 3]>,
    texture: Option<String>,
    texture_tile: Option<u32>,
    normal_map: Option<String>,
    world_tiled: bool,
}

fn material_key(spec: &MaterialSpec, world_tiled: bool) -> MaterialKey {
    let bits = |v: f32| v.to_bits();
    MaterialKey {
        base_color: spec.base_color.map(|c| c.map(bits)),
        metallic: spec.metallic.map(bits),
        roughness: spec.roughness.map(bits),
        opacity: spec.opacity.map(bits),
        emissive: spec.emissive.map(|c| c.map(bits)),
        texture: spec.texture.clone(),
        texture_tile: spec.texture_tile.map(bits),
        normal_map: spec.normal_map.clone(),
        world_tiled,
    }
}

/// Borrowed asset handles used while spawning one world.
struct SpawnCtx<'a> {
    meshes: &'a mut Assets<Mesh>,
    materials: &'a mut Assets<StandardMaterial>,
    /// `<Sky>` dome material — taken out of the world alongside the other
    /// asset collections so `build_sky` never has to remove a resource that
    /// `startup` already holds (a nested removal panicked and left
    /// `Assets<Mesh>` missing for the terrain bootstrap).
    sky_mats: &'a mut Assets<crate::sky::SkyMaterial>,
    asset_server: &'a AssetServer,
    /// Config.yaml do jogo — o `<MusicLayer>` resolve `{bgm_dir}/{layer}.ogg`
    /// daqui (apps mínimas de teste correm sem resource: dir canónico vazio
    /// deixa o load falhar com warn, como um ficheiro ausente).
    game_config: Option<crate::config::GameConfig>,
    /// World-tiled texture registry (single sampler writer em
    /// `crate::textures`): primitivas com `texture-tile-size` usam UVs em
    /// metros e precisam de REPEAT — registadas no próprio `load`.
    tiled: &'a mut crate::textures::WorldTiledTextures,
    /// Cache de malhas por forma+dimensões: as partes repetidas de uma
    /// composition/protótipo (paredes iguais, postes iguais) partilham o
    /// mesmo asset em vez de registar um por entidade. Primitivas com UVs
    /// world-space (`texture-tile-size` em Plane/Cuboid) NÃO entram — as
    /// UVs levam a translation do mundo assada.
    mesh_cache: std::collections::HashMap<MeshKey, Handle<Mesh>>,
    /// Cache de materiais por spec exata — mesmo contrato do mesh_cache.
    material_cache: std::collections::HashMap<MaterialKey, Handle<StandardMaterial>>,
    chip_counter: std::cell::Cell<usize>,
    mixer: std::cell::RefCell<Option<crate::music::AudioMixerSettings>>,
    /// Deferred HUD chip UI nodes (resource name + stack index).
    chips: std::cell::RefCell<Vec<(usize, String)>>,
    /// Deferred HUD screen elements (tag + raw attrs).
    hud: std::cell::RefCell<HudList>,
    /// `<UiStyle>` sources, in document order (later rules win).
    ui_styles: std::cell::RefCell<Vec<String>>,
    /// Índices (em `ui_styles`) das folhas ainda pendentes — as que precedem
    /// o PRÓXIMO `<UiRoot>` são as que o alimentam (shadow-DOM-lite).
    ui_pending_sheets: std::cell::RefCell<Vec<usize>>,
    /// Por `<UiRoot>` (mesma ordem de `ui_trees`): as folhas que o alimentam.
    ui_tree_sheets: std::cell::RefCell<Vec<Vec<usize>>>,
    /// `<UiRoot>` subtrees, built after the stylesheets are loaded.
    ui_trees: std::cell::RefCell<Vec<crate::xml::XmlNode>>,
    /// Deferred world-system resources (DayCycle/Weather/border/biomes/config).
    worldsys: crate::worldsys::PendingWorldSystems,
    /// `<Sky>` attrs — constrói o domo no fim do startup.
    sky_request: std::cell::RefCell<Option<Vec<(String, String)>>>,
}

/// Malha de primitiva com dedup: as partes repetidas de uma
/// composition/protótipo (paredes iguais, postes iguais) partilham o mesmo
/// asset. Primitivas com UVs world-space (`texture-tile-size` em
/// Plane/Cuboid) NÃO entram na cache — `scale_primitive_uvs` assa a
/// translation do mundo nas UVs.
fn primitive_mesh_handle(ctx: &mut SpawnCtx, shape: &Shape) -> Handle<Mesh> {
    let key = mesh_key(shape);
    if let Some(handle) = ctx.mesh_cache.get(&key) {
        return handle.clone();
    }
    let handle = ctx.meshes.add(primitive_mesh(shape));
    ctx.mesh_cache.insert(key, handle.clone());
    handle
}

/// Material com dedup — a chave é a spec canonizada em bits (+ flag de UV
/// world-space, que muda a textura carregada). Centenas de partes idênticas
/// passam a partilhar um único `StandardMaterial`.
fn material_handle(
    ctx: &mut SpawnCtx,
    spec: &MaterialSpec,
    world_tiled_uv: bool,
) -> Handle<StandardMaterial> {
    let key = material_key(spec, world_tiled_uv);
    if let Some(handle) = ctx.material_cache.get(&key) {
        return handle.clone();
    }
    let handle = build_material(
        spec,
        world_tiled_uv,
        ctx.materials,
        ctx.asset_server,
        ctx.tiled,
    );
    ctx.material_cache.insert(key, handle.clone());
    handle
}

/// Grupo interno de pedras de margem (`rocks="1"` num corpo de água):
/// templates do pool + ladder de LOD, entregues ao pipeline de spawner
/// normal (occupancy partilhada, gates, escolha de template por RNG).
fn push_shore_rock_group(
    out: &mut Vec<crate::spawner::SpawnGroupState>,
    asset_server: &AssetServer,
    candidates: Vec<bevy::math::Vec2>,
    at: bevy::math::Vec2,
    rocks: &crate::terrain::shore_rocks::ShoreRocksSpec,
) {
    if candidates.is_empty() {
        return;
    }
    const LOD_NEAR: f32 = 50.0;
    const LOD_MID: f32 = 120.0;
    let load = |url: &str| -> Handle<bevy::gltf::Gltf> {
        crate::meshopt::load_gltf(asset_server, url.trim_start_matches('/').to_owned())
    };
    let handles = crate::terrain::shore_rocks::ROCK_TEMPLATES
        .iter()
        .map(|url| load(url))
        .collect();
    let lod_handles = crate::terrain::shore_rocks::ROCK_LODS
        .iter()
        .map(|(l1, l2)| (l1.map(load), l2.map(load), LOD_NEAR, LOD_MID))
        .collect();
    let template_urls = crate::terrain::shore_rocks::ROCK_TEMPLATES
        .iter()
        .map(|s| s.to_string())
        .collect();
    let template_lods = crate::terrain::shore_rocks::ROCK_LODS
        .iter()
        .map(|(l1, l2)| crate::recipes::TemplateLod {
            lod1_url: l1.map(|s| s.to_string()),
            lod2_url: l2.map(|s| s.to_string()),
            near: LOD_NEAR,
            mid: LOD_MID,
        })
        .collect();
    let spec = crate::recipes::StaticSpawnerSpec {
        seed: at.x.to_bits() as u64 ^ ((at.y.to_bits() as u64) << 32),
        count: candidates.len() as u32,
        fixed_candidates: candidates,
        template_urls,
        template_lods,
        // Rochas de margem: coladas à água é o ponto — gates de água
        // desligados; estradas e ocupação partilhada continuam a valer.
        avoid_water: false,
        in_water: false,
        near_water: false,
        near_water_radius: crate::recipes::DEFAULT_NEAR_WATER_RADIUS,
        avoid_road: true,
        // Rochas de margem nunca em parede de cliff — candidatos são fixos
        // (sem re-roll), pelo que os rejeitados são simplesmente omitidos.
        avoid_cliff: true,
        cliff_margin: crate::recipes::DEFAULT_CLIFF_MARGIN,
        align_to_terrain: true,
        random_yaw: true,
        scale_min: 0.55,
        scale_max: rocks.scale_max,
        scale_axis_min: 0.85,
        scale_axis_max: 1.15,
        max_slope_deg: 55.0,
        footprint_radius: 0.8,
        avoid_overlaps: true,
        // Assentam meio afundadas — a base do GLB nunca fica a flutuar no
        // declive da margem.
        base_y_offset: -0.12,
        template_collider: Some(crate::physics::ColliderShape::Box {
            size: bevy::math::Vec3::new(0.9, 0.8, 0.9),
            offset: bevy::math::Vec3::ZERO,
        }),
        region_min: [0.0; 3],
        region_max: [0.0; 3],
        cluster_count: 0,
        cluster_radius: 0.0,
        max_distance: 0.0,
        template_script: None,
        activation_radius: crate::luau::DEFAULT_ACTIVATION_RADIUS,
        template_destructible: None,
        cull_distance: crate::render_lod::DEFAULT_STATIC_CULL,
        cast_shadows: true,
        max_slope_attempts: 4,
        density_per_km2: 0.0,
        max_instances: 0,
    };
    out.push(crate::spawner::SpawnGroupState {
        template_collider: spec.template_collider.clone(),
        spec,
        handles,
        lod_handles,
        done: false,
        failed: false,
        dynamic: false,
        template_script: None,
        activation_radius: crate::luau::DEFAULT_ACTIVATION_RADIUS,
        template_destructible: None,
        collider_handle: None,
    });
}

/// Recursively collect `<StaticSpawner>` specs and start their template loads.
fn collect_spawn_groups(
    specs: &[EntitySpec],
    asset_server: &AssetServer,
    out: &mut Vec<crate::spawner::SpawnGroupState>,
    exclusions: &mut Vec<crate::spawner::SpawnExclusion>,
) {
    for spec in specs {
        match &spec.kind {
            EntityKind::Lake { spec: lake } => {
                if lake.rocks {
                    let candidates = crate::terrain::shore_rocks::lake_candidates(
                        bevy::math::Vec2::new(lake.at.x, lake.at.y),
                        lake.radius,
                        lake.depth,
                        lake.water_offset,
                        &lake.rocks_spec,
                        out.len(),
                    );
                    push_shore_rock_group(
                        out,
                        asset_server,
                        candidates,
                        bevy::math::Vec2::new(lake.at.x, lake.at.y),
                        &lake.rocks_spec,
                    );
                }
                collect_spawn_groups(&spec.children, asset_server, out, exclusions);
            }
            EntityKind::River { spec: river } => {
                if river.rocks && river.path.len() >= 2 {
                    // As candidatas seguem o MESMO suavizado do carve
                    // (chaikin ×2 + resample) — as estações finais do rio.
                    let smoothed = crate::terrain::paths::chaikin_smooth(&river.path, 2, false);
                    let stations = crate::terrain::paths::resample(
                        &smoothed,
                        crate::terrain::water::RIVER_STATION_SPACING.max(0.5),
                    );
                    let centroid =
                        stations.iter().sum::<bevy::math::Vec2>() / stations.len().max(1) as f32;
                    let candidates = crate::terrain::shore_rocks::river_candidates(
                        &stations,
                        river.width,
                        river.depth,
                        river.water_offset,
                        river.bank_width,
                        &river.rocks_spec,
                        out.len(),
                    );
                    push_shore_rock_group(
                        out,
                        asset_server,
                        candidates,
                        centroid,
                        &river.rocks_spec,
                    );
                }
                collect_spawn_groups(&spec.children, asset_server, out, exclusions);
            }
            EntityKind::StaticSpawner { spec: group }
            | EntityKind::DynamicSpawner { spec: group } => {
                if group.template_urls.is_empty() {
                    // Sem template glTF: o parse já avisou ("skipped"); um
                    // grupo vazio nunca instancia (panic por len()-1 em
                    // spawner.rs se chegar lá).
                    continue;
                }
                let handles = group
                    .template_urls
                    .iter()
                    .map(|url| {
                        crate::meshopt::load_gltf(
                            asset_server,
                            url.trim_start_matches('/').to_owned(),
                        )
                    })
                    .collect();
                // Ladder de LOD do template (`lod1-url`/`lod2-url`): um
                // handle por tier, alinhado com `handles` pelo índice.
                let lod_handles = group
                    .template_lods
                    .iter()
                    .map(|lod| {
                        let load = |url: &Option<String>| {
                            url.as_ref().map(|url| {
                                crate::meshopt::load_gltf(
                                    asset_server,
                                    url.trim_start_matches('/').to_owned(),
                                )
                            })
                        };
                        (load(&lod.lod1_url), load(&lod.lod2_url), lod.near, lod.mid)
                    })
                    .collect();
                out.push(crate::spawner::SpawnGroupState {
                    spec: group.clone(),
                    handles,
                    lod_handles,
                    done: false,
                    failed: false,
                    // `<DynamicSpawner>` instances are driven by the AI system.
                    dynamic: matches!(spec.kind, EntityKind::DynamicSpawner { .. }),
                    template_script: group.template_script.clone(),
                    activation_radius: group.activation_radius,
                    template_collider: group.template_collider.clone(),
                    template_destructible: group.template_destructible.clone(),
                    collider_handle: group.template_collider.as_ref().and_then(|shape| {
                        // Só Mesh/Precompute consomem o glTF de colisão;
                        // Box/None/Auto nunca o usam — não pedir um load
                        // de path vazio.
                        let url = match shape {
                            crate::physics::ColliderShape::Mesh { url, .. }
                            | crate::physics::ColliderShape::Precompute { url } => url,
                            _ => return None,
                        };
                        Some(crate::meshopt::load_gltf(
                            asset_server,
                            url.trim_start_matches('/').to_owned(),
                        ))
                    }),
                });
            }
            EntityKind::SpawnExclusion { center, radius } => {
                exclusions.push(crate::spawner::SpawnExclusion {
                    center: bevy::math::Vec2::new(center[0], center[1]),
                    radius: *radius,
                });
            }
            EntityKind::Vegetation { spec } => {
                let spawner_spec = spec.to_spawner_spec();
                if spawner_spec.count == 0 || spawner_spec.template_urls.is_empty() {
                    continue;
                }
                let handles = spawner_spec
                    .template_urls
                    .iter()
                    .map(|url| {
                        crate::meshopt::load_gltf(
                            asset_server,
                            url.trim_start_matches('/').to_owned(),
                        )
                    })
                    .collect();
                out.push(crate::spawner::SpawnGroupState {
                    // Erva não tem ladder: as malhas de `<Vegetation>` já são
                    // de algumas dezenas de KB.
                    lod_handles: Vec::new(),
                    template_script: spawner_spec.template_script.clone(),
                    activation_radius: spawner_spec.activation_radius,
                    template_collider: spawner_spec.template_collider.clone(),
                    template_destructible: spawner_spec.template_destructible.clone(),
                    collider_handle: spawner_spec.template_collider.as_ref().map(|shape| {
                        let url = match shape {
                            crate::physics::ColliderShape::Mesh { url, .. }
                            | crate::physics::ColliderShape::Precompute { url } => url.clone(),
                            _ => String::new(),
                        };
                        crate::meshopt::load_gltf(
                            asset_server,
                            url.trim_start_matches('/').to_owned(),
                        )
                    }),
                    spec: spawner_spec,
                    handles,
                    done: false,
                    failed: false,
                    // Vegetation is scenery — never AI-driven.
                    dynamic: false,
                });
            }
            _ => collect_spawn_groups(&spec.children, asset_server, out, exclusions),
        }
    }
}

/// Recursively spawn one spec (and its children) as Bevy entities.
/// Attaches the Rapier body + collider a spec asks for.
///
/// Boxes are built here; `trimesh` / `precompute` / `auto` need mesh data that
/// is still loading, so they leave a [`crate::physics::PendingCollider`] for
/// [`crate::physics::resolve_pending_colliders`] to finish. The collision glTF
/// is requested now so the load is already in flight.
fn attach_physics(entity: &mut EntityWorldMut, ctx: &mut SpawnCtx, spec: &EntitySpec) {
    use crate::physics::{ColliderShape, PendingCollider, body_bundle, immediate_collider};

    let physics = &spec.physics;
    if physics.is_empty() {
        return;
    }
    if let Some((body, gravity)) = body_bundle(physics.body, physics.gravity_scale) {
        entity.insert((body, gravity));
    }
    match &physics.collider {
        ColliderShape::None => {}
        ColliderShape::Box { .. } => {
            if let Some((collider, transform)) = immediate_collider(&physics.collider) {
                if transform.translation == Vec3::ZERO {
                    entity.insert(collider);
                } else {
                    // Rapier positions a collider by its own entity transform,
                    // so `pos-offset-y` becomes a child holding the shape; the
                    // render mesh keeps the entity's placement untouched.
                    let parent = entity.id();
                    entity.world_scope(|world| {
                        world.spawn((Name::new("collider"), collider, transform, ChildOf(parent)));
                    });
                }
            }
        }
        ColliderShape::Auto => {
            // Primitiva (parte de `<Composition>` ou `collider="auto"`
            // autoral): a forma é conhecida AGORA — colisor exato, sem
            // PendingCollider nem AABB aproximado.
            if let EntityKind::Primitive { shape, .. } = &spec.kind {
                entity.insert(crate::physics::collider_for_shape(
                    shape,
                    Vec3::from(spec.transform.scale),
                ));
            } else {
                entity.insert(PendingCollider {
                    shape: ColliderShape::Auto,
                    gltf: None,
                    age: 0.0,
                });
            }
        }
        ColliderShape::Mesh { url, .. } | ColliderShape::Precompute { url } => {
            // Root-absolute asset paths are unapproved in bevy; strip the '/'.
            let handle = ctx
                .asset_server
                .load(url.trim_start_matches('/').to_string());
            entity.insert(PendingCollider {
                shape: physics.collider.clone(),
                gltf: Some(handle),
                age: 0.0,
            });
        }
        // Sphere/cylinder/capsule explícitos são imediatos, como a caixa.
        ColliderShape::Sphere { .. }
        | ColliderShape::Cylinder { .. }
        | ColliderShape::Capsule { .. } => {
            if let Some((collider, transform)) = immediate_collider(&physics.collider) {
                if transform.translation == Vec3::ZERO {
                    entity.insert(collider);
                } else {
                    let parent = entity.id();
                    entity.world_scope(|world| {
                        world.spawn((Name::new("collider"), collider, transform, ChildOf(parent)));
                    });
                }
            }
        }
    }
}

fn spawn_entity(
    world: &mut World,
    ctx: &mut SpawnCtx,
    spec: &EntitySpec,
    parent: Option<Entity>,
    stats: &mut SpawnStats,
    ambient: &mut Option<GlobalAmbientLight>,
) {
    // Ground features + `<Terrain>` are consumed by the terrain runtime
    // (collected in `startup`) — no entity is spawned for them.
    if is_ground_feature(&spec.kind) || matches!(spec.kind, EntityKind::Terrain { .. }) {
        return;
    }
    // Every entity gets Visibility: glTF scene children carry
    // InheritedVisibility and warn B0004 if any parent lacks it.
    let mut entity = world.spawn((build_transform(&spec.transform), Visibility::Inherited));
    if let Some(parent) = parent {
        entity.insert(ChildOf(parent));
    }
    if let Some(name) = &spec.name {
        entity.insert(Name::new(name.clone()));
    }
    // Script Luau da entidade: o runtime executa `on_update(dt)` por frame.
    if let Some(path) = &spec.script {
        entity.insert(crate::luau::LuaScriptRef { path: path.clone() });
    }
    // Destrutível standalone (`<Entity destructible="…">`) — o caminho do
    // spawner cobre as instâncias; este cobre props autorais soltos.
    if let Some(destructible) = &spec.destructible {
        entity.insert(crate::harvest::Destructible::from_spec(destructible));
    }
    attach_physics(&mut entity, ctx, spec);
    match &spec.kind {
        EntityKind::Group => {
            // Grupos autorais a y≈0 assentam no terreno real (a vila fica a
            // ~24.6 m no heightmap); conteúdo autoral elevado mantém offset
            // relativo ao novo y.
            entity.insert(crate::worldsys::SeatOnTerrain);
        }
        EntityKind::Composition {
            body, place, seat, ..
        } => {
            // Corpo do objeto composto: os colisores das partes (filhos com
            // `ColliderShape::Auto`, resolvidos em `attach_physics`) colam-se
            // a ESTE body — colisor composto do Rapier, um por primitiva.
            if let Some((rb, gravity)) = crate::physics::body_bundle(*body, None) {
                entity.insert((rb, gravity));
            }
            // Colocação: `place` explícito ganha (amostra o terreno onde o
            // autor pediu); sem `place`, assenta como os grupos (a menos que
            // `seat="none"` — objeto flutuante, mantém a cota autoral).
            if let Some(place) = place {
                entity.insert(crate::worldsys::PendingPlace {
                    at: Vec2::new(place.at[0], place.at[1]),
                    align_to_terrain: place.align_to_terrain,
                    base_y_offset: place.base_y_offset,
                });
            } else if *seat {
                // Composition assenta SEMPRE como bloco — mesmo espalhada
                // (uma caixa de 240 m não é repartida pelas partes).
                entity.insert((crate::worldsys::SeatOnTerrain, crate::worldsys::SeatAsUnit));
            }
        }
        // `<Use>` é expandido em parse-time (`recipes::expand_uses`) — se
        // chegou aqui, o mundo foi montado à mão; não spawna nada.
        EntityKind::Use { prototype, .. } => {
            bevy::log::warn!(
                "<Use prototype=\"{prototype}\"> chegou ao spawn sem expansão — ignorada"
            );
            return;
        }
        EntityKind::ParticleSystem { spec } => {
            let resolved = crate::particles::resolve(spec);
            let capacity = crate::particles::emitter_capacity(&resolved);
            let mesh = ctx.meshes.add(crate::particles::particle_mesh(capacity));
            let material = ctx.materials.add(StandardMaterial {
                base_color: Color::WHITE,
                unlit: true,
                alpha_mode: if resolved.additive {
                    AlphaMode::Add
                } else {
                    AlphaMode::Blend
                },
                ..Default::default()
            });
            entity.insert((
                Mesh3d(mesh),
                MeshMaterial3d(material),
                Visibility::Inherited,
                NotShadowCaster,
                crate::particles::ParticleEmitter {
                    // Seed derivada da posição (a transform já está na
                    // entidade): emissores distintos têm sequências
                    // distintas — sem flicker em lockstep.
                    sim: crate::particles::EmitterSim::seeded(
                        spec,
                        entity
                            .get::<Transform>()
                            .map(|t| t.translation)
                            .unwrap_or_default(),
                    ),
                    capacity,
                    culled: false,
                    idle: false,
                },
            ));
        }
        EntityKind::GltfScene { url } => {
            let path = url.trim_start_matches('/');
            let handle: Handle<Gltf> = crate::meshopt::load_gltf(ctx.asset_server, path.to_owned());
            // Cristais/vidro (r3): a URL marca os materiais da cena para o
            // patch transmissivo do `prop_tint` quando o glTF aterrar.
            if path.contains("crystal") || path.contains("glass") {
                entity.insert(crate::prop_tint::TransmissiveGltf);
            }
            entity.insert(GltfScenePending { handle });
        }
        EntityKind::Primitive { shape, material } => {
            // UV world-space (`texture-tile-size` > 0 em Plane/Cuboid, a
            // mesma fórmula das ribbons) ⇒ a textura precisa de REPEAT:
            // registada no `load` para o escritor único de samplers.
            let world_tiled_uv = material.texture.is_some()
                && material.texture_tile.is_some_and(|tile| tile > 0.0)
                && matches!(shape, Shape::Plane { .. } | Shape::Cuboid { .. });
            if let Some(tile) = material.texture_tile.filter(|_| world_tiled_uv) {
                // UV world-space: mesh DEDICADO — `scale_primitive_uvs` assa
                // a translation do mundo nas UVs, por isso decals em sítios
                // diferentes não podem partilhar o asset (decals no MESMO
                // sítio partilham material, que é o que alinha o padrão com
                // as ribbons das estradas).
                let mut primitive = primitive_mesh(shape);
                scale_primitive_uvs(&mut primitive, shape, tile, spec.transform.translation);
                let mesh = ctx.meshes.add(primitive);
                let mat = material_handle(ctx, material, world_tiled_uv);
                entity.insert((Mesh3d(mesh), MeshMaterial3d(mat), Visibility::Inherited));
            } else {
                // Cache por forma+dimensões/spec: paredes iguais de uma
                // composition, instâncias de um protótipo — um asset só.
                let mesh = primitive_mesh_handle(ctx, shape);
                let mat = material_handle(ctx, material, false);
                entity.insert((Mesh3d(mesh), MeshMaterial3d(mat), Visibility::Inherited));
            }
        }
        EntityKind::PointLight {
            color,
            intensity,
            radius,
            shadows,
        } => {
            let mut light = PointLight::default();
            if let Some([r, g, b]) = color {
                light.color = Color::srgb(*r, *g, *b);
            }
            if let Some(v) = intensity {
                light.intensity = *v;
            } else {
                // bevy's default point light is a cinema-scale light (~1M lm);
                // fall back to a game-appropriate Viber default instead.
                light.intensity = 1200.0;
            }
            if let Some(v) = radius {
                light.radius = *v;
            }
            if let Some(v) = shadows {
                light.shadow_maps_enabled = *v;
                if *v {
                    // PCSS (passe r2): penumbra que cresce com a distância do
                    // caster — no PointLight deriva do `radius` (a chama da
                    // lanterna ~0.35 m lê como emissor estendido). Requer
                    // feature `experimental_pbr_pcss`.
                    #[cfg(feature = "experimental_pbr_pcss")]
                    {
                        light.soft_shadows_enabled = true;
                    }
                    // Contact shadows: NÃO na PointLight. Em Bevy 0.19 o
                    // raymarch da depth (`calculate_contact_shadow`) está
                    // DENTRO do loop de cada luz em `pbr_functions.wgsl` — não
                    // é um passe único de ecrã, é um raymarch por luz e por
                    // pixel. Com até `LIGHT_BUDGET` (12) tochas no cluster de
                    // um pixel, são 12 raymarches fullscreen de 24 passos por
                    // frame para um raio de 0,5 m que o shadow map da lanterna
                    // já resolve: ganho visual nulo, conta real. A técnica fica
                    // no SOL, que é onde faz falta (herói→chão, poste→calçada).
                    // `VIBER_POINT_CONTACT_SHADOWS=1` devolve-o (A/B de QA).
                    light.contact_shadows_enabled = matches!(
                        std::env::var("VIBER_POINT_CONTACT_SHADOWS").as_deref(),
                        Ok("1") | Ok("true") | Ok("on") | Ok("yes")
                    );
                }
            }
            entity.insert(light);
        }
        EntityKind::DirectionalLight {
            color,
            illuminance,
            direction,
            shadows,
        } => {
            // The directional light is the world's sun: it casts by default.
            // Bevy ships shadows off, which reads as a flat, unlit scene —
            // `<PointLight>` keeps the off default instead, since a world can
            // hold dozens of them and each one costs a shadow cubemap.
            let mut light = DirectionalLight {
                shadow_maps_enabled: true,
                ..DirectionalLight::default()
            };
            if let Some([r, g, b]) = color {
                light.color = Color::srgb(*r, *g, *b);
            }
            if let Some(v) = illuminance {
                light.illuminance = *v;
            }
            if let Some(v) = shadows {
                light.shadow_maps_enabled = *v;
            }
            // The light travels along `direction`; bevy shines a directional
            // light along the entity's -Z, so rotate -Z onto the direction.
            let dir = Vec3::from(*direction);
            let mut transform = build_transform(&spec.transform);
            transform.rotation = Quat::from_rotation_arc(
                Vec3::NEG_Z,
                if dir.length_squared() > f32::EPSILON {
                    dir.normalize()
                } else {
                    Vec3::NEG_Y
                },
            );
            // Cascatas para a terceira pessoa à escala do herói: nítidas
            // perto, cobrindo ainda os edifícios à volta. O default do Bevy é
            // dimensionado para uma demo pequena e deixa o chão próximo sem
            // sombra num mundo deste tamanho.
            //
            // P1.8 (fotorrealismo): a distância máxima sobe para 600 m para as
            // serras distantes receberem sombra do sol. Trade-off: a cascata
            // mais longe (12 m → 600 m) cobre a faixa com MUITO menos
            // densidade de texels — suficiente para penumbras de montanha a
            // meia distância, mas o detalhe fino morre lá longe; o custo
            // paga-se no shadow map 4096² (recurso `DirectionalLightShadowMap`
            // no run, main.rs — o default do Bevy é 2048²). As 4 cascatas por
            // luz são o TETO do renderer do Bevy 0.19.1
            // (`MAX_CASCADES_PER_LIGHT`) — o builder aceita mais, mas o
            // extract do pbr corta na 4.ª com warning e subviews fora do
            // array de camadas do shadow map.
            let cascades = bevy::light::CascadeShadowConfigBuilder {
                num_cascades: 4,
                first_cascade_far_bound: 12.0,
                maximum_distance: 600.0,
                ..Default::default()
            }
            .build();
            // PCSS (passe r2): o SOL como emissor estendido (~1.5 m de tamaño
            // aparente na escala das sombras) — as sombras das árvores/muros
            // abrem penumbra suave à medida que se afastam do caster, em vez
            // da borda dura do shadow map. Filtro `Temporal` na câmara
            // (postfx.rs) + TAA limpam o noise. Requer feature
            // `experimental_pbr_pcss`.
            #[cfg(feature = "experimental_pbr_pcss")]
            {
                light.soft_shadow_size = Some(1.5);
            }
            // Contact shadows direcionais: o raymarch por-pixel da depth cobre
            // a folga de bias dos shadow maps junto dos pés dos casters.
            light.contact_shadows_enabled = true;
            entity.insert((light, cascades, bevy::light::VolumetricLight, transform, Visibility::Inherited));
        }
        // Ambient light uses the `GlobalAmbientLight` resource; it is not
        // spawned as an entity.
        EntityKind::AmbientLight { color, brightness } => {
            let mut light = GlobalAmbientLight::default();
            if let Some([r, g, b]) = color {
                light.color = Color::srgb(*r, *g, *b);
            }
            if let Some(v) = brightness {
                light.brightness = *v;
            }
            *ambient = Some(light);
        }
        EntityKind::OrbitCamera {
            target,
            distance,
            height,
            pitch_deg,
            mouse_sensitivity,
            follow_lag,
            turn_lag,
            min_terrain_distance,
            fov_deg,
        } => {
            let pitch = pitch_deg.unwrap_or_else(|| height.atan2(*distance).to_degrees());
            entity.insert((
                Camera3d::default(),
                OrbitCamera {
                    target: target.clone(),
                    distance: *distance,
                    height: *height,
                    pitch_deg: *pitch_deg,
                    pitch_state_deg: pitch,
                    yaw_deg: 0.0,
                    mouse_sensitivity: mouse_sensitivity.unwrap_or(0.12),
                    min_distance: 2.0,
                    max_distance: 80.0,
                    follow_lag: follow_lag.unwrap_or(crate::camera::DEFAULT_FOLLOW_LAG),
                    turn_lag: turn_lag.unwrap_or(crate::camera::DEFAULT_TURN_LAG),
                    min_terrain_distance: min_terrain_distance
                        .unwrap_or(crate::camera::DEFAULT_MIN_TERRAIN_DISTANCE),
                    follow_point: Vec3::ZERO,
                    smooth_yaw_deg: 0.0,
                    current_pos: Vec3::ZERO,
                    initialized: false,
                },
            ));
            // Authored FOV (simple-rpg asks for 64°; the Bevy default is 45°).
            if let Some(fov) = fov_deg {
                entity.insert(Projection::Perspective(PerspectiveProjection {
                    fov: fov.to_radians(),
                    ..PerspectiveProjection::default()
                }));
            }
            stats.has_camera = true;
        }
        EntityKind::DialogueNpc {
            dialogue_id,
            marker_height,
        } => {
            entity.insert((
                DialogueNpc {
                    dialogue_id: dialogue_id.clone(),
                },
                Visibility::Inherited,
            ));
            let marker_mesh = ctx.meshes.add(Mesh::from(Sphere::new(0.16)));
            let marker_mat = ctx.materials.add(StandardMaterial {
                base_color: Color::srgb(1.0, 0.82, 0.15),
                emissive: LinearRgba::new(3.0, 2.4, 0.5, 1.0),
                unlit: true,
                ..Default::default()
            });
            // with_children auto-parents the marker (no extra world borrow).
            entity.with_children(|children| {
                children.spawn((
                    Mesh3d(marker_mesh),
                    MeshMaterial3d(marker_mat),
                    Transform::from_translation(Vec3::Y * *marker_height),
                    Visibility::Inherited,
                    NotShadowCaster,
                ));
            });
        }
        EntityKind::ResourceChip {
            resource,
            icon: _,
            target: _,
        } => {
            let index = ctx.chip_counter.get() + 1;
            ctx.chip_counter.set(index);
            // HUD chip: UI node deferred to the end of startup (the entity
            // borrow holds `world`).
            ctx.chips.borrow_mut().push((index, resource.clone()));
        }
        EntityKind::AudioMixer { master, music, sfx } => {
            // Buses em 0..=1: um volume negativo do XML tocava a 200 % no
            // driver (`Volume::Linear` não faz clamp).
            ctx.mixer.replace(Some(crate::music::AudioMixerSettings {
                master: master.clamp(0.0, 1.0),
                music: music.clamp(0.0, 1.0),
                sfx: sfx.clamp(0.0, 1.0),
            }));
        }
        EntityKind::MusicLayer { layer, base_volume } => {
            // O playback é do backend kira — o `audio_loop_starter`
            // (crate::music) arranca o loop no bus de música quando o
            // componente pendente aparece; os buses do mixer aplicam-se ao
            // vivo, no canal. O path é `{bgm_dir}/{layer}.ogg` com o dir do
            // config.yaml do jogo.
            let url = match &ctx.game_config {
                Some(config) => config.bgm_path(layer),
                None => format!("{layer}.ogg"),
            };
            entity.insert((
                crate::music::MusicLayerTag {
                    layer: layer.clone(),
                    base_volume: *base_volume,
                },
                crate::music::AudioLoopPending {
                    url,
                    music: true,
                },
            ));
        }
        EntityKind::UiStyle { source } => {
            // Deferred like the HUD elements: the sheet is a resource, and the
            // element tree is only built after every stylesheet is loaded so
            // the first frame is already styled. O índice fica pendente até à
            // próxima raiz — é ela que o carrega (scoping por raiz).
            let index = ctx.ui_styles.borrow().len();
            ctx.ui_styles.borrow_mut().push(source.clone());
            ctx.ui_pending_sheets.borrow_mut().push(index);
        }
        EntityKind::UiTree { node } => {
            ctx.ui_trees.borrow_mut().push((**node).clone());
            // As folhas acumuladas desde a última raiz alimentam ESTA.
            ctx.ui_tree_sheets
                .borrow_mut()
                .push(std::mem::take(&mut *ctx.ui_pending_sheets.borrow_mut()));
            // A HUD script is not an NPC: it must keep running wherever the
            // player walks, so it opts out of the activation-radius LOD that
            // freezes distant scripts.
            entity.insert(crate::luau::ScriptActivation {
                radius: f32::INFINITY,
            });
        }
        EntityKind::HudElement { tag, attrs } => {
            if tag.eq_ignore_ascii_case("sky") {
                // O domo tem o seu próprio pipeline de construção no fim do
                // startup (precisa de Assets<Mesh> sem aliasing).
                ctx.sky_request.replace(Some(attrs.clone()));
            } else {
                ctx.hud.borrow_mut().push((tag.clone(), attrs.clone()));
            }
        }
        EntityKind::PlayerGltf { url } => {
            let path = url.trim_start_matches('/');
            let handle: Handle<Gltf> = crate::meshopt::load_gltf(ctx.asset_server, path.to_owned());
            entity.insert((
                GltfScenePending { handle },
                crate::player::Player::default(),
                // Character controller: the hero is moved by code but has to be
                // stopped by the world's static colliders (walls, buildings,
                // props), so Rapier resolves the motion instead of the
                // transform being written blind.
                bevy_rapier3d::prelude::RigidBody::KinematicPositionBased,
                crate::player::hero_collider(),
                crate::player::hero_controller(),
            ));
        }
        // Ground features + `<Terrain>` + spawner groups return before the
        // spawn above; this arm only satisfies exhaustiveness.
        EntityKind::Terrain { .. }
        | EntityKind::TerrainPad { .. }
        | EntityKind::Lake { .. }
        | EntityKind::River { .. }
        | EntityKind::Cliff { .. }
        | EntityKind::Cave { .. }
        | EntityKind::Arch { .. }
        | EntityKind::Bridge { .. }
        | EntityKind::RockFeatures { .. }
        | EntityKind::Road { .. }
        | EntityKind::RoadNetwork { .. }
        | EntityKind::GroundDecal { .. }
        | EntityKind::StaticSpawner { .. }
        | EntityKind::DynamicSpawner { .. }
        | EntityKind::SpawnExclusion { .. }
        | EntityKind::Vegetation { .. } => {}
        // World-system elements defer resources via ctx (entity borrows world):
        EntityKind::DayCycle {
            minute_of_day,
            minutes_per_real_second,
            dawn_minute,
            dusk_minute,
            ambient_day,
            ambient_night,
            drive_ambient,
            max_sun_elevation,
            sun_azimuth_base,
            min_sun_elevation,
        } => {
            ctx.worldsys.day_cycle = Some(crate::worldsys::DayCycleState::from_parts(
                *minute_of_day,
                *minutes_per_real_second,
                *dawn_minute,
                *dusk_minute,
                *ambient_day,
                *ambient_night,
                *drive_ambient,
                *max_sun_elevation,
                *sun_azimuth_base,
                *min_sun_elevation,
            ));
        }
        EntityKind::Weather {
            wind,
            wind_strength,
            clouds,
            rain,
            cycle,
        } => {
            ctx.worldsys.weather = Some(crate::worldsys::WeatherState {
                wind: *wind,
                wind_strength: *wind_strength,
                clouds: *clouds,
                rain: *rain,
                cycle: *cycle,
            });
        }
        EntityKind::BiomeRegion {
            id,
            display_name,
            polygon,
            fog_density,
            tint,
            pp_exposure,
            pp_bloom_strength,
        } => {
            ctx.worldsys.biomes.push(crate::worldsys::BiomeRegionData {
                id: id.clone(),
                display_name: display_name.clone(),
                polygon: polygon.clone(),
                fog_density: *fog_density,
                tint: *tint,
                pp_exposure: *pp_exposure,
                pp_bloom_strength: *pp_bloom_strength,
            });
        }
        EntityKind::WorldBorder {
            radius,
            warn_seconds,
            margin,
        } => {
            ctx.worldsys.border = Some(crate::worldsys::WorldBorderConfig {
                radius: *radius,
                warn_seconds: *warn_seconds,
                margin: *margin,
            });
        }
        EntityKind::InteriorScene { min, max } => {
            ctx.worldsys.interior_scene = Some(crate::worldsys::InteriorSceneConfig {
                min: *min,
                max: *max,
            });
        }
        EntityKind::EngineConfig { tag, attrs } => {
            ctx.worldsys
                .configs
                .push(crate::worldsys::EngineConfigData {
                    tag: tag.clone(),
                    attrs: attrs.clone(),
                });
        }
    }
    stats.entities += 1;
    let id = entity.id();
    for child in &spec.children {
        spawn_entity(world, ctx, child, Some(id), stats, ambient);
    }
}

fn build_transform(spec: &TransformSpec) -> Transform {
    let mut t = Transform::from_translation(Vec3::from(spec.translation));
    if let Some(q) = spec.rotation_quat {
        t.rotation = Quat::from_xyzw(q[0], q[1], q[2], q[3]);
    } else if let Some(e) = spec.euler_deg {
        let q = super::transform::euler_deg_to_quat(e);
        t.rotation = Quat::from_xyzw(q[0], q[1], q[2], q[3]);
    }
    t.scale = Vec3::from(spec.scale);
    t
}

fn primitive_mesh(shape: &Shape) -> Mesh {
    match shape {
        Shape::Cuboid { half_size } => Mesh::from(Cuboid::new(
            half_size[0] * 2.0,
            half_size[1] * 2.0,
            half_size[2] * 2.0,
        )),
        Shape::Sphere { radius } => Mesh::from(Sphere::new(*radius)),
        Shape::Cylinder {
            half_height,
            radius,
        } => Mesh::from(Cylinder::new(*radius, *half_height * 2.0)),
        Shape::Plane { half_size } => Mesh::from(Plane3d {
            half_size: Vec2::new(half_size[0], half_size[1]),
            ..Plane3d::default()
        }),
        Shape::Capsule {
            radius,
            half_height,
        } => Mesh::from(Capsule3d::new(*radius, *half_height * 2.0)),
    }
}

fn build_material(
    spec: &MaterialSpec,
    world_tiled_uv: bool,
    materials: &mut Assets<StandardMaterial>,
    asset_server: &AssetServer,
    tiled: &mut crate::textures::WorldTiledTextures,
) -> Handle<StandardMaterial> {
    let mut material = StandardMaterial::default();
    if let Some([r, g, b]) = spec.base_color {
        material.base_color = Color::srgb(r, g, b);
    }
    if let Some(v) = spec.metallic {
        material.metallic = v.clamp(0.0, 1.0);
    }
    if let Some(v) = spec.roughness {
        material.perceptual_roughness = v.clamp(0.0, 1.0);
    }
    if let Some(v) = spec.opacity {
        // `opacity="0"` = invisível (parede de colisor puro, o truque das
        // interiors do VibeGame); 0..1 = translúcido (Blend, sem depth write
        // — o renderer do Bevy trata isso no AlphaMode).
        let alpha = v.clamp(0.0, 1.0);
        material.base_color.set_alpha(alpha);
        if alpha < 1.0 {
            material.alpha_mode = AlphaMode::Blend;
        }
    }
    if let Some([r, g, b]) = spec.emissive {
        material.emissive = LinearRgba::rgb(r, g, b);
    }
    if let Some(url) = spec.texture.as_deref() {
        let handle = if world_tiled_uv {
            // UVs em metros: REPEAT via o registry do escritor único.
            crate::textures::load_tiled_image(asset_server, tiled, url)
        } else {
            asset_server.load(url.trim_start_matches('/').to_owned())
        };
        material.base_color_texture = Some(handle);
    }
    if let Some(url) = spec.normal_map.as_deref() {
        // O normal do pool é LINEAR: carregar pelo mesmo caminho da base
        // color dava sRGB e o mapa saía esbranquiçado (normais empurradas
        // para cima e o relevo a desaparecer). `load_tiled_image` regista o
        // sampler e devolve o handle cru do AssetServer, que já é linear
        // quando o ficheiro é KTX2 de dados.
        let handle = asset_server.load(url.trim_start_matches('/').to_owned());
        material.normal_map_texture = Some(handle);
    }
    materials.add(material)
}

/// Re-escala as UVs de uma primitiva para coordenadas de MUNDO divididas
/// por `tile`: `uv = world / tile`. Decals com o mesmo tile-size partilham
/// o padrão com as ribbons das estradas (mesma fórmula, mesma origem) — o
/// cobblestone da praça bate com o da estrada na junção.
fn scale_primitive_uvs(
    mesh: &mut bevy::mesh::Mesh,
    shape: &Shape,
    tile: f32,
    world_translation: [f32; 3],
) {
    if tile <= 0.0 {
        return;
    }
    let extents: [f32; 2] = match shape {
        Shape::Plane { half_size } => [half_size[0] * 2.0, half_size[1] * 2.0],
        Shape::Cuboid { half_size } => [half_size[0] * 2.0, half_size[2] * 2.0],
        _ => return, // esferas/cilindros/cápsulas: UV polares, tiling por extensão não se aplica
    };
    // canto mínimo do decal no mundo (a primitiva é centrada na translation)
    let min_x = world_translation[0] - extents[0] / 2.0;
    let min_z = world_translation[2] - extents[1] / 2.0;
    if let Some(bevy::mesh::VertexAttributeValues::Float32x2(values)) =
        mesh.attribute_mut(bevy::mesh::Mesh::ATTRIBUTE_UV_0)
    {
        for uv in values.iter_mut() {
            *uv = [
                (uv[0] * extents[0] + min_x) / tile,
                (uv[1] * extents[1] + min_z) / tile,
            ];
        }
    }
}

/// `<OrbitCamera>` follow for cameras with an explicit `target` (static
/// scene framing): keeps a fixed spherical offset from the named target.
/// An explicit `pitch` (degrees) overrides `height` as the ring seed via
/// `height = distance · tan(pitch)`.
///
/// Target-less cameras in worlds with a player are third-person cameras —
/// [`crate::camera::third_person_camera`] owns those.
#[allow(clippy::type_complexity)]
pub fn orbit_camera_follow(
    mut cameras: Query<(&mut Transform, &OrbitCamera)>,
    names: Query<(Entity, &Name)>,
    globals: Query<&GlobalTransform>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
) {
    let has_player = players.iter().next().is_some();
    for (mut cam, settings) in &mut cameras {
        if settings.target.is_none() && has_player {
            continue; // third-person camera: crate::camera owns it
        }
        let target_pos = match &settings.target {
            Some(target_name) => names
                .iter()
                .find(|(_, name)| name.as_str() == target_name)
                .and_then(|(entity, _)| globals.get(entity).ok())
                .map(|g| g.translation())
                .unwrap_or(Vec3::ZERO),
            // No `target` and no player: frame the world origin (where the
            // camera used to sit, staring at the ground, was worse).
            None => Vec3::ZERO,
        };
        let offset = crate::camera::camera_offset(
            settings.yaw_deg,
            settings.pitch_state_deg,
            settings.distance,
            0.0,
        );
        cam.translation = target_pos + offset;
        // Never sink below the terrain surface (VibeGame minTerrainDistance).
        // Piso SOB a câmara (Y conhecido): o enquadramento da intro sob um
        // overhang não é empurrado para cima da rocha.
        if let Some(runtime) = runtime.as_deref() {
            let min_y = runtime
                .surface_below(
                    cam.translation.x,
                    cam.translation.z,
                    cam.translation.y + 0.5,
                )
                .unwrap_or(f32::NEG_INFINITY)
                + 1.0;
            if cam.translation.y < min_y {
                cam.translation.y = min_y;
            }
        }
        // Pivot at the target's upper body, not its feet, so the subject sits
        // in the lower half of the frame instead of dead centre.
        cam.look_at(target_pos + Vec3::Y * settings.pivot_height(), Vec3::Y);
    }
}

/// Fallback camera motion: a slow cinematic orbit around the origin.
pub fn auto_orbit(time: Res<Time>, mut cameras: Query<(&mut Transform, &mut AutoOrbit)>) {
    for (mut transform, mut orbit) in &mut cameras {
        orbit.yaw += time.delta_secs() * 0.25;
        transform.translation = Vec3::new(
            orbit.yaw.cos() * orbit.radius,
            orbit.height,
            orbit.yaw.sin() * orbit.radius,
        );
        transform.look_at(Vec3::ZERO, Vec3::Y);
    }
}

/// Swap loaded `<GltfScene>` pendings for their default scene (parented under
/// the entity, so its transform applies). Load failures log once and leave an
/// empty node — a broken asset must not take the world down.
pub fn gltf_scene_spawner(
    mut commands: Commands,
    gltfs: Res<Assets<Gltf>>,
    server: Res<AssetServer>,
    pending: Query<(Entity, &GltfScenePending)>,
) {
    for (entity, scene) in &pending {
        match server.get_load_state(&scene.handle) {
            Some(LoadState::Loaded) => {
                if let Some(gltf) = gltfs.get(&scene.handle) {
                    match gltf.default_scene.clone() {
                        Some(root) => {
                            commands.entity(entity).insert(WorldAssetRoot(root));
                            // Characters ship a catalogue of clips; ask the
                            // animation module to bind them once the scene has
                            // spawned its `AnimationPlayer`.
                            if !gltf.animations.is_empty() {
                                commands
                                    .entity(entity)
                                    .insert(crate::animation::AnimatedScene {
                                        gltf: scene.handle.clone(),
                                    });
                            }
                        }
                        None => {
                            bevy::log::warn!("gltf scene has no default scene; leaving empty");
                        }
                    }
                }
                commands.entity(entity).remove::<GltfScenePending>();
            }
            Some(LoadState::Failed(err)) => {
                bevy::log::warn!("gltf asset failed to load: {err}");
                commands.entity(entity).remove::<GltfScenePending>();
            }
            _ => {}
        }
    }
}

/// Chaves de cache de mesh/material — a base do dedup de assets.
#[cfg(test)]
mod cache_key_tests {
    use super::*;

    #[test]
    fn test_mesh_key_equal_for_identical_shapes() {
        let a = Shape::Cuboid {
            half_size: [1.0, 0.5, 1.0],
        };
        let b = Shape::Cuboid {
            half_size: [1.0, 0.5, 1.0],
        };
        assert_eq!(mesh_key(&a), mesh_key(&b), "duas paredes iguais → 1 asset");
        let c = Shape::Cuboid {
            half_size: [1.0, 0.6, 1.0],
        };
        assert_ne!(
            mesh_key(&a),
            mesh_key(&c),
            "dimensão diferente → asset próprio"
        );
        // -0.0 e 0.0 têm bits diferentes mas são o mesmo float de facto —
        // para o cache, bits exatos são o contrato (XML idêntico → mesmos bits).
        let s1 = Shape::Sphere { radius: 0.5 };
        let s2 = Shape::Sphere { radius: 0.5 };
        assert_eq!(mesh_key(&s1), mesh_key(&s2));
        assert_ne!(mesh_key(&s1), mesh_key(&Shape::Sphere { radius: 0.6 }));
    }

    #[test]
    fn test_mesh_key_distinguishes_variants() {
        let base = mesh_key(&Shape::Cylinder {
            half_height: 1.0,
            radius: 0.5,
        });
        let capsule = mesh_key(&Shape::Capsule {
            half_height: 1.0,
            radius: 0.5,
        });
        assert_ne!(base, capsule, "cilindro e cápsula nunca partilham asset");
    }

    #[test]
    fn test_material_key_equal_for_identical_specs() {
        let mut a = MaterialSpec::default();
        a.base_color = Some([0.5, 0.25, 0.125]);
        a.roughness = Some(0.8);
        a.opacity = Some(1.0);
        let b = a.clone();
        assert_eq!(material_key(&a, false), material_key(&b, false));
        // A flag de UV world-space participa na chave (textura diferente).
        assert_ne!(material_key(&a, false), material_key(&a, true));
        let mut c = a.clone();
        c.opacity = Some(0.5);
        assert_ne!(material_key(&a, false), material_key(&c, false));
        // Textura com url diferente → material próprio.
        let mut d = a.clone();
        d.texture = Some("/assets/t.png".to_string());
        assert_ne!(material_key(&a, false), material_key(&d, false));
    }
}

#[cfg(test)]
mod terrain_collect_tests {
    use super::*;
    use crate::recipes::{EntitySpec, XmlNode};

    fn node(tag: &str, attrs: &[(&str, &str)]) -> XmlNode {
        XmlNode {
            tag: tag.to_string(),
            attrs: attrs
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
            text: String::new(),
            children: vec![],
        }
    }

    fn parse_tree(nodes: &[XmlNode]) -> Vec<EntitySpec> {
        let world = crate::recipes::parse_world(&[], nodes).expect("parses");
        world.entities
    }

    #[test]
    fn test_collect_applies_group_translation_to_pads() {
        let mut group = node("Group", &[("translation", "859 0 281")]);
        group.children = vec![node(
            "TerrainPad",
            &[("at", "0 0"), ("size", "280 260"), ("falloff", "24")],
        )];
        let entities = parse_tree(&[group]);
        let mut out = PendingTerrain::default();
        collect_terrain(&entities, &mut out);
        assert_eq!(out.features.pads.len(), 1);
        assert_eq!(out.features.pads[0].at, Vec2::new(859.0, 281.0));
    }

    #[test]
    fn test_collect_offsets_nested_rivers_and_networks() {
        let mut group = node("Group", &[("translation", "10 0 20")]);
        group.children = vec![
            node("River", &[("path", "0 0 10 0"), ("width", "4")]),
            node("RoadNetwork", &[("default-width", "4")]),
        ];
        let mut net_attrs = node("Way", &[("id", "w"), ("xz", "1 2")]);
        let mut seg = node("Segment", &[("a", "w"), ("b", "w"), ("via", "3 4")]);
        seg.children = vec![];
        net_attrs.children = vec![];
        // Way/Segment are children of the network element, not the group —
        // build the network node properly.
        let mut network = node("RoadNetwork", &[("default-width", "4")]);
        network.children = vec![
            node("Way", &[("id", "w"), ("xz", "1 2")]),
            node("Segment", &[("a", "w"), ("b", "w"), ("via", "3 4")]),
        ];
        group.children = vec![
            node("River", &[("path", "0 0 10 0"), ("width", "4")]),
            network,
        ];
        let _ = (&mut seg, &mut net_attrs);
        let entities = parse_tree(&[group]);
        let mut out = PendingTerrain::default();
        collect_terrain(&entities, &mut out);
        let river = &out.features.rivers[0];
        assert_eq!(river.path[0], Vec2::new(10.0, 20.0), "river offset applied");
        let net = &out.features.networks[0];
        assert_eq!(net.ways[0].at, Vec2::new(11.0, 22.0), "way offset applied");
        assert_eq!(
            net.segments[0].via[0],
            Vec2::new(13.0, 24.0),
            "via offset applied"
        );
    }
}

/// Loads every `<UiStyle>` and then builds every `<UiRoot>` tree.
///
/// Order matters: the stylesheet has to be complete before the first element
/// is spawned, otherwise the opening frame flashes unstyled boxes. A `src`
/// sheet is read from the world directory so a HUD can keep its CSS in its own
/// file instead of inlined in the world XML.
fn build_declarative_ui(
    world: &mut World,
    styles: &[String],
    trees: &[crate::xml::XmlNode],
    tree_sheets: &[Vec<usize>],
    world_dir: Option<&std::path::Path>,
    assets: &AssetServer,
) {
    if styles.is_empty() && trees.is_empty() {
        return;
    }
    // Idempotência de arranque: o processamento de specs pode re-correr (o
    // gate dos templates de glTF resolve depois do primeiro passe) e um
    // segundo build deixava uma CÓPIA ÓRFÃ do HUD inteiro por cima do jogo,
    // sem ids no registry (invisível a scripts, imóvel). Qualquer UI
    // declarativa pré-existente sai — a reconstrução parte de zero, aconteça
    // isto uma ou N vezes.
    {
        let mut stale: Vec<Entity> = world
            .query_filtered::<Entity, With<crate::ui::runtime::UiTag>>()
            .iter(world)
            .collect();
        // Folhas primeiro: o despawn de um pai leva os filhos e iterar uma
        // lista com entidades já despanadas daria erro.
        stale.sort_unstable_by_key(|e| std::cmp::Reverse(*e));
        for entity in stale {
            if let Ok(e) = world.get_entity_mut(entity) {
                e.despawn();
            }
        }
        world
            .resource_mut::<crate::ui::runtime::UiRegistry>()
            .by_id
            .clear();
    }
    let mut sheet = crate::ui::StyleSheet::default();
    // Índice (em `styles`) → índice de FOLHA no StyleSheet. Um ficheiro que
    // falha ao ler não cria folha; sem este mapa os índices desalinham e o
    // scoping por raiz perdia regras legítimas.
    let mut sheet_index_of = vec![usize::MAX; styles.len()];
    for (index, source) in styles.iter().enumerate() {
        match source.strip_prefix('@') {
            Some(relative) => {
                // O src é relativo à PASTA DO JOGO e o prefixo `ui/` faz
                // parte do caminho autor (`@ui/hud.css` → <jogo>/ui/hud.css)
                // — juntar um ui_dir do config DUPLICAVA o prefixo
                // (<jogo>/ui/ui/hud.css) e despia o HUD de estilo (caso
                // real 2026-09-08). O formato do src é contrato do XML.
                let base = world_dir.unwrap_or_else(|| std::path::Path::new("."));
                let path = base.join(relative.trim_start_matches('/'));
                match std::fs::read_to_string(&path) {
                    Ok(text) => sheet_index_of[index] = sheet.parse_into(&text),
                    Err(error) => bevy::log::warn!(
                        "ui: stylesheet {} could not be read ({error})",
                        path.display()
                    ),
                }
            }
            None => sheet_index_of[index] = sheet.parse_into(source),
        }
    }
    world.insert_resource(sheet);
    let font = crate::hud::HudAssets::get(world).font.clone();
    for (position, node) in trees.iter().enumerate() {
        let Some(root) = crate::ui::build_ui_tree(world, node, None, &font, assets) else {
            continue;
        };
        // Shadow-DOM-lite: a raiz vê só as folhas que a precederam no mundo.
        // Índices MAX (fonte falhada) não existem no sheet — inofensivos.
        let allowed: Vec<usize> = tree_sheets
            .get(position)
            .map(|pending| pending.iter().map(|i| sheet_index_of[*i]).collect())
            .unwrap_or_default();
        world
            .entity_mut(root)
            .insert(crate::ui::runtime::UiRootSheets(allowed));
    }
}
