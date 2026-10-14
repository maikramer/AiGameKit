//! The Profiler dock (Fase 5, Debugger): polls `viber.debug.prof()` on the
//! RUNNING game through the bridge every ~1.5 s — fps, frame ms (avg/p95) and
//! the top systems by average time (the same snapshot `viber debug prof
//! --tab sistemas` serves). The panel is lean text: name, group, avg, p95,
//! with the engine's hot threshold flagged.

use bevy::prelude::*;
use bevy::tasks::{Task, block_on, poll_once};
use std::time::Duration;

use crate::bridge::{BrpClientError, EngineStatus, ViberBridge, methods};

/// How often the running game is polled.
const POLL_INTERVAL: Duration = Duration::from_millis(1500);
/// Rows kept (top systems by avg).
const TOP: usize = 14;

/// The dock's scroll body.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ProfilerBody;

/// One rendered line.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ProfilerLine;

#[derive(Resource, Debug, Default)]
pub struct ProfilerState {
    task: Option<Task<Result<serde_json::Value, BrpClientError>>>,
    /// The latest snapshot, already formatted into lines.
    lines: Vec<(String, bool)>,
    dirty: bool,
    timer: Timer,
}

pub struct ProfilerPlugin;

impl Plugin for ProfilerPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<ProfilerState>()
            .add_systems(Update, (poll_profiler, render_profiler).chain());
    }
}

/// Polls `viber.debug.prof()` while the game runs; formats the snapshot.
fn poll_profiler(time: Res<Time>, bridge: Res<ViberBridge>, mut state: ResMut<ProfilerState>) {
    if !matches!(bridge.status, EngineStatus::Connected { .. }) {
        return;
    }
    if let Some(task) = &mut state.task
        && let Some(result) = block_on(poll_once(task))
    {
        state.task = None;
        match result {
            Ok(value) => {
                state.lines = format_snapshot(&value);
                state.dirty = true;
            }
            Err(error) => warn!("profiler: viber.debug.prof failed: {error}"),
        }
    }
    if state.task.is_none() && state.timer.tick(time.delta()).just_finished() {
        state.timer = Timer::new(POLL_INTERVAL, TimerMode::Repeating);
        state.task = Some(methods::lua_eval(
            &bridge.client,
            "return viber.debug.prof()",
        ));
    }
}

/// The snapshot as text lines: header, groups summary, top systems.
fn format_snapshot(value: &serde_json::Value) -> Vec<(String, bool)> {
    let mut out = Vec::new();
    let fps = value.get("fps").and_then(|v| v.as_f64());
    let frame = value.get("frame_ms");
    let avg = frame
        .and_then(|f| f.get("avg"))
        .and_then(|v| v.as_f64())
        .or_else(|| value.get("frame_ms_avg").and_then(|v| v.as_f64()));
    let p95 = frame.and_then(|f| f.get("p95")).and_then(|v| v.as_f64());
    let header = match (fps, avg) {
        (Some(fps), Some(avg)) => {
            let p95_text = p95.map(|p| format!(", p95 {p:.1} ms")).unwrap_or_default();
            format!("▶ {fps:.0} fps — frame {avg:.1} ms{p95_text}")
        }
        _ => "▶ sem amostras".to_string(),
    };
    out.push((header, false));
    if let Some(systems) = value.get("systems").and_then(|v| v.as_array()) {
        let mut rows: Vec<(String, f32, String)> = systems
            .iter()
            .filter_map(|system| {
                let avg = system.get("avg_ms")?.as_f64()? as f32;
                let name = system.get("name")?.as_str()?.to_string();
                let group = system
                    .get("group")
                    .and_then(|g| g.as_str())
                    .unwrap_or("?")
                    .to_string();
                Some((name, avg, group))
            })
            .collect();
        rows.sort_by(|a, b| b.1.total_cmp(&a.1));
        out.push((
            format!("{} sistema(s) — {} mais lentos", systems.len(), TOP),
            false,
        ));
        for (name, avg, group) in rows.into_iter().take(TOP) {
            let hot = avg >= 1.0;
            out.push((format!("{name}  [{group}]  {avg:.2} ms",), hot));
        }
    }
    if let Some(groups) = value.get("groups").and_then(|v| v.as_array()) {
        let names: Vec<String> = groups
            .iter()
            .filter_map(|group| group.get("group").and_then(|g| g.as_str()))
            .map(str::to_string)
            .collect();
        if !names.is_empty() {
            out.push((format!("groups: {}", names.join(" · ")), false));
        }
    }
    out
}

/// Rebuilds the lines when a new snapshot arrived.
fn render_profiler(
    mut commands: Commands,
    mut state: ResMut<ProfilerState>,
    bodies: Query<Entity, With<ProfilerBody>>,
) {
    if !state.dirty {
        return;
    }
    state.dirty = false;
    let Ok(body) = bodies.single() else {
        return;
    };
    commands.despawn_all::<With<ProfilerLine>>();
    for (text, hot) in &state.lines {
        commands.spawn((
            ProfilerLine,
            Text::new(text.clone()),
            TextFont::from_font_size(12.0),
            TextColor(if *hot {
                Color::srgb(0.95, 0.55, 0.45)
            } else {
                Color::srgb(0.82, 0.86, 0.9)
            }),
            Node {
                margin: UiRect::bottom(px(1)),
                ..default()
            },
            Pickable::IGNORE,
            ChildOf(body),
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn snapshot_formats_header_groups_and_hot_systems() {
        let value = json!({
            "fps": 59.5,
            "frame_ms": { "avg": 16.7, "p95": 21.3 },
            "groups": [
                { "group": "Update" },
                { "group": "FixedUpdate" }
            ],
            "systems": [
                { "name": "terrain::plugin", "group": "Update", "avg_ms": 2.5, "p95_ms": 4.0 },
                { "name": "luau::runtime", "group": "Update", "avg_ms": 0.4, "p95_ms": 0.6 },
                { "name": "grass::update", "group": "Update", "avg_ms": 1.2, "p95_ms": 1.9 }
            ]
        });
        let lines = format_snapshot(&value);
        let text: Vec<&str> = lines.iter().map(|(line, _)| line.as_str()).collect();
        assert!(
            text[0].contains("60 fps") || text[0].contains("59 fps"),
            "{text:?}"
        );
        assert!(text[0].contains("16.7 ms") && text[0].contains("21.3"));
        // os sistemas vêm ordenados por avg DESC; o quente (≥1 ms) marcado
        assert!(
            text.iter()
                .any(|line| line.contains("terrain::plugin") && line.contains("2.50"))
        );
        assert!(
            text.iter().position(|l| l.contains("terrain::plugin"))
                < text.iter().position(|l| l.contains("grass::update"))
        );
        assert!(
            text.iter().position(|l| l.contains("grass::update"))
                < text.iter().position(|l| l.contains("luau::runtime"))
        );
        let hot = lines.iter().filter(|(_, hot)| *hot).count();
        assert_eq!(hot, 2, "terrain (2.5) e grass (1.2) são quentes");
        assert!(
            text.iter()
                .any(|line| line.contains("Update · FixedUpdate"))
        );
    }

    #[test]
    fn empty_snapshot_degrades_to_a_placeholder() {
        let lines = format_snapshot(&json!({}));
        assert_eq!(lines.len(), 1);
        assert!(lines[0].0.contains("sem amostras"));
    }
}
