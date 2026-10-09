//! Snapshot "Mundo" do profiler — o equivalente do `world-debug.ts` do
//! VibeGame: pose do player, câmara e entidades num raio, cada uma com
//! rótulo resolvido (nome → script → forma de colisor → eid) e tags de
//! componentes. O texto da janela fica compacto; o JSON (bridge/export)
//! leva o detalhe rico por entidade.

use std::collections::BTreeMap;

use serde::Serialize;

use bevy::prelude::*;
use bevy_rapier3d::prelude::{Collider, RigidBody, Sensor, Sleeping};

use crate::ai::EnemyCreature;
use crate::combat::Corpse;
use crate::luau::{LuaScriptRef, ScriptActivation};
use crate::particles::ParticleEmitter;
use crate::player::Player;
use crate::terrain::plugin::TerrainChunk;
use crate::vitals::Health;

/// Raio por omissão das entidades próximas (o `DEFAULT_NEARBY_RADIUS` do
/// VibeGame); PageUp/PageDown ajusta.
pub const DEFAULT_NEARBY_RADIUS: f32 = 30.0;
/// Teto de entidades listadas (as mais próximas primeiro).
pub const DEFAULT_NEARBY_LIMIT: usize = 24;

/// Vetor serializável — o `WorldDebugVec3` do VibeGame.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct Vec3Snap {
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

impl From<Vec3> for Vec3Snap {
    fn from(v: Vec3) -> Self {
        Self {
            x: v.x,
            y: v.y,
            z: v.z,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct PlayerInfo {
    pub entity: u64,
    pub name: String,
    pub pos: Vec3Snap,
    /// Yaw em graus (o `eulerYDeg` do VibeGame).
    pub yaw_deg: f32,
    pub grounded: bool,
    pub speed: f32,
    pub vel: Vec3Snap,
}

#[derive(Debug, Clone, Serialize)]
pub struct CameraInfo {
    pub entity: u64,
    pub name: String,
    pub pos: Vec3Snap,
}

/// Uma entidade próxima: linha compacta da janela + tags/detalhe para JSON.
#[derive(Debug, Clone, Serialize)]
pub struct NearbyEntity {
    pub entity: u64,
    /// `Entity` bruto para o segundo passe de tags (não serializado — o
    /// índice `entity` acima é a vista JSON).
    #[serde(skip)]
    pub handle: Entity,
    pub name: String,
    /// De onde veio o rótulo — o `labelSource` do VibeGame.
    pub label_source: &'static str,
    pub pos: Vec3Snap,
    pub dist: f32,
    pub tags: Vec<&'static str>,
    #[serde(skip_serializing)]
    pub detail_line: String,
}

/// Snapshot do tab Mundo.
#[derive(Debug, Clone, Serialize)]
pub struct WorldSnapshot {
    pub frame: u64,
    pub nearby_radius: f32,
    /// Entidades no raio antes do corte do limite.
    pub nearby_in_radius: usize,
    pub origin: Vec3Snap,
    pub player: Option<PlayerInfo>,
    pub camera: Option<CameraInfo>,
    pub nearby: Vec<NearbyEntity>,
    pub entity_count: usize,
}

/// Nome do corpo de um `RigidBody` — os mesmos rótulos do VibeGame.
fn body_type_name(body: &RigidBody) -> &'static str {
    match body {
        RigidBody::Fixed => "fixed",
        RigidBody::Dynamic => "dynamic",
        RigidBody::KinematicPositionBased => "kinematic-pos",
        RigidBody::KinematicVelocityBased => "kinematic-vel",
    }
}

/// Forma resumida de um colisor (via `ShapeType` do parry).
fn collider_shape_name(collider: &Collider) -> String {
    format!("{:?}", collider.raw.shape_type())
}

/// Rótulo de uma entidade: `Name` → script → colisor → rigidbody → `#eid`
/// (a mesma cascata do `resolveEntityLabel` do VibeGame).
fn resolve_label(
    name: Option<&Name>,
    script: Option<&LuaScriptRef>,
    collider: Option<&Collider>,
    has_rigidbody: bool,
    entity: Entity,
) -> (String, &'static str) {
    if let Some(name) = name {
        let trimmed = name.as_str().trim();
        if !trimmed.is_empty() {
            return (trimmed.to_string(), "name");
        }
    }
    if let Some(script) = script {
        let stem = script.path.rsplit('/').next().unwrap_or(&script.path);
        if !stem.is_empty() {
            return (stem.to_string(), "script");
        }
    }
    if let Some(collider) = collider {
        return (collider_shape_name(collider), "collider");
    }
    if has_rigidbody {
        return ("rigidbody".to_string(), "tag");
    }
    (format!("#{}", entity.index()), "eid")
}

/// Recolhe o snapshot do tab Mundo. `world` exclusivo para poder iterar
/// várias queries num passe — corre ao ritmo de refrescamento da janela
/// (5 Hz), não por frame.
pub fn snapshot(world: &mut World, nearby_radius: f32, frame: u64) -> WorldSnapshot {
    let (player_entity, player_info, origin) = {
        let mut q = world.query::<(Entity, &GlobalTransform, &Player, Option<&Name>)>();
        match q.iter(world).next() {
            Some((entity, transform, player, name)) => {
                let pos = transform.translation();
                let (yaw, _, _) = transform.rotation().to_euler(EulerRot::YXZ);
                let info = PlayerInfo {
                    entity: entity.to_bits(),
                    name: name
                        .map(|n| n.as_str().to_string())
                        .unwrap_or_else(|| "player".into()),
                    pos: pos.into(),
                    yaw_deg: yaw.to_degrees(),
                    grounded: player.grounded,
                    speed: (player.vel_x * player.vel_x + player.vel_z * player.vel_z).sqrt(),
                    vel: Vec3Snap {
                        x: player.vel_x,
                        y: player.vel_y,
                        z: player.vel_z,
                    },
                };
                (Some(entity), Some(info), pos)
            }
            None => (None, None, Vec3::ZERO),
        }
    };

    let camera_entity = {
        let mut q = world.query_filtered::<Entity, With<Camera>>();
        q.iter(world).next()
    };
    let camera_info = camera_entity.and_then(|entity| {
        world.get::<GlobalTransform>(entity).map(|t| CameraInfo {
            entity: entity.to_bits(),
            name: world
                .get::<Name>(entity)
                .map(|n| n.as_str().to_string())
                .unwrap_or_else(|| "camera".into()),
            pos: t.translation().into(),
        })
    });

    // ---- entidades próximas ------------------------------------------------
    let radius_sq = nearby_radius * nearby_radius;
    let mut nearby: Vec<NearbyEntity> = Vec::new();
    let mut entity_count = 0usize;

    let mut q = world.query::<(
        Entity,
        Option<&GlobalTransform>,
        Option<&Name>,
        Option<&LuaScriptRef>,
        Option<&ScriptActivation>,
        Option<&Health>,
        Option<&EnemyCreature>,
        Option<&Corpse>,
        Option<&RigidBody>,
        Option<&Collider>,
        Option<&ParticleEmitter>,
        Option<&TerrainChunk>,
    )>();
    for row in q.iter(world) {
        let (
            entity,
            transform,
            name,
            script,
            activation,
            health,
            enemy,
            corpse,
            rigidbody,
            collider,
            emitter,
            chunk,
        ) = row;
        // Entidades internas da engine (schedules/sistemas no Bevy 0.19 vivem
        // como entidades e têm `Name`) não interessam: exigem uma âncora de
        // jogo — transform ou componente de gameplay. O `Name` sozinho é só
        // fonte de rótulo.
        let anchored = transform.is_some()
            || script.is_some()
            || collider.is_some()
            || rigidbody.is_some()
            || health.is_some()
            || enemy.is_some()
            || corpse.is_some()
            || emitter.is_some()
            || chunk.is_some();
        if anchored {
            entity_count += 1;
        }
        if Some(entity) == player_entity || Some(entity) == camera_entity {
            continue;
        }
        let pos = transform.map(|t| t.translation()).unwrap_or(Vec3::ZERO);
        let dist_sq = pos.distance_squared(origin);
        if dist_sq > radius_sq {
            continue;
        }
        if !anchored {
            continue;
        }

        let mut tags: Vec<&'static str> = Vec::new();
        if script.is_some() {
            tags.push("script");
        }
        if activation.is_some() {
            tags.push("activation");
        }
        if health.is_some() {
            tags.push("health");
        }
        if enemy.is_some() {
            tags.push("enemy");
        }
        if corpse.is_some() {
            tags.push("corpse");
        }
        if rigidbody.is_some() {
            tags.push("rigidbody");
        }
        if collider.is_some() {
            tags.push("collider");
        }
        if emitter.is_some() {
            tags.push("particles");
        }
        if chunk.is_some() {
            tags.push("chunk");
        }

        let (label, label_source) =
            resolve_label(name, script, collider, rigidbody.is_some(), entity);
        let mut detail = String::new();
        if let Some(script) = script {
            detail.push_str(&format!("script={}", script.path));
            if let Some(activation) = activation {
                detail.push_str(&format!(" r={:.0}m", activation.radius));
            }
        }
        if let Some(health) = health {
            if !detail.is_empty() {
                detail.push(' ');
            }
            detail.push_str(&format!("hp={:.0}/{:.0}", health.current, health.max));
        }
        if let Some(enemy) = enemy {
            if !detail.is_empty() {
                detail.push(' ');
            }
            detail.push_str(&format!("ia={:?}", enemy.state));
        }
        if let Some(body) = rigidbody {
            if !detail.is_empty() {
                detail.push(' ');
            }
            detail.push_str(body_type_name(body));
        }

        nearby.push(NearbyEntity {
            entity: entity.to_bits(),
            handle: entity,
            name: label,
            label_source,
            pos: pos.into(),
            dist: dist_sq.sqrt(),
            tags,
            detail_line: detail,
        });
    }
    nearby.sort_by(|a, b| a.dist.total_cmp(&b.dist));
    let nearby_in_radius = nearby.len();
    nearby.truncate(DEFAULT_NEARBY_LIMIT);

    // Segundo passe (só na lista final): sensor/dormindo — não participam do
    // filtro "interessante", só refinam tags.
    for entry in &mut nearby {
        if let Some(sleeping) = world.get::<Sleeping>(entry.handle)
            && sleeping.sleeping
        {
            entry.tags.push("sleeping");
        }
        if world.get::<Sensor>(entry.handle).is_some() {
            entry.tags.push("sensor");
        }
    }

    WorldSnapshot {
        frame,
        nearby_radius,
        nearby_in_radius,
        origin: origin.into(),
        player: player_info,
        camera: camera_info,
        nearby,
        entity_count,
    }
}

/// Linhas de texto da janela para o tab Mundo (compacto; JSON tem o resto).
pub fn window_lines(snap: &WorldSnapshot) -> Vec<String> {
    let mut lines = Vec::new();
    let fmt_pos = |p: &Vec3Snap| format!("{:.1} {:.1} {:.1}", p.x, p.y, p.z);
    match &snap.player {
        Some(p) => {
            lines.push(format!(
                "player {}  {}  yaw {:.0}°",
                p.name,
                fmt_pos(&p.pos),
                p.yaw_deg
            ));
            lines.push(format!(
                "  vel {:.1} {:.1}  speed {:.1}  chão {}",
                p.vel.x,
                p.vel.z,
                p.speed,
                if p.grounded { "sim" } else { "não" }
            ));
        }
        None => lines.push("player (nenhum)".into()),
    }
    match &snap.camera {
        Some(c) => lines.push(format!("câmera {}  {}", c.name, fmt_pos(&c.pos))),
        None => lines.push("câmera (nenhuma)".into()),
    }
    lines.push(format!(
        "próximas {}/{} ≤{:.0}m  (entidades {})",
        snap.nearby.len(),
        snap.nearby_in_radius,
        snap.nearby_radius,
        snap.entity_count
    ));
    for n in &snap.nearby {
        lines.push(format!(
            "  {:>5.1}m {} {}{}",
            n.dist,
            n.name,
            fmt_pos(&n.pos),
            if n.tags.is_empty() {
                String::new()
            } else {
                format!(" [{}]", n.tags.join(","))
            }
        ));
    }
    lines
}

/// Payload JSON do tab (com `detail` por entidade).
pub fn json(snap: &WorldSnapshot) -> serde_json::Value {
    serde_json::to_value(snap).unwrap_or(serde_json::Value::Null)
}

/// Contagens por tag para o export agregado (o resumo "quem é o quê").
pub fn tag_counts(snap: &WorldSnapshot) -> BTreeMap<String, usize> {
    let mut counts = BTreeMap::new();
    for n in &snap.nearby {
        for tag in &n.tags {
            *counts.entry((*tag).to_string()).or_default() += 1;
        }
    }
    counts
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ai::EnemyState;
    use bevy::transform::components::Transform;

    fn player() -> Player {
        Player {
            vel_x: 1.0,
            ..Default::default()
        }
    }

    #[test]
    fn test_resolve_label_cascade() {
        let entity = Entity::from_raw_u32(7).unwrap();
        let (name, source) = resolve_label(
            Some(&Name::new("Portão")),
            Some(&LuaScriptRef {
                path: "doors/gate.lua".into(),
            }),
            None,
            false,
            entity,
        );
        assert_eq!(name, "Portão");
        assert_eq!(source, "name");

        let (name, source) = resolve_label(
            None,
            Some(&LuaScriptRef {
                path: "doors/gate.lua".into(),
            }),
            None,
            false,
            entity,
        );
        assert_eq!(name, "gate.lua");
        assert_eq!(source, "script");

        let (name, source) = resolve_label(None, None, None, false, entity);
        assert_eq!(name, "#7");
        assert_eq!(source, "eid");
    }

    #[test]
    fn test_body_type_names() {
        assert_eq!(body_type_name(&RigidBody::Fixed), "fixed");
        assert_eq!(body_type_name(&RigidBody::Dynamic), "dynamic");
        assert_eq!(
            body_type_name(&RigidBody::KinematicPositionBased),
            "kinematic-pos"
        );
    }

    #[test]
    fn test_window_lines_without_player() {
        let snap = WorldSnapshot {
            frame: 0,
            nearby_radius: 30.0,
            nearby_in_radius: 0,
            origin: Vec3Snap {
                x: 0.0,
                y: 0.0,
                z: 0.0,
            },
            player: None,
            camera: None,
            nearby: vec![],
            entity_count: 12,
        };
        let lines = window_lines(&snap);
        assert!(
            lines.iter().any(|l| l.contains("player (nenhum)")),
            "{lines:?}"
        );
        assert!(
            lines.iter().any(|l| l.contains("entidades 12")),
            "{lines:?}"
        );
    }

    #[test]
    fn test_snapshot_from_world_counts_entities() {
        let mut world = World::new();
        world.init_resource::<Time>();
        world.spawn((
            Transform::from_translation(Vec3::ZERO),
            GlobalTransform::default(),
            player(),
        ));
        // Fora do raio: não entra na lista (mas conta em entity_count).
        // GlobalTransform explícito: num World cru não há propagação.
        world.spawn((
            Transform::from_translation(Vec3::new(1000.0, 0.0, 0.0)),
            GlobalTransform::from_translation(Vec3::new(1000.0, 0.0, 0.0)),
            Name::new("longe"),
            LuaScriptRef {
                path: "x.lua".into(),
            },
        ));
        // Perto e com script: entra.
        world.spawn((
            Transform::from_translation(Vec3::new(5.0, 0.0, 0.0)),
            GlobalTransform::from_translation(Vec3::new(5.0, 0.0, 0.0)),
            LuaScriptRef {
                path: "slime.lua".into(),
            },
            ScriptActivation::default(),
            Health::default(),
            EnemyCreature {
                state: EnemyState::Chase,
                ..Default::default()
            },
        ));

        let snap = snapshot(&mut world, DEFAULT_NEARBY_RADIUS, 120);
        assert_eq!(snap.entity_count, 3);
        assert_eq!(snap.frame, 120);
        assert!(snap.player.is_some());
        assert!((snap.player.as_ref().unwrap().speed - 1.0).abs() < 1e-4);
        assert_eq!(snap.nearby.len(), 1, "{snap:?}");
        let near = &snap.nearby[0];
        assert_eq!(near.name, "slime.lua");
        assert_eq!(near.label_source, "script");
        assert!(near.tags.contains(&"script"));
        assert!(near.tags.contains(&"health"));
        assert!(near.tags.contains(&"enemy"));
        assert!(
            near.detail_line.contains("slime.lua"),
            "{}",
            near.detail_line
        );
        assert!(near.detail_line.contains("Chase"), "{}", near.detail_line);

        let counts = tag_counts(&snap);
        assert_eq!(counts.get("enemy"), Some(&1));
        assert_eq!(counts.get("health"), Some(&1));

        let json = json(&snap);
        assert!(json["player"].is_object(), "{json}");
        assert_eq!(json["nearby"][0]["name"], "slime.lua");
    }

    #[test]
    fn test_nearby_radius_respected() {
        let mut world = World::new();
        world.init_resource::<Time>();
        world.spawn((Transform::IDENTITY, GlobalTransform::default(), player()));
        world.spawn((
            Transform::from_translation(Vec3::new(3.0, 0.0, 0.0)),
            GlobalTransform::from_translation(Vec3::new(3.0, 0.0, 0.0)),
            LuaScriptRef {
                path: "perto.lua".into(),
            },
        ));
        world.spawn((
            Transform::from_translation(Vec3::new(40.0, 0.0, 0.0)),
            GlobalTransform::from_translation(Vec3::new(40.0, 0.0, 0.0)),
            LuaScriptRef {
                path: "meio.lua".into(),
            },
        ));
        world.spawn((
            Transform::from_translation(Vec3::new(400.0, 0.0, 0.0)),
            GlobalTransform::from_translation(Vec3::new(400.0, 0.0, 0.0)),
            LuaScriptRef {
                path: "longe.lua".into(),
            },
        ));

        let snap = snapshot(&mut world, 10.0, 0);
        assert_eq!(snap.nearby_in_radius, 1);
        assert_eq!(snap.nearby[0].name, "perto.lua");

        let snap = snapshot(&mut world, 50.0, 0);
        assert_eq!(snap.nearby_in_radius, 2);
    }
}
