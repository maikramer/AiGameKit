//! Picking de NÓS da cena: um raio → o nó XML mais específico que o utilizador está a ver.
//!
//! O raio testa-se contra o AABB de cada malha (o que se VÊ), e o hit mais próximo sobe pela
//! hierarquia até ao primeiro ancestral com [`SceneSource`] — a entidade declarada mais
//! específica (uma `<GltfScene>` dentro de um grupo seleciona a instância, não a vila
//! inteira). O raycast de física serve de OCLUSOR: uma malha atrás de um monte não é apanhada
//! através do terreno. Usado pelo ViberEditor e por `viber.scene.pick` (agentes).

use bevy::camera::primitives::Aabb;
use bevy::prelude::*;

use super::SceneSource;
use super::doc::{NodeRef, SceneDoc};
use crate::bridge::selection::{ray_aabb, world_aabb_of};

/// Um nó apanhado pelo raio.
#[derive(Debug, Clone, PartialEq)]
pub struct Picked {
    pub node: NodeRef,
    /// A entidade declarada do nó (a que tem o `SceneSource`).
    pub entity: Entity,
    /// Ponto mundo do hit (no AABB da malha).
    pub point: Vec3,
    pub distance: f32,
}

/// Folga (m) para uma malha contar como visível quando o oclusor bate logo à frente dela
/// (o AABB é maior do que a malha; o collider pode coincidir com a própria malha).
const OCCLUSION_SLACK: f32 = 1.0;

/// O nó sob o raio (`dir` normalizado), até `max_distance`.
pub fn pick(world: &mut World, origin: Vec3, dir: Vec3, max_distance: f32) -> Option<Picked> {
    let occluder = physics_hit(world, origin, dir, max_distance).unwrap_or(max_distance);
    let limit = (occluder + OCCLUSION_SLACK).min(max_distance);
    let mut meshes = world.query_filtered::<Entity, With<Aabb>>();
    let mesh_entities: Vec<Entity> = meshes.iter(world).collect();
    let mut best: Option<(Entity, f32)> = None;
    for mesh in mesh_entities {
        let Some(bounds) = world_aabb_of(mesh, world) else {
            continue;
        };
        let Some(distance) = ray_aabb(origin, dir, bounds) else {
            continue;
        };
        if distance > limit || best.is_some_and(|(_, d)| d <= distance) {
            continue;
        }
        // Só malhas que pertencem a um nó declarado (o terreno, a relva, o céu não são).
        if declared_ancestor(world, mesh).is_some() {
            best = Some((mesh, distance));
        }
    }
    let (mesh, distance) = best?;
    let entity = declared_ancestor(world, mesh)?;
    let source = world.get::<SceneSource>(entity)?.0.clone();
    let node = world.get_resource::<SceneDoc>()?.node_ref_for(&source)?;
    Some(Picked {
        node,
        entity,
        point: origin + dir * distance,
        distance,
    })
}

/// O ancestral mais próximo (incluindo a própria) com [`SceneSource`].
pub fn declared_ancestor(world: &World, entity: Entity) -> Option<Entity> {
    let mut current = entity;
    for _ in 0..256 {
        if world.get::<SceneSource>(current).is_some() {
            return Some(current);
        }
        current = world.get::<ChildOf>(current)?.parent();
    }
    None
}

/// Primeira superfície de física no raio (terreno incluído) — o oclusor.
fn physics_hit(world: &mut World, origin: Vec3, dir: Vec3, max_distance: f32) -> Option<f32> {
    use bevy_rapier3d::prelude::{
        QueryFilter, RapierContextColliders, RapierContextSimulation, RapierQueryPipeline,
        RapierRigidBodySet,
    };
    use bevy_rapier3d::rapier::parry::query::DefaultQueryDispatcher;
    let context = world
        .iter_entities()
        .find(|e| e.contains::<RapierContextSimulation>())
        .map(|e| e.id())?;
    let simulation = world.get::<RapierContextSimulation>(context)?;
    let colliders = world.get::<RapierContextColliders>(context)?;
    let bodies = world.get::<RapierRigidBodySet>(context)?;
    RapierQueryPipeline::new_scoped(
        &simulation.broad_phase,
        colliders,
        bodies,
        &QueryFilter::default(),
        &DefaultQueryDispatcher,
        |pipeline| pipeline.cast_ray(origin, dir, max_distance, true),
    )
    .map(|(_, distance)| distance)
}

#[cfg(test)]
mod tests {
    use super::*;
    use bevy::math::Vec3A;

    fn mesh(world: &mut World, parent: Entity, at: Vec3) -> Entity {
        world
            .spawn((
                Transform::from_translation(at),
                GlobalTransform::from_translation(at),
                Aabb {
                    center: Vec3A::ZERO,
                    half_extents: Vec3A::splat(1.0),
                },
                ChildOf(parent),
            ))
            .id()
    }

    #[test]
    fn picks_the_nearest_declared_node() {
        let tmp = tempfile::tempdir().unwrap();
        let doc = SceneDoc::load(&super::super::doc::tests::fixture(tmp.path())).unwrap();
        let mut world = World::new();
        let source = |doc: &SceneDoc, sel: &str| {
            SceneSource(std::sync::Arc::new(
                doc.origin_of(&doc.resolve(sel).unwrap()),
            ))
        };
        let props = world
            .spawn((
                Transform::default(),
                GlobalTransform::default(),
                source(&doc, "name=props"),
            ))
            .id();
        let crate_e = world
            .spawn((
                Transform::default(),
                GlobalTransform::default(),
                source(&doc, "#1/0"),
                ChildOf(props),
            ))
            .id();
        let barrel = world
            .spawn((
                Transform::default(),
                GlobalTransform::default(),
                source(&doc, "#1/1"),
                ChildOf(props),
            ))
            .id();
        mesh(&mut world, crate_e, Vec3::new(0.0, 0.0, -10.0));
        mesh(&mut world, barrel, Vec3::new(0.0, 0.0, -20.0));
        // Uma malha sem nó declarado (terreno) MAIS PERTO não é selecionável.
        let terrain = world
            .spawn((Transform::default(), GlobalTransform::default()))
            .id();
        mesh(&mut world, terrain, Vec3::new(0.0, 0.0, -5.0));
        world.insert_resource(doc);

        let picked = pick(&mut world, Vec3::ZERO, Vec3::NEG_Z, 100.0).expect("hit");
        assert_eq!(
            picked.entity, crate_e,
            "o caixote, não o grupo nem o terreno"
        );
        assert_eq!(picked.node, NodeRef::new(0, vec![1, 0]));
        assert!((picked.distance - 9.0).abs() < 1e-4);
        assert!(pick(&mut world, Vec3::new(50.0, 0.0, 0.0), Vec3::NEG_Z, 100.0).is_none());
        // Para lá do alcance pedido não conta.
        assert!(pick(&mut world, Vec3::ZERO, Vec3::NEG_Z, 5.0).is_none());
    }
}
