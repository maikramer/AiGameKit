//! AI driver — pure-pursuit sobre a linha de corrida (port do
//! `ai-driver.ts` original). A IA escreve SO os inputs do mesmo
//! [`VehicleInput`] do jogador: conduz o modelo físico idêntico.

use bevy::math::Vec2;
use bevy::prelude::*;

use super::race::{RaceProgress, RaceState, TrackInfo};
use super::vehicle::{DRIFT_MIN_SPEED, Vehicle, VehicleInput};

/// Look-ahead base (m) + por m/s de velocidade (`ai-driver.ts`).
const LOOK_AHEAD_BASE: f32 = 8.0;
const LOOK_AHEAD_PER_SPEED: f32 = 0.35;
/// Orçamento lateral de curvatura (m/s²) — v = √(a/κ).
const LAT_ACCEL: f32 = 20.0;
/// Bandas de rubber-band: atrás acelera, à frente abranda (clamp).
const RUBBER_NEAR: f32 = 0.92;
const RUBBER_FAR: f32 = 1.12;
/// Stuck: velocidade abaixo disto conta como atasco (m/s).
const STUCK_SPEED: f32 = 1.5;
/// Tempo de atasco antes da recuperação (s) / duração da ré (s).
const STUCK_TIME: f32 = 3.0;
const RECOVER_TIME: f32 = 1.2;

/// Conduz os karts `driver="ai"`: escreve o `VehicleInput` deles antes do
/// movimento. Pure-pursuit para um ponto à frente na linha de corrida
/// (corta apexes), velocidade-alvo da pior curvatura dentro da distância de
/// travagem, rubber-band ao jogador, recuperação de atasco por progresso.
pub fn ai_driver(
    time: Res<Time>,
    director: Option<Res<super::race::RaceDirector>>,
    info: Option<Res<TrackInfo>>,
    mut karts: Query<(
        &Transform,
        &mut Vehicle,
        &mut RaceProgress,
        &mut VehicleInput,
    )>,
) {
    let Some(info) = info else {
        return;
    };
    if info.road.is_none() {
        return;
    }
    let Some(director) = director else {
        return;
    };
    let dt = time.delta_secs();
    let racing = director.racing_active();
    let launch = director.in_launch();
    let elapsed = time.elapsed_secs();

    // Rubber-band: gap de progresso até ao kart do jogador.
    let player_total = karts
        .iter()
        .filter(|(_, v, ..)| v.is_player())
        .map(|(_, _, p, _)| p.total)
        .next()
        .unwrap_or(0.0);

    for (transform, mut vehicle, progress, mut input) in &mut karts {
        if vehicle.is_player() {
            continue;
        }
        if !racing {
            // Na grelha/countdown a IA segura o acelerador no fim da contagem
            // (rival mais lento rev mais tarde); inputs mortos em Finished.
            let throttle = if launch
                && matches!(director.state, RaceState::Countdown(t) if t < 1.8 + (1.0 - skill_of(&vehicle)))
            {
                1.0
            } else {
                0.0
            };
            *input = VehicleInput {
                throttle,
                ..VehicleInput::default()
            };
            continue;
        }
        let skill = skill_of(&vehicle);
        let pos = Vec2::new(transform.translation.x, transform.translation.z);

        // Stuck/recovery: sem progresso e devagar → conta; recuperando → ré.
        if vehicle.speed.abs() < STUCK_SPEED && input.brake <= 0.0 {
            vehicle.stuck += dt;
        } else if vehicle.speed.abs() > STUCK_SPEED {
            vehicle.stuck = 0.0;
        }
        let recovering = vehicle.stuck > STUCK_TIME;
        if recovering {
            if vehicle.stuck > STUCK_TIME + RECOVER_TIME {
                vehicle.stuck = 0.0;
                *input = VehicleInput::default();
                continue;
            }
            // Ré com esterço invertido para largar o obstáculo.
            *input = VehicleInput {
                throttle: 0.0,
                brake: 1.0,
                steer: -input.steer,
                handbrake: false,
                nitro: false,
            };
            continue;
        }

        // ── pure pursuit ───────────────────────────────────────────────
        let s = progress.last_s.unwrap_or(0.0);
        let look_ahead = (LOOK_AHEAD_BASE + vehicle.speed * LOOK_AHEAD_PER_SPEED).clamp(8.0, 30.0);
        let line_offset = line_offset_of(&vehicle);
        let Some((station, tangent)) = info.at(s + look_ahead) else {
            continue;
        };
        let perp = Vec2::new(-tangent.y, tangent.x);
        let target = station + perp * line_offset;
        let to_target = target - pos;
        // Convenção da engine: h = atan2(vx, vz) (forward = (sin h, cos h)).
        let desired = to_target.x.atan2(to_target.y);
        let mut err = desired - vehicle.heading;
        while err > std::f32::consts::PI {
            err -= std::f32::consts::TAU;
        }
        while err < -std::f32::consts::PI {
            err += std::f32::consts::TAU;
        }
        let steer = (err * 2.2).clamp(-1.0, 1.0);
        let mut steer = steer;

        // ── velocidade-alvo: pior curvatura na distância de travagem ──
        let brake_dist =
            (vehicle.speed * vehicle.speed / (2.0 * vehicle.stats.brake) + 10.0).clamp(15.0, 90.0);
        let kappa = info.max_curvature_ahead(s + 5.0, brake_dist);
        let v_corner = if kappa > 1e-4 {
            (LAT_ACCEL * (0.75 + 0.25 * skill) / kappa).sqrt()
        } else {
            vehicle.stats.top_speed
        };
        let gap = player_total - progress.total; // + = IA atrás do jogador
        let rubber = (1.0_f32 + gap * 0.004).clamp(RUBBER_NEAR, RUBBER_FAR);
        let target_speed = (vehicle.stats.top_speed * skill * rubber).min(v_corner);

        let mut throttle = if vehicle.speed < target_speed - 0.5 {
            1.0
        } else {
            0.0
        };
        let mut brake = if vehicle.speed > target_speed + 1.5 {
            1.0
        } else {
            0.0
        };
        // Drift nas curvas fechadas com velocidade de entrada.
        let sharp_ahead = info.max_curvature_ahead(s + 3.0, 15.0);
        let handbrake =
            sharp_ahead > 0.02 && vehicle.speed > DRIFT_MIN_SPEED && vehicle.speed < 26.0;
        // Nitro nas retas (curvatura baixa à frente) com tanque cheio.
        let straight = info.max_curvature_ahead(s + 5.0, 40.0) < 0.004;
        let nitro = straight && vehicle.nitro > 0.6 && kappa < 0.004;
        if recovering {
            throttle = 0.0;
            brake = 1.0;
        }
        // Erros programados por skill: às vezes entra largo (lockup).
        if skill < 0.85
            && sharp_ahead > 0.01
            && (elapsed * 0.37 + progress.total * 0.01).fract() < 0.04
        {
            steer *= 0.6;
        }
        *input = VehicleInput {
            throttle,
            brake,
            steer,
            handbrake,
            nitro,
        };
    }
}

/// Skill do kart: deriva do template (`rival-vega` ~0.94 … `rook` ~0.74) ou
/// do atributo `skill` quando presente. Default 0.8.
fn skill_of(vehicle: &Vehicle) -> f32 {
    match vehicle.template.as_str() {
        t if t.contains("vega") => 0.94,
        t if t.contains("koda") => 0.87,
        t if t.contains("mira") => 0.8,
        t if t.contains("rook") => 0.74,
        _ => 0.8,
    }
}

/// Offset lateral na linha de corrida (corta apexes / alterna por kart).
fn line_offset_of(vehicle: &Vehicle) -> f32 {
    match vehicle.template.as_str() {
        t if t.contains("vega") => -1.4,
        t if t.contains("koda") => 0.8,
        t if t.contains("mira") => 2.4,
        t if t.contains("rook") => -0.6,
        _ => 0.0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skill_and_line_offset_follow_the_roster() {
        let mut v = Vehicle {
            driver: super::super::vehicle::Driver::Ai,
            template: "rival-vega".to_string(),
            ..template_min()
        };
        assert!((skill_of(&v) - 0.94).abs() < 1e-4);
        assert!((line_offset_of(&v) - (-1.4)).abs() < 1e-4);
        v.template = "rival-rook".to_string();
        assert!((skill_of(&v) - 0.74).abs() < 1e-4);
        v.template = "kart".to_string();
        assert!((skill_of(&v) - 0.8).abs() < 1e-4, "default");
    }

    fn template_min() -> Vehicle {
        Vehicle {
            driver: super::super::vehicle::Driver::Ai,
            template: String::new(),
            stats: Default::default(),
            speed: 0.0,
            slip: 0.0,
            heading: 0.0,
            steer: 0.0,
            nitro: 0.0,
            nitro_active: false,
            turbo: 0.0,
            turbo_super: false,
            drift_charge: 0.0,
            racing: false,
            airborne: false,
            air_vy: 0.0,
            prev_climb: 0.0,
            spin: 0.0,
            rev: 0.0,
            wheelspin: 0.0,
            stuck: 0.0,
            smoke_cd: 0.0,
            boost_cd: 0.0,
        }
    }
}
