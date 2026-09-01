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
fn knockback_system(
    time: Res<Time>,
    terrain: Option<Res<TerrainRuntime>>,
    mut knocked: Query<(Entity, &mut Transform, &mut Knockback)>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for (entity, mut transform, mut knockback) in &mut knocked {
        let step = knockback.velocity * dt;
        let mut x = transform.translation.x + step.x;
        let mut z = transform.translation.z + step.z;
        knockback.velocity *= (1.0 - KNOCKBACK_DECAY * dt).max(0.0);
        if knockback.velocity.length() < KNOCKBACK_EPSILON {
            knockback.velocity = Vec3::ZERO;
            commands.entity(entity).remove::<Knockback>();
        }
        if let Some(terrain) = terrain.as_deref() {
            let y = terrain.sample(x, z);
            transform.translation.y = y;
            let _ = &mut x;
            let _ = &mut z;
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
        let axis = fall.axis.normalize_or_zero();
        transform.rotation = Quat::from_axis_angle(axis, angle.to_radians());
    }
}

// ── plugin ──────────────────────────────────────────────────────────────

pub struct PhysicsFxPlugin;

impl Plugin for PhysicsFxPlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(Update, (knockback_system, falling_system));
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

    #[test]
    fn test_fall_angle_eases_in() {
        assert!((fall_angle(0.0, 0.9).abs()) < 1e-4);
        let mid = fall_angle(0.45, 0.9);
        assert!(mid < FALL_ANGLE_DEG / 2.0, "ease-in: metade do tempo < metade do ângulo ({mid})");
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
}
