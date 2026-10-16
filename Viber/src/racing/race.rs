//! Race director — grid, countdown, voltas por progresso acumulado,
//! posições ao vivo e resultados (port do `race-director.ts`).
//!
//! As voltas contam por **progresso acumulado ao longo da pista** (delta-s
//! wrap-aware), não por cruzamentos de linha: impossível enganar com
//! reversão ou projeção no braço errado — a pista cruza-se a si própria por
//! baixo do viaduto.

use bevy::input::keyboard::KeyCode;
use bevy::math::{Quat, Vec2, Vec3};
use bevy::prelude::*;

use super::vehicle::Vehicle;
use crate::terrain::paths::{nearest_on_path, path_length};
use crate::terrain::runtime::TerrainRuntime;

/// Estados do diretor (`idle→grid→countdown→racing→finished`).
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

/// Config `<Race laps countdown grid-s>` (engine config, defaults da referência).
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
            // Grelha 30 m ANTES da linha: o GO larga cruzando a meta.
            grid_s: -30.0,
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

/// Rampa sobre o traçado (`<TrackRamp s length height [width lateral]>`):
/// lomba que sobe `height` em `length` metros de arco e termina em lábio —
/// a queda do perfil lança o kart (o controller já carrega o `prev_climb`).
#[derive(Debug, Clone)]
pub struct TrackRamp {
    /// Início da rampa (arco da pista, m).
    pub s: f32,
    /// Comprimento da subida (m).
    pub length: f32,
    /// Altura no lábio (m).
    pub height: f32,
    /// Meia-largura efetiva (m) — fora dela a rampa não empurra.
    pub half_width: f32,
    /// Deslocamento lateral do centro da pista (m).
    pub lateral: f32,
}

/// Zona de boost (`<BoostPad s [length width lateral]>`): passar por cima
/// dá turbo + nitro (cooldown por kart no controller).
#[derive(Debug, Clone)]
pub struct BoostPad {
    /// Início da zona (arco da pista, m).
    pub s: f32,
    /// Comprimento da zona ao longo do arco (m).
    pub length: f32,
    /// Meia-largura efetiva (m).
    pub half_width: f32,
    /// Deslocamento lateral do centro da pista (m).
    pub lateral: f32,
}

impl TrackRamp {
    fn from_engine(cfg: &crate::worldsys::EngineConfigData) -> Option<Self> {
        let num = |k: &str, d: f32| cfg.f32_attr(k).unwrap_or(d);
        let s = cfg.f32_attr("s")?;
        Some(Self {
            s,
            length: num("length", 22.0).max(4.0),
            height: num("height", 2.2).max(0.5),
            half_width: num("width", 8.0).max(2.0) * 0.5,
            lateral: num("lateral", 0.0),
        })
    }
}

impl BoostPad {
    fn from_engine(cfg: &crate::worldsys::EngineConfigData) -> Option<Self> {
        let num = |k: &str, d: f32| cfg.f32_attr(k).unwrap_or(d);
        let s = cfg.f32_attr("s")?;
        Some(Self {
            s,
            length: num("length", 8.0).max(2.0),
            half_width: num("width", 8.0).max(2.0) * 0.5,
            lateral: num("lateral", 0.0),
        })
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
    /// Lombas declaráveis (`<TrackRamp>`).
    pub ramps: Vec<TrackRamp>,
    /// Zonas de boost (`<BoostPad>`).
    pub pads: Vec<BoostPad>,
}

impl TrackInfo {
    /// Offset vertical (m) e rampa ativa num arco `s` — a lomba soma à
    /// superfície de condução e termina em lábio (a queda lança o kart).
    /// `lateral` é o desvio lateral do ponto à linha (m).
    pub fn ramp_offset(&self, s: f32, lateral: f32) -> (f32, Option<&TrackRamp>) {
        for ramp in &self.ramps {
            let t = s - ramp.s;
            let t = if t < -4.0 { t + self.lap_length } else { t };
            if (0.0..=ramp.length).contains(&t) && (lateral - ramp.lateral).abs() <= ramp.half_width
            {
                // smoothstep: sobe suave e corta no lábio (queda seca a seguir).
                let k = (t / ramp.length).clamp(0.0, 1.0);
                let k = k * k * (3.0 - 2.0 * k);
                return (ramp.height * k, Some(ramp));
            }
        }
        (0.0, None)
    }

    /// A zona de boost sob (s, lateral), se houver.
    pub fn boost_pad_at(&self, s: f32, lateral: f32) -> bool {
        self.pads.iter().any(|pad| {
            let t = s - pad.s;
            let t = if t < -4.0 { t + self.lap_length } else { t };
            (0.0..=pad.length).contains(&t) && (lateral - pad.lateral).abs() <= pad.half_width
        })
    }
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
            ramps: Vec::new(),
            pads: Vec::new(),
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

/// Alinha a grelha UMA vez e faz correr a máquina de estados. [R] reinicia
/// a corrida INTEIRA (grelha + countdown + cronómetros).
pub fn race_director_system(
    time: Res<Time>,
    keys: Res<ButtonInput<KeyCode>>,
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
    // [R] = reinício total (volta ao grid; re-conta countdown e voltas).
    if keys.just_pressed(KeyCode::KeyR) && director.state != RaceState::Grid {
        director.state = RaceState::Grid;
        director.race_time = 0.0;
        director.player_result = None;
        director.session_best = None;
        director.message = Some(("REINÍCIO".to_string(), 1.4));
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
                // Convenção da engine: forward = (sin h, cos h) → h = atan2(tx, tz).
                let heading = tangent.x.atan2(tangent.y);
                if let Ok((_, mut transform, mut vehicle, mut progress)) = karts.get_mut(*entity) {
                    transform.translation = Vec3::new(at.x, y, at.y);
                    transform.rotation = Quat::from_rotation_y(heading);
                    vehicle.heading = heading;
                    vehicle.speed = 0.0;
                    vehicle.racing = false;
                    // A grelha está ANTES da linha: o progresso começa
                    // negativo e o lap 1 abre ao cruzar a meta no GO.
                    progress.lap = 0;
                    progress.total = config.grid_s.min(0.0);
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
        let s = hit.station(road.stations.len());
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
            ramps: Vec::new(),
            pads: Vec::new(),
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

/// Coleta `<TrackRamp s length height width lateral>` e `<BoostPad s length
/// width lateral>` do mundo para o [`TrackInfo`] (uma vez).
pub fn collect_track_fx(
    mut done: Local<bool>,
    configs: Option<Res<crate::worldsys::EngineConfigs>>,
    mut info: ResMut<TrackInfo>,
) {
    if *done || info.road.is_none() {
        return;
    }
    let Some(configs) = configs else {
        return;
    };
    for cfg in configs.all("trackramp") {
        if let Some(ramp) = TrackRamp::from_engine(cfg) {
            info!(target: "viber::racing", "track ramp: s={:.0} len={:.0} h={:.1}", ramp.s, ramp.length, ramp.height);
            info.ramps.push(ramp);
        }
    }
    for cfg in configs.all("boostpad") {
        if let Some(pad) = BoostPad::from_engine(cfg) {
            info!(target: "viber::racing", "boost pad: s={:.0} len={:.0}", pad.s, pad.length);
            info.pads.push(pad);
        }
    }
    *done = true;
}

/// Visuais das rampas (cunha) e pads (placa luminosa) — spawnados UMA vez
/// com o traçado pronto, filhos do mundo, poses derivadas do arco.
pub fn spawn_track_props(
    mut commands: Commands,
    mut done: Local<bool>,
    info: Option<Res<TrackInfo>>,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    if *done {
        return;
    }
    let Some(info) = info else {
        return;
    };
    if info.road.is_none() || (info.ramps.is_empty() && info.pads.is_empty()) {
        return;
    }
    *done = true;
    for ramp in &info.ramps {
        let Some((station, tangent)) = info.at(ramp.s) else {
            continue;
        };
        let perp = Vec2::new(-tangent.y, tangent.x);
        let center = station + perp * ramp.lateral;
        let yaw = tangent.y.atan2(tangent.x);
        // Cunha: comprimento `length` no sentido da pista, largura 2·half,
        // subindo `height` até o lábio. Malha própria (prisma triangular).
        let (l, w, h) = (ramp.length, ramp.half_width * 2.0, ramp.height);
        let mut positions = Vec::with_capacity(18);
        // 0..3: triângulo lateral esquerdo (x=-w/2), 3..6: direito, 6..11:
        // rampa inclinada (2 tris + topo), 11..16: traseira + base.
        let (x0, x1) = (-w / 2.0, w / 2.0);
        let (z0, z1) = (-l / 2.0, l / 2.0); // z = ao longo da pista
        let apex = |x| [x, h, z1];
        let base = |x| [x, 0.0, z0];
        // lateral esquerdo
        positions.push([x0, 0.0, z0]);
        positions.push(apex(x0));
        positions.push(base(x0));
        // lateral direito
        positions.push(base(x1));
        positions.push(apex(x1));
        positions.push([x1, 0.0, z0]);
        // rampa (superfície inclinada): 2 tris
        positions.push(base(x0));
        positions.push(apex(x0));
        positions.push(apex(x1));
        positions.push(base(x0));
        positions.push(apex(x1));
        positions.push(base(x1));
        // traseira vertical (no lábio)
        positions.push(apex(x0));
        positions.push([x0, 0.0, z1]);
        positions.push([x1, 0.0, z1]);
        positions.push(apex(x0));
        positions.push([x1, 0.0, z1]);
        positions.push(apex(x1));
        let normals: Vec<[f32; 3]> = (0..18).map(|_| [0.0, 1.0, 0.0]).collect();
        let uvs: Vec<[f32; 2]> = (0..18).map(|_| [0.0, 0.0]).collect();
        let colors: Vec<[f32; 4]> = (0..18).map(|_| [1.0; 4]).collect();
        let indices: bevy::mesh::Indices = bevy::mesh::Indices::U32((0..18u32).collect());
        let mesh = bevy::mesh::Mesh::new(
            bevy::mesh::PrimitiveTopology::TriangleList,
            bevy::asset::RenderAssetUsages::MAIN_WORLD,
        )
        .with_inserted_attribute(bevy::mesh::Mesh::ATTRIBUTE_POSITION, positions)
        .with_inserted_attribute(bevy::mesh::Mesh::ATTRIBUTE_NORMAL, normals)
        .with_inserted_attribute(bevy::mesh::Mesh::ATTRIBUTE_UV_0, uvs)
        .with_inserted_attribute(bevy::mesh::Mesh::ATTRIBUTE_COLOR, colors)
        .with_inserted_indices(indices);
        let mesh_handle = meshes.add(mesh);
        let material = materials.add(StandardMaterial {
            base_color: Color::srgb(0.85, 0.45, 0.15),
            perceptual_roughness: 0.6,
            double_sided: true,
            ..StandardMaterial::default()
        });
        commands.spawn((
            Name::new(format!("ramp-s{}", ramp.s as i32)),
            Mesh3d(mesh_handle),
            MeshMaterial3d(material),
            Transform::from_xyz(center.x, 0.2, center.y).with_rotation(Quat::from_rotation_y(yaw)),
            Visibility::Inherited,
        ));
    }
    for pad in &info.pads {
        let Some((station, tangent)) = info.at(pad.s + pad.length * 0.5) else {
            continue;
        };
        let perp = Vec2::new(-tangent.y, tangent.x);
        let center = station + perp * pad.lateral;
        let yaw = tangent.y.atan2(tangent.x);
        let mesh_handle = meshes.add(Mesh::from(Plane3d {
            half_size: Vec2::new(pad.half_width * 2.0, pad.length),
            ..Plane3d::default()
        }));
        let material = materials.add(StandardMaterial {
            base_color: Color::srgb(0.15, 0.85, 1.0),
            emissive: LinearRgba::rgb(0.35, 1.4, 2.2),
            perceptual_roughness: 0.4,
            ..StandardMaterial::default()
        });
        commands.spawn((
            Name::new(format!("boostpad-s{}", pad.s as i32)),
            Mesh3d(mesh_handle),
            MeshMaterial3d(material),
            // Plane3d nasce no plano XY a olhar +Z: deita (-90° X) e vira
            // para o rumo da pista; o z local do plano percorre o comprimento.
            Transform::from_xyz(center.x, 0.35, center.y).with_rotation(
                Quat::from_rotation_y(yaw) * Quat::from_rotation_x(-std::f32::consts::FRAC_PI_2),
            ),
            Visibility::Inherited,
        ));
    }
}

#[cfg(test)]
mod probe_tests {
    use super::*;
    use crate::terrain::paths::chaikin_smooth_channels;

    #[test]
    fn probe_arc_length_of_racer_track() {
        let raw: Vec<Vec2> = vec![
            (-470., -470.),
            (-280., -460.),
            (-100., -445.),
            (60., -428.),
            (230., -390.),
            (360., -320.),
            (440., -200.),
            (470., -110.),
            (480., -20.),
            (430., 70.),
            (330., 140.),
            (200., 165.),
            (60., 140.),
            (-60., 90.),
            (-190., 40.),
            (-290., 60.),
            (-430., 120.),
            (-520., 260.),
            (-460., 400.),
            (-300., 490.),
            (-120., 520.),
            (40., 540.),
            (180., 570.),
            (320., 560.),
            (450., 520.),
            (540., 430.),
            (520., 300.),
            (470., 190.),
            (400., 110.),
            (330., 30.),
            (300., -60.),
            (250., -160.),
            (120., -230.),
            (-60., -280.),
            (-250., -300.),
            (-420., -300.),
            (-560., -330.),
            (-640., -390.),
            (-600., -450.),
        ]
        .into_iter()
        .map(|(x, z)| Vec2::new(x, z))
        .collect();
        let mut e = vec![0.0; raw.len()];
        let mut w = vec![18.0; raw.len()];
        let mut smoothed = chaikin_smooth_channels(&raw, &mut [&mut e, &mut w], 2, true);
        let before = path_length(&smoothed);
        smoothed.push(smoothed[0]);
        let after = path_length(&smoothed);
        let stations =
            crate::terrain::paths::resample_channels(&smoothed, &mut [&mut e, &mut w], 2.734);
        let lap = path_length(&stations);
        // O circuito bakado mede ~5,1 km (o log de arranque diz "volta de
        // 5123 m") — o traçado autorado tem de bater com isso.
        assert!(
            (5300.0..=5360.0).contains(&after),
            "arco chaikin do traçado: {after}"
        );
        assert!((5100.0..=5200.0).contains(&lap), "volta resampled: {lap}");
        assert!(stations.len() > 1900, "estações: {}", stations.len());
        let _ = before;
    }
}
