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
use crate::terrain::features::TerrainFeatures;
use crate::terrain::roads::{RoadNetworkSpec, RoadSpec, SegmentSpec, WaySpec};
use crate::terrain::spec::TerrainPadSpec;
use crate::terrain::water::{LakeSpec, RiverSpec};

/// Marker for `<OrbitCamera>`: keeps its offset from a named target.
#[derive(Debug, Component)]
pub struct OrbitCamera {
    pub target: Option<String>,
    pub distance: f32,
    pub height: f32,
    /// `Some` only when the world set `pitch` explicitly; it overrides
    /// `height` (see [`orbit_camera_follow`]).
    pub pitch_deg: Option<f32>,
}

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

fn collect_terrain(specs: &[EntitySpec], out: &mut PendingTerrain) {
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
    {
        let mut ctx = SpawnCtx {
            meshes: &mut meshes,
            materials: &mut materials,
            asset_server: &asset_server,
        };
        for spec in &parsed.entities {
            if is_ground_feature(&spec.kind) {
                continue;
            }
            spawn_entity(world, &mut ctx, spec, None, &mut stats, &mut ambient);
        }
    }
    if let Some(light) = ambient {
        world.insert_resource(light);
    }
    world.insert_resource(meshes);
    world.insert_resource(materials);
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

/// Borrowed asset handles used while spawning one world.
struct SpawnCtx<'a> {
    meshes: &'a mut Assets<Mesh>,
    materials: &'a mut Assets<StandardMaterial>,
    asset_server: &'a AssetServer,
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
            EntityKind::StaticSpawner { spec } | EntityKind::DynamicSpawner { spec } => {
                let handles = spec
                    .template_urls
                    .iter()
                    .map(|url| asset_server.load::<Gltf>(url.trim_start_matches('/').to_owned()))
                    .collect();
                out.push(crate::spawner::SpawnGroupState {
                    spec: spec.clone(),
                    handles,
                    done: false,
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
                    .map(|url| asset_server.load::<Gltf>(url.trim_start_matches('/').to_owned()))
                    .collect();
                out.push(crate::spawner::SpawnGroupState {
                    spec: spawner_spec,
                    handles,
                    done: false,
                });
            }
            _ => collect_spawn_groups(&spec.children, asset_server, out, exclusions),
        }
    }
}

/// Recursively spawn one spec (and its children) as Bevy entities.
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
    let mut entity = world.spawn(build_transform(&spec.transform));
    if let Some(parent) = parent {
        entity.insert(ChildOf(parent));
    }
    if let Some(name) = &spec.name {
        entity.insert(Name::new(name.clone()));
    }
    match &spec.kind {
        EntityKind::Group => {}
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
            let handle: Handle<Gltf> = ctx.asset_server.load(path.to_owned());
            entity.insert(GltfScenePending { handle });
        }
        EntityKind::Primitive { shape, material } => {
            let mesh = build_mesh(shape, ctx.meshes);
            let mat = build_material(material, ctx.materials);
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
            let mut light = DirectionalLight::default();
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
            entity.insert((light, transform, Visibility::Inherited));
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
        } => {
            entity.insert((
                Camera3d::default(),
                OrbitCamera {
                    target: target.clone(),
                    distance: *distance,
                    height: *height,
                    pitch_deg: *pitch_deg,
                },
            ));
            stats.has_camera = true;
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

fn build_mesh(shape: &Shape, meshes: &mut Assets<Mesh>) -> Handle<Mesh> {
    let mesh = match shape {
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
    };
    meshes.add(mesh)
}

fn build_material(
    spec: &MaterialSpec,
    materials: &mut Assets<StandardMaterial>,
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
    materials.add(material)
}

/// `<OrbitCamera>` follow: keeps a fixed spherical offset from the named
/// target. An explicit `pitch` (degrees) overrides `height` via
/// `height = distance · tan(pitch)`; input-driven yaw lands with the player
/// in a later phase.
pub fn orbit_camera_follow(
    mut cameras: Query<(&mut Transform, &OrbitCamera)>,
    names: Query<(Entity, &Name)>,
    globals: Query<&GlobalTransform>,
) {
    for (mut cam, settings) in &mut cameras {
        let target_pos = match &settings.target {
            Some(target_name) => names
                .iter()
                .find(|(_, name)| name.as_str() == target_name)
                .and_then(|(entity, _)| globals.get(entity).ok())
                .map(|g| g.translation())
                .unwrap_or(Vec3::ZERO),
            None => Vec3::ZERO,
        };
        let height = match settings.pitch_deg {
            Some(pitch) => settings.distance * pitch.to_radians().tan(),
            None => settings.height,
        };
        cam.translation = target_pos + Vec3::new(0.0, height, settings.distance);
        cam.look_at(target_pos, Vec3::Y);
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
