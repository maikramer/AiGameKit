//! The toolbar: Save on the left, ▶ Play / ⏸ Pause / ⏹ Stop centered, language toggle and
//! status on the right.
//!
//! Play (F5) saves the edited files and runs the world as a separate game process
//! (`viber run --bridge`), Godot-style; Stop (F8) ends it. Pause (F6) freezes the running
//! game's simulation through the bridge (`viber.debug.step(0)` / `viber.debug.play()`).
//! The editor's own scene stays frozen.
//!
//! Every button label and tooltip is bilingual: spawned with [`t`] plus a [`LangLabel`] /
//! [`LangTooltip`] marker, rewritten in place when [`Lang`] changes.

use bevy::feathers::containers::flex_spacer;
use bevy::feathers::controls::{ButtonVariant, FeathersToolButton};
use bevy::feathers::display::caption;
use bevy::feathers::theme::{ThemeBackgroundColor, ThemeBorderColor};
use bevy::feathers::tokens;
use bevy::prelude::*;
use bevy::tasks::{Task, block_on, poll_once};
use bevy::ui_widgets::Activate;
use serde_json::Value;

use super::icons::{self, fa, icon, icon_caption};
use super::strings::{
    Lang, LangButton, LangLabel, LangTooltip, StrKey, game_running, game_starting, needs_reload,
    paused_suffix, t,
};
use super::tooltip::Tooltip;
use crate::app::EditorConfig;
use crate::bridge::{BrpClientError, EngineStatus, ViberBridge, launch, methods};
use crate::scene::doc::SceneDoc;

/// The status label on the right.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct StatusLabel;

/// The status dot on the far right (colored by game state).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct StatusDot;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct SaveButton;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct PlayButton;

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct PauseButton;

/// The Pause button's icon span (swapped pause ⇄ play with the state).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct PauseIcon;

/// "Play from here" (F9): arranca o jogo e teleporta o herói para onde a câmara
/// do editor olha (`viber.debug.tp` quando a bridge liga).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct PlayHereButton;

/// O alvo pendente do play-from-here (consumido quando a bridge liga).
#[derive(Resource, Debug, Default)]
pub struct PendingPlayFrom(pub Option<[f32; 2]>);

#[derive(Component, Debug, Clone, Copy, Default)]
pub struct StopButton;

/// Whether the running game is frozen by the editor, and the in-flight freeze/resume call.
#[derive(Resource, Debug, Default)]
pub struct GamePause {
    paused: bool,
    task: Option<Task<Result<Value, BrpClientError>>>,
}

pub fn toolbar() -> impl Scene {
    let lang = Lang::default();
    bsn! {
        Node {
            width: percent(100),
            min_height: px(36),
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(12),
            padding: px(8),
            border: UiRect::all(px(1)),
            border_radius: px(4),
        }
        ThemeBackgroundColor(tokens::SUBPANE_HEADER_BG)
        ThemeBorderColor(tokens::SUBPANE_HEADER_BORDER)
        Children [
            @logo_badge()
            --
            @FeathersToolButton {
                @variant: ButtonVariant::Plain,
                @caption: bsn! {
                    Node { column_gap: px(6), align_items: AlignItems::Center }
                    Children [
                        @icon(fa::FLOPPY_DISK, 13.0, icons::ICON)
                        --
                        @caption(t(lang, StrKey::BtnSave)) LangLabel(StrKey::BtnSave)
                    ]
                },
            }
            SaveButton
            Tooltip(t(lang, StrKey::TipSave))
            LangTooltip(StrKey::TipSave)
            on(save_clicked)
            --
            @flex_spacer()
            --
            @FeathersToolButton {
                @variant: ButtonVariant::Primary,
                @caption: bsn! {
                    Node { column_gap: px(6), align_items: AlignItems::Center }
                    Children [
                        @icon(fa::PLAY, 13.0, Color::WHITE)
                        --
                        @caption(t(lang, StrKey::BtnPlay)) LangLabel(StrKey::BtnPlay)
                    ]
                },
            }
            PlayButton
            Tooltip(t(lang, StrKey::TipPlay))
            LangTooltip(StrKey::TipPlay)
            on(play_clicked)
            --
            @FeathersToolButton {
                @variant: ButtonVariant::Plain,
                @caption: bsn! {
                    @icon_caption(fa::LOCATION_DOT, t(lang, StrKey::BtnPlayHere), Color::srgb(0.55, 0.85, 0.6))
                },
            }
            PlayHereButton
            Tooltip(t(lang, StrKey::TipPlayHere))
            LangTooltip(StrKey::TipPlayHere)
            on(play_here_clicked)
            --
            @FeathersToolButton {
                @variant: ButtonVariant::Plain,
                @caption: bsn! {
                    Node { column_gap: px(6), align_items: AlignItems::Center }
                    Children [
                        @icon(fa::PAUSE, 13.0, icons::ICON) PauseIcon
                        --
                        @caption(t(lang, StrKey::BtnPause)) LangLabel(StrKey::BtnPause)
                    ]
                },
            }
            PauseButton
            Tooltip(t(lang, StrKey::TipPause))
            LangTooltip(StrKey::TipPause)
            on(pause_clicked)
            --
            @FeathersToolButton {
                @variant: ButtonVariant::Plain,
                @caption: bsn! {
                    Node { column_gap: px(6), align_items: AlignItems::Center }
                    Children [
                        @icon(fa::STOP, 13.0, Color::srgb(0.95, 0.55, 0.5))
                        --
                        @caption(t(lang, StrKey::BtnStop)) LangLabel(StrKey::BtnStop)
                    ]
                },
            }
            StopButton
            Tooltip(t(lang, StrKey::TipStop))
            LangTooltip(StrKey::TipStop)
            on(stop_clicked)
            --
            @flex_spacer()
            --
            // The language toggle: caption shows the active code (PT/EN), flipped by the
            // observer; `sync_lang_labels` rewrites it alongside every other label.
            @FeathersToolButton {
                @variant: ButtonVariant::Plain,
                @caption: bsn! {
                    Node { column_gap: px(6), align_items: AlignItems::Center }
                    Children [
                        @icon(fa::GLOBE, 13.0, icons::ICON)
                        --
                        @caption(lang.code()) LangButton
                    ]
                },
            }
            Tooltip(t(lang, StrKey::TipLang))
            LangTooltip(StrKey::TipLang)
            on(super::strings::toggle_lang)
            --
            @status_area()
        ]
    }
}

/// The editor's mark (text2d-generated) at the far left of the toolbar.
fn logo_badge() -> impl Scene {
    bsn! {
        Node {
            width: px(22),
            height: px(22),
            margin: {UiRect::horizontal(px(2))},
        }
        ImageNode { image: icons::LOGO_ICON }
        Tooltip("Viber Editor")
    }
}

/// The status area on the right: a state-colored dot, then the status label.
fn status_area() -> impl Scene {
    bsn! {
        Node {
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(8),
        }
        Pickable::IGNORE
        Children [
            Node {
                width: px(9),
                height: px(9),
                border_radius: px(5),
            }
            BackgroundColor(Color::srgb(0.45, 0.47, 0.5))
            StatusDot
            --
            @caption("—") StatusLabel
        ]
    }
}

/// Refreshes the status dot and label: world, unsaved marker, tool, last edit, game state.
/// Runs every frame (writes only on change), so a language toggle re-localizes immediately;
/// only the last-edit message keeps the language it was written in.
#[allow(clippy::too_many_arguments)] // Bevy system: one parameter per query/resource.
pub fn update_status(
    lang: Res<Lang>,
    bridge: Res<ViberBridge>,
    doc: Res<SceneDoc>,
    pause: Res<GamePause>,
    tool: Res<crate::viewport::gizmo::GizmoTool>,
    edit: Res<crate::scene::EditStatus>,
    sync: Res<viber::scene::sync::SceneSyncStatus>,
    mut labels: Query<&mut Text, With<StatusLabel>>,
    mut dots: Query<&mut BackgroundColor, With<StatusDot>>,
) {
    let world = doc
        .world_path()
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    let dirty = if doc.is_dirty() { " *" } else { "" };
    let mode = match tool.mode {
        crate::viewport::gizmo::Mode::Select => t(*lang, StrKey::HintSelect),
        crate::viewport::gizmo::Mode::Translate => t(*lang, StrKey::HintMove),
        crate::viewport::gizmo::Mode::Rotate => t(*lang, StrKey::HintRotate),
        crate::viewport::gizmo::Mode::Scale => t(*lang, StrKey::HintScale),
        crate::viewport::gizmo::Mode::Terrain => t(*lang, StrKey::HintTerrain),
    };
    let space = if tool.local { "local" } else { "global" };
    let (game, dot) = match &bridge.status {
        EngineStatus::Stopped => (String::new(), Color::srgb(0.45, 0.47, 0.5)),
        EngineStatus::WaitingForBridge => (
            game_starting(*lang).to_string(),
            Color::srgb(0.95, 0.75, 0.35),
        ),
        EngineStatus::Connected { version, .. } => {
            (game_running(*lang, version), Color::srgb(0.45, 0.8, 0.5))
        }
        EngineStatus::Failed(_) => (String::new(), Color::srgb(0.9, 0.4, 0.4)),
    };
    let game = if pause.paused && matches!(bridge.status, EngineStatus::Connected { .. }) {
        format!("{game}{}", paused_suffix(*lang))
    } else {
        game
    };
    let reload = match sync.needs_reload.len() {
        0 => String::new(),
        n => needs_reload(*lang, n),
    };
    let last = if edit.0.is_empty() {
        String::new()
    } else {
        format!(" | {}", edit.0)
    };
    let wanted = format!("{world}{dirty} | {mode} ({space}){last}{reload}{game}");
    if let Ok(mut text) = labels.single_mut()
        && text.0 != wanted
    {
        text.0 = wanted;
    }
    if let Ok(mut color) = dots.single_mut()
        && color.0 != dot
    {
        color.0 = dot;
    }
}

/// F5 plays, F6 pauses, F8 stops.
pub fn play_shortcuts(
    keys: Res<ButtonInput<KeyCode>>,
    mut commands: Commands,
    mut bridge: ResMut<ViberBridge>,
    mut pause: ResMut<GamePause>,
) {
    if keys.just_pressed(KeyCode::F5) {
        commands.queue(play);
    }
    if keys.just_pressed(KeyCode::F9) {
        commands.queue(play_from_here);
    }
    if keys.just_pressed(KeyCode::F6) && matches!(bridge.status, EngineStatus::Connected { .. }) {
        let wanted = !pause.paused;
        set_paused(&mut pause, &bridge, wanted);
    }
    if keys.just_pressed(KeyCode::F8) {
        bridge.stop();
    }
}

fn save_clicked(
    activate: On<Activate>,
    buttons: Query<(), With<SaveButton>>,
    mut commands: Commands,
) {
    if buttons.contains(activate.event_target()) {
        commands.queue(crate::scene::save_world);
    }
}

fn play_clicked(
    activate: On<Activate>,
    buttons: Query<(), With<PlayButton>>,
    mut commands: Commands,
) {
    if buttons.contains(activate.event_target()) {
        commands.queue(play);
    }
}

fn stop_clicked(
    activate: On<Activate>,
    buttons: Query<(), With<StopButton>>,
    mut bridge: ResMut<ViberBridge>,
) {
    if buttons.contains(activate.event_target()) {
        bridge.stop();
    }
}

/// Flips the running game's freeze state through the bridge.
fn pause_clicked(
    activate: On<Activate>,
    buttons: Query<(), With<PauseButton>>,
    bridge: Res<ViberBridge>,
    mut pause: ResMut<GamePause>,
) {
    if !buttons.contains(activate.event_target()) {
        return;
    }
    if !matches!(bridge.status, EngineStatus::Connected { .. }) {
        info!("pause: no running game to freeze — press Play first");
        return;
    }
    let wanted = !pause.paused;
    set_paused(&mut pause, &bridge, wanted);
}

fn play_here_clicked(
    activate: On<Activate>,
    buttons: Query<(), With<PlayHereButton>>,
    mut commands: Commands,
) {
    if buttons.contains(activate.event_target()) {
        commands.queue(play_from_here);
    }
}

/// O `play` do "from here": grava a pose-alvo da câmara de editor ANTES de lançar.
fn play_from_here(world: &mut World) {
    let target = world
        .get_resource::<viber::editor_camera::EditorCamState>()
        .map(|camera| [camera.target.x, camera.target.z]);
    world.resource_mut::<PendingPlayFrom>().0 = target;
    play(world);
}

/// Quando a bridge liga com um play-from-here pendente, teleporta o herói.
pub fn poll_play_from_here(
    bridge: Res<ViberBridge>,
    mut pending: ResMut<PendingPlayFrom>,
    mut state: ResMut<GamePause>,
) {
    let Some(at) = pending.0.take() else {
        return;
    };
    if !matches!(bridge.status, EngineStatus::Connected { .. }) {
        // ainda a arrancar: mantém o alvo até ligar
        pending.0 = Some(at);
        return;
    }
    let code = format!("viber.debug.tp({}, {})", at[0], at[1]);
    state.task = Some(methods::lua_eval(&bridge.client, &code));
}

/// Queues the freeze/resume call and flips the tracked state.
fn set_paused(pause: &mut GamePause, bridge: &ViberBridge, wanted: bool) {
    let task = if wanted {
        methods::pause_game(&bridge.client)
    } else {
        methods::resume_game(&bridge.client)
    };
    pause.task = Some(task);
    pause.paused = wanted;
}

/// Settles the freeze/resume call and refreshes the Pause button's look: latched (Primary,
/// play icon = resume) while paused, plain pause icon otherwise.
pub fn poll_pause(
    bridge: Res<ViberBridge>,
    mut pause: ResMut<GamePause>,
    mut variants: Query<&mut ButtonVariant, With<PauseButton>>,
    mut icons: Query<&mut Text, With<PauseIcon>>,
) {
    // A new run (or a dead one) always starts unpaused.
    if pause.paused && !matches!(bridge.status, EngineStatus::Connected { .. }) {
        pause.paused = false;
    }
    if let Some(task) = &mut pause.task
        && let Some(result) = block_on(poll_once(task))
    {
        pause.task = None;
        if let Err(error) = result {
            warn!("pause/resume call failed: {error}");
        }
    }
    let (variant, icon) = if pause.paused {
        (ButtonVariant::Primary, fa::PLAY.to_string())
    } else {
        (ButtonVariant::Plain, fa::PAUSE.to_string())
    };
    if let Ok(mut current) = variants.single_mut()
        && *current != variant
    {
        *current = variant;
    }
    if let Ok(mut text) = icons.single_mut()
        && text.0 != icon
    {
        text.0 = icon;
    }
}

/// Saves (through the engine's editing API), then runs the world as a separate game process.
fn play(world: &mut World) {
    crate::scene::save_world(world);
    let explicit = world.resource::<EditorConfig>().viber_bin.clone();
    let binary = match launch::viber_binary(explicit.as_deref()) {
        Ok(binary) => binary,
        Err(error) => {
            error!("play: no viber binary — {error:#}");
            return;
        }
    };
    let path = world.resource::<SceneDoc>().world_path().to_path_buf();
    let mut bridge = world.resource_mut::<ViberBridge>();
    match bridge.play(&binary, &path) {
        Ok(()) => info!("play: {} on bridge :{}", path.display(), bridge.port),
        Err(error) => error!("play failed: {error:#}"),
    }
}
