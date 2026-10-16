//! Snapshot "Física" do profiler — o equivalente do `physics-debug.ts`
//! original, adaptado ao bevy_rapier3d 0.36: corpos por tipo e sono, colisores
//! por forma, sensores, CCTs, pendentes de mesh, gravidade/timestep do
//! contexto Rapier e a duração do `PhysicsSet::StepSimulation` (medida pelas
//! âncoras em [`crate::profiler::timed::physics_anchors`]).
//!
//! As contagens ECS estão sempre disponíveis (mesmo sem contexto Rapier);
//! `available` distingue os dois casos, como no original.

use std::collections::BTreeMap;

use serde::Serialize;

use bevy::prelude::*;
use bevy_rapier3d::control::KinematicCharacterController;
use bevy_rapier3d::plugin::context::{
    RapierContextColliders, RapierContextSimulation, RapierRigidBodySet,
};
use bevy_rapier3d::prelude::{Collider, RapierConfiguration, RigidBody, Sensor, Sleeping};

use crate::physics::PendingCollider;

use super::timed;

#[derive(Debug, Clone, Default, Serialize)]
pub struct Bodies {
    pub total: usize,
    pub fixed: usize,
    pub dynamic: usize,
    pub kinematic: usize,
    pub sleeping: usize,
    pub awake: usize,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct Colliders {
    pub total: usize,
    pub sensors: usize,
    pub by_shape: BTreeMap<String, usize>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RapierWorld {
    pub bodies: usize,
    pub colliders: usize,
    pub impulse_joints: usize,
    /// `IntegrationParameters.dt` — passo do solver (s).
    pub timestep: f32,
}

/// Snapshot do tab Física.
#[derive(Debug, Clone, Serialize)]
pub struct PhysicsSnapshot {
    /// `false` quando não há contexto Rapier (mundo sem física).
    pub available: bool,
    pub bodies: Bodies,
    pub colliders: Colliders,
    /// Character controllers (Rapier `KinematicCharacterController`); o player
    /// Viber usa o seu próprio controlador, pelo que em prática é 0 — mantido
    /// para paridade com a referência.
    pub cct: usize,
    pub pending_colliders: usize,
    pub gravity: [f32; 3],
    pub rapier: Option<RapierWorld>,
    /// ms do último `PhysicsSet::StepSimulation` + média da janela.
    pub step: Option<StepTiming>,
}

#[derive(Debug, Clone, Copy, Serialize)]
pub struct StepTiming {
    pub last_ms: f32,
    pub avg_ms: f32,
    pub p95_ms: f32,
}

/// Recolhe contagens de física de um mundo real.
pub fn snapshot(world: &mut World, frame_avg_ms: f32) -> PhysicsSnapshot {
    let mut bodies = Bodies::default();
    let mut q_bodies = world.query::<(&RigidBody, Option<&Sleeping>)>();
    for (body, sleeping) in q_bodies.iter(world) {
        bodies.total += 1;
        match body {
            RigidBody::Fixed => bodies.fixed += 1,
            RigidBody::Dynamic => bodies.dynamic += 1,
            RigidBody::KinematicPositionBased | RigidBody::KinematicVelocityBased => {
                bodies.kinematic += 1
            }
        }
        // O componente traz o estado no campo (`Sleeping.sleeping`) — a
        // presença do componente só diz que o corpo é gerível pelo
        // island manager.
        match sleeping.map(|s| s.sleeping) {
            Some(true) => bodies.sleeping += 1,
            _ => bodies.awake += 1,
        }
    }

    let mut colliders = Colliders::default();
    let mut q_colliders = world.query::<&Collider>();
    for collider in q_colliders.iter(world) {
        colliders.total += 1;
        let shape = format!("{:?}", collider.raw.shape_type());
        *colliders.by_shape.entry(shape).or_default() += 1;
    }
    let mut q_sensors = world.query_filtered::<(), With<Sensor>>();
    colliders.sensors = q_sensors.iter(world).count();

    let mut q_cct = world.query_filtered::<(), With<KinematicCharacterController>>();
    let cct = q_cct.iter(world).count();

    let mut q_pending = world.query_filtered::<(), With<PendingCollider>>();
    let pending_colliders = q_pending.iter(world).count();

    let mut gravity = [0.0f32; 3];
    let mut q_conf = world.query::<&RapierConfiguration>();
    if let Some(conf) = q_conf.iter(world).next() {
        gravity = [conf.gravity.x, conf.gravity.y, conf.gravity.z];
    }

    // Contexto Rapier (single-simulação: a 1.ª entidade com o componente).
    let mut rapier = None;
    let mut q_sim = world.query::<(
        &RapierContextSimulation,
        &RapierContextColliders,
        &RapierRigidBodySet,
        &bevy_rapier3d::plugin::context::RapierContextJoints,
    )>();
    if let Some((sim, ctx_colliders, ctx_bodies, joints)) = q_sim.iter(world).next() {
        rapier = Some(RapierWorld {
            bodies: ctx_bodies.bodies.len(),
            colliders: ctx_colliders.colliders.len(),
            impulse_joints: joints.impulse_joints.len(),
            timestep: sim.integration_parameters.dt,
        });
    }

    let step = systems_snapshot_step(frame_avg_ms);

    PhysicsSnapshot {
        available: rapier.is_some(),
        bodies,
        colliders,
        cct,
        pending_colliders,
        gravity,
        rapier,
        step,
    }
}

/// Extrai o timing `physics.step` (âncoras) do anel de sistemas.
fn systems_snapshot_step(frame_avg_ms: f32) -> Option<StepTiming> {
    timed::systems_snapshot(frame_avg_ms)
        .into_iter()
        .find(|s| s.name == "physics.step")
        .map(|s| StepTiming {
            last_ms: s.last_ms,
            avg_ms: s.avg_ms,
            p95_ms: s.p95_ms,
        })
}

/// Linhas de texto da janela para o tab Física.
pub fn window_lines(snap: &PhysicsSnapshot) -> Vec<String> {
    let mut lines = Vec::new();
    if !snap.available {
        lines.push("física: (sem contexto Rapier)".into());
        return lines;
    }
    let b = &snap.bodies;
    lines.push(format!(
        "corpos {} (fixos {} · din {} · cin {})",
        b.total, b.fixed, b.dynamic, b.kinematic
    ));
    lines.push(format!(
        "  sono {} dormindo · {} acordados",
        b.sleeping, b.awake
    ));
    let shapes: Vec<String> = snap
        .colliders
        .by_shape
        .iter()
        .map(|(k, v)| format!("{k} {v}"))
        .collect();
    lines.push(format!(
        "colisores {}  sensores={}",
        snap.colliders.total, snap.colliders.sensors
    ));
    lines.push(format!("  {}", shapes.join("  ")));
    lines.push(format!(
        "cct {}  pendentes {}  gravidade {:.1} {:.1} {:.1}",
        snap.cct, snap.pending_colliders, snap.gravity[0], snap.gravity[1], snap.gravity[2]
    ));
    if let Some(r) = &snap.rapier {
        lines.push(format!(
            "rapier   corpos={}  colisores={}  juntas={}  dt={:.4}",
            r.bodies, r.colliders, r.impulse_joints, r.timestep
        ));
    }
    match snap.step {
        Some(s) => lines.push(format!(
            "step     {:.2} ms  média {:.2}  p95 {:.2}",
            s.last_ms, s.avg_ms, s.p95_ms
        )),
        None => lines.push("step     (sem amostras)".into()),
    }
    lines
}

/// Payload JSON do tab.
pub fn json(snap: &PhysicsSnapshot) -> serde_json::Value {
    serde_json::to_value(snap).unwrap_or(serde_json::Value::Null)
}

#[cfg(test)]
mod tests {
    use super::*;
    use bevy::transform::components::Transform;
    use bevy_rapier3d::geometry::Collider;

    #[test]
    fn test_snapshot_counts_components() {
        let mut world = World::new();
        world.init_resource::<Time>();
        // Sem contexto Rapier → available=false mas contagens ECS presentes.
        world.spawn((
            Transform::IDENTITY,
            RigidBody::Fixed,
            Collider::cuboid(1.0, 1.0, 1.0),
        ));
        world.spawn((
            Transform::IDENTITY,
            RigidBody::Dynamic,
            Collider::ball(0.5),
            Sleeping {
                sleeping: true,
                ..Default::default()
            },
        ));
        world.spawn((Transform::IDENTITY, Collider::ball(0.25), Sensor));

        let snap = snapshot(&mut world, 16.0);
        assert!(!snap.available);
        assert_eq!(snap.bodies.total, 2);
        assert_eq!(snap.bodies.fixed, 1);
        assert_eq!(snap.bodies.dynamic, 1);
        assert_eq!(snap.bodies.sleeping, 1);
        assert_eq!(snap.bodies.awake, 1);
        assert_eq!(snap.colliders.total, 3);
        assert_eq!(snap.colliders.sensors, 1);
        assert_eq!(snap.colliders.by_shape.get("Cuboid"), Some(&1), "{snap:?}");
        assert_eq!(snap.colliders.by_shape.get("Ball"), Some(&2));

        let lines = window_lines(&snap);
        assert!(lines[0].contains("sem contexto"), "{lines:?}");
        let json = json(&snap);
        assert_eq!(json["bodies"]["total"], 2, "{json}");
        assert_eq!(json["available"], false);
    }
}
