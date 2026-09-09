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
    /// On a primitive (incl. as parte de uma `<Composition>`) resolve no
    /// spawn para um colisor EXATO derivado da forma — sem espera de malha.
    Auto,
    /// Explicit box, full size in meters, with an optional local offset.
    Box { size: Vec3, offset: Vec3 },
    /// Explicit sphere (exact ball collider — `shape: sphere; radius: …`).
    Sphere { radius: f32 },
    /// Explicit cylinder (Y axis — `shape: cylinder; radius: …; half-height: …`).
    Cylinder { radius: f32, half_height: f32 },
    /// Explicit capsule (Y axis — `shape: capsule; radius: …; half-height: …`).
    Capsule { radius: f32, half_height: f32 },
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
            // `size` presente mas não parseável (ex.: 2 valores) caía em
            // silêncio num cubo 1×1×1 — avisa como o mesh-url em falta.
            let size = match get("size") {
                Some(s) => match parse_vec3(&s) {
                    Some(v) => v,
                    None => {
                        return (
                            ColliderShape::None,
                            Some(format!(
                                "collider `{trimmed}`: box `size:` needs 1 or 3 values — ignored"
                            )),
                        );
                    }
                },
                None => Vec3::ONE,
            };
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
        "sphere" | "ball" => {
            let radius = get("radius")
                .and_then(|r| r.parse::<f32>().ok())
                .unwrap_or(0.5);
            (ColliderShape::Sphere { radius }, None)
        }
        "cylinder" => {
            let radius = get("radius")
                .and_then(|r| r.parse::<f32>().ok())
                .unwrap_or(0.5);
            let half_height = get("half-height")
                .and_then(|h| h.parse::<f32>().ok())
                .unwrap_or(0.5);
            (
                ColliderShape::Cylinder {
                    radius,
                    half_height,
                },
                None,
            )
        }
        "capsule" => {
            let radius = get("radius")
                .and_then(|r| r.parse::<f32>().ok())
                .unwrap_or(0.5);
            let half_height = get("half-height")
                .and_then(|h| h.parse::<f32>().ok())
                .unwrap_or(0.5);
            (
                ColliderShape::Capsule {
                    radius,
                    half_height,
                },
                None,
            )
        }
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
    /// Segundos esperando o glTF. Passou de [`PENDING_TIMEOUT`], resolve por
    /// AABB (da entidade ou da cena filha) — colisor garantido em vez de
    /// parede atravessável.
    pub age: f32,
}

/// Segundos de espera pelo glTF antes do fallback por AABB.
pub const PENDING_TIMEOUT: f32 = 4.0;

/// Colliders already baked from a glTF, keyed by asset and shape kind.
///
/// The world reuses a handful of props hundreds of times — `simple-rpg` places
/// the same crate 44 times and the same wall segment 32 — and every instance
/// would otherwise re-triangulate the identical mesh. Rapier colliders are
/// reference-counted internally, so the clones share one shape. The failure
/// reason (when the bake failed) is cached too, so the warning is emitted
/// once per asset instead of once per placed instance.
#[derive(Resource, Default)]
pub struct ColliderCache {
    baked: std::collections::HashMap<(AssetId<bevy::gltf::Gltf>, bool), BakedCollider>,
}

/// Outcome of one asset's bake: the collider, or the reason there is none.
#[derive(Clone, Debug, Default)]
pub struct BakedCollider {
    pub collider: Option<Collider>,
    /// Why the bake failed — for the warn-once log only.
    pub reason: Option<String>,
}

impl ColliderCache {
    /// Number of distinct baked shapes (cache entries).
    pub fn len(&self) -> usize {
        self.baked.len()
    }

    /// True when nothing has been baked yet.
    pub fn is_empty(&self) -> bool {
        self.baked.is_empty()
    }
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
        app.init_resource::<ColliderCache>()
            .init_resource::<TerrainCollisionStatus>()
            .add_plugins(RapierPhysicsPlugin::<NoUserData>::default())
            .add_systems(
                bevy::app::Update,
                (
                    resolve_pending_colliders,
                    // Depois do LOD: o swap de coluna despacha caixas e spawna
                    // as novas no mesmo frame, e sem esta ordem o streaming
                    // via a grelha do frame anterior — um ou mais frames sem
                    // chão debaixo do herói a cada troca de LOD.
                    stream_voxel_colliders.after(crate::terrain::plugin::TerrainSet::Columns),
                ),
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

/// Builds the collider for a shape that needs no asset (explicit box,
/// sphere, cylinder, capsule). Deferred shapes return `None` and are handled
/// by the resolver.
pub fn immediate_collider(shape: &ColliderShape) -> Option<(Collider, Transform)> {
    let collider = match shape {
        ColliderShape::Box { size, offset: _ } => {
            let half = (*size * 0.5).max(Vec3::splat(1e-3));
            Collider::cuboid(half.x, half.y, half.z)
        }
        ColliderShape::Sphere { radius } => Collider::ball(radius.max(1e-3)),
        ColliderShape::Cylinder {
            radius,
            half_height,
        } => Collider::cylinder(half_height.max(1e-3), radius.max(1e-3)),
        ColliderShape::Capsule {
            radius,
            half_height,
        } => Collider::capsule_y(half_height.max(1e-3), radius.max(1e-3)),
        _ => return None,
    };
    // Só a caixa tem offset autoral; as restantes assentam no próprio transform.
    let offset = match shape {
        ColliderShape::Box { offset, .. } => *offset,
        _ => Vec3::ZERO,
    };
    Some((collider, Transform::from_translation(offset)))
}

/// Colisor EXATO para a forma de uma primitiva (partes de uma
/// `<Composition>`, ou qualquer primitiva com `collider="auto"`) — derivado
/// da geometria, multiplicado pelo scale da parte, sem PendingCollider.
///
/// Aproximações assumidas (baratas e visuais-mente corretas): esfera com
/// scale não-uniforme usa o maior eixo (elipsoide → ball); cilindro/cápsula
/// usam o maior eixo horizontal para o raio (elipse → círculo).
pub fn collider_for_shape(shape: &crate::recipes::Shape, scale: Vec3) -> Collider {
    match shape {
        crate::recipes::Shape::Cuboid { half_size } => Collider::cuboid(
            (half_size[0] * scale.x).max(1e-3),
            (half_size[1] * scale.y).max(1e-3),
            (half_size[2] * scale.z).max(1e-3),
        ),
        crate::recipes::Shape::Sphere { radius } => {
            Collider::ball((radius * scale.max_element()).max(1e-3))
        }
        crate::recipes::Shape::Cylinder {
            half_height,
            radius,
        } => Collider::cylinder(
            (half_height * scale.y).max(1e-3),
            (radius * scale.x.max(scale.z)).max(1e-3),
        ),
        crate::recipes::Shape::Capsule {
            radius,
            half_height,
        } => Collider::capsule_y(
            (half_height * scale.y).max(1e-3),
            (radius * scale.x.max(scale.z)).max(1e-3),
        ),
        // Planos XZ: lâmina fina (a mesma convenção do Pad/Plane do VibeGame).
        crate::recipes::Shape::Plane { half_size } => Collider::cuboid(
            (half_size[0] * scale.x).max(1e-3),
            0.02,
            (half_size[1] * scale.z).max(1e-3),
        ),
    }
}

/// Finishes [`PendingCollider`]s whose glTF has finished loading.
///
/// `Auto` resolves from the entity's rendered [`Aabb`]; the mesh shapes bake a
/// Rapier shape out of every primitive in the loaded glTF.
#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
pub fn resolve_pending_colliders(
    mut commands: Commands,
    time: Res<Time>,
    server: Res<AssetServer>,
    mut cache: ResMut<ColliderCache>,
    gltfs: Res<Assets<bevy::gltf::Gltf>>,
    gltf_meshes: Res<Assets<bevy::gltf::GltfMesh>>,
    meshes: Res<Assets<Mesh>>,
    mut pending: Query<
        (
            Entity,
            &mut PendingCollider,
            Option<&bevy::camera::primitives::Aabb>,
            Option<&Children>,
            Option<&GlobalTransform>,
        ),
        Without<ColliderResolved>,
    >,
    scene_bounds: Query<(&GlobalTransform, Option<&bevy::camera::primitives::Aabb>)>,
    mut debug_colliders: Local<u32>,
) {
    for (entity, mut request, aabb, children, entity_global) in pending.iter_mut() {
        request.age += time.delta_secs();
        match &request.shape {
            ColliderShape::Auto => {
                // The AABB only exists once the entity's mesh is loaded.
                let Some(aabb) = aabb else {
                    if request.age < PENDING_TIMEOUT {
                        continue;
                    }
                    // Cena demorou: AABB dos filhos ou desiste.
                    if let Some((center, half)) = fallback_children_aabb(children, &scene_bounds) {
                        fallback_cuboid_child(&mut commands, entity, entity_global, center, half);
                    }
                    commands.entity(entity).insert(ColliderResolved);
                    continue;
                };
                let half = Vec3::from(aabb.half_extents).max(Vec3::splat(1e-3));
                commands
                    .entity(entity)
                    .insert((Collider::cuboid(half.x, half.y, half.z), ColliderResolved));
            }
            ColliderShape::Mesh { .. } | ColliderShape::Precompute { .. } => {
                let Some(handle) = request.gltf.as_ref() else {
                    bevy::log::warn!("physics: PendingCollider sem handle (entity {entity:?})");
                    commands.entity(entity).insert(ColliderResolved);
                    continue;
                };
                if *debug_colliders < 8 {
                    *debug_colliders += 1;
                    // Diagnóstico temporário de load — trace em produção
                    // (warn poluía o log por cada collider pendente).
                    bevy::log::trace!(
                        "physics: estado do load = {:?} ({entity:?})",
                        server.get_load_state(handle)
                    );
                }
                // A collision glTF that cannot be loaded must not leave the
                // prop pending forever. The asset pool is missing dozens of
                // `*_collision.glb` files that the world still references, so
                // this is the common case, not an edge case: fall back to the
                // rendered bounds, which gives the prop *some* collision
                // instead of letting the player walk through it.
                if request.age > PENDING_TIMEOUT
                    && matches!(
                        server.get_load_state(handle),
                        Some(bevy::asset::LoadState::Loading)
                    )
                {
                    bevy::log::warn!(
                        "physics: glTF de colisão atrasado (>{} s) — fallback AABB (entity {entity:?})",
                        PENDING_TIMEOUT
                    );
                    if let Some((center, half)) = fallback_children_aabb(children, &scene_bounds) {
                        fallback_cuboid_child(&mut commands, entity, entity_global, center, half);
                    }
                    commands.entity(entity).insert(ColliderResolved);
                    continue;
                }
                if matches!(
                    server.get_load_state(handle),
                    Some(bevy::asset::LoadState::Failed(_))
                ) {
                    match aabb {
                        Some(aabb) => {
                            let half = Vec3::from(aabb.half_extents).max(Vec3::splat(1e-3));
                            commands.entity(entity).insert((
                                Collider::cuboid(half.x, half.y, half.z),
                                ColliderResolved,
                            ));
                        }
                        // No bounds yet either — give up rather than spin.
                        None => {
                            bevy::log::warn!(
                                "physics: colisão falhou ao carregar e sem Aabb — sem collider (entity {entity:?})"
                            );
                            commands.entity(entity).insert(ColliderResolved);
                        }
                    }
                    continue;
                }
                let Some(gltf) = gltfs.get(handle) else {
                    continue; // still loading
                };
                let convex = matches!(request.shape, ColliderShape::Precompute { .. });
                let key = (handle.id(), convex);
                let cached = match cache.baked.get(&key) {
                    Some(cached) => cached.clone(),
                    None => {
                        let (built, reason) =
                            collider_from_gltf(gltf, &gltf_meshes, &meshes, convex);
                        if built.is_none() && gltf_meshes_pending(gltf, &gltf_meshes, &meshes) {
                            // Meshes derivados ainda a materializar — o bake
                            // é retry no próximo tick; NÃO cachear o None
                            // (uma corrida no arranque envenenava o asset
                            // inteiro: 1.ª entidade sem collider, todas as
                            // seguintes a herdar da cache).
                            continue;
                        }
                        if built.is_none() {
                            // Warn-once POR ASSET: o bake corre uma única vez
                            // por asset (a cache guarda o motivo); as instâncias
                            // seguintes resolvem silenciosamente abaixo.
                            bevy::log::warn!(
                                "physics: bake falhou para {:?} — {} (asset {key:?})",
                                request.shape,
                                reason.as_deref().unwrap_or("motivo desconhecido")
                            );
                        }
                        let entry = BakedCollider {
                            collider: built.clone(),
                            reason,
                        };
                        cache.baked.insert(key, entry.clone());
                        entry
                    }
                };
                let Some(collider) = cached.collider else {
                    // O asset já foi reportado (warn-once acima, na 1.ª falha
                    // do bake) — dar a esta instância o mesmo destino sem
                    // repetir o warn 300× no log.
                    commands.entity(entity).insert(ColliderResolved);
                    continue;
                };
                // Both anchors currently resolve to the entity origin: the
                // pipeline already exports collision meshes with y = 0 at the
                // footprint, so `mesh-anchor: base` is a statement of that fact
                // rather than a correction to apply.
                commands.entity(entity).insert((collider, ColliderResolved));
            }
            // Boxes/spheres/cylinders/capsules are built at spawn time;
            // `None` never becomes a collider.
            ColliderShape::Box { .. }
            | ColliderShape::Sphere { .. }
            | ColliderShape::Cylinder { .. }
            | ColliderShape::Capsule { .. }
            | ColliderShape::None => {
                commands.entity(entity).insert(ColliderResolved);
            }
        }
    }
}

/// AABB unido das cenas filhas (mundo). `None` quando nada tem bounds.
fn fallback_children_aabb(
    children: Option<&Children>,
    scene_bounds: &Query<(&GlobalTransform, Option<&bevy::camera::primitives::Aabb>)>,
) -> Option<(Vec3, Vec3)> {
    let children = children?;
    let mut min = Vec3::splat(f32::INFINITY);
    let mut max = Vec3::splat(f32::NEG_INFINITY);
    for child in children.iter() {
        let Ok((transform, aabb)) = scene_bounds.get(child) else {
            continue;
        };
        let Some(aabb) = aabb else { continue };
        let center = transform.transform_point(aabb.center.into());
        let half = Vec3::from(aabb.half_extents);
        min = min.min(center - half);
        max = max.max(center + half);
    }
    if !min.is_finite() || !max.is_finite() {
        return None;
    }
    let center = (min + max) * 0.5;
    Some((center, (max - min) * 0.5))
}

/// Fallback AABB como FILHO da entidade, com o offset relativo ao centro do
/// prop. Inserir `Transform::from_translation(center)` na própria entidade —
/// `center` é world-space — teleportava o prop e quebrava a hierarquia.
fn fallback_cuboid_child(
    commands: &mut Commands,
    entity: Entity,
    entity_global: Option<&GlobalTransform>,
    center: Vec3,
    half: Vec3,
) {
    let local = entity_global
        .map(|g| g.affine().inverse().transform_point3(center))
        .unwrap_or(center);
    let collider = commands
        .spawn((
            Name::new("collider"),
            Collider::cuboid(half.x.max(1e-3), half.y.max(1e-3), half.z.max(1e-3)),
            Transform::from_translation(local),
        ))
        .id();
    commands.entity(entity).add_child(collider);
}

/// Bakes one Rapier collider out of every mesh primitive in a glTF.
///
/// `convex` picks a convex hull (cheap, used for `precompute` on rocks and
/// trees, which have no authored collision mesh); otherwise an exact triangle
/// mesh, which is what the pipeline's dedicated `*_collision.glb` files are
/// for. Multiple primitives become one compound collider.
/// True while any mesh derived from the glTF's primitives has not landed in
/// the `Mesh` assets yet (or the `GltfMesh` itself is still pending) — i.e. a
/// empty bake right now is a race, not malformed geometry.
fn gltf_meshes_pending(
    gltf: &bevy::gltf::Gltf,
    gltf_meshes: &Assets<bevy::gltf::GltfMesh>,
    meshes: &Assets<Mesh>,
) -> bool {
    gltf.meshes.iter().any(|mesh_handle| {
        let Some(gltf_mesh) = gltf_meshes.get(mesh_handle) else {
            return true;
        };
        gltf_mesh
            .primitives
            .iter()
            .any(|primitive| meshes.get(&primitive.mesh).is_none())
    })
}

/// Positions plus triangle indices of a Bevy mesh, in Rapier's layout.
///
/// This exists because [`Collider::from_bevy_mesh`] rejects any mesh whose
/// index buffer is gone — and Bevy's glTF loader throws it away for every
/// primitive that ships without `NORMAL`: it calls `duplicate_vertices()`
/// (which sets `indices` to `None`) before `compute_flat_normals()`. The
/// pipeline's `*_collision.glb` files are position-only by design, so *every*
/// `shape: trimesh` prop — the city wall, the houses, the interiors — baked to
/// nothing and the hero walked straight through them. Only `shape: box` props
/// (the plaza benches, crates and barrels) still collided, because those never
/// go through a mesh at all.
///
/// An un-indexed triangle list is still a triangle list: number the vertices
/// in order and the geometry is recovered exactly.
pub fn mesh_vertices_indices(mesh: &Mesh) -> Option<(Vec<Vec3>, Vec<[u32; 3]>)> {
    use bevy::mesh::VertexAttributeValues;
    use bevy::render::mesh::{Indices, PrimitiveTopology};

    if mesh.primitive_topology() != PrimitiveTopology::TriangleList {
        return None;
    }
    let vertices: Vec<Vec3> = match mesh.attribute(Mesh::ATTRIBUTE_POSITION)? {
        VertexAttributeValues::Float32x3(values) => values.iter().map(|v| Vec3::from(*v)).collect(),
        _ => return None,
    };
    let indices: Vec<[u32; 3]> = match mesh.indices() {
        Some(Indices::U16(idx)) => idx
            .chunks_exact(3)
            .map(|i| [i[0] as u32, i[1] as u32, i[2] as u32])
            .collect(),
        Some(Indices::U32(idx)) => idx.chunks_exact(3).map(|i| [i[0], i[1], i[2]]).collect(),
        // Un-indexed: every three vertices are one triangle.
        None => (0..vertices.len() as u32 / 3)
            .map(|t| [t * 3, t * 3 + 1, t * 3 + 2])
            .collect(),
    };
    if indices.is_empty() {
        return None;
    }
    Some((vertices, indices))
}

/// Bakes one Rapier shape out of a single mesh, with the failure reason.
///
/// `Err` carries the reason every attempt failed — used only for the
/// warn-once log in the resolver.
pub fn collider_from_mesh_with_reason(mesh: &Mesh, convex: bool) -> Result<Collider, String> {
    let (vertices, indices) = mesh_vertices_indices(mesh).ok_or_else(|| {
        "mesh sem vértices/índices utilizáveis (POSITION em falta ou topologia não-TriangleList)"
            .to_string()
    })?;
    if convex {
        Collider::convex_hull(&vertices)
            .ok_or_else(|| "convex hull falhou: sem volume convexo".to_string())
    } else {
        bake_trimesh_escalating(vertices, indices)
    }
}

/// Bakes one Rapier shape out of a single mesh.
pub fn collider_from_mesh(mesh: &Mesh, convex: bool) -> Option<Collider> {
    collider_from_mesh_with_reason(mesh, convex).ok()
}

/// Bakes an exact triangle mesh with progressively less demanding flags.
///
/// The ladder is deterministic — the first rung that yields a shape wins,
/// always in the same order:
///
/// 1. `FIX_INTERNAL_EDGES | DELETE_DEGENERATE_TRIANGLES` — the healthy path.
///    Implica merge de vértices duplicados e povoa pseudo-normais: sem elas
///    o parry devolve normal de contacto por triângulo e o character
///    controller engancha em cada aresta interna do mesh (props, paredes,
///    casas — e agora o terreno);
/// 2. `MERGE_DUPLICATE_VERTICES | DELETE_DEGENERATE_TRIANGLES` — merge sem
///    pseudo-normais, para geometria cuja estrutura rebenta o
///    `FIX_INTERNAL_EDGES`;
/// 3. default flags (no merge) — for meshes whose duplicate structure breaks
///    the merge preprocessing;
/// 4. convex hull of the vertices — an approximate collider still blocks the
///    player, which is strictly better than an empty bake (the prop becomes a
///    ghost).
///
/// `Err` lists what each rung reported, for the warn-once log only. Note: on
/// parry 3d 0.30.2 `TriMesh::with_flags` swallows topology errors internally
/// (`let _ = set_flags`) and only ever fails with `EmptyIndices` — so on this
/// parry the ladder normally resolves on rung 1 and the later rungs are
/// defense in depth against geometry edge cases and future parry upgrades.
pub fn bake_trimesh_escalating(
    vertices: Vec<Vec3>,
    indices: Vec<[u32; 3]>,
) -> Result<Collider, String> {
    let ladder = [
        (
            "FIX_INTERNAL_EDGES|DELETE_DEGENERATE_TRIANGLES",
            TriMeshFlags::FIX_INTERNAL_EDGES | TriMeshFlags::DELETE_DEGENERATE_TRIANGLES,
        ),
        (
            "MERGE_DUPLICATE_VERTICES|DELETE_DEGENERATE_TRIANGLES",
            TriMeshFlags::MERGE_DUPLICATE_VERTICES | TriMeshFlags::DELETE_DEGENERATE_TRIANGLES,
        ),
        ("default (sem merge)", TriMeshFlags::empty()),
    ];
    let mut reasons: Vec<String> = Vec::new();
    for (label, flags) in ladder {
        match Collider::trimesh_with_flags(vertices.clone(), indices.clone(), flags) {
            Ok(collider) => return Ok(collider),
            Err(err) => reasons.push(format!("trimesh flags {label} falhou: {err}")),
        }
    }
    // Último recurso: melhor um collider aproximado que nenhum.
    match Collider::convex_hull(&vertices) {
        Some(collider) => Ok(collider),
        None => {
            reasons.push("convex hull falhou: sem volume convexo".to_string());
            Err(reasons.join("; "))
        }
    }
}

fn collider_from_gltf(
    gltf: &bevy::gltf::Gltf,
    gltf_meshes: &Assets<bevy::gltf::GltfMesh>,
    meshes: &Assets<Mesh>,
    convex: bool,
) -> (Option<Collider>, Option<String>) {
    let mut parts: Vec<(Vec3, Quat, Collider)> = Vec::new();
    let mut failures: Vec<String> = Vec::new();
    for gltf_mesh_handle in &gltf.meshes {
        let Some(gltf_mesh) = gltf_meshes.get(gltf_mesh_handle) else {
            failures.push("GltfMesh derivado ainda não carregado".to_string());
            continue;
        };
        for primitive in &gltf_mesh.primitives {
            let Some(mesh) = meshes.get(&primitive.mesh) else {
                failures.push("Mesh derivado ainda não carregado".to_string());
                continue;
            };
            match collider_from_mesh_with_reason(mesh, convex) {
                Ok(collider) => parts.push((Vec3::ZERO, Quat::IDENTITY, collider)),
                Err(reason) => failures.push(reason),
            }
        }
    }
    let collider = match parts.len() {
        0 => None,
        1 => Some(parts.remove(0).2),
        _ => Some(Collider::compound(parts)),
    };
    // Motivo só para o log — agregado das primitivas que não bakearam.
    let reason = if failures.is_empty() {
        None
    } else {
        Some(failures.join("; "))
    };
    (collider, reason)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A position-only triangle list, the shape the glTF loader hands us for
    /// every `*_collision.glb`: no `NORMAL`, so Bevy un-indexes the primitive
    /// while computing flat normals.
    fn unindexed_quad() -> Mesh {
        use bevy::asset::RenderAssetUsages;
        use bevy::render::mesh::PrimitiveTopology;

        let positions: Vec<[f32; 3]> = vec![
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
        ];
        let mut mesh = Mesh::new(
            PrimitiveTopology::TriangleList,
            RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
        );
        mesh.insert_attribute(Mesh::ATTRIBUTE_POSITION, positions);
        mesh
    }

    #[test]
    fn test_mesh_without_indices_still_bakes() {
        // `Collider::from_bevy_mesh` returns `None` here — that is the bug
        // that left the city wall, the houses and every interior prop without
        // a collider while the box-collider benches still blocked the hero.
        let mesh = unindexed_quad();
        assert!(
            Collider::from_bevy_mesh(
                &mesh,
                &ComputedColliderShape::TriMesh(TriMeshFlags::MERGE_DUPLICATE_VERTICES)
            )
            .is_none(),
            "premise: rapier refuses an un-indexed mesh"
        );

        let (vertices, indices) = mesh_vertices_indices(&mesh).expect("positions are readable");
        assert_eq!(vertices.len(), 6);
        assert_eq!(indices, vec![[0, 1, 2], [3, 4, 5]]);
        assert!(collider_from_mesh(&mesh, false).is_some(), "trimesh bakes");
        assert!(collider_from_mesh(&mesh, true).is_some(), "hull bakes");
    }

    #[test]
    fn test_mesh_with_indices_is_unchanged() {
        use bevy::render::mesh::Indices;

        let mut mesh = unindexed_quad();
        mesh.insert_indices(Indices::U16(vec![0, 1, 2, 3, 4, 5]));
        let (_, indices) = mesh_vertices_indices(&mesh).expect("indexed mesh reads");
        assert_eq!(indices, vec![[0, 1, 2], [3, 4, 5]]);
        assert!(collider_from_mesh(&mesh, false).is_some());
    }

    #[test]
    fn test_mesh_without_positions_bakes_nothing() {
        use bevy::asset::RenderAssetUsages;
        use bevy::render::mesh::PrimitiveTopology;

        let mesh = Mesh::new(
            PrimitiveTopology::TriangleList,
            RenderAssetUsages::MAIN_WORLD | RenderAssetUsages::RENDER_WORLD,
        );
        assert!(mesh_vertices_indices(&mesh).is_none());
        assert!(collider_from_mesh(&mesh, false).is_none());
    }

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

        // A malformed `size` is reported and ignored — it used to fall back
        // silently to a 1×1×1 unit box, which hid the authoring bug.
        let (shape, warning) = parse_collider("shape: box; size: nonsense");
        assert_eq!(shape, ColliderShape::None);
        assert!(warning.is_some(), "malformed box size is reported");
        // Two values are likewise rejected (vectors are 1 or 3 values).
        let (shape, warning) = parse_collider("shape: box; size: 1 2");
        assert_eq!(shape, ColliderShape::None);
        assert!(warning.is_some(), "2-value box size is reported");
        // `size` absent keeps the unit-box default (documented shorthand).
        let (shape, _) = parse_collider("shape: box");
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

/// Columns within this many chunk edges of the player keep a collider.
///
/// The whole terrain cannot be collidable at once: `simple-rpg` is a 4000 m
/// world of 64 m columns, and a collider each is tens of megabytes of solver
/// data for ground the player cannot reach this frame. Colliders stream in
/// and out with the player instead (camera only as fallback for worlds
/// without a hero).
pub const PHYSICS_CHUNK_RADIUS: f32 = 3.0;

/// Marks a voxel COLUMN that currently owns a terrain trimesh collider.
///
/// O collider vive na entidade da coluna (um trimesh por coluna, triângulos
/// dos caixas transvoxel), não nas caixas — uma aresta interna a cada 64 m
/// em vez de a cada 32 m, e zero superfícies coincidentes na pilha vertical.
#[derive(Debug, Component)]
pub struct VoxelCollider;

/// Banda de colisão em metros para um spec.
///
/// `chunk_size × PHYSICS_CHUNK_RADIUS` limitado à banda LOD 0
/// (`lod_distance`): dentro dela o ladder impõe piso de LOD 0 às colunas,
/// por isso "collider == o que se vê" é exato em todo o lado onde o player
/// consegue tocar — nunca há collider assado de geometria mais grosseira do
/// que a desenhada.
pub fn collision_keep_within(spec: &crate::terrain::spec::TerrainSpec) -> f32 {
    (spec.chunk_size * PHYSICS_CHUNK_RADIUS).min(spec.lod_distance())
}

/// Distância XZ de um ponto ao AABB de uma coluna.
///
/// Às coordenadas da grelha e não ao `Transform` da coluna: a entidade senta-se
/// na origem do mundo e a pilha de caixas é alta — medir em 3D ao centro
/// deixava cair o collider debaixo dos pés do herói quando ele está no topo.
pub fn column_xz_distance(p: Vec3, coords: UVec2, half: f32, edge: f32) -> f32 {
    let x0 = -half + coords.x as f32 * edge;
    let z0 = -half + coords.y as f32 * edge;
    let cx = p.x.clamp(x0, x0 + edge);
    let cz = p.z.clamp(z0, z0 + edge);
    ((p.x - cx) * (p.x - cx) + (p.z - cz) * (p.z - cz)).sqrt()
}

/// Há chão de terreno carregado sob o herói?
///
/// Publicado por [`stream_voxel_colliders`] a cada frame. O player só recorre
/// ao chão analítico (`surface_below`) quando `ready == false` — arranque,
/// teleporte para coluna ainda por assar. Com chão carregado, o collider é a
/// autoridade e "não grounded" é airborne legítimo, não desculpa para snap.
#[derive(Debug, Resource, Default)]
pub struct TerrainCollisionStatus {
    pub ready: bool,
}

/// Triângulos acumulados das caixas de UMA coluna voxel, prontos para assar.
///
/// Os buffers já existem — são exatamente os que o transvoxel devolve para
/// render ([`crate::terrain::voxel::build_box_mesh`]) — acumulá-los não paga
/// memória extra além do bake em curso. Posições são origin-relative nos
/// três eixos (o contracto de [`crate::terrain::voxel::spawn_box_entity`]),
/// por isso cada caixa entra com o próprio `origin` como offset.
#[derive(Debug, Default)]
pub struct ColumnColliderBake {
    vertices: Vec<Vec3>,
    indices: Vec<[u32; 3]>,
}

impl ColumnColliderBake {
    pub fn new() -> Self {
        Self::default()
    }

    /// Caminho quente: os buffers que o mesher acabou de produzir.
    pub fn add_mesh_data(&mut self, origin: Vec3, data: &crate::terrain::mesh::ChunkMeshData) {
        let base = self.vertices.len() as u32;
        self.vertices
            .extend(data.positions.iter().map(|p| origin + Vec3::from(*p)));
        for tri in data.indices.chunks_exact(3) {
            self.indices
                .push([tri[0] + base, tri[1] + base, tri[2] + base]);
        }
    }

    /// Caminho de reparação: ler de volta um `Mesh` já residente no asset
    /// store (CPU-resident por construção, `to_bevy_mesh`).
    pub fn add_bevy_mesh(&mut self, origin: Vec3, mesh: &Mesh) {
        let Some((vertices, indices)) = mesh_vertices_indices(mesh) else {
            return;
        };
        let base = self.vertices.len() as u32;
        self.vertices
            .extend(vertices.into_iter().map(|p| origin + p));
        for tri in indices {
            self.indices
                .push([tri[0] + base, tri[1] + base, tri[2] + base]);
        }
    }

    pub fn is_empty(&self) -> bool {
        self.indices.is_empty()
    }

    pub fn triangle_count(&self) -> usize {
        self.indices.len()
    }

    /// Assa o trimesh da coluna.
    ///
    /// `FIX_INTERNAL_EDGES` (que implica merge de vértices duplicados) povoa
    /// pseudo-normais: sem elas o parry devolve normal de contacto por
    /// triângulo e o character controller engancha em cada aresta interna —
    /// o clássico ghost collision de trimesh. Sem fallback convex-hull: um
    /// hull de uma coluna de 64 m seria um bloco mentiroso; bake falhado é
    /// warn + coluna sem collider, e a rede de segurança do player cobre.
    pub fn bake(&self) -> Option<Collider> {
        if self.is_empty() {
            return None;
        }
        match Collider::trimesh_with_flags(
            self.vertices.clone(),
            self.indices.clone(),
            TriMeshFlags::FIX_INTERNAL_EDGES | TriMeshFlags::DELETE_DEGENERATE_TRIANGLES,
        ) {
            Ok(collider) => Some(collider),
            Err(err) => {
                bevy::log::warn!(
                    "terrain collider: bake do trimesh da coluna falhou ({err}) — sem collider"
                );
                None
            }
        }
    }
}

/// Adds and removes the terrain collider on voxel COLUMNS around the player.
///
/// No caminho 100% volumétrico TODA a superfície é coluna voxel com trimesh —
/// este streaming é a única fonte de colisão de terreno em runtime. O bake em
/// si acontece no swap do LOD (`terrain::plugin`) e no spawn da coluna
/// (`terrain::voxel::spawn_column`); aqui vive a manutenção: tirar o collider
/// quando a coluna sai da banda e reparar colunas construídas sem collider
/// (player chegou depois — teleporte/fast-travel), lendo de volta os meshes
/// já residentes.
#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
pub fn stream_voxel_colliders(
    mut commands: Commands,
    mut status: ResMut<TerrainCollisionStatus>,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    cameras: Query<&GlobalTransform, (With<Camera3d>, Without<crate::player::Player>)>,
    columns: Query<(
        Entity,
        &crate::terrain::plugin::TerrainChunk,
        Option<&VoxelCollider>,
        Option<&Children>,
    )>,
    boxes: Query<(&crate::terrain::voxel::VoxelChunk, &Mesh3d)>,
    meshes: Res<Assets<Mesh>>,
    mut repairs: Local<u32>,
) {
    status.ready = false;
    let Some(runtime) = runtime else { return };
    let Some(anchor) = players.iter().next().or_else(|| cameras.iter().next()) else {
        return;
    };
    if runtime.spec.collision_resolution == 0 {
        return;
    }
    let cam = anchor.translation();
    let keep_within = collision_keep_within(&runtime.spec);
    let drop_beyond = keep_within * 1.25;
    let half = runtime.spec.world_size * 0.5;
    let edge = crate::terrain::plugin::chunk_edge(&runtime.spec);
    *repairs = 0;

    for (entity, column, has_collider, children) in &columns {
        let distance = column_xz_distance(cam, column.coords, half, edge);
        match (has_collider.is_some(), distance) {
            (true, d) if d <= keep_within => status.ready = true,
            // try_* e não insert/remove: as colunas MORREM sob os pés deste
            // sistema (o swap do LOD e o cull despawnam no mesmo frame) —
            // aplicar num despawnado seria panic de engine.
            (true, d) if d > drop_beyond => {
                commands
                    .entity(entity)
                    .try_remove::<Collider>()
                    .try_remove::<RigidBody>()
                    .try_remove::<VoxelCollider>();
            }
            // Coluna em banda sem collider: assar dos meshes já residentes
            // (CPU-resident, zero cópia extra). 1 coluna por frame chega —
            // o caso é teleporte, não streaming contínuo.
            (false, d) if d <= keep_within && *repairs < 1 => {
                let Some(children) = children else { continue };
                let mut bake = ColumnColliderBake::new();
                for kid in children.iter() {
                    if let Ok((box_chunk, mesh3d)) = boxes.get(kid)
                        && let Some(mesh) = meshes.get(&mesh3d.0)
                    {
                        bake.add_bevy_mesh(box_chunk.origin, mesh);
                    }
                }
                if let Some(collider) = bake.bake() {
                    commands
                        .entity(entity)
                        .try_insert((collider, RigidBody::Fixed, VoxelCollider));
                    status.ready = true;
                    *repairs += 1;
                }
            }
            _ => {}
        }
    }
}

#[cfg(test)]
mod terrain_collider_tests {
    use super::*;
    use crate::terrain::mesh::ChunkMeshData;

    /// Dois triângulos formando um quadrado unitário a y=5, origin-relative.
    fn quad_mesh_data(y: f32) -> ChunkMeshData {
        ChunkMeshData {
            positions: vec![[0.0, y, 0.0], [1.0, y, 0.0], [1.0, y, 1.0], [0.0, y, 1.0]],
            normals: vec![[0.0, 1.0, 0.0]; 4],
            uvs: vec![[0.0, 0.0]; 4],
            colors: Vec::new(),
            indices: vec![0, 1, 2, 0, 2, 3],
        }
    }

    #[test]
    fn test_the_bake_offsets_boxes_into_world_space() {
        let mut bake = ColumnColliderBake::new();
        bake.add_mesh_data(Vec3::new(10.0, 0.0, 20.0), &quad_mesh_data(5.0));
        bake.add_mesh_data(Vec3::new(-10.0, 8.0, -20.0), &quad_mesh_data(5.0));
        assert_eq!(bake.triangle_count(), 4, "2 caixas × 2 triângulos");
        // Vértices da 2.ª caixa: base = 4, então o índice global 4 é o canto
        // mínimo dela (origin + [0,5,0]).
        assert_eq!(bake.indices[2], [4, 5, 6]);
        assert_eq!(
            bake.vertices[4],
            Vec3::new(-10.0, 13.0, -20.0),
            "origin da caixa soma às posições origin-relative"
        );
    }

    #[test]
    fn test_an_empty_bake_builds_nothing() {
        let bake = ColumnColliderBake::new();
        assert!(bake.is_empty());
        assert!(
            bake.bake().is_none(),
            "coluna sem geometria não tem collider"
        );
    }

    #[test]
    fn test_the_baked_collider_is_a_trimesh_with_pseudo_normals() {
        // FIX_INTERNAL_EDGES povoa pseudo-normais — é o que impede o
        // character controller de enganchare nas arestas internas entre
        // caixas. Se um parry futuro as retirar deste rung, o teste avisa.
        let mut bake = ColumnColliderBake::new();
        bake.add_mesh_data(Vec3::ZERO, &quad_mesh_data(5.0));
        let collider = bake.bake().expect("quad bakes");
        match collider.raw.as_typed_shape() {
            bevy_rapier3d::parry::shape::TypedShape::TriMesh(trimesh) => {
                assert!(
                    trimesh.pseudo_normals().is_some(),
                    "FIX_INTERNAL_EDGES tem de produzir pseudo-normais"
                );
            }
            other => panic!("esperado trimesh, veio {other:?}"),
        }
    }

    #[test]
    fn test_column_distance_is_xz_to_the_aabb() {
        let (half, edge) = (128.0_f32, 64.0_f32);
        let coords = UVec2::new(2, 2); // XZ [0, 64]²
        // Dentro do quadrado: 0.
        assert_eq!(
            column_xz_distance(Vec3::new(10.0, 500.0, 30.0), coords, half, edge),
            0.0
        );
        // Fora: distância euclidiana ao rectângulo, Y ignorado.
        let d = column_xz_distance(Vec3::new(80.0, 0.0, 30.0), coords, half, edge);
        assert!((d - 16.0).abs() < 1e-4, "d={d}");
    }
}

/// Colisores exatos de primitivas: `collider_for_shape` (partes de
/// composition e `collider="auto"` em primitivas) e as novas formas
/// imediatas do `parse_collider`.
#[cfg(test)]
mod shape_collider_tests {
    use super::*;
    use crate::recipes::Shape;

    #[test]
    fn test_collider_for_cuboid_scales_each_axis() {
        let shape = Shape::Cuboid {
            half_size: [1.0, 2.0, 3.0],
        };
        let collider = collider_for_shape(&shape, Vec3::new(2.0, 1.0, 0.5));
        let cuboid = collider.as_cuboid().unwrap();
        // half extents: [1,2,3] × scale → [2,2,1.5]
        assert_eq!(cuboid.half_extents().x, 2.0);
        assert_eq!(cuboid.half_extents().y, 2.0);
        assert_eq!(cuboid.half_extents().z, 1.5);
    }

    #[test]
    fn test_collider_for_sphere_uses_max_axis_on_nonuniform_scale() {
        let collider = collider_for_shape(&Shape::Sphere { radius: 0.5 }, Vec3::new(1.0, 3.0, 1.0));
        assert_eq!(
            collider.as_ball().unwrap().radius(),
            1.5,
            "elipsoide → ball do maior eixo"
        );
    }

    #[test]
    fn test_collider_for_cylinder_and_capsule() {
        let cyl = collider_for_shape(
            &Shape::Cylinder {
                half_height: 1.0,
                radius: 0.5,
            },
            Vec3::new(2.0, 2.0, 1.0),
        );
        assert_eq!(cyl.as_cylinder().unwrap().half_height(), 2.0);
        assert_eq!(cyl.as_cylinder().unwrap().radius(), 1.0);

        let cap = collider_for_shape(
            &Shape::Capsule {
                half_height: 0.5,
                radius: 0.25,
            },
            Vec3::ONE,
        );
        assert_eq!(cap.as_capsule().unwrap().half_height(), 0.5);
        assert_eq!(cap.as_capsule().unwrap().radius(), 0.25);
    }

    #[test]
    fn test_collider_for_plane_is_thin_slab() {
        let collider = collider_for_shape(
            &Shape::Plane {
                half_size: [4.0, 2.0],
            },
            Vec3::ONE,
        );
        let cuboid = collider.as_cuboid().unwrap();
        assert_eq!(cuboid.half_extents().x, 4.0);
        assert_eq!(
            cuboid.half_extents().y,
            0.02,
            "lâmina fina (convenção Pad/Plane)"
        );
        assert_eq!(cuboid.half_extents().z, 2.0);
    }

    #[test]
    fn test_parse_collider_sphere_cylinder_capsule() {
        let (shape, warning) = parse_collider("shape: sphere; radius: 0.8");
        assert_eq!(warning, None);
        assert_eq!(shape, ColliderShape::Sphere { radius: 0.8 });

        let (shape, warning) = parse_collider("shape: cylinder; radius: 1; half-height: 2");
        assert_eq!(warning, None);
        assert_eq!(
            shape,
            ColliderShape::Cylinder {
                radius: 1.0,
                half_height: 2.0
            }
        );

        let (shape, warning) = parse_collider("shape: capsule");
        assert_eq!(warning, None);
        assert_eq!(
            shape,
            ColliderShape::Capsule {
                radius: 0.5,
                half_height: 0.5
            }
        );

        // Todas são imediatas (nada pendente).
        for shape in [
            ColliderShape::Sphere { radius: 1.0 },
            ColliderShape::Cylinder {
                radius: 1.0,
                half_height: 1.0,
            },
            ColliderShape::Capsule {
                radius: 1.0,
                half_height: 1.0,
            },
        ] {
            assert!(immediate_collider(&shape).is_some());
        }
    }
}
