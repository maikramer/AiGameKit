//! Spawning: entity IR → Bevy entities, meshes, lights and cameras.

use std::path::PathBuf;

use bevy::asset::LoadState;
use bevy::audio::{AudioPlayer, PlaybackSettings, Volume};
use bevy::gltf::Gltf;
use bevy::light::NotShadowCaster;
use bevy::math::primitives::{Capsule3d, Cuboid, Cylinder, Plane3d, Sphere};
use bevy::prelude::*;
use bevy::world_serialization::WorldAssetRoot;

use super::{EntityKind, EntitySpec, MaterialSpec, ParsedWorld, Shape, TransformSpec};
use crate::terrain::TerrainSpec;
use crate::terrain::features::TerrainFeatures;
use crate::terrain::roads::{RoadNetworkSpec, RoadSpec, SegmentSpec, WaySpec};
use crate::terrain::spec::TerrainPadSpec;
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
            | EntityKind::Road { .. }
            | EntityKind::RoadNetwork { .. }
            | EntityKind::StaticSpawner { .. }
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
    // Ground features are collected (not spawned) for the terrain runtime.
    let mut pending_terrain = PendingTerrain {
        base_dir: pending.base_dir,
        ..PendingTerrain::default()
    };
    collect_terrain(&parsed.entities, &mut pending_terrain);
    world.insert_resource(pending_terrain);
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
    });
    let mut stats = SpawnStats::default();
    let mut ambient: Option<GlobalAmbientLight> = None;
    let (chips, hud_elements, mixer_settings, pending_worldsys, _sky_request) = {
        let mut ctx = SpawnCtx {
            meshes: &mut meshes,
            materials: &mut materials,
            sky_mats: &mut sky_mats,
            asset_server: &asset_server,
            chip_counter: std::cell::Cell::new(0),
            mixer: std::cell::RefCell::new(None),
            chips: std::cell::RefCell::new(Vec::new()),
            hud: std::cell::RefCell::new(Vec::new()),
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
        let mixer = ctx.mixer.into_inner();
        let pending_worldsys = ctx.worldsys.consume();
        let sky_request = ctx.sky_request.into_inner();
        (
            ctx.chips.into_inner(),
            hud_elements,
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
    if let Some(day) = pending_worldsys.day_cycle {
        world.insert_resource(day);
    }
    if let Some(weather) = pending_worldsys.weather {
        world.insert_resource(weather);
    }
    if let Some(border) = pending_worldsys.border {
        world.insert_resource(border);
    }
    if !pending_worldsys.biomes.is_empty() {
        world.insert_resource(crate::worldsys::BiomeRegions {
            list: pending_worldsys.biomes.clone(),
        });
    }
    for config in pending_worldsys.configs {
        world.insert_resource(config);
    }
    if let Some(mixer) = mixer_settings {
        world.insert_resource(mixer);
    }
    world.insert_resource(meshes);
    world.insert_resource(materials);
    world.insert_resource(sky_mats);
    world.insert_resource(asset_server);
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
    chip_counter: std::cell::Cell<usize>,
    mixer: std::cell::RefCell<Option<crate::music::AudioMixerSettings>>,
    /// Deferred HUD chip UI nodes (resource name + stack index).
    chips: std::cell::RefCell<Vec<(usize, String)>>,
    /// Deferred HUD screen elements (tag + raw attrs).
    hud: std::cell::RefCell<HudList>,
    /// Deferred world-system resources (DayCycle/Weather/border/biomes/config).
    worldsys: crate::worldsys::PendingWorldSystems,
    /// `<Sky>` attrs — constrói o domo no fim do startup.
    sky_request: std::cell::RefCell<Option<Vec<(String, String)>>>,
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
            EntityKind::StaticSpawner { spec: group }
            | EntityKind::DynamicSpawner { spec: group } => {
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
                out.push(crate::spawner::SpawnGroupState {
                    spec: group.clone(),
                    handles,
                    done: false,
                    // `<DynamicSpawner>` instances are driven by the AI system.
                    dynamic: matches!(spec.kind, EntityKind::DynamicSpawner { .. }),
                    template_script: group.template_script.clone(),
                    activation_radius: group.activation_radius,
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
                    template_script: spawner_spec.template_script.clone(),
                    activation_radius: spawner_spec.activation_radius,
                    spec: spawner_spec,
                    handles,
                    done: false,
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
            entity.insert(PendingCollider {
                shape: ColliderShape::Auto,
                gltf: None,
            });
        }
        ColliderShape::Mesh { url, .. } | ColliderShape::Precompute { url } => {
            // Root-absolute asset paths are unapproved in bevy; strip the '/'.
            let handle = ctx
                .asset_server
                .load(url.trim_start_matches('/').to_string());
            entity.insert(PendingCollider {
                shape: physics.collider.clone(),
                gltf: Some(handle),
            });
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
    attach_physics(&mut entity, ctx, spec);
    match &spec.kind {
        EntityKind::Group => {
            // Grupos autorais a y≈0 assentam no terreno real (a vila fica a
            // ~24.6 m no heightmap); conteúdo autoral elevado mantém offset
            // relativo ao novo y.
            entity.insert(crate::worldsys::SeatOnTerrain);
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
                    sim: crate::particles::EmitterSim::new(spec),
                    capacity,
                },
            ));
        }
        EntityKind::GltfScene { url } => {
            let path = url.trim_start_matches('/');
            let handle: Handle<Gltf> = crate::meshopt::load_gltf(ctx.asset_server, path.to_owned());
            entity.insert(GltfScenePending { handle });
        }
        EntityKind::Primitive { shape, material } => {
            let mut primitive = primitive_mesh(shape);
            if material.texture.is_some() {
                if let Some(tile) = material.texture_tile {
                    scale_primitive_uvs(&mut primitive, shape, tile);
                }
            }
            let mesh = ctx.meshes.add(primitive);
            let mat = build_material(material, ctx.materials, ctx.asset_server);
            entity.insert((Mesh3d(mesh), MeshMaterial3d(mat), Visibility::Inherited));
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
            // Cascades tuned for a character-scale third-person view: crisp
            // near the hero, still covering the buildings around him. Bevy's
            // default bound is sized for a small demo scene and leaves the
            // near ground unshadowed in a world this size.
            let cascades = bevy::light::CascadeShadowConfigBuilder {
                num_cascades: 4,
                first_cascade_far_bound: 12.0,
                maximum_distance: 220.0,
                ..Default::default()
            }
            .build();
            entity.insert((light, cascades, transform, Visibility::Inherited));
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
            ctx.mixer.replace(Some(crate::music::AudioMixerSettings {
                master: *master,
                music: *music,
                sfx: *sfx,
            }));
        }
        EntityKind::MusicLayer { layer, base_volume } => {
            let url = format!("assets/audio/bgm/{layer}.ogg");
            let handle = ctx.asset_server.load::<bevy::audio::AudioSource>(url);
            entity.insert((
                AudioPlayer(handle),
                PlaybackSettings::LOOP.with_volume(Volume::Linear(0.0)),
                crate::music::MusicLayerTag {
                    layer: layer.clone(),
                    base_volume: *base_volume,
                },
            ));
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
        | EntityKind::Road { .. }
        | EntityKind::RoadNetwork { .. }
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
            polygon,
            fog_density,
            tint,
        } => {
            ctx.worldsys.biomes.push(crate::worldsys::BiomeRegionData {
                id: id.clone(),
                polygon: polygon.clone(),
                fog_density: *fog_density,
                tint: *tint,
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
    materials: &mut Assets<StandardMaterial>,
    asset_server: &AssetServer,
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
    if let Some(url) = spec.texture.as_deref() {
        material.base_color_texture =
            Some(asset_server.load(url.trim_start_matches('/').to_owned()));
    }
    materials.add(material)
}

/// Re-escala as UVs (0..1) de uma primitiva para `extent / tile`, de modo que
/// `texture-tile-size` seja metros por repetição da textura.
fn scale_primitive_uvs(mesh: &mut bevy::mesh::Mesh, shape: &Shape, tile: f32) {
    if tile <= 0.0 {
        return;
    }
    let extents: [f32; 2] = match shape {
        Shape::Plane { half_size } => [half_size[0] * 2.0, half_size[1] * 2.0],
        Shape::Cuboid { half_size } => [half_size[0] * 2.0, half_size[2] * 2.0],
        _ => return, // esferas/cilindros/cápsulas: UV polares, tiling por extensão não se aplica
    };
    let scale = [extents[0] / tile, extents[1] / tile];
    if let Some(bevy::mesh::VertexAttributeValues::Float32x2(values)) =
        mesh.attribute_mut(bevy::mesh::Mesh::ATTRIBUTE_UV_0)
    {
        for uv in values.iter_mut() {
            *uv = [uv[0] * scale[0], uv[1] * scale[1]];
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
        if let Some(runtime) = runtime.as_deref() {
            let min_y = runtime.sample(cam.translation.x, cam.translation.z) + 1.0;
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
