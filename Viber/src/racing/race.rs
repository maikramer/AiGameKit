//! Race director — grid, countdown, voltas por progresso acumulado,
//! posições ao vivo e resultados (port do `race-director.ts` do VibeGame).
//!
//! As voltas contam por **progresso acumulado ao longo da pista** (delta-s
//! wrap-aware), não por cruzamentos de linha: impossível enganar com
//! reversão ou projeção no braço errado — a pista cruza-se a si própria por
//! baixo do viaduto.

use bevy::math::{Quat, Vec2, Vec3};
use bevy::prelude::*;

use super::vehicle::Vehicle;
use crate::terrain::paths::{nearest_on_path, path_length};
use crate::terrain::runtime::TerrainRuntime;

/// Estados do diretor (VibeGame `idle→grid→countdown→racing→finished`).
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub enum RaceState {
    /// Karts na grelha, esperando todos os veículos.
    #[default]
    Grid,
    /// Semáforo a contar (valor = segundos restantes).
    Countdown(f32),
    Racing,
    Finished,
}

/// Config `<Race laps countdown grid-s>` (engine config, defaults VibeGame).
#[derive(Debug, Clone, Resource)]
pub struct RaceConfig {
    pub laps: u32,
    pub countdown: f32,
    /// Posição (arco, m) da primeira fila da grelha.
    pub grid_s: f32,
    /// Espaço entre filas (m) / colunas (±m).
    pub grid_row: f32,
    pub grid_col: f32,
}

impl Default for RaceConfig {
    fn default() -> Self {
        Self {
            laps: 3,
            countdown: 3.0,
            grid_s: 35.0,
            grid_row: 7.0,
            grid_col: 2.6,
        }
    }
}

impl RaceConfig {
    pub fn from_engine(configs: &crate::worldsys::EngineConfigs) -> Self {
        let mut out = Self::default();
        let Some(cfg) = configs.first("race") else {
            return out;
        };
        if let Some(v) = cfg.attr("laps").and_then(|v| v.trim().parse::<u32>().ok()) {
            out.laps = v.max(1);
        }
        if let Some(v) = cfg
            .attr("countdown")
            .and_then(|v| v.trim().parse::<f32>().ok())
        {
            out.countdown = v.clamp(0.5, 10.0);
        }
        if let Some(v) = cfg
            .attr("grid-s")
            .and_then(|v| v.trim().parse::<f32>().ok())
        {
            out.grid_s = v.max(0.0);
        }
        out
    }
}

/// Diretor global da corrida.
#[derive(Debug, Resource)]
pub struct RaceDirector {
    pub state: RaceState,
    pub total_laps: u32,
    /// Tempo de corrida (s, desde o GO).
    pub race_time: f32,
    /// Mensagem para o HUD (texto, ttl s) — GO/FINAL LAP/META.
    pub message: Option<(String, f32)>,
    /// Melhor volta da sessão (persistida pelo save em `world_kv`).
    pub session_best: Option<f32>,
    /// Resultado do jogador quando `Finished` (posição, tempo total).
    pub player_result: Option<(u32, f32)>,
}

impl Default for RaceDirector {
    fn default() -> Self {
        Self {
            state: RaceState::Grid,
            total_laps: 3,
            race_time: 0.0,
            message: None,
            session_best: None,
            player_result: None,
        }
    }
}

impl RaceDirector {
    /// Inputs vivos (corrida a decorrer).
    pub fn racing_active(&self) -> bool {
        matches!(self.state, RaceState::Racing)
    }

    /// Janela de arranque (Grid/Countdown): hold-W conta revs.
    pub fn in_launch(&self) -> bool {
        matches!(self.state, RaceState::Grid | RaceState::Countdown(_))
    }

    /// Número do countdown para o HUD (None fora do countdown).
    pub fn countdown_n(&self) -> Option<u32> {
        match self.state {
            RaceState::Countdown(t) => Some((t.ceil() as u32).clamp(1, 3)),
            _ => None,
        }
    }
}

/// Progresso de corrida de um kart (voltas/posições/cronómetros).
#[derive(Debug, Clone, Component, Default)]
pub struct RaceProgress {
    /// Volta atual (1-based).
    pub lap: u32,
    /// Progresso acumulado ao longo da pista (m, anti-cheat).
    pub total: f32,
    /// Última projeção de arco (None = ainda não projetado).
    pub last_s: Option<f32>,
    /// Instante do início da volta atual.
    pub lap_start: f32,
    pub last_lap: Option<f32>,
    pub best_lap: Option<f32>,
    pub finished: bool,
    pub finish_time: f32,
    /// Posição ao vivo (1-based).
    pub position: u32,
    /// Timer de sentido errado (s acumulado).
    pub wrong_way: f32,
}

impl RaceProgress {
    /// Volta que o progresso acumulado representa (1-based).
    pub fn lap_for(total: f32, lap_length: f32) -> u32 {
        ((total / lap_length.max(1.0)).floor() as u32) + 1
    }
}

/// Traçado da pista de corrida (a estrada fechada mais longa do mundo), com
/// derivadas para a IA e a grelha.
#[derive(Debug, Clone, Resource, Default)]
pub struct TrackInfo {
    /// Índice em `TerrainRuntime.roads`.
    pub road: Option<usize>,
    /// Comprimento da volta (m).
    pub lap_length: f32,
    /// Estações XZ (spacing ~1 m do resample das roads).
    pub stations: Vec<Vec2>,
    /// Tangente unitária por estação.
    pub tangents: Vec<Vec2>,
    /// Curvatura (1/m) por estação.
    pub curvature: Vec<f32>,
}

impl TrackInfo {
    /// Estação + tangente num arco `s` (wrap para pistas fechadas).
    pub fn at(&self, s: f32) -> Option<(Vec2, Vec2)> {
        let n = self.stations.len();
        if n < 2 {
            return None;
        }
        let i = (s.rem_euclid(self.lap_length.max(1.0)) as usize).min(n - 1);
        Some((
            self.stations[i],
            self.tangents.get(i).copied().unwrap_or(Vec2::Y),
        ))
    }

    /// Curvatura máxima à frente dentro de `dist` metros (wrap).
    pub fn max_curvature_ahead(&self, s: f32, dist: f32) -> f32 {
        let n = self.curvature.len();
        if n == 0 {
            return 0.0;
        }
        // Wrap: janelas perto do fim da volta continuam no início (o
        // `min(n-1)` antigo colapsava o começo da janela na última estação).
        let start = (s.max(0.0) as usize) % n;
        let span = (dist.max(0.0) as usize).min(n);
        (start..start + span)
            .map(|i| self.curvature[i % n])
            .fold(0.0_f32, f32::max)
    }

    /// Deriva de uma estrada (estações/tangentes/curvatura).
    pub fn from_road(road: &crate::terrain::roads::RoadPath) -> Self {
        let lap_length = path_length(&road.stations);
        let n = road.stations.len();
        let tangents: Vec<Vec2> = (0..n)
            .map(|i| {
                let a = road.stations[i];
                let b = road.stations[(i + 1) % n];
                (b - a).normalize_or_zero()
            })
            .collect();
        let curvature: Vec<f32> = (0..n)
            .map(|i| {
                let prev = road.stations[(i + n - 1) % n];
                let cur = road.stations[i];
                let next = road.stations[(i + 1) % n];
                let d1 = (cur - prev).normalize_or_zero();
                let d2 = (next - cur).normalize_or_zero();
                let cross = (d1.x * d2.y - d1.y * d2.x).abs();
                let ds = prev.distance(cur).max(1e-3) + cur.distance(next).max(1e-3);
                (cross / ds).min(1.0)
            })
            .collect();
        Self {
            road: None,
            lap_length,
            stations: road.stations.clone(),
            tangents,
            curvature,
        }
    }

    /// A melhor pista de corrida no runtime: a estrada FECHADA mais longa
    /// (o circuito), fallback a mais longa.
    pub fn from_runtime(runtime: &TerrainRuntime) -> Self {
        let mut best: Option<(usize, f32)> = None;
        for (i, road) in runtime.roads.iter().enumerate() {
            let len = path_length(&road.stations);
            let closed = road.stations.len() > 2
                && road
                    .stations
                    .first()
                    .map(|p| p.distance(*road.stations.last().unwrap_or(p)))
                    < Some(4.0);
            let score = len * if closed { 2.0 } else { 1.0 };
            if best.is_none() || score > best.unwrap().1 {
                best = Some((i, score));
            }
        }
        let Some((idx, _)) = best else {
            return Self::default();
        };
        let mut out = Self::from_road(&runtime.roads[idx]);
        out.road = Some(idx);
        out
    }
}

/// Constrói o [`TrackInfo`] quando o terreno bootstrap já tem estradas.
pub fn build_track_info(
    mut done: Local<bool>,
    runtime: Option<Res<TerrainRuntime>>,
    mut info: ResMut<TrackInfo>,
) {
    if *done || info.road.is_some() {
        return;
    }
    let Some(runtime) = runtime else {
        return;
    };
    if runtime.roads.is_empty() {
        return;
    }
    *info = TrackInfo::from_runtime(&runtime);
    if info.road.is_some() {
        *done = true;
        info!(
            target: "viber::racing",
            "race track: road {} — volta de {:.0} m",
            info.road.unwrap_or(0),
            info.lap_length
        );
    }
}

/// Alinha a grelha UMA vez e faz correr a máquina de estados.
pub fn race_director_system(
    time: Res<Time>,
    config: Res<RaceConfig>,
    mut director: ResMut<RaceDirector>,
    info: Option<Res<TrackInfo>>,
    runtime: Option<Res<TerrainRuntime>>,
    mut karts: Query<(Entity, &mut Transform, &mut Vehicle, &mut RaceProgress)>,
) {
    if let Some((_, ttl)) = &mut director.message {
        *ttl -= time.delta_secs();
        if *ttl <= 0.0 {
            director.message = None;
        }
    }
    match director.state {
        RaceState::Grid => {
            // Espera traçado + terreno + karts; alinha a grelha 2×N
            // (jogador na pole pela ordem de spawn, IA atrás) e arma o
            // countdown. O `raced` evita realinhar em trocas de cena.
            let (Some(info), Some(runtime)) = (info, runtime) else {
                return;
            };
            if info.road.is_none() || info.lap_length < 50.0 {
                return;
            }
            let entities: Vec<Entity> = karts.iter().map(|(e, ..)| e).collect();
            if entities.is_empty() {
                return;
            }
            let mut rows: Vec<(Entity, bool)> = karts
                .iter()
                .map(|(e, _, v, _)| (e, v.is_player()))
                .collect();
            rows.sort_by_key(|(_, player)| !*player);
            let mut placed = 0;
            for (row, (entity, _)) in rows.iter().enumerate() {
                let s = config.grid_s - row as f32 * config.grid_row;
                let col = if row % 2 == 0 {
                    -config.grid_col
                } else {
                    config.grid_col
                };
                let Some((station, tangent)) = info.at(s) else {
                    continue;
                };
                let perp = Vec2::new(-tangent.y, tangent.x);
                let at = station + perp * col;
                let y = runtime
                    .road_surface(at.x, at.y)
                    .map(|surf| surf.y)
                    .unwrap_or_else(|| runtime.sample(at.x, at.y));
                let heading = tangent.y.atan2(tangent.x);
                if let Ok((_, mut transform, mut vehicle, mut progress)) = karts.get_mut(*entity) {
                    transform.translation = Vec3::new(at.x, y, at.y);
                    transform.rotation = Quat::from_rotation_y(heading);
                    vehicle.heading = heading;
                    vehicle.speed = 0.0;
                    vehicle.racing = false;
                    progress.lap = 1;
                    progress.total = 0.0;
                    progress.last_s = None;
                    progress.lap_start = 0.0;
                    placed += 1;
                }
            }
            if placed > 0 {
                director.state = RaceState::Countdown(config.countdown.max(0.5));
                director.total_laps = config.laps;
                director.message = Some(("PREPARADOS".to_string(), 1.5));
                let _ = &entities;
            }
        }
        RaceState::Countdown(t) => {
            let next = t - time.delta_secs();
            if next <= 0.0 {
                director.state = RaceState::Racing;
                director.race_time = 0.0;
                for (_, _, mut vehicle, _) in karts.iter_mut() {
                    vehicle.racing = true;
                }
                director.message = Some(("GO!".to_string(), 1.2));
            } else {
                director.state = RaceState::Countdown(next);
                let n = next.ceil() as u32;
                let prev = t.ceil() as u32;
                if n != prev {
                    director.message = Some((format!("{n}"), 0.9));
                }
            }
        }
        RaceState::Racing => {
            director.race_time += time.delta_secs();
        }
        RaceState::Finished => {}
    }
}

/// Progresso por kart: delta-s wrap-aware → voltas, cronómetros, wrong-way.
pub fn race_progress_system(
    time: Res<Time>,
    mut director: ResMut<RaceDirector>,
    info: Option<Res<TrackInfo>>,
    runtime: Option<Res<TerrainRuntime>>,
    mut racers: Query<(&Transform, &Vehicle, &mut RaceProgress)>,
) {
    let (Some(info), Some(runtime)) = (info, runtime) else {
        return;
    };
    if !director.racing_active() {
        return;
    }
    let dt = time.delta_secs();
    let lap_len = info.lap_length;
    let Some(road) = info.road.and_then(|ri| runtime.roads.get(ri)) else {
        return;
    };
    for (transform, vehicle, mut progress) in &mut racers {
        let p = Vec2::new(transform.translation.x, transform.translation.z);
        let Some(hit) = nearest_on_path(&road.stations, p) else {
            continue;
        };
        let s = hit.station(road.stations.len()) as f32;
        if let Some(last) = progress.last_s {
            let mut ds = s - last;
            let half = lap_len * 0.5;
            if ds < -half {
                ds += lap_len;
            } else if ds > half {
                ds -= lap_len;
            }
            // Anti-cheat: só conta para a frente, e nunca mais do que um
            // salto de respawn (teleportes não fabricam voltas).
            if ds > 0.0 {
                progress.total += ds.min(lap_len * 0.25);
                progress.wrong_way = (progress.wrong_way - dt * 2.0).max(0.0);
            } else if ds < -0.5 {
                progress.wrong_way += dt;
            }
            let lap = RaceProgress::lap_for(progress.total, lap_len);
            if lap > progress.lap {
                let lap_time = director.race_time - progress.lap_start;
                if progress.lap >= 1 && progress.last_s.is_some() {
                    progress.last_lap = Some(lap_time);
                    if progress.best_lap.is_none_or(|b| lap_time < b) {
                        progress.best_lap = Some(lap_time);
                        if vehicle.is_player() {
                            director.session_best = Some(lap_time);
                        }
                    }
                }
                progress.lap_start = director.race_time;
                progress.lap = lap;
                if vehicle.is_player() {
                    if lap == director.total_laps {
                        director.message = Some(("VOLTA FINAL".to_string(), 2.5));
                    } else if lap < director.total_laps {
                        director.message = Some((format!("VOLTA {lap}"), 1.8));
                    }
                }
            }
        } else {
            progress.lap_start = director.race_time;
        }
        progress.last_s = Some(s);
        if !progress.finished && progress.lap > director.total_laps {
            progress.finished = true;
            progress.finish_time = director.race_time;
            if vehicle.is_player() {
                director.player_result = Some((progress.position, director.race_time));
                director.state = RaceState::Finished;
                director.message = Some((format!("META — P{}", progress.position), 5.0));
            }
        }
    }
}

/// Posições ao vivo: terminados primeiro (por tempo), resto por progresso.
pub fn race_rank_system(mut racers: Query<(Entity, &mut RaceProgress)>) {
    let mut rows: Vec<(u8, f32, f32, Entity)> = racers
        .iter()
        .map(|(e, p)| {
            let done = if p.finished { 0u8 } else { 1 };
            (done, p.finish_time, -p.total, e)
        })
        .collect();
    rows.sort_by(|a, b| {
        a.0.cmp(&b.0)
            .then(a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
            .then(a.2.partial_cmp(&b.2).unwrap_or(std::cmp::Ordering::Equal))
    });
    for (position, (_, _, _, entity)) in rows.iter().enumerate() {
        if let Ok((_, mut progress)) = racers.get_mut(*entity) {
            progress.position = position as u32 + 1;
        }
    }
}

/// Carrega o `<Race laps countdown grid-s>` para os recursos do diretor.
pub fn race_config_system(
    mut done: Local<bool>,
    configs: Option<Res<crate::worldsys::EngineConfigs>>,
    mut race_config: ResMut<RaceConfig>,
) {
    if *done {
        return;
    }
    let Some(configs) = configs else {
        return;
    };
    *race_config = RaceConfig::from_engine(&configs);
    *done = true;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn square_track() -> TrackInfo {
        let mut stations = Vec::new();
        for i in 0..10 {
            stations.push(Vec2::new(i as f32 * 10.0, 0.0));
        }
        for i in 0..10 {
            stations.push(Vec2::new(100.0, i as f32 * 10.0));
        }
        for i in 0..10 {
            stations.push(Vec2::new(100.0 - i as f32 * 10.0, 100.0));
        }
        for i in 0..10 {
            stations.push(Vec2::new(0.0, 100.0 - i as f32 * 10.0));
        }
        let n = stations.len();
        let tangents: Vec<Vec2> = (0..n)
            .map(|i| {
                let a = stations[i];
                let b = stations[(i + 1) % n];
                (b - a).normalize_or_zero()
            })
            .collect();
        let mut curvature = vec![0.0; n];
        curvature[20] = 0.5;
        TrackInfo {
            road: Some(0),
            lap_length: 400.0,
            stations,
            tangents,
            curvature,
        }
    }

    #[test]
    fn at_wraps_around_the_lap() {
        let t = square_track();
        let (a, _) = t.at(0.0).expect("at 0");
        let (b, _) = t.at(450.0).expect("at 450 (wrap)");
        assert!(a.distance(b) < 60.0, "450 m wraps to 50 m: {a} vs {b}");
    }

    #[test]
    fn max_curvature_ahead_scans_window() {
        let t = square_track();
        assert!(t.max_curvature_ahead(0.0, 200.0) >= 0.5);
        assert_eq!(t.max_curvature_ahead(230.0, 30.0), 0.0);
    }

    #[test]
    fn lap_for_counts_by_accumulated_distance() {
        assert_eq!(RaceProgress::lap_for(0.0, 100.0), 1);
        assert_eq!(RaceProgress::lap_for(99.0, 100.0), 1);
        assert_eq!(RaceProgress::lap_for(101.0, 100.0), 2);
        assert_eq!(RaceProgress::lap_for(250.0, 100.0), 3);
    }

    #[test]
    fn wrap_delta_crosses_the_seam_forward() {
        // Ultima estação (s=395) → primeira (s=2): delta +7, não −393.
        let mut ds = 2.0 - 395.0;
        let lap = 400.0;
        if ds < -lap * 0.5 {
            ds += lap;
        }
        assert!((ds - 7.0_f32).abs() < 1e-4);
    }

    #[test]
    fn race_config_reads_engine_tags() {
        let configs = crate::worldsys::EngineConfigs {
            list: vec![crate::worldsys::EngineConfigData {
                tag: "race".into(),
                attrs: vec![
                    ("laps".into(), "5".into()),
                    ("countdown".into(), "2".into()),
                ],
            }],
        };
        let cfg = RaceConfig::from_engine(&configs);
        assert_eq!(cfg.laps, 5);
        assert_eq!(cfg.countdown, 2.0);
        assert_eq!(cfg.grid_s, 35.0, "defaults intactos");
    }
}
