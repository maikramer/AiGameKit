//! Profiler da engine — o análogo nativo do `?profiler=1` do VibeGame:
//! FPS/frame-time em janela deslizante, contadores de jogo (entidades,
//! scripts Luau ativos dentro do raio de congelamento, emissores de
//! partículas, chunks de terreno) e um overlay `bevy_ui` togglável com
//! **F3** (visível por omissão). O mesmo snapshot é exposto pela debug
//! bridge como `viber.profiler` e pelo cliente `viber debug prof`, para QA
//! headless sem abrir o overlay.
//!
//! `VIBER_PROF_LOG=1` liga o `LogDiagnosticsPlugin` (fps/frame_time/entity_
//! count no terminal a cada segundo — as entradas caem no ring-buffer da
//! bridge quando `--bridge` está activo).

use std::collections::VecDeque;

use bevy::diagnostic::{
    Diagnostic, DiagnosticsStore, EntityCountDiagnosticsPlugin, FrameTimeDiagnosticsPlugin,
    LogDiagnosticsPlugin,
};
use bevy::prelude::*;
use serde_json::json;

use crate::luau::{LuaScriptRef, ScriptActivation};
use crate::particles::ParticleEmitter;
use crate::player::Player;
use crate::terrain::plugin::TerrainChunk;

/// Frames guardados na janela deslizante (~3 s a 60 fps).
const FRAME_WINDOW: usize = 180;
/// Refrescamento do texto do overlay (s) — evitar reescrever texto a 60 fps.
const OVERLAY_REFRESH: f32 = 0.25;

/// Marker da raiz do overlay (toggle de visibilidade).
#[derive(Component)]
struct ProfilerOverlay;

/// Marker do nó de texto (corpo reescrito a cada refrescamento).
#[derive(Component)]
struct ProfilerText;

/// Estado do overlay: refresh throttle + visibilidade (**F3** alterna).
#[derive(Resource)]
struct HudState {
    timer: f32,
    visible: bool,
}

impl Default for HudState {
    fn default() -> Self {
        Self {
            timer: 0.0,
            visible: true,
        }
    }
}

/// Janela deslizante de frame-times (ms) — média/pico/min-fps recentes,
/// independentes do histórico exponencial do `DiagnosticsStore`.
#[derive(Resource, Default)]
pub struct FrameStats {
    samples: VecDeque<f32>,
}

impl FrameStats {
    /// Registra um frame-time em ms, evictando fora da janela.
    pub fn push(&mut self, frame_ms: f32) {
        if self.samples.len() == FRAME_WINDOW {
            self.samples.pop_front();
        }
        self.samples.push_back(frame_ms);
    }

    fn avg_ms(&self) -> Option<f32> {
        if self.samples.is_empty() {
            return None;
        }
        let n = self.samples.len();
        Some(self.samples.iter().sum::<f32>() / n as f32)
    }

    fn max_ms(&self) -> Option<f32> {
        self.samples
            .iter()
            .fold(None, |acc: Option<f32>, &v| Some(acc.map_or(v, |m| m.max(v))))
    }

    /// Pior fps da janela (`1000/max_ms`); `None` sem amostras.
    pub fn min_fps(&self) -> Option<f32> {
        self.max_ms().map(|ms| if ms > 0.0 { 1000.0 / ms } else { 0.0 })
    }
}

/// Contadores de jogo para overlay/snapshot — recolhidos das queries.
#[derive(Debug, Clone, Copy, Default)]
pub struct GameCounters {
    pub entities: usize,
    pub scripts_total: usize,
    /// Scripts cujo `ScriptActivation` inclui o player (ou sem raio: sempre
    /// activos). É o "LOD de IA" — o resto está congelado.
    pub scripts_active: usize,
    pub particle_emitters: usize,
    pub terrain_chunks: usize,
}

/// Uma linha por script para [`count_active_scripts`]: posição global e raio
/// de activação (`None` = sem componente).
pub type ScriptRow = (Option<Vec3>, Option<f32>);

/// Conta scripts activos: entidade com `ScriptActivation` conta quando o
/// player está dentro do raio; sem componente, conta sempre (POIs/interações
/// baratas). Sem player no mundo, nada com raio está activo.
pub fn count_active_scripts(player_pos: Option<Vec3>, scripts: &[ScriptRow]) -> usize {
    scripts
        .iter()
        .filter(|&&(pos, radius)| match (radius, player_pos) {
            (None, _) => true,
            (Some(_), None) => false,
            (Some(r), Some(p)) => pos.is_some_and(|s| s.distance_squared(p) <= r * r),
        })
        .count()
}

/// Recolhe os contadores de um mundo real (queries por archetype, barato).
pub fn collect_counters(world: &mut World) -> GameCounters {
    let entities = world.entities().count_spawned() as usize;

    let player_pos: Option<Vec3> = {
        let mut q = world.query_filtered::<&GlobalTransform, With<Player>>();
        q.iter(world).next().map(GlobalTransform::translation)
    };

    let mut scripts_total = 0usize;
    let mut script_rows: Vec<ScriptRow> = Vec::new();
    let mut q_scripts = world.query_filtered::<
        (Option<&GlobalTransform>, Option<&ScriptActivation>),
        With<LuaScriptRef>,
    >();
    for (transform, activation) in q_scripts.iter(world) {
        scripts_total += 1;
        script_rows.push((transform.map(GlobalTransform::translation), activation.map(|a| a.radius)));
    }

    let mut q_emitters = world.query_filtered::<(), With<ParticleEmitter>>();
    let particle_emitters = q_emitters.iter(world).count();

    let mut q_chunks = world.query_filtered::<(), With<TerrainChunk>>();
    let terrain_chunks = q_chunks.iter(world).count();

    GameCounters {
        entities,
        scripts_total,
        scripts_active: count_active_scripts(player_pos, &script_rows),
        particle_emitters,
        terrain_chunks,
    }
}

/// FPS/frame-time suavizados do `DiagnosticsStore` (`None` sem o plugin).
fn diagnostics_snapshot(store: &DiagnosticsStore) -> (Option<f64>, Option<f64>) {
    (
        store
            .get(&FrameTimeDiagnosticsPlugin::FPS)
            .and_then(Diagnostic::smoothed),
        store
            .get(&FrameTimeDiagnosticsPlugin::FRAME_TIME)
            .and_then(Diagnostic::average),
    )
}

/// Snapshot JSON do profiler — o corpo do método `viber.profiler`.
pub fn snapshot(world: &mut World) -> serde_json::Value {
    let counters = collect_counters(world);
    let (fps, frame_ms_avg) = match world.get_resource::<DiagnosticsStore>() {
        Some(store) => diagnostics_snapshot(store),
        None => (None, None),
    };
    let (avg, max, min_fps) = world
        .get_resource::<FrameStats>()
        .map(|s| (s.avg_ms(), s.max_ms(), s.min_fps()))
        .unwrap_or((None, None, None));
    let uptime = world
        .get_resource::<Time>()
        .map(Time::elapsed_secs)
        .unwrap_or(0.0);

    json!({
        "fps": fps,
        "frame_ms_avg": frame_ms_avg,
        "frame_ms": { "avg": avg, "max": max },
        "min_fps_window": min_fps,
        "entities": counters.entities,
        "scripts": {
            "total": counters.scripts_total,
            "active": counters.scripts_active,
        },
        "particle_emitters": counters.particle_emitters,
        "terrain_chunks": counters.terrain_chunks,
        "uptime_s": uptime,
    })
}

/// Corpo do overlay — pura para os testes (uma linha por métrica).
pub fn overlay_body(
    fps: Option<f64>,
    stats: (Option<f32>, Option<f32>, Option<f32>),
    counters: &GameCounters,
    uptime_s: f32,
) -> String {
    let fps_text = fps
        .map(|v| format!("FPS {v:.0}"))
        .unwrap_or_else(|| "FPS —".into());
    let min_text = stats
        .2
        .map(|v| format!("  pior {v:.0}"))
        .unwrap_or_default();
    let frame_text = stats
        .0
        .map(|avg| format!("frame {avg:.1} ms"))
        .unwrap_or_else(|| "frame —".into());
    let max_text = stats
        .1
        .map(|v| format!("  pico {v:.1}"))
        .unwrap_or_default();
    format!(
        "{fps_text}{min_text}\n{frame_text}{max_text}\nentidades {}\nscripts {} (ativos {})\npartículas {} emissores\nterreno {} chunks\nuptime {uptime_s:.0} s\nF3 esconde",
        counters.entities,
        counters.scripts_total,
        counters.scripts_active,
        counters.particle_emitters,
        counters.terrain_chunks,
    )
}

/// Plugin do profiler: diagnósticos canónicos + janela deslizante + overlay.
pub struct ProfilerPlugin;

impl Plugin for ProfilerPlugin {
    fn build(&self, app: &mut App) {
        app.add_plugins((
            FrameTimeDiagnosticsPlugin::default(),
            EntityCountDiagnosticsPlugin {
                max_history_length: 120,
            },
        ));
        if std::env::var_os("VIBER_PROF_LOG").is_some() {
            app.add_plugins(LogDiagnosticsPlugin::default());
        }
        app.init_resource::<FrameStats>()
            .init_resource::<HudState>()
            .add_systems(Startup, spawn_overlay)
            .add_systems(Update, (record_frame_time, profiler_overlay_update).chain());
    }
}

fn record_frame_time(time: Res<Time>, mut stats: ResMut<FrameStats>) {
    stats.push(time.delta_secs() * 1000.0);
}

fn spawn_overlay(mut commands: Commands) {
    commands
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(8.0),
                right: Val::Px(8.0),
                padding: UiRect::all(Val::Px(8.0)),
                flex_direction: FlexDirection::Column,
                border_radius: BorderRadius::all(Val::Px(6.0)),
                ..Default::default()
            },
            BackgroundColor(Color::srgba(0.06, 0.06, 0.06, 0.72)),
            Name::new("profiler"),
            ProfilerOverlay,
        ))
        .with_children(|panel| {
            panel.spawn((
                Text::new(""),
                TextColor(Color::srgba(0.86, 0.90, 0.82, 1.0)),
                TextFont::from_font_size(13.0),
                ProfilerText,
            ));
        });
}

/// Toggle **F3** + reescrita do corpo do overlay (throttle `OVERLAY_REFRESH`).
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn profiler_overlay_update(
    mut state: ResMut<HudState>,
    time: Res<Time>,
    keys: Res<ButtonInput<KeyCode>>,
    mut q_text: Query<&mut Text, With<ProfilerText>>,
    mut q_root: Query<&mut Visibility, With<ProfilerOverlay>>,
    stats: Res<FrameStats>,
    diagnostics: Option<Res<DiagnosticsStore>>,
    players: Query<&GlobalTransform, With<Player>>,
    scripts: Query<
        (Option<&GlobalTransform>, Option<&ScriptActivation>),
        With<LuaScriptRef>,
    >,
    entities: Query<Entity>,
    emitters: Query<(), With<ParticleEmitter>>,
    chunks: Query<(), With<TerrainChunk>>,
) {
    if keys.just_pressed(KeyCode::F3) {
        state.visible = !state.visible;
    }
    let wanted = if state.visible {
        Visibility::Inherited
    } else {
        Visibility::Hidden
    };
    for mut vis in &mut q_root {
        if *vis != wanted {
            *vis = wanted;
        }
    }
    if !state.visible {
        return;
    }

    state.timer += time.delta_secs();
    if state.timer < OVERLAY_REFRESH {
        return;
    }
    state.timer = 0.0;

    let fps = diagnostics
        .as_deref()
        .map(diagnostics_snapshot)
        .and_then(|(fps, _)| fps);
    let frame_stats = (stats.avg_ms(), stats.max_ms(), stats.min_fps());

    let player_pos = players.iter().next().map(GlobalTransform::translation);
    let rows: Vec<ScriptRow> = scripts
        .iter()
        .map(|(t, a)| (t.map(GlobalTransform::translation), a.map(|act| act.radius)))
        .collect();
    let counters = GameCounters {
        entities: entities.iter().count(),
        scripts_total: rows.len(),
        scripts_active: count_active_scripts(player_pos, &rows),
        particle_emitters: emitters.iter().count(),
        terrain_chunks: chunks.iter().count(),
    };
    let body = overlay_body(fps, frame_stats, &counters, time.elapsed_secs());
    if let Ok(mut text) = q_text.single_mut() {
        *text = Text::new(body);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_frame_stats_window() {
        let mut stats = FrameStats::default();
        assert_eq!(stats.avg_ms(), None);
        for ms in [16.0f32, 18.0, 20.0] {
            stats.push(ms);
        }
        assert!((stats.avg_ms().unwrap() - 18.0).abs() < 1e-4);
        assert!((stats.max_ms().unwrap() - 20.0).abs() < 1e-4);
        assert!((stats.min_fps().unwrap() - 50.0).abs() < 1e-3);
        // janela evicta: 180+ amostras de 1 ms apagam os valores antigos
        for _ in 0..(FRAME_WINDOW + 40) {
            stats.push(1.0);
        }
        assert!((stats.avg_ms().unwrap() - 1.0).abs() < 1e-4);
    }

    #[test]
    fn test_frame_stats_min_fps_guards_zero() {
        let mut stats = FrameStats::default();
        stats.push(0.0);
        assert_eq!(stats.min_fps(), Some(0.0), "ms=0 não pode virar infinito");
    }

    fn row(x: f32, radius: Option<f32>) -> ScriptRow {
        (Some(Vec3::new(x, 0.0, 0.0)), radius)
    }

    #[test]
    fn test_active_scripts_radius() {
        let scripts = vec![
            row(10.0, Some(45.0)),  // dentro
            row(100.0, Some(45.0)), // fora
            row(500.0, None),       // sem raio: sempre activo
        ];
        let player = Some(Vec3::ZERO);
        assert_eq!(count_active_scripts(player, &scripts), 2);
        // sem player: só os sem-raio contam (congelamento total)
        assert_eq!(count_active_scripts(None, &scripts), 1);
    }

    #[test]
    fn test_overlay_body_lines() {
        let counters = GameCounters {
            entities: 15432,
            scripts_total: 96,
            scripts_active: 12,
            particle_emitters: 35,
            terrain_chunks: 481,
        };
        let body = overlay_body(
            Some(60.4),
            (Some(16.6), Some(32.1), Some(41.3)),
            &counters,
            123.4,
        );
        assert!(body.contains("FPS 60"), "{body}");
        assert!(body.contains("pior 41"), "{body}");
        assert!(body.contains("frame 16.6 ms"), "{body}");
        assert!(body.contains("entidades 15432"), "{body}");
        assert!(body.contains("scripts 96 (ativos 12)"), "{body}");
        assert!(body.contains("F3"), "{body}");
    }

    #[test]
    fn test_snapshot_from_world() {
        let mut world = World::new();
        world.init_resource::<FrameStats>();
        world.init_resource::<DiagnosticsStore>();
        world.init_resource::<Time>();
        // dois scripts sem player: o de raio fica inactivo, o sem raio activo
        world.spawn((
            GlobalTransform::default(),
            LuaScriptRef {
                path: "slime.lua".into(),
            },
            ScriptActivation::default(),
        ));
        world.spawn((
            GlobalTransform::default(),
            LuaScriptRef {
                path: "well.lua".into(),
            },
        ));

        let snap = snapshot(&mut world);
        assert_eq!(snap["scripts"]["total"], 2, "{snap}");
        assert_eq!(snap["scripts"]["active"], 1, "{snap}");
        assert!(snap["uptime_s"].as_f64().is_some(), "{snap}");
        assert!(snap["frame_ms"]["avg"].is_null(), "sem amostras ainda");

        // entidades: recursos vivem como entidades internas no Bevy 0.16+, por
        // isso o teste compara o DELTA (agnóstico aos internals)
        let before = snap["entities"].as_u64().expect("entities numérico");
        world.spawn((GlobalTransform::default(), LuaScriptRef { path: "x".into() }));
        let after = snapshot(&mut world)["entities"].as_u64().expect("numérico");
        assert_eq!(after - before, 1, "+1 entidade por spawn");
    }
}
