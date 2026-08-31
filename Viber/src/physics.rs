//! Physics — Rapier colliders and rigid bodies from the declarative world XML.
//!
//! VibeGame drives its browser runtime with Rapier (compiled to WASM); Viber
//! uses the same engine natively through `bevy_rapier3d`, so tuning carries
//! over instead of being re-derived against a second solver.
//!
//! # XML contract
//!
//! Two attributes, both VibeGame component strings, on any entity:
//!
//! ```xml
//! <Entity collider="shape: box; size: 0.8 0.8 0.8" rigidbody="type: fixed" />
//! <Entity collider="shape: trimesh; mesh-url: /assets/x_collision.glb; mesh-anchor: base" />
//! <Entity collider="shape: precompute; mesh-url: /assets/rock_lod0.glb" />
//! <Entity collider="auto" />
//! <Entity collider="none" />
//! <Group body="fixed">…</Group>
//! ```
//!
//! `collider` picks the shape, `rigidbody` (or the `body` shorthand on
//! `<Group>`) picks the body kind. Everything in `simple-rpg` is `fixed`: the
//! world is static scenery, and the moving things (hero, creatures) are
//! character controllers over the terrain heightfield rather than rigid bodies
//! — see [`crate::player`].
//!
//! # Deferred shapes
//!
//! `trimesh`, `precompute` and `auto` cannot be built at spawn time: they need
//! mesh data that is still loading. Those entities get a [`PendingCollider`]
//! and [`resolve_pending_colliders`] converts them once the glTF arrives, so a
//! slow asset delays one prop instead of blocking the world.

use bevy::prelude::*;
use bevy_rapier3d::prelude::*;

use crate::recipes::parse_component_string;

/// Where a collision mesh's origin sits relative to the entity.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum MeshAnchor {
    /// Origin as authored (the glTF's own origin).
    #[default]
    Origin,
    /// Origin at the base of the mesh — the pipeline's `reorigin-feet`
    /// convention, where y = 0 is the footprint.
    Base,
}

/// The collision shape an entity asks for.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum ColliderShape {
    /// No collider at all (`collider="none"`, and the default).
    #[default]
    None,
    /// Box derived from the entity's own rendered bounds (`collider="auto"`).
    Auto,
    /// Explicit box, full size in meters, with an optional local offset.
    Box { size: Vec3, offset: Vec3 },
    /// Exact triangle mesh from a dedicated collision glTF.
    Mesh { url: String, anchor: MeshAnchor },
    /// Convex hull baked from a render mesh — the cheap stand-in used for
    /// rocks and trees, which have no authored collision mesh.
    Precompute { url: String },
}

/// Rigid-body kind requested by `rigidbody="type: …"` / `body="…"`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BodyKind {
    /// No body — a bare collider, or nothing at all.
    #[default]
    None,
    /// Immovable scenery.
    Fixed,
    /// Simulated by the solver.
    Dynamic,
    /// Moved by code, pushes dynamics.
    Kinematic,
}

/// Everything the physics runtime needs for one entity.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct PhysicsSpec {
    pub collider: ColliderShape,
    pub body: BodyKind,
    pub mass: Option<f32>,
    pub gravity_scale: Option<f32>,
}

impl PhysicsSpec {
    /// Nothing to spawn for this entity.
    pub fn is_empty(&self) -> bool {
        self.collider == ColliderShape::None && self.body == BodyKind::None
    }
}

/// Parses a `collider="…"` attribute.
///
/// Accepts the two bare forms (`none`, `auto`) and the component-string form
/// (`shape: box; size: …`). An unknown or malformed value yields
/// [`ColliderShape::None`] plus a warning string, so one bad prop never fails
/// the whole world.
pub fn parse_collider(value: &str) -> (ColliderShape, Option<String>) {
    let trimmed = value.trim();
    match trimmed.to_ascii_lowercase().as_str() {
        "" | "none" | "false" | "0" => return (ColliderShape::None, None),
        "auto" | "true" | "1" => return (ColliderShape::Auto, None),
        _ => {}
    }
    let props = parse_component_string(trimmed);
    let get = |key: &str| {
        props
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.trim().to_string())
    };
    let Some(shape) = get("shape") else {
        return (
            ColliderShape::None,
            Some(format!("collider `{trimmed}`: no `shape:` — ignored")),
        );
    };
    match shape.to_ascii_lowercase().as_str() {
        "none" => (ColliderShape::None, None),
        "auto" => (ColliderShape::Auto, None),
        "box" | "cuboid" => {
            let size = get("size")
                .and_then(|s| parse_vec3(&s))
                .unwrap_or(Vec3::ONE);
            // Only the Y offset is used in practice (`pos-offset-y`), but the
            // three axes are accepted for symmetry.
            let offset = Vec3::new(
                get("pos-offset-x")
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(0.0),
                get("pos-offset-y")
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(0.0),
                get("pos-offset-z")
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(0.0),
            );
            (ColliderShape::Box { size, offset }, None)
        }
        "trimesh" | "mesh" => match get("mesh-url") {
            Some(url) => {
                let anchor = match get("mesh-anchor").as_deref() {
                    Some("base") => MeshAnchor::Base,
                    _ => MeshAnchor::Origin,
                };
                (ColliderShape::Mesh { url, anchor }, None)
            }
            None => (
                ColliderShape::None,
                Some(format!("collider `{trimmed}`: trimesh without `mesh-url`")),
            ),
        },
        "precompute" | "convex" => match get("mesh-url") {
            Some(url) => (ColliderShape::Precompute { url }, None),
            None => (
                ColliderShape::None,
                Some(format!(
                    "collider `{trimmed}`: precompute without `mesh-url`"
                )),
            ),
        },
        other => (
            ColliderShape::None,
            Some(format!("collider shape `{other}`: not supported — ignored")),
        ),
    }
}

/// Parses a `rigidbody="…"` attribute (component string) or the `body="…"`
/// shorthand (a bare kind).
pub fn parse_body(value: &str) -> (BodyKind, Option<f32>, Option<f32>) {
    let trimmed = value.trim();
    if let Some(kind) = bare_body_kind(trimmed) {
        return (kind, None, None);
    }
    let props = parse_component_string(trimmed);
    let get = |key: &str| props.iter().find(|(k, _)| k == key).map(|(_, v)| v.trim());
    let kind = get("type")
        .and_then(bare_body_kind)
        .unwrap_or(BodyKind::None);
    let mass = get("mass").and_then(|v| v.parse().ok());
    let gravity_scale = get("gravity-scale").and_then(|v| v.parse().ok());
    (kind, mass, gravity_scale)
}

fn bare_body_kind(value: &str) -> Option<BodyKind> {
    match value.trim().to_ascii_lowercase().as_str() {
        "none" | "" => Some(BodyKind::None),
        "fixed" | "static" => Some(BodyKind::Fixed),
        "dynamic" => Some(BodyKind::Dynamic),
        "kinematic" | "kinematicposition" | "kinematic-position" => Some(BodyKind::Kinematic),
        _ => None,
    }
}

fn parse_vec3(value: &str) -> Option<Vec3> {
    let parts: Vec<f32> = value
        .split([' ', ','])
        .filter(|p| !p.trim().is_empty())
        .filter_map(|p| p.trim().parse().ok())
        .collect();
    match parts.len() {
        1 => Some(Vec3::splat(parts[0])),
        3 => Some(Vec3::new(parts[0], parts[1], parts[2])),
        _ => None,
    }
}

// ------------------------------------------------------------------ runtime

/// A collider that still needs mesh data before it can be built.
#[derive(Debug, Component, Clone)]
pub struct PendingCollider {
    pub shape: ColliderShape,
    /// Handle of the glTF being loaded for `Mesh` / `Precompute` shapes.
    pub gltf: Option<Handle<bevy::gltf::Gltf>>,
}

/// Marker for entities whose collider has been resolved (or given up on), so
/// the resolver never revisits them.
#[derive(Debug, Component)]
pub struct ColliderResolved;

/// Rapier wiring for Viber.
///
/// Registers the Rapier plugin itself; collider/body components are attached
/// by [`crate::recipes::spawn`] as entities are created, and deferred shapes
/// are finished by [`resolve_pending_colliders`].
#[derive(Default)]
pub struct PhysicsPlugin {
    /// Draw collider wireframes (`viber run --physics-debug`).
    pub debug: bool,
}

impl bevy::app::Plugin for PhysicsPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.add_plugins(RapierPhysicsPlugin::<NoUserData>::default())
            .add_systems(
                bevy::app::Update,
                (resolve_pending_colliders, stream_terrain_colliders),
            );
        if self.debug {
            app.add_plugins(RapierDebugRenderPlugin::default());
        }
    }
}

/// Inserts the Rapier body for a [`BodyKind`], if any.
pub fn body_bundle(
    kind: BodyKind,
    gravity_scale: Option<f32>,
) -> Option<(RigidBody, GravityScale)> {
    let body = match kind {
        BodyKind::None => return None,
        BodyKind::Fixed => RigidBody::Fixed,
        BodyKind::Dynamic => RigidBody::Dynamic,
        BodyKind::Kinematic => RigidBody::KinematicPositionBased,
    };
    Some((body, GravityScale(gravity_scale.unwrap_or(1.0))))
}

/// Builds the collider for a shape that needs no asset, i.e. an explicit box.
/// Deferred shapes return `None` and are handled by the resolver.
pub fn immediate_collider(shape: &ColliderShape) -> Option<(Collider, Transform)> {
    match shape {
        ColliderShape::Box { size, offset } => {
            let half = (*size * 0.5).max(Vec3::splat(1e-3));
            Some((
                Collider::cuboid(half.x, half.y, half.z),
                Transform::from_translation(*offset),
            ))
        }
        _ => None,
    }
}

/// Finishes [`PendingCollider`]s whose glTF has finished loading.
///
/// `Auto` resolves from the entity's rendered [`Aabb`]; the mesh shapes bake a
/// Rapier shape out of every primitive in the loaded glTF.
#[allow(clippy::type_complexity)]
pub fn resolve_pending_colliders(
    mut commands: Commands,
    gltfs: Res<Assets<bevy::gltf::Gltf>>,
    gltf_meshes: Res<Assets<bevy::gltf::GltfMesh>>,
    meshes: Res<Assets<Mesh>>,
    pending: Query<
        (
            Entity,
            &PendingCollider,
            Option<&bevy::camera::primitives::Aabb>,
        ),
        Without<ColliderResolved>,
    >,
) {
    for (entity, request, aabb) in &pending {
        match &request.shape {
            ColliderShape::Auto => {
                // The AABB only exists once the entity's mesh is loaded.
                let Some(aabb) = aabb else { continue };
                let half = Vec3::from(aabb.half_extents).max(Vec3::splat(1e-3));
                commands
                    .entity(entity)
                    .insert((Collider::cuboid(half.x, half.y, half.z), ColliderResolved));
            }
            ColliderShape::Mesh { .. } | ColliderShape::Precompute { .. } => {
                let Some(handle) = request.gltf.as_ref() else {
                    commands.entity(entity).insert(ColliderResolved);
                    continue;
                };
                let Some(gltf) = gltfs.get(handle) else {
                    continue; // still loading
                };
                let convex = matches!(request.shape, ColliderShape::Precompute { .. });
                let Some(collider) = collider_from_gltf(gltf, &gltf_meshes, &meshes, convex) else {
                    commands.entity(entity).insert(ColliderResolved);
                    continue;
                };
                // Both anchors currently resolve to the entity origin: the
                // pipeline already exports collision meshes with y = 0 at the
                // footprint, so `mesh-anchor: base` is a statement of that fact
                // rather than a correction to apply.
                commands.entity(entity).insert((collider, ColliderResolved));
            }
            // Boxes are built at spawn time; `None` never becomes a collider.
            ColliderShape::Box { .. } | ColliderShape::None => {
                commands.entity(entity).insert(ColliderResolved);
            }
        }
    }
}

/// Bakes one Rapier collider out of every mesh primitive in a glTF.
///
/// `convex` picks a convex hull (cheap, used for `precompute` on rocks and
/// trees, which have no authored collision mesh); otherwise an exact triangle
/// mesh, which is what the pipeline's dedicated `*_collision.glb` files are
/// for. Multiple primitives become one compound collider.
fn collider_from_gltf(
    gltf: &bevy::gltf::Gltf,
    gltf_meshes: &Assets<bevy::gltf::GltfMesh>,
    meshes: &Assets<Mesh>,
    convex: bool,
) -> Option<Collider> {
    let shape = if convex {
        ComputedColliderShape::ConvexHull
    } else {
        ComputedColliderShape::TriMesh(TriMeshFlags::MERGE_DUPLICATE_VERTICES)
    };
    let mut parts: Vec<(Vec3, Quat, Collider)> = Vec::new();
    for gltf_mesh_handle in &gltf.meshes {
        let Some(gltf_mesh) = gltf_meshes.get(gltf_mesh_handle) else {
            continue;
        };
        for primitive in &gltf_mesh.primitives {
            let Some(mesh) = meshes.get(&primitive.mesh) else {
                continue;
            };
            if let Some(collider) = Collider::from_bevy_mesh(mesh, &shape) {
                parts.push((Vec3::ZERO, Quat::IDENTITY, collider));
            }
        }
    }
    match parts.len() {
        0 => None,
        1 => Some(parts.remove(0).2),
        _ => Some(Collider::compound(parts)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_collider_bare_forms() {
        assert_eq!(parse_collider("none").0, ColliderShape::None);
        assert_eq!(parse_collider("  NONE ").0, ColliderShape::None);
        assert_eq!(parse_collider("").0, ColliderShape::None);
        assert_eq!(parse_collider("auto").0, ColliderShape::Auto);
        assert_eq!(parse_collider("Auto").0, ColliderShape::Auto);
    }

    #[test]
    fn test_parse_collider_box() {
        let (shape, warning) = parse_collider("shape: box; size: 0.8 0.9 1.0");
        assert_eq!(
            shape,
            ColliderShape::Box {
                size: Vec3::new(0.8, 0.9, 1.0),
                offset: Vec3::ZERO,
            }
        );
        assert!(warning.is_none());
    }

    #[test]
    fn test_parse_collider_box_with_offset() {
        // The tall city-wall segments in `simple-rpg` use this exact form.
        let (shape, _) = parse_collider("shape: box; size: 1.5 7.0 2.8; pos-offset-y: 3.5");
        assert_eq!(
            shape,
            ColliderShape::Box {
                size: Vec3::new(1.5, 7.0, 2.8),
                offset: Vec3::new(0.0, 3.5, 0.0),
            }
        );
    }

    #[test]
    fn test_parse_collider_trimesh_and_anchor() {
        let (shape, _) = parse_collider(
            "shape: trimesh; mesh-url: /assets/meshes/village/anvil_collision.glb; mesh-anchor: base",
        );
        assert_eq!(
            shape,
            ColliderShape::Mesh {
                url: "/assets/meshes/village/anvil_collision.glb".into(),
                anchor: MeshAnchor::Base,
            }
        );
        // Without `mesh-anchor` the glTF's own origin is kept.
        let (shape, _) = parse_collider("shape: trimesh; mesh-url: /a.glb");
        assert_eq!(
            shape,
            ColliderShape::Mesh {
                url: "/a.glb".into(),
                anchor: MeshAnchor::Origin,
            }
        );
    }

    #[test]
    fn test_parse_collider_precompute() {
        let (shape, _) =
            parse_collider("shape: precompute; mesh-url: /assets/meshes/props/rock_mossy_lod0.glb");
        assert_eq!(
            shape,
            ColliderShape::Precompute {
                url: "/assets/meshes/props/rock_mossy_lod0.glb".into(),
            }
        );
    }

    #[test]
    fn test_parse_collider_bad_input_warns_but_never_panics() {
        let (shape, warning) = parse_collider("shape: trimesh");
        assert_eq!(shape, ColliderShape::None);
        assert!(warning.is_some(), "missing mesh-url is reported");

        let (shape, warning) = parse_collider("size: 1 2 3");
        assert_eq!(shape, ColliderShape::None);
        assert!(warning.is_some(), "missing shape is reported");

        let (shape, warning) = parse_collider("shape: teapot");
        assert_eq!(shape, ColliderShape::None);
        assert!(warning.is_some(), "unknown shape is reported");

        // A malformed size falls back to a unit box rather than failing.
        let (shape, _) = parse_collider("shape: box; size: nonsense");
        assert_eq!(
            shape,
            ColliderShape::Box {
                size: Vec3::ONE,
                offset: Vec3::ZERO
            }
        );
    }

    #[test]
    fn test_parse_body_component_string() {
        // Every rigidbody in `simple-rpg` is this exact string.
        let (kind, mass, gravity) = parse_body("type: fixed; mass: 0; gravity-scale: 0");
        assert_eq!(kind, BodyKind::Fixed);
        assert_eq!(mass, Some(0.0));
        assert_eq!(gravity, Some(0.0));
    }

    #[test]
    fn test_parse_body_bare_shorthand() {
        assert_eq!(parse_body("fixed").0, BodyKind::Fixed);
        assert_eq!(parse_body("none").0, BodyKind::None);
        assert_eq!(parse_body("dynamic").0, BodyKind::Dynamic);
        assert_eq!(parse_body("kinematic").0, BodyKind::Kinematic);
        assert_eq!(parse_body("nonsense").0, BodyKind::None);
    }

    #[test]
    fn test_immediate_collider_only_builds_boxes() {
        let boxed = ColliderShape::Box {
            size: Vec3::new(2.0, 4.0, 6.0),
            offset: Vec3::Y,
        };
        let (_, transform) = immediate_collider(&boxed).expect("box builds immediately");
        assert_eq!(transform.translation, Vec3::Y);
        assert!(immediate_collider(&ColliderShape::Auto).is_none());
        assert!(immediate_collider(&ColliderShape::None).is_none());
        assert!(
            immediate_collider(&ColliderShape::Mesh {
                url: "/a.glb".into(),
                anchor: MeshAnchor::Base,
            })
            .is_none(),
            "mesh colliders wait for their asset"
        );
    }

    #[test]
    fn test_body_bundle_kinds() {
        assert!(body_bundle(BodyKind::None, None).is_none());
        let (body, gravity) = body_bundle(BodyKind::Fixed, Some(0.0)).expect("fixed body");
        assert!(matches!(body, RigidBody::Fixed));
        assert_eq!(gravity.0, 0.0);
        let (body, gravity) = body_bundle(BodyKind::Dynamic, None).expect("dynamic body");
        assert!(matches!(body, RigidBody::Dynamic));
        assert_eq!(gravity.0, 1.0, "gravity scale defaults to 1");
    }

    #[test]
    fn test_physics_spec_is_empty() {
        assert!(PhysicsSpec::default().is_empty());
        assert!(
            !PhysicsSpec {
                collider: ColliderShape::Auto,
                ..PhysicsSpec::default()
            }
            .is_empty()
        );
    }
}

// ------------------------------------------------------- terrain collision

/// Chunks within this many chunk edges of the camera keep a collider.
///
/// The whole terrain cannot be collidable at once: `simple-rpg` is a 4000 m
/// world of 64 m chunks, and a heightfield collider each (65x65 samples at the
/// default `collision-resolution`) is tens of megabytes of solver data for
/// ground the player cannot reach this frame. Colliders stream in and out with
/// the camera instead.
pub const PHYSICS_CHUNK_RADIUS: f32 = 3.0;

/// Marks a terrain chunk that currently owns a heightfield collider.
#[derive(Debug, Component)]
pub struct TerrainCollider;

/// Adds and removes terrain chunk colliders around the camera.
#[allow(clippy::type_complexity)]
pub fn stream_terrain_colliders(
    mut commands: Commands,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    cameras: Query<&GlobalTransform, With<Camera3d>>,
    chunks: Query<
        (Entity, &Transform, Option<&TerrainCollider>),
        With<crate::terrain::plugin::TerrainChunk>,
    >,
) {
    let Some(runtime) = runtime else { return };
    let Ok(camera) = cameras.single() else { return };
    let camera_xz = {
        let t = camera.translation();
        Vec2::new(t.x, t.z)
    };

    let spec = &runtime.spec;
    let keep_within = spec.chunk_size * PHYSICS_CHUNK_RADIUS;
    // A little hysteresis so a chunk on the boundary does not rebuild its
    // collider every frame the camera jitters across it.
    let drop_beyond = keep_within * 1.25;
    let resolution = spec.collision_resolution.max(1);

    for (entity, transform, has_collider) in &chunks {
        let center = Vec2::new(transform.translation.x, transform.translation.z);
        let distance = center.distance(camera_xz);
        match (has_collider.is_some(), distance) {
            (false, d) if d <= keep_within => {
                if let Some(collider) =
                    chunk_heightfield(&runtime.grid, center, spec.chunk_size, resolution)
                {
                    commands
                        .entity(entity)
                        .insert((collider, RigidBody::Fixed, TerrainCollider));
                }
            }
            (true, d) if d > drop_beyond => {
                commands
                    .entity(entity)
                    .remove::<Collider>()
                    .remove::<RigidBody>()
                    .remove::<TerrainCollider>();
            }
            _ => {}
        }
    }
}

/// Builds a Rapier heightfield for one chunk, sampled from the terrain grid.
///
/// Rapier lays a 3D heightfield out row-major over the XZ plane, centred on the
/// collider's own transform — which is exactly where the chunk entity sits — so
/// the samples are taken relative to `center` and the collider needs no offset.
fn chunk_heightfield(
    grid: &crate::terrain::brush::BrushGrid,
    center: Vec2,
    size: f32,
    resolution: u32,
) -> Option<Collider> {
    if size <= 0.0 || !size.is_finite() {
        return None;
    }
    let n = resolution as usize + 1;
    let step = size / resolution as f32;
    let half = size * 0.5;
    let mut heights = Vec::with_capacity(n * n);
    // Row-major: row index walks +X, column index walks +Z.
    for row in 0..n {
        for col in 0..n {
            let x = center.x - half + row as f32 * step;
            let z = center.y - half + col as f32 * step;
            heights.push(grid.sample(x, z));
        }
    }
    Some(Collider::heightfield(
        heights,
        n,
        n,
        Vec3::new(size, 1.0, size),
    ))
}
