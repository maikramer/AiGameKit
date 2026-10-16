//! Vehicle — kart arcade cinemático (port do `vehicle-control.ts` do plugin
//! racing do VibeGame, modelo deliberadamente SEM rigidbody).
//!
//! O estado autoritativo é `heading` + `speed` + slip lateral; a pose mundial
//! integra no Update e assenta na superfície de condução
//! ([`TerrainRuntime::road_surface`] — tabuleiro nos vãos suspensos, terreno
//! carvado no resto). Colisões: paredes lógicas da pista (meia-largura +
//! shoulder) e push circular kart-vs-kart.

use bevy::math::Vec3;
use bevy::prelude::*;
use std::collections::HashMap;

use crate::terrain::runtime::TerrainRuntime;

/// Gravidade arcade do VibeGame (`vehicle-control.ts`).
pub const GRAVITY: f32 = 22.0;
/// Resistência quadrática (`DRAG`).
pub const DRAG: f32 = 0.0016;
/// Resistência rolante constante (`ROLLING`).
pub const ROLLING: f32 = 1.6;
/// Falloff do esterço com a velocidade (`STEER_FALLOFF`): yaw rate efetivo =
/// `max_steer · falloff/(falloff + speed)`.
pub const STEER_FALLOFF: f32 = 26.0;
/// Velocidade mínima para o handbrake travar drift (m/s).
pub const DRIFT_MIN_SPEED: f32 = 12.0;
/// Carga (s) para mini-turbo / super-turbo (VibeGame 1.0 / 2.1).
pub const MINI_TURBO_CHARGE: f32 = 1.0;
pub const SUPER_TURBO_CHARGE: f32 = 2.1;
/// Duração dos turbos de drift (s).
pub const MINI_TURBO_TIME: f32 = 0.85;
pub const SUPER_TURBO_TIME: f32 = 1.6;
/// Grip relativo fora do asfalto (relva, VibeGame surface 0.42).
pub const OFFROAD_GRIP: f32 = 0.42;
/// Escapatória lógica fora da pista (m, VibeGame `shoulder` 3.5).
pub const TRACK_SHOULDER: f32 = 3.5;
/// Faixa ativa das paredes além do shoulder (m).
pub const WALL_RANGE: f32 = 30.0;
/// Raio de contacto kart-vs-kart (m).
pub const KART_RADIUS: f32 = 1.4;
/// Rev mínimo para o arranque foguete (`vehicle-control.ts` 0.72).
pub const ROCKET_REV: f32 = 0.72;

/// Quem conduz: o teclado (um por mundo) ou a IA ([`crate::racing::ai`]).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Driver {
    #[default]
    Player,
    Ai,
}

/// Stats de um `<VehicleTemplate id="kart" …>` — tuning autoral por kart.
#[derive(Debug, Clone)]
pub struct VehicleStats {
    pub top_speed: f32,
    pub accel: f32,
    pub brake: f32,
    pub engine_brake: f32,
    /// Yaw rate máximo a velocidade ~0 (rad/s).
    pub max_steer: f32,
    /// Rapidez de resposta do esterço (rad equivalente/s de lerp).
    pub steer_speed: f32,
    /// Aderência (mata o slip fora de drift).
    pub grip: f32,
    /// Fração de grip durante drift (VibeGame `drift-grip` 0.3).
    pub drift_grip: f32,
    /// Multiplicador de aceleração com nitro (VibeGame `boost` 2.6).
    pub boost: f32,
    /// Recarga de nitro por segundo.
    pub boost_recharge: f32,
}

impl Default for VehicleStats {
    fn default() -> Self {
        // Stats do kart do jogador no grid.xml do VibeGame.
        Self {
            top_speed: 52.0,
            accel: 27.0,
            brake: 52.0,
            engine_brake: 7.0,
            max_steer: 2.7,
            steer_speed: 11.0,
            grip: 7.5,
            drift_grip: 0.3,
            boost: 2.6,
            boost_recharge: 0.28,
        }
    }
}

impl VehicleStats {
    /// Constrói de um [`crate::worldsys::EngineConfigData`] (`<VehicleTemplate>`).
    pub fn from_config(
        config: &crate::worldsys::EngineConfigData,
        warnings: &mut Vec<String>,
    ) -> Result<Self, String> {
        let id = config
            .attr("id")
            .map(str::trim)
            .filter(|id| !id.is_empty())
            .ok_or("<VehicleTemplate> sem id")?
            .to_string();
        let mut num = |key: &str, default: f32, min: f32| -> f32 {
            match config.attr(key) {
                None => default,
                Some(raw) => match raw.trim().parse::<f32>() {
                    Ok(v) if v.is_finite() && v > min => v,
                    _ => {
                        warnings.push(format!(
                            "vehicle '{id}': {key}='{raw}' inválido — {default}"
                        ));
                        default
                    }
                },
            }
        };
        Ok(Self {
            top_speed: num("top-speed", 52.0, 1.0),
            accel: num("accel", 27.0, 0.1),
            brake: num("brake", 52.0, 0.1),
            engine_brake: num("engine-brake", 7.0, 0.0),
            max_steer: num("max-steer", 2.7, 0.05),
            steer_speed: num("steer-speed", 11.0, 0.1),
            grip: num("grip", 7.5, 0.1),
            drift_grip: num("drift-grip", 0.3, 0.01),
            boost: num("boost", 2.6, 0.1),
            boost_recharge: num("boost-recharge", 0.28, 0.0),
        })
    }
}

/// Catálogo `<VehicleTemplate id> → stats`.
#[derive(Debug, Clone, Default, Resource)]
pub struct VehicleTemplates(pub HashMap<String, VehicleStats>);

impl VehicleTemplates {
    pub fn get(&self, id: &str) -> VehicleStats {
        self.0.get(id).cloned().unwrap_or_default()
    }
}

/// Inputs do condutor do frame — o jogador escreve do teclado, a IA escreve
/// por cima ([`crate::racing::ai`]); o movimento consome.
#[derive(Debug, Clone, Copy, Default, Component)]
pub struct VehicleInput {
    /// 0..1 (W/seta cima).
    pub throttle: f32,
    /// 0..1 (S/seta baixo).
    pub brake: f32,
    /// −1..1 (A/D ou setas).
    pub steer: f32,
    /// Space — drift.
    pub handbrake: bool,
    /// Shift — nitro.
    pub nitro: bool,
}

/// Marca o kart do jogador (som de motor, `viber.vehicle.*`, respawn).
#[derive(Debug, Clone, Copy, Component)]
pub struct PlayerKart;

/// Estado de um kart. O kart do jogador leva também [`crate::player::Player`]
/// (câmara/minimapa/save seguem o herói); o `player_movement` salta entidades
/// com este componente.
#[derive(Debug, Clone, Component)]
pub struct Vehicle {
    pub driver: Driver,
    /// Id do `<VehicleTemplate>` (default `kart`).
    pub template: String,
    pub stats: VehicleStats,
    // ── estado dinâmico ────────────────────────────────────────────────
    /// Velocidade para a frente (m/s, negativa em marcha-atrás).
    pub speed: f32,
    /// Ângulo de deriva (rad, 0 = alinhado com o heading).
    pub slip: f32,
    /// Rumo do kart no plano (rad, mundo; 0 = +Z como o Transform).
    pub heading: f32,
    /// Esterço suavizado (−1..1).
    pub steer: f32,
    /// Nitro disponível (0..1).
    pub nitro: f32,
    /// Nitro a ser gasto neste frame (FOV/som leem daqui).
    pub nitro_active: bool,
    /// Turbo ativo restante (s) — mini/super turbo de drift ou arranque.
    pub turbo: f32,
    /// Turbo é super (teto ×1.18)?
    pub turbo_super: bool,
    /// Carga de drift atual (s).
    pub drift_charge: f32,
    /// A correr (countdown/Racing) — inputs vivos.
    pub racing: bool,
    /// No ar (crista de rampa).
    pub airborne: bool,
    pub air_vy: f32,
    /// Razão de subida do terreno no frame anterior (m/s) — o pop do salto
    /// de rampa usa-a (VibeGame: `slope × speed` no lábio).
    pub prev_climb: f32,
    /// Spin-out restante (s) — contacto forte pela traseira.
    pub spin: f32,
    /// Rev de arranque (Grid/Countdown, hold W) 0..1.
    pub rev: f32,
    /// Overrev (WHEELSPIN) restante (s).
    pub wheelspin: f32,
    /// Stuck timer (recuperação da IA).
    pub stuck: f32,
    /// FX: acumulador do fumo de drift.
    pub smoke_cd: f32,
}

impl Vehicle {
    /// Ratio de velocidade para o teto — FOV/som/AI.
    pub fn speed_ratio(&self) -> f32 {
        (self.speed / self.stats.top_speed.max(1.0)).clamp(-0.3, 1.2)
    }

    /// Playerkart? (driver player).
    pub fn is_player(&self) -> bool {
        self.driver == Driver::Player
    }
}

/// Parse do `<Vehicle driver template model-url model-yaw …>`.
#[derive(Debug, Clone)]
pub struct VehicleSpec {
    pub driver: Driver,
    pub template: String,
    pub model_url: Option<String>,
    /// Rotação do modelo filho (rad) — os karts nascem deitados ao longo de
    /// X (`model-yaw="90"` os põe a apontar +Z como o resto da engine).
    pub model_yaw: f32,
    /// Escala do modelo filho.
    pub model_scale: f32,
}

impl VehicleSpec {
    pub fn parse(attrs: &[(String, String)], ctx_tag: &str) -> Result<Self, anyhow::Error> {
        let mut spec = Self {
            driver: Driver::Player,
            template: "kart".to_string(),
            model_url: None,
            model_yaw: 0.0,
            model_scale: 1.0,
        };
        for (key, value) in attrs {
            let kctx = format!("{ctx_tag} {key}");
            match key.as_str() {
                "driver" => {
                    spec.driver = match value.trim().to_ascii_lowercase().as_str() {
                        "player" => Driver::Player,
                        "ai" => Driver::Ai,
                        other => anyhow::bail!("{kctx}: driver inválido `{other}` (player|ai)"),
                    };
                }
                "template" => spec.template = value.trim().to_string(),
                "model-url" => spec.model_url = Some(value.trim().to_string()),
                "model-yaw" => {
                    spec.model_yaw = crate::xml::values::parse_f32(value, &kctx)?.to_radians()
                }
                "model-scale" => spec.model_scale = crate::xml::values::parse_f32(value, &kctx)?,
                _ => {}
            }
        }
        Ok(spec)
    }
}

pub fn forward_of(heading: f32) -> Vec3 {
    Vec3::new(heading.sin(), 0.0, heading.cos())
}

/// Snapshot de um kart para os contactos (evita conflito de queries B0001).
struct KartSnap {
    entity: Entity,
    pos: Vec3,
}

/// Movimento arcade por frame. Corre ANTES do `player_movement` (a câmara lê
/// o resultado no mesmo frame) e escreve o Transform na superfície de
/// condução.
pub fn vehicle_movement(
    time: Res<Time>,
    keys: Res<ButtonInput<bevy::input::keyboard::KeyCode>>,
    menus: Res<crate::menus::MenusOpen>,
    runtime: Option<Res<TerrainRuntime>>,
    director: Option<Res<crate::racing::race::RaceDirector>>,
    track: Option<Res<crate::racing::race::TrackInfo>>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
    mut commands: Commands,
    mut q: Query<(Entity, &mut Transform, &mut Vehicle, &mut VehicleInput)>,
    mut players: Query<&mut crate::player::Player>,
) {
    let Some(runtime) = runtime else {
        return;
    };
    let dt = time.delta_secs();
    let racing_allowed = director.as_ref().map(|d| d.racing_active()).unwrap_or(true);
    let in_launch = director.as_ref().map(|d| d.in_launch()).unwrap_or(false);
    let menus_open = menus.any();
    let (w, s_key, a, d) = (
        keys.pressed(bevy::input::keyboard::KeyCode::KeyW)
            || keys.pressed(bevy::input::keyboard::KeyCode::ArrowUp),
        keys.pressed(bevy::input::keyboard::KeyCode::KeyS)
            || keys.pressed(bevy::input::keyboard::KeyCode::ArrowDown),
        keys.pressed(bevy::input::keyboard::KeyCode::KeyA)
            || keys.pressed(bevy::input::keyboard::KeyCode::ArrowLeft),
        keys.pressed(bevy::input::keyboard::KeyCode::KeyD)
            || keys.pressed(bevy::input::keyboard::KeyCode::ArrowRight),
    );
    let shift = keys.pressed(bevy::input::keyboard::KeyCode::ShiftLeft)
        || keys.pressed(bevy::input::keyboard::KeyCode::ShiftRight);
    let space = keys.pressed(bevy::input::keyboard::KeyCode::Space);
    let respawn = keys.just_pressed(bevy::input::keyboard::KeyCode::KeyR);

    // Contactos kart-vs-kart: snapshot → push circular + penalidade à
    // traseira forte (aplicado por entidade no loop abaixo).
    let snap: Vec<KartSnap> = q
        .iter()
        .map(|(e, t, _, _)| KartSnap {
            entity: e,
            pos: t.translation,
        })
        .collect();
    let speeds: HashMap<Entity, f32> = q.iter().map(|(e, _, v, _)| (e, v.speed)).collect();
    let mut pushes: HashMap<Entity, Vec3> = HashMap::new();
    let mut bumped: Vec<Entity> = Vec::new();
    for (i, ka) in snap.iter().enumerate() {
        for kb in snap.iter().skip(i + 1) {
            let delta = kb.pos - ka.pos;
            let dist = delta.length();
            let min = KART_RADIUS * 2.0;
            if dist >= min || dist < 1e-4 {
                continue;
            }
            let half = delta / dist * ((min - dist) * 0.5);
            *pushes.entry(ka.entity).or_default() -= half;
            *pushes.entry(kb.entity).or_default() += half;
            let va = speeds.get(&ka.entity).copied().unwrap_or(0.0);
            let vb = speeds.get(&kb.entity).copied().unwrap_or(0.0);
            if (va - vb).abs() > 10.0 {
                bumped.push(ka.entity);
                bumped.push(kb.entity);
                sfx.write(crate::ambient::SfxEvent {
                    clip: crate::ambient::SfxClip::Crash,
                    position: Some(ka.pos + delta * 0.5),
                });
            }
        }
    }

    for (entity, mut transform, mut vehicle, mut input) in &mut q {
        // ── input do jogador (a IA já escreveu o `VehicleInput` dela) ────
        if vehicle.is_player() && !menus_open {
            let racing = racing_allowed && vehicle.spin <= 0.0;
            input.throttle = if racing && w { 1.0 } else { 0.0 };
            input.brake = if racing && s_key { 1.0 } else { 0.0 };
            input.steer = if racing {
                ((d as i32 - a as i32) as f32).clamp(-1.0, 1.0)
            } else {
                0.0
            };
            input.handbrake = racing && space;
            input.nitro = racing && shift;
        } else if menus_open && vehicle.is_player() {
            *input = VehicleInput::default();
        }

        // ── arranque foguete / overrev (Grid + Countdown) ──────────────
        if in_launch && vehicle.is_player() {
            if input.throttle > 0.0 {
                vehicle.rev = (vehicle.rev + 0.9 * dt).min(1.0);
            } else {
                vehicle.rev = (vehicle.rev - 1.8 * dt).max(0.0);
            }
            if vehicle.rev >= 1.0 && vehicle.wheelspin <= 0.0 {
                vehicle.wheelspin = 1.4; // overrev: meia potência 1.4 s
            }
        } else if !in_launch && vehicle.rev > 0.0 {
            if vehicle.rev >= ROCKET_REV && vehicle.wheelspin <= 0.0 {
                vehicle.speed = vehicle.speed.max(6.5);
                vehicle.turbo = 1.15;
                vehicle.turbo_super = false;
            }
            vehicle.rev = 0.0;
        }

        let stats = vehicle.stats.clone();
        let heading_vec = forward_of(vehicle.heading);
        let mut pos = transform.translation;

        // ── respawn (R): reposito na estação de pista mais próxima ─────
        if respawn && vehicle.is_player() && racing_allowed {
            let face = pos + heading_vec * 6.0;
            let target = runtime
                .road_surface(face.x, face.z)
                .or_else(|| runtime.road_surface(pos.x, pos.z));
            if let Some(surf) = target {
                let road = &runtime.roads[surf.road];
                if let Some(hit) = crate::terrain::paths::nearest_on_path(
                    &road.stations,
                    bevy::math::Vec2::new(pos.x, pos.z),
                ) {
                    let ahead = (hit.station(road.stations.len()) as usize + 3)
                        .min(road.stations.len() - 1);
                    let at = road.stations[ahead];
                    let next_i = (ahead + 1) % road.stations.len();
                    let tangent = (road.stations[next_i] - at).normalize_or_zero();
                    pos = Vec3::new(at.x, surf.y + 0.3, at.y);
                    vehicle.heading = tangent.y.atan2(tangent.x);
                    vehicle.speed = 0.0;
                    vehicle.slip = 0.0;
                    vehicle.airborne = false;
                    vehicle.air_vy = 0.0;
                    vehicle.drift_charge = 0.0;
                    transform.translation = pos;
                    transform.rotation = bevy::math::Quat::from_rotation_y(vehicle.heading);
                    continue;
                }
            }
        }

        // ── superfície de condução ─────────────────────────────────────
        let surface = runtime.road_surface(pos.x, pos.z);
        let ground_y = surface
            .map(|s| s.y)
            .unwrap_or_else(|| runtime.sample(pos.x, pos.z));
        let on_track = surface.is_some();
        let grip_mult = if on_track { 1.0 } else { OFFROAD_GRIP };

        // ── aceleração / travão ────────────────────────────────────────
        let mut top = stats.top_speed;
        let mut accel = stats.accel;
        if vehicle.turbo > 0.0 {
            accel *= 1.5;
            if vehicle.turbo_super {
                top *= 1.18;
            }
        }
        let nitro_active = input.nitro && vehicle.nitro > 0.02;
        vehicle.nitro_active = nitro_active;
        if nitro_active {
            vehicle.nitro = (vehicle.nitro - 0.35 * dt).max(0.0);
            accel *= stats.boost;
            top *= 1.15;
        } else if racing_allowed {
            vehicle.nitro = (vehicle.nitro + stats.boost_recharge * dt).min(1.0);
        }
        let speed = vehicle.speed;
        let headroom = (1.0 - speed / top).max(0.0);
        let resist = DRAG * speed * speed + ROLLING;
        // Compensação das resistências com o acelerador: a velocidade estabiliza
        // EXATAMENTE no teto (VibeGame vehicle-control.ts 603-620).
        let mut drive =
            accel * headroom * input.throttle * grip_mult * 0.34 + resist * input.throttle;
        if input.brake > 0.0 {
            if speed > 0.5 {
                drive -= stats.brake * input.brake * 0.34;
            } else {
                // ré
                drive = -16.0 * input.brake * grip_mult * 0.34 - resist * input.brake * 0.5;
            }
        }
        if input.throttle <= 0.0 && input.brake <= 0.0 && speed.abs() > 0.1 {
            drive -= speed.signum() * stats.engine_brake * 0.34;
        }
        // Declive: a gravidade projeta na tangente da pista (amostragem
        // à frente/atrás ao longo do heading).
        if surface.is_some() {
            let ahead = pos + heading_vec * 4.0;
            let behind = pos - heading_vec * 4.0;
            let ahead_y = runtime
                .road_surface(ahead.x, ahead.z)
                .map(|s| s.y)
                .unwrap_or_else(|| runtime.sample(ahead.x, ahead.z));
            let behind_y = runtime
                .road_surface(behind.x, behind.z)
                .map(|s| s.y)
                .unwrap_or_else(|| runtime.sample(behind.x, behind.z));
            let slope = (ahead_y - behind_y) / 8.0;
            drive -= GRAVITY * slope * 0.5;
        }
        let mut new_speed = speed + drive * dt;
        if speed.abs() < 0.15 && input.throttle <= 0.0 && input.brake <= 0.0 {
            new_speed = 0.0;
        }
        new_speed = new_speed.clamp(-12.0, top);
        if vehicle.airborne {
            new_speed = speed; // sem atrito no ar
        }

        // ── esterço + drift ────────────────────────────────────────────
        let falloff = STEER_FALLOFF / (STEER_FALLOFF + speed.abs());
        let yaw_rate = input.steer * stats.max_steer * falloff * new_speed.signum();
        let steer_lerp = (stats.steer_speed * dt).clamp(0.0, 1.0);
        vehicle.steer += (input.steer - vehicle.steer) * steer_lerp;
        let drifting = input.handbrake && new_speed.abs() > DRIFT_MIN_SPEED && !vehicle.airborne;
        if drifting {
            vehicle.drift_charge += dt;
            // O slip persegue a traseira solta: alvo proporcional ao esterço,
            // resposta escalada pelo (1 − drift_grip).
            let target = -vehicle.steer * 0.42;
            let rate = 2.5 * (1.0 - stats.drift_grip).max(0.2);
            vehicle.slip += (target - vehicle.slip).clamp(-rate * dt, rate * dt);
            vehicle.slip = vehicle.slip.clamp(-0.55, 0.55);
        } else {
            if vehicle.drift_charge > MINI_TURBO_CHARGE {
                if vehicle.drift_charge >= SUPER_TURBO_CHARGE {
                    vehicle.turbo_super = true;
                    vehicle.turbo = SUPER_TURBO_TIME;
                } else {
                    vehicle.turbo_super = false;
                    vehicle.turbo = MINI_TURBO_TIME;
                }
            }
            vehicle.drift_charge = 0.0;
            let decay = (stats.grip * dt * 2.0).clamp(0.0, 1.0);
            vehicle.slip *= 1.0 - decay;
        }
        if !vehicle.airborne {
            vehicle.heading += yaw_rate * dt;
        }

        // ── timers ─────────────────────────────────────────────────────
        if vehicle.turbo > 0.0 {
            vehicle.turbo = (vehicle.turbo - dt).max(0.0);
        }
        if vehicle.spin > 0.0 {
            vehicle.spin = (vehicle.spin - dt).max(0.0);
            new_speed *= 0.9;
        }
        if vehicle.wheelspin > 0.0 {
            vehicle.wheelspin = (vehicle.wheelspin - dt).max(0.0);
            new_speed *= 0.55;
        }

        // ── integração da posição ──────────────────────────────────────
        let move_dir = forward_of(vehicle.heading - vehicle.slip);
        let mut next = pos + move_dir * new_speed * dt;
        // Paredes lógicas: |dist ao centro| ≤ half_width + shoulder — no
        // traçado SEMPRE (o projection funciona fora da ribbon; sem isto uma
        // saída grande ficava livre para atravessar o campo até voltar).
        if let Some(track) = track.as_ref()
            && let Some(road_idx) = track.road
            && let Some(road) = runtime.roads.get(road_idx)
        {
            if let Some(hit) = crate::terrain::paths::nearest_on_path(
                &road.stations,
                bevy::math::Vec2::new(next.x, next.z),
            ) {
                let hw = crate::terrain::paths::station_lerp(&road.half_width, &hit);
                let limit = hw + TRACK_SHOULDER;
                let to_p = bevy::math::Vec2::new(next.x, next.z) - hit.point;
                let dist = to_p.length();
                // Só paredes na faixa junto ao corredor: uma saída grande
                // desliza ao longo da barreira em vez de ficar perdida no
                // campo, mas longe da pista o mundo é explorável.
                if dist > limit && dist < limit + WALL_RANGE && dist > 1e-4 {
                    let normal = to_p / dist;
                    let clamped = hit.point + normal * limit;
                    next.x = clamped.x;
                    next.z = clamped.y;
                    let vel = move_dir * new_speed;
                    let frontal = (-vel.dot(Vec3::new(normal.x, 0.0, normal.y))).max(0.0);
                    if frontal > 6.0 {
                        new_speed *= 0.55;
                        sfx.write(crate::ambient::SfxEvent {
                            clip: crate::ambient::SfxClip::Crash,
                            position: Some(next),
                        });
                    } else {
                        new_speed *= 0.995;
                    }
                }
            }
        }
        if let Some(push) = pushes.get(&entity) {
            next += *push;
        }
        for &b in &bumped {
            if b == entity {
                new_speed *= 0.9;
            }
        }

        // ── vertical: assenta / lança no ar ────────────────────────────
        let next_ground = runtime
            .road_surface(next.x, next.z)
            .map(|s| s.y)
            .unwrap_or_else(|| runtime.sample(next.x, next.z));
        if vehicle.airborne {
            vehicle.air_vy -= GRAVITY * dt;
            let mut y = pos.y + vehicle.air_vy * dt;
            if y <= next_ground {
                y = next_ground;
                vehicle.airborne = false;
                if vehicle.air_vy < -8.0 {
                    new_speed *= 0.9;
                }
                vehicle.air_vy = 0.0;
            }
            next.y = y;
        } else {
            let climb = (next_ground - ground_y) / dt.max(1e-4);
            if climb < -3.5 && new_speed > 8.0 {
                // O chão caiu rápido — crista de rampa: lança com a subida
                // anterior (VibeGame `vertical = slope × speed`).
                vehicle.airborne = true;
                vehicle.air_vy = vehicle.prev_climb.clamp(0.0, 14.0);
                next.y = pos.y;
            } else {
                next.y = next_ground;
            }
            vehicle.prev_climb = vehicle.prev_climb * 0.8 + climb * 0.2;
        }
        vehicle.speed = new_speed;
        transform.translation = next;
        transform.rotation = bevy::math::Quat::from_rotation_y(vehicle.heading);

        // ── sync com o Player (câmara/minimapa/HUD leem daqui) ─────────
        if vehicle.is_player()
            && let Ok(mut player) = players.get_mut(entity)
        {
            player.vel_x = move_dir.x * new_speed;
            player.vel_z = move_dir.z * new_speed;
            player.grounded = !vehicle.airborne;
        }

        // ── FX: fumo de drift (tier por carga) ─────────────────────────
        vehicle.smoke_cd -= dt;
        if drifting && vehicle.smoke_cd <= 0.0 {
            vehicle.smoke_cd = 0.05;
            let rear = next - heading_vec * 1.2;
            spawn_drift_fx(&mut commands, rear, vehicle.drift_charge);
        }
    }
}

/// Fumo de drift: cinzento no tier 0, faíscas azuis no mini-turbo, laranja
/// no super (mesma linguagem do `vehicle-fx.ts`).
fn spawn_drift_fx(commands: &mut Commands, at: Vec3, charge: f32) {
    let (preset, color) = if charge >= SUPER_TURBO_CHARGE {
        ("sparks", Some([1.0_f32, 0.7, 0.28]))
    } else if charge >= MINI_TURBO_CHARGE {
        ("sparks", Some([0.22_f32, 0.82, 1.0]))
    } else {
        ("smoke", None)
    };
    let spec = crate::recipes::ParticleSpec {
        preset: preset.to_string(),
        emission_rate: None,
        life: None,
        speed: None,
        size: None,
        color,
        shape_radius: None,
        looping: false,
        world_space: false,
    };
    crate::particles::spawn_burst(commands, &spec, at, 6);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stats_default_match_vibegame_grid() {
        let s = VehicleStats::default();
        assert_eq!(s.top_speed, 52.0);
        assert_eq!(s.accel, 27.0);
        assert_eq!(s.drift_grip, 0.3);
        assert_eq!(s.boost, 2.6);
    }

    #[test]
    fn spec_parse_driver_and_yaw() {
        let attrs = vec![
            ("driver".to_string(), "ai".to_string()),
            ("template".to_string(), "rival".to_string()),
            (
                "model-url".to_string(),
                "/assets/meshes/vehicles/kart_rival_lod0.glb".to_string(),
            ),
            ("model-yaw".to_string(), "90".to_string()),
        ];
        let spec = VehicleSpec::parse(&attrs, "<Vehicle>").expect("parse");
        assert_eq!(spec.driver, Driver::Ai);
        assert_eq!(spec.template, "rival");
        assert_eq!(spec.model_yaw, std::f32::consts::FRAC_PI_2);
        let bad = vec![("driver".to_string(), "ghost".to_string())];
        assert!(VehicleSpec::parse(&bad, "<Vehicle>").is_err());
    }

    #[test]
    fn forward_matches_engine_convention() {
        // heading 0 = +Z; heading +90° = +X (mesma convenção do Quat::from_rotation_y).
        assert!((forward_of(0.0) - Vec3::Z).length() < 1e-6);
        assert!((forward_of(std::f32::consts::FRAC_PI_2) - Vec3::X).length() < 1e-6);
    }
}
