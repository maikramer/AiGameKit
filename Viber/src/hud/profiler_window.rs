//! Janela do profiler (tecla **P**): painel lateral com gráfico de
//! frame-times, estatísticas ao vivo (fps/ms/entidades/scripts/partículas/
//! chunks) e posições de câmera/player. Consome os dados públicos do
//! módulo `profiler` (`FrameStats`, `collect_counters`, `DiagnosticsStore`).

use bevy::prelude::*;

use super::assets::{HudAssets, gradient_overlay, label, panel_base, panel_edge, panel_shadow};
use super::widgets::{GraphBar, StatValue, sparkline, stat_row};

/// Tipos de valor que a janela actualiza (marcadores [`StatValue`]).
mod kinds {
    pub const FPS: usize = 0;
    pub const MS: usize = 1;
    pub const ENTITIES: usize = 2;
    pub const SCRIPTS: usize = 3;
    pub const SCRIPTS_ACTIVE: usize = 4;
    pub const PARTICLES: usize = 5;
    pub const CHUNKS: usize = 6;
    pub const CAM: usize = 7;
    pub const PLAYER: usize = 8;
    pub const SPEED: usize = 10;
    pub const UPTIME: usize = 9;
}

/// Janela raiz (visibilidade alterna com **P**).
#[derive(Component)]
struct ProfilerWindow;

/// Estado da janela: aberta? histórico de frame-times, refresh.
#[derive(Resource)]
struct ProfilerWindowState {
    open: bool,
    history: Vec<f32>,
    head: usize,
    filled: usize,
    refresh: f32,
}

const HISTORY_SLOTS: usize = 60;
const REFRESH_SECS: f32 = 0.2;

/// Constrói a janela (chamado uma vez pelo `HudScreenLayer`).
pub fn build_profiler_window(world: &mut World, hud: &HudAssets) {
    if world.get_resource::<ProfilerWindowState>().is_none() {
        world.insert_resource(ProfilerWindowState {
            open: false,
            history: vec![0.0; HISTORY_SLOTS],
            head: 0,
            filled: 0,
            refresh: 0.0,
        });
    }
    world
        .spawn((
            Node {
                position_type: PositionType::Absolute,
                top: Val::Px(172.0),
                right: Val::Px(14.0),
                width: Val::Px(300.0),
                flex_direction: FlexDirection::Column,
                padding: UiRect::all(Val::Px(14.0)),
                border: UiRect::all(Val::Px(1.5)),
                border_radius: BorderRadius::all(Val::Px(16.0)),
                ..Default::default()
            },
            panel_base(),
            panel_edge(),
            panel_shadow(),
            Visibility::Hidden,
            Name::new("hud:profiler"),
            ProfilerWindow,
        ))
        .with_children(|p| {
            p.spawn(gradient_overlay(hud, 16.0));
        })
        .with_children(|panel| {
            panel.spawn((
                Node {
                    margin: UiRect::bottom(Val::Px(8.0)),
                    ..Default::default()
                },
                label(hud, "PROFILER", 18.0, Color::srgb(0.95, 0.85, 0.6)),
            ));
            panel.spawn((
                Node {
                    margin: UiRect::bottom(Val::Px(10.0)),
                    ..Default::default()
                },
                label(
                    hud,
                    "P alterna · valores ao vivo",
                    10.0,
                    Color::srgba(0.85, 0.82, 0.74, 0.6),
                ),
            ));
            sparkline(panel, HISTORY_SLOTS, 3.0, 2.0, 44.0);
            // Estatísticas: (rótulo, valor inicial, kind).
            for (label_text, kind) in [
                ("fps", kinds::FPS),
                ("frame", kinds::MS),
                ("entidades", kinds::ENTITIES),
                ("scripts", kinds::SCRIPTS),
                ("scripts activos", kinds::SCRIPTS_ACTIVE),
                ("partículas (emissores)", kinds::PARTICLES),
                ("terreno (chunks)", kinds::CHUNKS),
                ("câmera", kinds::CAM),
                ("player", kinds::PLAYER),
                ("velocidade", kinds::PLAYER + 1000),
                ("uptime", kinds::UPTIME),
            ] {
                stat_row(panel, hud, label_text, "—", kind, 272.0);
            }
        });
}

/// Toggle **P** + atualização ao vivo: histórico de frame-times, barras do
/// gráfico (altura ∝ ms, cor por orçamento) e textos das estatísticas.
/// Sistema exclusivo: lê o mundo inteiro (contadores + câmera + player) e
/// os textos da janela num só passe, a cada [`REFRESH_SECS`].
pub fn hud_profiler_window(world: &mut World) {
    let dt_ms = world.resource::<Time>().delta_secs() * 1000.0;
    let toggle = world
        .resource::<ButtonInput<KeyCode>>()
        .just_pressed(KeyCode::KeyP);

    // 1. Amostra o frame e avança o histórico.
    if let Some(mut state) = world.get_resource_mut::<ProfilerWindowState>() {
        if toggle {
            state.open = !state.open;
        }
        let head = state.head;
        state.history[head] = dt_ms;
        state.head = (head + 1) % state.history.len();
        state.filled = state.filled.max(state.head);
        state.refresh += dt_ms / 1000.0;
    }
    let open = world.resource::<ProfilerWindowState>().open;

    // 2. Visibilidade da janela.
    let mut windows = world.query::<(&mut Visibility, &ProfilerWindow)>();
    for (mut visibility, _) in windows.iter_mut(world) {
        *visibility = if open {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
    }
    if !open {
        return;
    }

    // 3. Refresh throttle dos textos/barras.
    let should_refresh = world.resource::<ProfilerWindowState>().refresh >= REFRESH_SECS;
    if should_refresh {
        world.resource_mut::<ProfilerWindowState>().refresh = 0.0;
    } else {
        return;
    }

    // ---- dados ----
    let fps = world
        .get_resource::<bevy::diagnostic::DiagnosticsStore>()
        .and_then(|d| {
            d.get(&bevy::diagnostic::FrameTimeDiagnosticsPlugin::FPS)
                .and_then(|v| v.smoothed())
        })
        .unwrap_or(0.0);
    let counters = crate::profiler::collect_counters(world);
    let cam_pos = world
        .query_filtered::<&GlobalTransform, With<Camera>>()
        .iter(world)
        .next()
        .map(|t| t.translation())
        .unwrap_or_default();
    let (player_pos, player_speed) = world
        .query_filtered::<(&GlobalTransform, &crate::player::Player), With<crate::player::Player>>()
        .iter(world)
        .next()
        .map(|(t, p)| {
            (
                t.translation(),
                (p.vel_x * p.vel_x + p.vel_z * p.vel_z).sqrt(),
            )
        })
        .unwrap_or((Vec3::ZERO, 0.0));
    let uptime = world.resource::<Time>().elapsed_secs();

    // ---- barras do gráfico (mais recente à direita) ----
    let (history, head) = {
        let s = world.resource::<ProfilerWindowState>();
        (s.history.clone(), s.head)
    };
    let mut bars = world.query::<(&mut Node, &mut BackgroundColor, &GraphBar)>();
    for (mut node, mut color, bar) in bars.iter_mut(world) {
        let slot = if bar.slot < head {
            bar.slot + history.len() - head
        } else {
            bar.slot - head
        };
        let ms = history[slot.min(history.len() - 1)];
        let frac = (ms / 33.3).clamp(0.05, 1.0);
        node.height = Val::Px(4.0 + 40.0 * frac);
        *color = BackgroundColor(if ms <= 16.7 {
            Color::srgb(0.36, 0.72, 0.30)
        } else if ms <= 33.3 {
            Color::srgb(0.92, 0.75, 0.2)
        } else {
            Color::srgb(0.9, 0.32, 0.22)
        });
    }

    // ---- textos ----
    let fmt_pos = |p: Vec3| format!("{:.1}, {:.1}, {:.1}", p.x, p.y, p.z);
    let values: Vec<(usize, String)> = vec![
        (kinds::FPS, format!("{fps:.0}")),
        (kinds::MS, format!("{dt_ms:.1} ms")),
        (kinds::ENTITIES, counters.entities.to_string()),
        (kinds::SCRIPTS, counters.scripts_total.to_string()),
        (kinds::SCRIPTS_ACTIVE, counters.scripts_active.to_string()),
        (kinds::PARTICLES, counters.particle_emitters.to_string()),
        (kinds::CHUNKS, counters.terrain_chunks.to_string()),
        (kinds::CAM, fmt_pos(cam_pos)),
        (kinds::PLAYER, fmt_pos(player_pos)),
        (kinds::SPEED, format!("{player_speed:.1} m/s")),
        (kinds::UPTIME, format!("{uptime:.0} s")),
    ];
    let mut stats = world.query::<(&StatValue, &mut Text)>();
    for (stat, mut text) in stats.iter_mut(world) {
        if let Some((_, value)) = values.iter().find(|(k, _)| *k == stat.kind) {
            if text.0 != *value {
                text.0 = value.clone();
            }
        }
    }
}
