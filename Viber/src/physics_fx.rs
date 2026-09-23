//! Física Fase 3 (loop 10 do port simple-rpg) — efeitos físicos sobre a
//! arquitetura transform-driven da engine (as criaturas movem-se por
//! comandos de script e o herói por cinemática própria — o equivalente do
//! Character Controller do VibeGame; não há rigidbodies dinâmicos para
//! receber impulsos Rapier):
//!
//! - **Knockback**: [`Knockback`] — velocidade horizontal com decaimento
//!   exponencial aplicada ao Transform do atingido (melee, golpe forte,
//!   bomba, dano de script com origem conhecida).
//! - **Destrutíveis com queda** (`break-style: fall` do XML): [`Falling`]
//!   tomba a entidade (rotação progressiva) e despawna no fim — os scripts
//!   de colheita chamam `viber.topple()` no último golpe.
//!
//! Pure fns (`knockback_after`, `fall_angle`, `radial_strength`) testadas.

use bevy::prelude::*;

use crate::profiler::{Group, timed};
use crate::terrain::runtime::TerrainRuntime;

/// Decaimento exponencial do knockback (por segundo).
pub const KNOCKBACK_DECAY: f32 = 6.0;
/// Velocidade mínima abaixo da qual o knockback termina.
pub const KNOCKBACK_EPSILON: f32 = 0.05;
/// Duração da queda de um destrutível (s).
pub const FALL_DURATION: f32 = 0.9;
/// Ângulo total da queda (graus).
pub const FALL_ANGLE_DEG: f32 = 88.0;

// ── componentes ─────────────────────────────────────────────────────────

/// Velocidade horizontal residual de um impacto (m/s).
#[derive(Debug, Clone, Component)]
pub struct Knockback {
    pub velocity: Vec3,
}

/// Destrutível a tombar (`break-style: fall`).
#[derive(Debug, Clone, Component)]
pub struct Falling {
    pub axis: Vec3,
    pub timer: f32,
    /// Orientação no momento da queda — composta com o tombamento para o
    /// prop não "popear" para identidade (perdia o yaw autoral/random-yaw).
    pub initial: Quat,
}

// ── lógica pura (testada) ───────────────────────────────────────────────

/// Velocidade de knockback a partir da direção (normalizada internamente).
pub fn knockback_after(direction: Vec3, strength: f32) -> Knockback {
    let mut dir = direction;
    dir.y = 0.0;
    let flat = dir.normalize_or_zero();
    Knockback {
        velocity: flat * strength,
    }
}

/// Tombamento de `angle_deg` em torno de `axis`. Eixo nulo (herói em cima do
/// tronco) cai para +X: `from_axis_angle(ZERO, a)` dá um quaternião NÃO
/// unitário, que no `Transform` encolhia o prop (a meio a 90°) em vez de o
/// rodar.
pub fn fall_rotation(axis: Vec3, angle_deg: f32) -> Quat {
    let axis = axis.try_normalize().unwrap_or(Vec3::X);
    Quat::from_axis_angle(axis, angle_deg.to_radians())
}

/// Ângulo de queda (graus) no instante `t` de uma queda de `duration`.
pub fn fall_angle(t: f32, duration: f32) -> f32 {
    let phase = (t / duration).clamp(0.0, 1.0);
    // ease-in (acelera ao cair)
    FALL_ANGLE_DEG * phase * phase
}

/// Força radial com falloff linear (igual às bombas).
pub fn radial_strength(distance: f32, radius: f32, strength: f32) -> Option<f32> {
    if distance > radius {
        return None;
    }
    Some(strength * (1.0 - 0.6 * (distance / radius)))
}

// ── sistemas ────────────────────────────────────────────────────────────

/// Aplica e decai o knockback; senta o Y no terreno quando disponível.
///
/// - Cadáveres (`Corpse`) ficam de fora: mortos por strike/bomba no MESMO
///   frame tinham o `Knockback` inserido por cima da animação de morte.
/// - O HERÓI só tem o knockback DECAÍDO aqui: o deslocamento entra no
///   pedido do character controller em [`crate::player::player_movement`]
///   (escrito no Transform atravessava paredes; um Y-slam a meio de um salto
///   sentava-o no chão). Criaturas assentam na superfície sob elas.
/// - XZ clampado ao disco do [`crate::worldsys::WorldBorderConfig`] (a mesma
///   matemática de `worldsys::world_border_clamp`): o empurrão não pode
///   expulsar ninguém do mundo — excepto dentro da bolsa de interior, que
///   vive declaradamente fora do disco (o clamp teleportava para o vale).
#[allow(clippy::type_complexity)]
fn knockback_system(
    time: Res<Time>,
    terrain: Option<Res<TerrainRuntime>>,
    border: Option<Res<crate::worldsys::WorldBorderConfig>>,
    interior: Option<Res<crate::worldsys::InteriorSceneConfig>>,
    mut knocked: Query<
        (
            Entity,
            &mut Transform,
            &mut Knockback,
            Option<&crate::player::Player>,
        ),
        Without<crate::combat::Corpse>,
    >,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    let limit = border.as_deref().map(|b| b.radius - b.margin);
    for (entity, mut transform, mut knockback, player) in &mut knocked {
        let step = knockback.velocity * dt;
        let mut x = transform.translation.x + step.x;
        let mut z = transform.translation.z + step.z;
        knockback.velocity *= (1.0 - KNOCKBACK_DECAY * dt).max(0.0);
        if knockback.velocity.length() < KNOCKBACK_EPSILON {
            knockback.velocity = Vec3::ZERO;
            commands.entity(entity).remove::<Knockback>();
        }
        if player.is_some() {
            continue;
        }
        let in_interior = interior
            .as_deref()
            .is_some_and(|scene| scene.contains(transform.translation.x, transform.translation.z));
        if let Some(limit) = limit.filter(|_| !in_interior) {
            let dist_sq = x * x + z * z;
            if dist_sq > limit * limit {
                let scale = limit / dist_sq.sqrt();
                x *= scale;
                z *= scale;
            }
        }
        if let Some(terrain) = terrain.as_deref() {
            transform.translation.y = crate::player::ground_near(terrain, x, z, transform.translation.y);
        }
        transform.translation.x = x;
        transform.translation.z = z;
    }
}

/// Tomba destrutíveis e despawna no fim da queda.
fn falling_system(
    time: Res<Time>,
    mut falling: Query<(Entity, &mut Transform, &mut Falling)>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut transform, mut fall) in &mut falling {
        fall.timer += dt;
        if fall.timer >= FALL_DURATION {
            commands.entity(entity).despawn();
            continue;
        }
        let angle = fall_angle(fall.timer, FALL_DURATION);
        transform.rotation = fall_rotation(fall.axis, angle) * fall.initial;
    }
}

// ── plugin ──────────────────────────────────────────────────────────────

pub struct PhysicsFxPlugin;

impl Plugin for PhysicsFxPlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(
            Update,
            (
                timed(Group::Fx, knockback_system),
                timed(Group::Fx, falling_system),
            ),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_knockback_flattens_y() {
        let kb = knockback_after(Vec3::new(3.0, 9.0, 4.0), 6.0);
        assert!(kb.velocity.y.abs() < 1e-5, "sem componente vertical");
        // direção (3,4) normalizada × 6
        let expected = Vec3::new(0.6, 0.0, 0.8) * 6.0;
        assert!((kb.velocity - expected).length() < 1e-3);
    }

    #[test]
    fn test_knockback_zero_direction_safe() {
        let kb = knockback_after(Vec3::ZERO, 6.0);
        assert_eq!(kb.velocity, Vec3::ZERO);
    }

    /// Eixo nulo continua a dar uma ROTAÇÃO (quaternião unitário) — antes
    /// encolhia o prop em vez de o tombar.
    #[test]
    fn test_fall_rotation_stays_unit_on_a_zero_axis() {
        let q = fall_rotation(Vec3::ZERO, 90.0);
        assert!((q.length() - 1.0).abs() < 1e-5, "unit: {}", q.length());
        let tipped = q * Vec3::Y;
        assert!(tipped.y.abs() < 1e-4, "fully tipped: {tipped}");
        let q = fall_rotation(Vec3::new(0.0, 0.0, 5.0), 90.0);
        assert!((q * Vec3::Y - Vec3::NEG_X).length() < 1e-4, "axis is normalized");
    }

    #[test]
    fn test_fall_angle_eases_in() {
        assert!((fall_angle(0.0, 0.9).abs()) < 1e-4);
        let mid = fall_angle(0.45, 0.9);
        assert!(
            mid < FALL_ANGLE_DEG / 2.0,
            "ease-in: metade do tempo < metade do ângulo ({mid})"
        );
        assert!((fall_angle(0.9, 0.9) - FALL_ANGLE_DEG).abs() < 1e-3);
        // passa do fim: clamp
        assert!((fall_angle(5.0, 0.9) - FALL_ANGLE_DEG).abs() < 1e-3);
    }

    #[test]
    fn test_radial_strength_falloff() {
        assert!((radial_strength(0.0, 6.0, 10.0).unwrap() - 10.0).abs() < 1e-4);
        let edge = radial_strength(6.0, 6.0, 10.0).unwrap();
        assert!((edge - 4.0).abs() < 1e-4, "40% na borda");
        assert!(radial_strength(7.0, 6.0, 10.0).is_none());
    }

    /// App headless mínima com o plugin (relógio avançado à mão, como em
    /// `ai.rs` — sem TimePlugin o delta real microscópico não estraga os
    /// passos do knockback).
    fn fx_app() -> App {
        let mut app = App::new();
        app.add_plugins(MinimalPlugins.build().disable::<bevy::time::TimePlugin>());
        app.add_plugins(PhysicsFxPlugin);
        app.init_resource::<Time>();
        app
    }

    /// R2-G7: cadáveres (`Corpse`) não são empurrados pelo knockback —
    /// o strike/bomba inseria `Knockback` em mortos do mesmo frame.
    #[test]
    fn test_knockback_skips_corpses_headless() {
        let mut app = fx_app();
        let alive = app
            .world_mut()
            .spawn((
                Transform::from_xyz(0.0, 0.0, 0.0),
                Knockback {
                    velocity: Vec3::X * 2.0,
                },
            ))
            .id();
        let dead = app
            .world_mut()
            .spawn((
                Transform::from_xyz(0.0, 0.0, 0.0),
                Knockback {
                    velocity: Vec3::X * 2.0,
                },
                crate::combat::Corpse {
                    timer: crate::combat::CORPSE_LIFETIME,
                },
            ))
            .id();
        app.world_mut()
            .resource_mut::<Time>()
            .advance_by(std::time::Duration::from_millis(100));
        app.update();
        let alive_x = app.world().get::<Transform>(alive).unwrap().translation.x;
        let dead_x = app.world().get::<Transform>(dead).unwrap().translation.x;
        assert!(alive_x > 0.0, "vivo desloca: {alive_x}");
        assert_eq!(dead_x, 0.0, "cadáver fica no lugar: {dead_x}");
    }

    /// R2-G5a: o HERÓI não leva Y-slam — com terreno REAL na app (o caminho
    /// do slam ativo), o Transform dele fica intacto (o deslocamento entra
    /// pelo character controller no `player_movement`) e o knockback decai.
    #[test]
    fn test_knockback_keeps_player_y_headless() {
        use std::sync::Arc;

        let mut app = fx_app();
        // Grid procedural pequeno (mesmo setup dos testes de `ai.rs`) — sem
        // isto o branch do Y-slam nem corria e o teste não apanhava a
        // regressão.
        let spec = crate::terrain::spec::TerrainSpec {
            world_size: 128.0,
            max_height: 40.0,
            seed: 5,
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
        app.insert_resource(TerrainRuntime {
            spec,
            grid: Arc::new(grid),
            water: vec![],
            roads: vec![],
            pads: vec![],
            voxel: Arc::new(crate::terrain::voxel::VoxelField::default()),
            deltas: std::sync::Arc::new(crate::terrain::delta::DeltaGrid::default()),
        });

        let hero = app
            .world_mut()
            .spawn((
                crate::player::Player::default(),
                Transform::from_xyz(10.0, 3.5, 10.0),
                Knockback {
                    velocity: Vec3::X * 2.0,
                },
            ))
            .id();
        // Um NÃO-player na mesma zona: leva o Y-slam (comportamento mantido).
        let prop = app
            .world_mut()
            .spawn((
                Transform::from_xyz(10.0, 3.5, 10.0),
                Knockback {
                    velocity: Vec3::X * 2.0,
                },
            ))
            .id();

        app.world_mut()
            .resource_mut::<Time>()
            .advance_by(std::time::Duration::from_millis(100));
        app.update();

        let hero_t = app.world().get::<Transform>(hero).unwrap().translation;
        assert_eq!(hero_t, Vec3::new(10.0, 3.5, 10.0), "Transform do herói intocado");
        let hero_kb = app.world().get::<Knockback>(hero).unwrap().velocity;
        assert!(hero_kb.x < 2.0 && hero_kb.x > 0.0, "knockback do herói decai: {hero_kb:?}");
        let prop_t = app.world().get::<Transform>(prop).unwrap().translation;
        let ground = app
            .world()
            .resource::<TerrainRuntime>()
            .sample_mesh_surface(prop_t.x, prop_t.z);
        assert!(
            (prop_t.y - ground).abs() < 1e-3,
            "não-player assenta na superfície: {} vs {ground}",
            prop_t.y
        );
    }

    /// R2-G5b: o deslocamento do knockback é clampado ao disco do
    /// WorldBorder (mesma matemática de `worldsys::world_border_clamp`).
    #[test]
    fn test_knockback_clamps_to_world_border_headless() {
        let mut app = fx_app();
        app.insert_resource(crate::worldsys::WorldBorderConfig {
            radius: 100.0,
            warn_seconds: 5.0,
            margin: 10.0,
        });
        // 1 empurrão forte: (90,0) + X·2·1 s = 92 — o limite é 90
        // (radius − margin): fica em 90.
        let id = app
            .world_mut()
            .spawn((
                Transform::from_xyz(90.0, 0.0, 0.0),
                Knockback {
                    velocity: Vec3::X * 2.0,
                },
            ))
            .id();
        app.world_mut()
            .resource_mut::<Time>()
            .advance_by(std::time::Duration::from_secs_f32(1.0));
        app.update();
        let t = app.world().get::<Transform>(id).unwrap().translation;
        let dist = (t.x * t.x + t.z * t.z).sqrt();
        assert!(
            (dist - 90.0).abs() < 1e-3,
            "clampado ao disco (limit 90): dist={dist}"
        );
        // Dentro do disco: o empurrão aplica-se sem clamp. Relógio avançado
        // por update (sem TimePlugin o delta é o que aqui se der).
        let inner = app
            .world_mut()
            .spawn((
                Transform::from_xyz(0.0, 0.0, 0.0),
                Knockback {
                    velocity: Vec3::X * 2.0,
                },
            ))
            .id();
        app.world_mut()
            .resource_mut::<Time>()
            .advance_by(std::time::Duration::from_millis(100));
        app.update();
        let t = app.world().get::<Transform>(inner).unwrap().translation;
        assert!(t.x > 0.0, "sem clamp dentro do disco: {t:?}");
    }
}
