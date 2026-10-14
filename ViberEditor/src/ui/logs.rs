//! The engine log panel, fed by the bridge's `viber.logs` ring buffer.

use crate::bridge::BrpClientError;
use bevy::prelude::*;
use bevy::tasks::{Task, block_on, poll_once};
use std::time::Duration;

use crate::bridge::{EngineStatus, ViberBridge, methods};

/// How often the ring buffer is polled.
const POLL_INTERVAL: Duration = Duration::from_millis(500);
/// How many entries are kept in the panel.
const KEPT_ENTRIES: usize = 200;

/// Marks the node that holds the log lines.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct LogsBody;

/// Marks one rendered log line, so the whole set can be replaced in one command.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct LogLine;

#[derive(Resource)]
pub struct BridgeLogs {
    entries: Vec<methods::LogEntry>,
    dirty: bool,
    task: Option<Task<Result<serde_json::Value, BrpClientError>>>,
    next_poll: Timer,
    /// O dump da árvore remota (`viber.tree`) pedido pelo botão do dock.
    tree_task: Option<Task<Result<serde_json::Value, BrpClientError>>>,
}

impl Default for BridgeLogs {
    fn default() -> Self {
        Self {
            entries: Vec::new(),
            dirty: false,
            task: None,
            next_poll: Timer::new(POLL_INTERVAL, TimerMode::Repeating),
            tree_task: None,
        }
    }
}

/// Polls `viber.logs` while the engine bridge is connected.
pub fn poll_logs(time: Res<Time>, bridge: Res<ViberBridge>, mut logs: ResMut<BridgeLogs>) {
    if !matches!(bridge.status, EngineStatus::Connected { .. }) {
        return;
    }

    if let Some(task) = &mut logs.task
        && let Some(result) = block_on(poll_once(task))
    {
        logs.task = None;
        match result {
            Ok(value) => match serde_json::from_value::<Vec<methods::LogEntry>>(value) {
                Ok(entries) => {
                    let start = entries.len().saturating_sub(KEPT_ENTRIES);
                    let tail = entries[start..].to_vec();
                    if tail != logs.entries {
                        logs.entries = tail;
                        logs.dirty = true;
                    }
                }
                Err(error) => warn!("malformed viber.logs answer: {error}"),
            },
            Err(error) => warn!("viber.logs call failed: {error}"),
        }
    }

    if logs.task.is_none() && logs.next_poll.tick(time.delta()).just_finished() {
        logs.task = Some(methods::logs(&bridge.client, KEPT_ENTRIES));
    }

    // Dump da árvore remota: entra como linhas sintéticas no fim do painel.
    if let Some(task) = &mut logs.tree_task
        && let Some(result) = block_on(poll_once(task))
    {
        logs.tree_task = None;
        match result {
            Ok(value) => {
                let text = serde_json::to_string_pretty(&value).unwrap_or_default();
                let start = text.len().saturating_sub(6000);
                for line in text[start..].lines() {
                    logs.entries.push(methods::LogEntry {
                        time: 0.0,
                        level: "info".into(),
                        target: "viber.tree".into(),
                        message: line.to_owned(),
                    });
                }
                let excess = logs.entries.len().saturating_sub(KEPT_ENTRIES * 2);
                logs.entries.drain(..excess);
                logs.dirty = true;
            }
            Err(error) => warn!("viber.tree call failed: {error}"),
        }
    }
}

/// O botão do dock: pede a árvore de entidades da engine a correr.
pub fn dump_tree(world: &mut World) {
    let bridge = world.resource::<ViberBridge>();
    if !matches!(bridge.status, EngineStatus::Connected { .. }) {
        return;
    }
    let task = methods::entity_tree(&bridge.client, 3);
    world.resource_mut::<BridgeLogs>().tree_task = Some(task);
}

/// Rebuilds the log lines when new entries arrived.
pub fn render_logs(
    mut commands: Commands,
    mut logs: ResMut<BridgeLogs>,
    bodies: Query<Entity, With<LogsBody>>,
) {
    if !logs.dirty {
        return;
    }
    logs.dirty = false;
    let Ok(body) = bodies.single() else {
        return;
    };
    commands.despawn_all::<With<LogLine>>();
    for entry in &logs.entries {
        commands.spawn((
            LogLine,
            Text::new(format_line(entry)),
            TextColor(level_color(&entry.level)),
            Node {
                margin: UiRect::bottom(px(1)),
                ..default()
            },
            Pickable::IGNORE,
            ChildOf(body),
        ));
    }
}

/// One `HH:MM:SS [LEVEL] target: message` line, timestamps in UTC.
fn format_line(entry: &methods::LogEntry) -> String {
    let secs = entry.time.floor() as i64;
    let time_of_day = secs.rem_euclid(86_400);
    let (h, m, s) = (
        time_of_day / 3600,
        (time_of_day % 3600) / 60,
        time_of_day % 60,
    );
    format!(
        "{:02}:{:02}:{:02} [{:<5}] {}: {}",
        h, m, s, entry.level, entry.target, entry.message
    )
}

fn level_color(level: &str) -> Color {
    match level {
        "ERROR" => Color::srgb(0.94, 0.44, 0.44),
        "WARN" => Color::srgb(0.94, 0.78, 0.44),
        "DEBUG" | "TRACE" => Color::srgb(0.62, 0.65, 0.68),
        _ => Color::srgb(0.85, 0.87, 0.9),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(level: &str) -> methods::LogEntry {
        methods::LogEntry {
            time: 86_399.0 + 0.5,
            level: level.into(),
            target: "viber::terrain".into(),
            message: "chunk up".into(),
        }
    }

    #[test]
    fn formats_line_with_utc_time_and_padded_level() {
        assert_eq!(
            format_line(&entry("INFO")),
            "23:59:59 [INFO ] viber::terrain: chunk up"
        );
    }

    #[test]
    fn level_colors_differ_between_error_warn_and_info() {
        let error = level_color("ERROR");
        let warn = level_color("WARN");
        let info = level_color("INFO");
        assert_ne!(error, warn);
        assert_ne!(warn, info);
        assert_ne!(error, info);
    }
}
