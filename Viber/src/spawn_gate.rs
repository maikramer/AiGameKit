//! `<SpawnGate target-entity="player" y-fallback="150" skin-distance="0.05">`
//! — segura uma entidade no ar até haver chão de colisão por baixo dela
//! (port do plugin `spawn-gate` do VibeGame).
//!
//! O terreno voxel assa os colliders em streaming à volta do herói: no
//! arranque (e numa entidade dinâmica autorada longe dele) o corpo existe
//! antes do chão. O portão congela a entidade na posição de espera (`y` =
//! `y-fallback`, ou o autorado), sem gravidade nem input, até a coluna que
//! contém o seu XZ ter [`crate::physics::VoxelCollider`]; aí assenta-a na
//! superfície sólida (+ `skin-distance`) e solta-a — uma só vez.
//!
//! Fora do campo do heightmap (bolsas de interior) ou num terreno sem
//! colisão (`collision-resolution="0"`) não há chão por esperar: a entidade
//! solta-se no primeiro frame, no Y autorado. [`GATE_TIMEOUT_S`] é a rede de
//! segurança de uma coluna que nunca chega a assar (longe do herói).

use bevy::prelude::*;
use bevy_rapier3d::prelude::{KinematicCharacterController, RigidBodyDisabled, Velocity};

use crate::player::Player;
use crate::worldsys::EngineConfigs;

/// Folga por omissão acima da superfície (m) — a do VibeGame.
pub const DEFAULT_SKIN: f32 = 0.05;
/// Tempo máximo de espera (s) antes de soltar sobre o chão analítico.
pub const GATE_TIMEOUT_S: f32 = 20.0;
/// Frames a tentar resolver o `target-entity` antes de desistir.
const RESOLVE_FRAMES: u32 = 600;

/// Um `<SpawnGate>` do mundo, por resolver.
#[derive(Debug, Clone, PartialEq)]
pub struct GateSpec {
    /// Nome da entidade (`player` também casa com o herói sem `name`).
    pub target: String,
    pub y_fallback: Option<f32>,
    pub skin: f32,
}

impl GateSpec {
    pub fn from_config(config: &crate::worldsys::EngineConfigData) -> Self {
        Self {
            target: config
                .attr("target-entity")
                .map(str::trim)
                .filter(|t| !t.is_empty())
                .unwrap_or("player")
                .to_string(),
            y_fallback: config.f32_attr("y-fallback"),
            skin: config
                .f32_attr("skin-distance")
                .map(|s| s.max(0.0))
                .unwrap_or(DEFAULT_SKIN),
        }
    }
}

/// Portões declarados ainda à procura da entidade-alvo.
#[derive(Debug, Default, Resource)]
pub struct PendingSpawnGates {
    pub list: Vec<GateSpec>,
    frames: u32,
}

/// A entidade está congelada à espera de chão.
#[derive(Debug, Clone, Component)]
pub struct SpawnGated {
    /// Posição de espera (XZ autorado, Y = `y-fallback` ou o autorado).
    pub hold: Vec3,
    /// Y autorado — o chão fora do campo do heightmap.
    pub authored_y: f32,
    pub skin: f32,
    pub waited: f32,
}

fn collect_gates(mut commands: Commands, configs: Option<Res<EngineConfigs>>) {
    let list: Vec<GateSpec> = configs
        .iter()
        .flat_map(|c| {
            c.all("spawngate")
                .map(GateSpec::from_config)
                .collect::<Vec<_>>()
        })
        .collect();
    if !list.is_empty() {
        commands.insert_resource(PendingSpawnGates { list, frames: 0 });
    }
}

/// Casa cada portão com a sua entidade (o herói e as cenas glTF podem nascer
/// frames depois do spawn do mundo) e congela-a.
#[allow(clippy::type_complexity)]
fn resolve_gates(
    mut commands: Commands,
    pending: Option<ResMut<PendingSpawnGates>>,
    named: Query<(Entity, &Name, &Transform), Without<SpawnGated>>,
    players: Query<(Entity, &Transform), (With<Player>, Without<SpawnGated>)>,
) {
    let Some(mut pending) = pending else {
        return;
    };
    pending.frames += 1;
    pending.list.retain(|gate| {
        let found = named
            .iter()
            .find(|(_, name, _)| name.as_str() == gate.target)
            .map(|(e, _, t)| (e, *t))
            .or_else(|| {
                (gate.target == "player")
                    .then(|| players.iter().next().map(|(e, t)| (e, *t)))
                    .flatten()
            });
        let Some((entity, transform)) = found else {
            return true;
        };
        let authored = transform.translation;
        let hold = Vec3::new(
            authored.x,
            gate.y_fallback.unwrap_or(authored.y),
            authored.z,
        );
        commands.entity(entity).insert((
            SpawnGated {
                hold,
                authored_y: authored.y,
                skin: gate.skin,
                waited: 0.0,
            },
            RigidBodyDisabled,
        ));
        info!(
            "spawn-gate: '{}' em espera a y={:.1} até haver chão de colisão",
            gate.target, hold.y
        );
        false
    });
    if pending.list.is_empty() {
        commands.remove_resource::<PendingSpawnGates>();
    } else if pending.frames >= RESOLVE_FRAMES {
        for gate in &pending.list {
            warn!(
                "spawn-gate: target-entity '{}' não existe no mundo — ignorado",
                gate.target
            );
        }
        commands.remove_resource::<PendingSpawnGates>();
    }
}

/// A coluna voxel que contém `p` já tem collider?
fn ground_ready_at(
    p: Vec3,
    runtime: &crate::terrain::runtime::TerrainRuntime,
    columns: &Query<&crate::terrain::plugin::TerrainChunk, With<crate::physics::VoxelCollider>>,
) -> bool {
    let half = runtime.spec.world_size * 0.5;
    let edge = crate::terrain::plugin::chunk_edge(&runtime.spec);
    columns
        .iter()
        .any(|column| crate::physics::column_xz_distance(p, column.coords, half, edge) <= 0.0)
}

/// Y de soltura: a superfície sólida sob o XZ (+ folga) dentro do campo; o
/// Y autorado fora dele (interiores) ou sem terreno.
pub fn release_y(
    terrain: Option<&crate::terrain::runtime::TerrainRuntime>,
    gate: &SpawnGated,
) -> f32 {
    match terrain {
        Some(t) if t.in_field(gate.hold.x, gate.hold.z) => {
            crate::player::landing_position(terrain, gate.hold).y + gate.skin
        }
        _ => gate.authored_y,
    }
}

/// Segura as entidades em espera e solta-as quando o chão chega. Corre
/// DEPOIS do `player_movement`: o que o input/gravidade quiserem fazer ao
/// herói neste frame é desfeito aqui.
#[allow(clippy::type_complexity)]
fn hold_gated(
    mut commands: Commands,
    time: Res<Time<Real>>,
    runtime: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    columns: Query<&crate::terrain::plugin::TerrainChunk, With<crate::physics::VoxelCollider>>,
    mut gated: Query<(
        Entity,
        &mut Transform,
        &mut SpawnGated,
        Option<&mut Player>,
        Option<&mut KinematicCharacterController>,
        Option<&mut Velocity>,
    )>,
) {
    for (entity, mut transform, mut gate, player, controller, velocity) in &mut gated {
        gate.waited += time.delta_secs();
        let is_player = player.is_some();
        let release = match runtime.as_deref() {
            None => false,
            Some(rt) => {
                !rt.in_field(gate.hold.x, gate.hold.z)
                    || rt.spec.collision_resolution == 0
                    || ground_ready_at(gate.hold, rt, &columns)
                    || gate.waited >= GATE_TIMEOUT_S
            }
        };
        if let Some(mut player) = player {
            player.vel_y = 0.0;
        }
        if let Some(mut controller) = controller {
            controller.translation = Some(Vec3::ZERO);
        }
        if let Some(mut velocity) = velocity {
            *velocity = Velocity::zero();
        }
        if !release {
            transform.translation = gate.hold;
            continue;
        }
        if gate.waited >= GATE_TIMEOUT_S {
            warn!(
                "spawn-gate: chão de colisão não chegou em {GATE_TIMEOUT_S:.0} s — solto sobre o terreno analítico"
            );
        }
        let y = release_y(runtime.as_deref(), &gate);
        transform.translation = Vec3::new(gate.hold.x, y, gate.hold.z);
        let mut entity = commands.entity(entity);
        entity.remove::<(SpawnGated, RigidBodyDisabled)>();
        // O herói ganha a tutela pós-teleporte: o chão analítico segura-o nos
        // frames em que o collider acabado de assar ainda não entrou no step.
        if is_player {
            entity.insert(crate::player::TeleportSettle::default());
        }
        info!("spawn-gate: solto a y={y:.2} após {:.2} s", gate.waited);
    }
}

/// Liga o `<SpawnGate>`; inerte em mundos sem a tag.
pub struct SpawnGatePlugin;

impl Plugin for SpawnGatePlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(Startup, collect_gates.after(crate::recipes::spawn::startup))
            .add_systems(
                Update,
                (resolve_gates, hold_gated)
                    .chain()
                    .after(crate::player::player_movement)
                    .before(crate::camera::third_person_camera),
            );
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use super::*;
    use crate::terrain::plugin::TerrainChunk;
    use crate::terrain::runtime::TerrainRuntime;

    fn runtime(collision_resolution: u32) -> TerrainRuntime {
        let spec = crate::terrain::spec::TerrainSpec {
            world_size: 128.0,
            max_height: 40.0,
            seed: 5,
            collision_resolution,
            ..crate::terrain::spec::TerrainSpec::default()
        };
        let map = crate::terrain::heightmap::HeightMapU16::procedural(
            &spec,
            spec.resolution.max(1) as usize,
        );
        let grid = crate::terrain::brush::BrushGrid::from_height_map(
            &map,
            spec.world_size,
            spec.max_height,
            spec.height_smoothing,
        )
        .expect("grid builds");
        TerrainRuntime {
            spec,
            grid: Arc::new(grid),
            water: vec![],
            roads: vec![],
            pads: vec![],
            voxel: Arc::new(crate::terrain::voxel::VoxelField::default()),
            deltas: Arc::new(crate::terrain::delta::DeltaGrid::default()),
        }
    }

    fn gate_app(rt: Option<TerrainRuntime>, at: Vec3) -> (App, Entity) {
        let mut app = App::new();
        app.init_resource::<Time<Real>>()
            .insert_resource(PendingSpawnGates {
                list: vec![GateSpec {
                    target: "crate".into(),
                    y_fallback: Some(90.0),
                    skin: 0.05,
                }],
                frames: 0,
            })
            .add_systems(Update, (resolve_gates, hold_gated).chain());
        if let Some(rt) = rt {
            app.insert_resource(rt);
        }
        let entity = app
            .world_mut()
            .spawn((Name::new("crate"), Transform::from_translation(at)))
            .id();
        (app, entity)
    }

    fn column_under(rt: &TerrainRuntime, p: Vec3) -> TerrainChunk {
        let half = rt.spec.world_size * 0.5;
        let edge = crate::terrain::plugin::chunk_edge(&rt.spec);
        TerrainChunk {
            coords: UVec2::new(((p.x + half) / edge) as u32, ((p.z + half) / edge) as u32),
            lod: 0,
            built_lod: 0,
            built_neighbours: [0; 4],
            built_edit_rev: 0,
        }
    }

    #[test]
    fn test_gate_holds_at_fallback_until_the_column_has_a_collider() {
        let at = Vec3::new(10.0, 3.0, 10.0);
        let rt = runtime(64);
        let column = column_under(&rt, at);
        let surface = crate::player::landing_position(Some(&rt), Vec3::new(10.0, 90.0, 10.0)).y;
        let (mut app, entity) = gate_app(Some(rt), at);
        for _ in 0..3 {
            app.update();
            // Alguém (gravidade, input) tenta mexer-lhe — o portão desfaz.
            app.world_mut()
                .get_mut::<Transform>(entity)
                .unwrap()
                .translation
                .y -= 5.0;
        }
        app.update();
        assert!(app.world().get::<SpawnGated>(entity).is_some());
        assert!(app.world().get::<RigidBodyDisabled>(entity).is_some());
        assert_eq!(
            app.world().get::<Transform>(entity).unwrap().translation.y,
            90.0
        );

        // Uma coluna noutro sítio não chega.
        let mut far = column;
        far.coords.x = far.coords.x.wrapping_add(1);
        app.world_mut().spawn((far, crate::physics::VoxelCollider));
        app.update();
        assert!(app.world().get::<SpawnGated>(entity).is_some());

        app.world_mut()
            .spawn((column, crate::physics::VoxelCollider));
        app.update();
        assert!(app.world().get::<SpawnGated>(entity).is_none(), "solto");
        assert!(app.world().get::<RigidBodyDisabled>(entity).is_none());
        let y = app.world().get::<Transform>(entity).unwrap().translation.y;
        assert!(
            (y - (surface + 0.05)).abs() < 1e-3,
            "assenta na superfície + skin: {y} vs {surface}"
        );
    }

    #[test]
    fn test_gate_without_collision_releases_on_the_first_frame() {
        let at = Vec3::new(10.0, 3.0, 10.0);
        let (mut app, entity) = gate_app(Some(runtime(0)), at);
        app.update();
        assert!(app.world().get::<SpawnGated>(entity).is_none());
    }

    #[test]
    fn test_gate_outside_the_field_keeps_the_authored_y() {
        let at = Vec3::new(5000.0, 12.5, 5000.0);
        let (mut app, entity) = gate_app(Some(runtime(64)), at);
        app.update();
        assert!(app.world().get::<SpawnGated>(entity).is_none());
        assert_eq!(
            app.world().get::<Transform>(entity).unwrap().translation,
            at
        );
    }

    #[test]
    fn test_gate_waits_for_the_terrain_runtime() {
        let at = Vec3::new(10.0, 3.0, 10.0);
        let (mut app, entity) = gate_app(None, at);
        app.update();
        app.update();
        assert!(app.world().get::<SpawnGated>(entity).is_some());
    }

    #[test]
    fn test_spec_defaults_to_the_player() {
        let config = crate::worldsys::EngineConfigData {
            tag: "spawngate".into(),
            attrs: vec![("y-fallback".into(), "150".into())],
        };
        let spec = GateSpec::from_config(&config);
        assert_eq!(spec.target, "player");
        assert_eq!(spec.y_fallback, Some(150.0));
        assert_eq!(spec.skin, DEFAULT_SKIN);
    }
}
