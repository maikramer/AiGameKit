//! The History dock (Fase A4): the engine's undo/redo stacks as a clickable
//! list — clicking an undo entry rewinds to that state (N undos in a row);
//! clicking a redo entry replays. Reads `viber::scene::ops::History` (the
//! labels accessor the engine gained for this panel).

use bevy::feathers::controls::{ButtonVariant, FeathersToolButton};
use bevy::feathers::display::caption;
use bevy::prelude::*;
use bevy::ui_widgets::Activate;
use viber::scene::ops::History;

use super::icons::{self, fa, icon_caption};
use super::strings::{Lang, StrKey, t};

/// The dock's scroll body.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct HistoryBody;

/// An undo entry (`0` = how many undos from now).
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq)]
pub struct UndoEntry(pub usize);

/// A redo entry (`0` = the next redo).
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq)]
pub struct RedoEntry(pub usize);

#[derive(Resource, Debug, Default)]
pub struct HistoryState {
    undo: Vec<String>,
    redo: Vec<String>,
    dirty: bool,
    refresh: f32,
}

pub struct HistoryPlugin;

impl Plugin for HistoryPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<HistoryState>()
            .add_systems(Update, (poll_history, render_history).chain());
    }
}

/// Polls the engine's History (2 Hz — labels só mudam em transações).
fn poll_history(
    time: Res<Time>,
    history: Option<Res<History>>,
    doc: Res<viber::scene::doc::SceneDoc>,
    mut state: ResMut<HistoryState>,
) {
    state.refresh -= time.delta_secs();
    if state.refresh > 0.0 && !doc.is_changed() {
        return;
    }
    state.refresh = 0.5;
    let Some(history) = history else {
        return;
    };
    let undo = history.undo_labels();
    let redo = history.redo_labels();
    if undo != state.undo.iter().map(String::as_str).collect::<Vec<_>>()
        || redo != state.redo.iter().map(String::as_str).collect::<Vec<_>>()
    {
        state.undo = undo.iter().map(|s| s.to_string()).collect();
        state.redo = redo.iter().map(|s| s.to_string()).collect();
        state.dirty = true;
    }
}

/// Rebuilds the list when the stacks changed.
fn render_history(
    mut commands: Commands,
    mut state: ResMut<HistoryState>,
    lang: Res<Lang>,
    bodies: Query<Entity, With<HistoryBody>>,
    mut built: Local<bool>,
) {
    // 1.ª passagem também desenha (o estado vazio "sem edições" não esperava por uma)
    if !state.dirty && *built && !lang.is_changed() {
        return;
    }
    let Ok(body) = bodies.single() else {
        return;
    };
    state.dirty = false;
    *built = true;
    commands.entity(body).despawn_related::<Children>();
    if state.undo.is_empty() && state.redo.is_empty() {
        let label = t(*lang, StrKey::HistoryEmpty).to_owned();
        commands.spawn_scene(bsn! { @caption(label) }).insert((
            TextColor(Color::srgb(0.55, 0.58, 0.64)),
            Pickable::IGNORE,
            ChildOf(body),
        ));
        return;
    }
    // undo: a mais recente no fim — clicar na entrada i = undo (len-1-i)×
    for (index, label) in state.undo.iter().enumerate() {
        let undos = state.undo.len() - 1 - index;
        commands
            .spawn_scene(bsn! {
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! {
                        @icon_caption(fa::ROTATE_LEFT, {format!("  {label}").leak()}, icons::ICON_DIM)
                    },
                }
            })
            .insert((UndoEntry(undos), ChildOf(body)))
            .observe(undo_clicked);
    }
    // separador + redo (a próxima primeiro)
    if !state.redo.is_empty() {
        let label = t(*lang, StrKey::HistoryRedo).to_owned();
        commands.spawn_scene(bsn! { @caption(label) }).insert((
            TextColor(Color::srgb(0.55, 0.58, 0.64)),
            Node {
                margin: UiRect::vertical(px(6)),
                ..default()
            },
            Pickable::IGNORE,
            ChildOf(body),
        ));
        for (index, label) in state.redo.iter().enumerate() {
            commands
                .spawn_scene(bsn! {
                    @FeathersToolButton {
                        @variant: ButtonVariant::Plain,
                        @caption: bsn! {
                            @icon_caption(fa::ROTATE_RIGHT, {format!("  {label}").leak()}, icons::ICON_DIM)
                        },
                    }
                })
                .insert((RedoEntry(index), ChildOf(body)))
                .observe(redo_clicked);
        }
    }
    // observers for undo rows (spawn_scene above needs them attached after)
    // — see the observe calls below; spawn_scene with .observe needs the entity
}

fn undo_clicked(activate: On<Activate>, entries: Query<&UndoEntry>, mut commands: Commands) {
    let Ok(entry) = entries.get(activate.event_target()) else {
        return;
    };
    let times = entry.0;
    commands.queue(move |world: &mut World| {
        for _ in 0..=times {
            let _ = viber::scene::live::undo(world);
        }
    });
}

fn redo_clicked(activate: On<Activate>, entries: Query<&RedoEntry>, mut commands: Commands) {
    let Ok(entry) = entries.get(activate.event_target()) else {
        return;
    };
    let times = entry.0;
    commands.queue(move |world: &mut World| {
        for _ in 0..=times {
            let _ = viber::scene::live::redo(world);
        }
    });
}
