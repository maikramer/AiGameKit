//! The Luau console: a line editor that evaluates code in the engine's live REPL (`viber.lua`).

use crate::bridge::BrpClientError;
use bevy::input::ButtonState;
use bevy::input::keyboard::{KeyCode, KeyboardInput};
use bevy::input_focus::InputFocus;
use bevy::prelude::*;
use bevy::tasks::{Task, block_on, poll_once};
use bevy::text::EditableText;

use super::strings::{Lang, StrKey, console_call_failed, console_malformed, t};
use crate::bridge::{EngineStatus, ViberBridge, methods};

/// How many history blocks are kept in the panel.
const KEPT_BLOCKS: usize = 200;

/// Marks the console body pane.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ConsoleBody;

/// Marks the console's text input.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ConsoleInput;

/// Marks the scrollable history node.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ConsoleHistory;

/// Marks one rendered history line, so the whole set can be replaced in one command.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ConsoleLine;

/// One block of console history: the code that was run and what came back.
#[derive(Debug, Clone, PartialEq)]
pub enum Block {
    Input { code: String },
    Output { ok: bool, text: String },
}

#[derive(Resource, Default)]
pub struct ConsoleState {
    history: Vec<Block>,
    dirty: bool,
    /// A line was just submitted; clear the input once its borrow is released.
    clear_input: bool,
    task: Option<Task<Result<serde_json::Value, BrpClientError>>>,
}

/// Submits the input line when Enter is pressed while the console input is focused.
pub fn submit_console(
    mut keys: MessageReader<KeyboardInput>,
    focus: Option<Res<InputFocus>>,
    inputs: Query<Entity, With<ConsoleInput>>,
    editable: Query<&EditableText>,
    bridge: Res<ViberBridge>,
    mut state: ResMut<ConsoleState>,
) {
    if !matches!(bridge.status, EngineStatus::Connected { .. }) {
        return;
    }
    let Ok(input_entity) = inputs.single() else {
        return;
    };
    let console_focused = focus
        .as_deref()
        .and_then(|focus| focus.get())
        .is_some_and(|entity| entity == input_entity);
    if !console_focused {
        return;
    }

    let enter_pressed = keys
        .read()
        .any(|key| key.key_code == KeyCode::Enter && key.state == ButtonState::Pressed);
    if !enter_pressed || state.task.is_some() {
        return;
    }

    let Ok(input) = editable.get(input_entity) else {
        return;
    };
    let code = input.value().to_string();
    if code.trim().is_empty() {
        return;
    }
    state.history.push(Block::Input { code: code.clone() });
    trim_history(&mut state.history);
    state.dirty = true;
    state.clear_input = true;
    state.task = Some(methods::lua_eval(&bridge.client, &code));
}

/// Clears a just-submitted line. Runs after [`submit_console`], once the text is no longer borrowed.
pub fn clear_submitted_input(
    mut state: ResMut<ConsoleState>,
    mut inputs: Query<&mut EditableText, With<ConsoleInput>>,
) {
    if !state.clear_input {
        return;
    }
    state.clear_input = false;
    if let Ok(mut input) = inputs.single_mut() {
        input.clear();
    }
}

/// Collects the pending REPL answer into the history.
pub fn poll_console(lang: Res<Lang>, bridge: Res<ViberBridge>, mut state: ResMut<ConsoleState>) {
    if !matches!(bridge.status, EngineStatus::Connected { .. }) {
        return;
    }
    let Some(task) = &mut state.task else {
        return;
    };
    let Some(result) = block_on(poll_once(task)) else {
        return;
    };
    state.task = None;
    let block = match result {
        Ok(value) => match serde_json::from_value::<methods::LuaEval>(value) {
            Ok(eval) if eval.ok => Block::Output {
                ok: true,
                text: eval
                    .result
                    .unwrap_or_else(|| t(*lang, StrKey::ConOk).into()),
            },
            Ok(eval) => Block::Output {
                ok: false,
                text: eval
                    .error
                    .unwrap_or_else(|| t(*lang, StrKey::ConFailed).into()),
            },
            Err(error) => Block::Output {
                ok: false,
                text: console_malformed(*lang, &error),
            },
        },
        Err(error) => Block::Output {
            ok: false,
            text: console_call_failed(*lang, &error),
        },
    };
    state.history.push(block);
    trim_history(&mut state.history);
    state.dirty = true;
}

/// Rebuilds the console history when it changed.
pub fn render_console(
    mut commands: Commands,
    mut state: ResMut<ConsoleState>,
    histories: Query<Entity, With<ConsoleHistory>>,
) {
    if !state.dirty {
        return;
    }
    state.dirty = false;
    let Ok(history) = histories.single() else {
        return;
    };
    commands.despawn_all::<With<ConsoleLine>>();
    for block in &state.history {
        let (text, color) = match block {
            Block::Input { code } => (format!("> {code}"), Color::srgb(0.78, 0.84, 0.95)),
            Block::Output { ok: true, text } => (text.clone(), Color::srgb(0.66, 0.9, 0.66)),
            Block::Output { ok: false, text } => (text.clone(), Color::srgb(0.95, 0.5, 0.5)),
        };
        commands.spawn((
            ConsoleLine,
            Text::new(text),
            TextColor(color),
            Node {
                margin: UiRect::bottom(px(2)),
                ..default()
            },
            Pickable::IGNORE,
            ChildOf(history),
        ));
    }
}

fn trim_history(history: &mut Vec<Block>) {
    let start = history.len().saturating_sub(KEPT_BLOCKS);
    history.drain(..start);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn history_is_trimmed_to_the_cap() {
        let mut history = (0..KEPT_BLOCKS + 10)
            .map(|i| Block::Input {
                code: i.to_string(),
            })
            .collect();
        trim_history(&mut history);
        assert_eq!(history.len(), KEPT_BLOCKS);
        assert_eq!(
            history.first(),
            Some(&Block::Input {
                code: "10".to_string()
            })
        );
    }
}
