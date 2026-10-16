//! Racing — suporte de jogo de corrida no Viber (port do plugin `racing` do
//! VibeGame, Fase 1: core de corrida).
//!
//! Ativa-se com `gameplay: racing` no config.yaml. As peças:
//!
//! * [`vehicle`] — kart arcade cinemático (`<Vehicle>`, `<VehicleTemplate>`),
//!   drift com mini/super-turbo, nitro, arranque foguete, paredes lógicas da
//!   pista, fumo de drift;
//! * [`race`] — diretor de corrida (grelha 2×N, countdown, voltas por
//!   progresso acumulado, posições ao vivo, resultados);
//! * [`ai`] — rivais pure-pursuit sobre a linha de corrida (mesmo modelo
//!   físico do jogador);
//! * [`audio`] — loop de motor com pitch por velocidade + skid.
//!
//! Exposição a scripts/HUD: `viber.race.*` e `viber.vehicle.*` (leitura +
//! respawn), instalada no `luau::api` sobre o [`RaceSnapshot`].

pub mod ai;
pub mod audio;
pub mod race;
pub mod vehicle;

use bevy::prelude::*;

/// Snapshot por-frame do estado da corrida para `viber.race.*` /
/// `viber.vehicle.*` (escrito pelo [`publish_lua_state`] no `ScriptCtx`).
#[derive(Debug, Clone, Default)]
pub struct RaceSnapshot {
    /// Estado do diretor: "grid" | "countdown" | "racing" | "finished".
    pub state: String,
    pub lap: u32,
    pub laps: u32,
    pub position: u32,
    pub total_positions: u32,
    pub race_time: f32,
    pub countdown: Option<u32>,
    pub best_lap: Option<f32>,
    pub last_lap: Option<f32>,
    pub wrong_way: bool,
    /// Cartaz atual do diretor (GO!/VOLTA FINAL/META…), com ttl.
    pub message: Option<String>,
    // ── kart do jogador ────────────────────────────────────────────────
    pub speed: f32,
    pub top_speed: f32,
    pub nitro: f32,
    pub nitro_active: bool,
    pub drifting: bool,
    pub drift_charge: f32,
    pub turbo: f32,
}

/// Plugin de corrida — registado em `app.rs` só com `gameplay: racing`.
pub struct RacingPlugin;

impl bevy::app::Plugin for RacingPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.init_resource::<vehicle::VehicleTemplates>();
        app.init_resource::<race::RaceConfig>();
        app.init_resource::<race::RaceDirector>();
        app.init_resource::<race::TrackInfo>();
        app.init_resource::<audio::EngineAudio>();
        app.init_resource::<SpeedFov>();
        // `<Race laps countdown grid-s>` + `<VehicleTemplate id …>` → recursos.
        app.add_systems(
            bevy::app::Update,
            (race::race_config_system, collect_vehicle_templates).chain(),
        );
        // Ordem por frame: traçado → diretor (estado) → IA (inputs) →
        // movimento → progresso/posições → som → snapshot Lua. Tudo ANTES do
        // `player_movement`/câmara (o kart do jogador é lido por elas no
        // mesmo frame) e antes do `luau_update` (viber.race.* fresco).
        app.add_systems(
            bevy::app::Update,
            (
                race::build_track_info,
                race::race_director_system,
                ai::ai_driver,
                vehicle::vehicle_movement,
                race::race_progress_system,
                race::race_rank_system,
                audio::engine_audio_system,
                publish_lua_state,
                speed_fov_system,
            )
                .chain()
                .before(crate::player::player_movement)
                .before(crate::luau::luau_update),
        );
        // O bónus de FOV soma POR CIMA do base+kick que o `fov_kick_system`
        // escreve (racing ⇒ !rpg ⇒ o CameraFx existe sempre).
        app.add_systems(
            bevy::app::Update,
            speed_fov_apply.after(crate::camera::fov_kick_system),
        );
        app.add_systems(bevy::app::Startup, audio::load_engine_audio);
    }
}

/// Bónus de FOV alvo por velocidade (graus) — chase cam do VibeGame
/// (`fov 74`, `fov-boost 13`): +8° ao teto, +5° extra em turbo/nitro.
const FOV_BONUS_MAX: f32 = 8.0;
const FOV_BONUS_TURBO: f32 = 5.0;
const FOV_LERP: f32 = 2.5;

/// Estado suavizado do bónus (partilhado entre os dois sistemas).
#[derive(Debug, Default, Resource)]
struct SpeedFov(f32);

/// Calcula o bónus suavizado a partir do kart do jogador.
fn speed_fov_system(
    time: Res<Time>,
    karts: Query<&vehicle::Vehicle, With<vehicle::PlayerKart>>,
    mut state: ResMut<SpeedFov>,
) {
    let target = match karts.iter().next() {
        Some(kart) => {
            let ratio = kart.speed_ratio().clamp(0.0, 1.0);
            FOV_BONUS_MAX * ratio
                + if kart.turbo > 0.0 || kart.nitro_active {
                    FOV_BONUS_TURBO
                } else {
                    0.0
                }
        }
        None => 0.0,
    };
    let lerp = (FOV_LERP * time.delta_secs()).clamp(0.0, 1.0);
    state.0 += (target - state.0) * lerp;
}

/// Aplica o bónus por cima do fov do frame (base + kick do CameraFx).
fn speed_fov_apply(
    state: Res<SpeedFov>,
    mut cameras: Query<
        (&mut Projection, &crate::camera::BaseFov),
        With<crate::recipes::spawn::OrbitCamera>,
    >,
) {
    if state.0.abs() < 0.01 {
        return;
    }
    for (mut projection, _base) in &mut cameras {
        let Projection::Perspective(persp) = projection.as_mut() else {
            continue;
        };
        persp.fov += state.0.to_radians();
    }
}

/// Coleta os `<VehicleTemplate id …>` do mundo para o catálogo de stats.
pub fn collect_vehicle_templates(
    mut done: Local<bool>,
    configs: Option<Res<crate::worldsys::EngineConfigs>>,
    mut templates: ResMut<vehicle::VehicleTemplates>,
) {
    if *done {
        return;
    }
    let Some(configs) = configs else {
        return;
    };
    let mut warnings = Vec::new();
    for cfg in configs.all("vehicletemplate") {
        match vehicle::VehicleStats::from_config(cfg, &mut warnings) {
            Ok(stats) => {
                let id = cfg.attr("id").unwrap_or("kart").trim().to_string();
                templates.0.insert(id, stats);
            }
            Err(e) => warnings.push(e),
        }
        for w in &warnings {
            warn!(target: "viber::racing", "{w}");
        }
        warnings.clear();
    }
    *done = true;
}

/// Publica o [`RaceSnapshot`] no `ScriptCtx` ANTES do `luau_update` — os
/// scripts veem o frame atual sem tocar no teto de 16 parâmetros do
/// `luau_update`.
pub fn publish_lua_state(
    mut host: ResMut<crate::luau::LuaScriptHost>,
    director: Option<Res<race::RaceDirector>>,
    karts: Query<(&vehicle::Vehicle, &race::RaceProgress), With<vehicle::PlayerKart>>,
) {
    let Some(mut ctx) = host.lua.app_data_mut::<crate::luau::ScriptCtx>() else {
        return;
    };
    let Some(director) = director else {
        ctx.race = None;
        return;
    };
    let kart = karts.iter().next();
    let total_positions = karts.iter().count() as u32;
    ctx.race = Some(RaceSnapshot {
        state: match director.state {
            race::RaceState::Grid => "grid",
            race::RaceState::Countdown(_) => "countdown",
            race::RaceState::Racing => "racing",
            race::RaceState::Finished => "finished",
        }
        .to_string(),
        lap: kart.map(|(_, p)| p.lap).unwrap_or(0),
        laps: director.total_laps,
        position: kart.map(|(_, p)| p.position).unwrap_or(0),
        total_positions,
        race_time: director.race_time,
        countdown: director.countdown_n(),
        best_lap: kart.and_then(|(_, p)| p.best_lap),
        last_lap: kart.and_then(|(_, p)| p.last_lap),
        wrong_way: kart.map(|(_, p)| p.wrong_way > 0.7).unwrap_or(false),
        message: director.message.as_ref().map(|(m, _)| m.clone()),
        speed: kart.map(|(v, _)| v.speed).unwrap_or(0.0),
        top_speed: kart.map(|(v, _)| v.stats.top_speed).unwrap_or(1.0),
        nitro: kart.map(|(v, _)| v.nitro).unwrap_or(0.0),
        nitro_active: kart.map(|(v, _)| v.nitro_active).unwrap_or(false),
        drifting: kart.map(|(v, _)| v.drift_charge > 0.0).unwrap_or(false),
        drift_charge: kart.map(|(v, _)| v.drift_charge).unwrap_or(0.0),
        turbo: kart.map(|(v, _)| v.turbo).unwrap_or(0.0),
    });
}
