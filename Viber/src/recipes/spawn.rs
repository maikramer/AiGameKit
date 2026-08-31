//! Spawning: entity IR → Bevy entities, meshes, lights and cameras.

use bevy::math::primitives::{Capsule3d, Cuboid, Cylinder, Plane3d, Sphere};
use bevy::prelude::*;

use super::{EntityKind, EntitySpec, MaterialSpec, ParsedWorld, Shape, TransformSpec};

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
#[derive(Resource)]
pub struct PendingWorld(pub ParsedWorld);

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
    let parsed = pending.0;
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
    let mut stats = SpawnStats::default();
    let mut ambient: Option<GlobalAmbientLight> = None;
    for spec in &parsed.entities {
        spawn_entity(
            world,
            &mut meshes,
            &mut materials,
            spec,
            None,
            &mut stats,
            &mut ambient,
        );
    }
    if let Some(light) = ambient {
        world.insert_resource(light);
    }
    world.insert_resource(meshes);
    world.insert_resource(materials);
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

/// Recursively spawn one spec (and its children) as Bevy entities.
fn spawn_entity(
    world: &mut World,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
    spec: &EntitySpec,
    parent: Option<Entity>,
    stats: &mut SpawnStats,
    ambient: &mut Option<GlobalAmbientLight>,
) {
    let mut entity = world.spawn(build_transform(&spec.transform));
    if let Some(parent) = parent {
        entity.insert(ChildOf(parent));
    }
    if let Some(name) = &spec.name {
        entity.insert(Name::new(name.clone()));
    }
    match &spec.kind {
        EntityKind::Group => {}
        EntityKind::Primitive { shape, material } => {
            let mesh = build_mesh(shape, meshes);
            let mat = build_material(material, materials);
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
    }
    stats.entities += 1;
    let id = entity.id();
    for child in &spec.children {
        spawn_entity(world, meshes, materials, child, Some(id), stats, ambient);
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
