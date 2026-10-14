//! The 3D | UI | Script mode strip (Fase 4, Godot-style top selector).
//!
//! * **3D** — the normal editing viewport.
//! * **UI** — the GAME's UI alone: the game UI camera takes the window, letterboxed
//!   16:9 over a dark background; the tree/inspector keep editing `<UiRoot>` attrs
//!   live (the sync re-spawns UI nodes).
//! * **Script** — a panel over the viewport with the world's `.lua` files: click to
//!   preview, "Open externally" for the real editor.

use bevy::feathers::controls::{ButtonVariant, FeathersToolButton};
use bevy::feathers::display::caption;
use bevy::prelude::*;
use bevy::ui_widgets::Activate;
use viber::scene::doc::SceneDoc;
use viber::scene::files::{FileEntry, FileKind};

use super::files::FilesState;
use super::icons::{self, fa, icon_caption};
use super::strings::{Lang, StrKey, t};
use super::tooltip::Tooltip;
use crate::scene::EditStatus;

/// The editor's main mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum EditorMode {
    #[default]
    ThreeD,
    Ui,
    Script,
}

#[derive(Resource, Debug, Default, Clone, Copy, PartialEq)]
pub struct EditorModeState(pub EditorMode);

/// A mode button (`0` is the mode).
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ModeButton(pub EditorMode);

/// The floating strip (top-center of the viewport).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ModeStrip;

/// The script panel overlay (covers the viewport in Script mode).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ScriptPanel;

/// The preview text of the selected script.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ScriptPreview;

/// A script row.
#[derive(Component, Debug, Clone, PartialEq, Eq)]
pub struct ScriptRow(pub String);

#[derive(Resource, Debug, Default)]
pub struct ScriptState {
    pub selected: Option<String>,
}

pub struct ModeStripPlugin;

impl Plugin for ModeStripPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<EditorModeState>()
            .init_resource::<ScriptState>()
            .add_systems(
                Update,
                (
                    refresh_mode_strip,
                    apply_mode,
                    render_script_panel,
                    open_script_clicked,
                ),
            );
    }
}

/// The strip, as a scene (child of the viewport container, top-center).
pub fn mode_strip() -> impl Scene {
    bsn! {
        ModeStrip
        GlobalZIndex(11)
        Node {
            position_type: PositionType::Absolute,
            left: percent(50),
            top: px(10),
            flex_direction: FlexDirection::Row,
            column_gap: px(4),
            padding: px(4),
            border: UiRect::all(px(1)),
            border_radius: px(6),
        }
        BackgroundColor(Color::srgba(0.07, 0.08, 0.1, 0.9))
        BorderColor::all(Color::srgba(0.3, 0.33, 0.38, 0.9))
        Pickable::IGNORE
        Children [
            @mode_button(EditorMode::ThreeD, fa::CUBES, StrKey::Mode3D)
            @mode_button(EditorMode::Ui, fa::DISPLAY, StrKey::ModeUi)
            @mode_button(EditorMode::Script, fa::TERMINAL, StrKey::ModeScript)
        ]
    }
}

fn mode_button(mode: EditorMode, glyph: char, key: StrKey) -> impl Scene {
    bsn! {
        @FeathersToolButton {
            @variant: ButtonVariant::Plain,
            @caption: bsn! { @icon_caption(glyph, t(Lang::default(), key), icons::ICON) },
        }
        ModeButton(mode)
        Tooltip(t(Lang::default(), key))
        on(mode_clicked)
    }
}

/// The script panel overlay (shown in Script mode by [`apply_mode`]).
pub fn script_panel() -> impl Scene {
    bsn! {
        ScriptPanel
        GlobalZIndex(10)
        Node {
            position_type: PositionType::Absolute,
            width: percent(100),
            height: percent(100),
            display: Display::None,
            flex_direction: FlexDirection::Row,
            column_gap: px(6),
            padding: UiRect::axes(px(10), px(44)),
        }
        BackgroundColor(Color::srgb(0.09, 0.1, 0.12))
        Pickable::IGNORE
        Children [
            // esquerda: a lista de scripts
            Node {
                width: px(260),
                flex_shrink: 0.0,
                flex_direction: FlexDirection::Column,
                overflow: Overflow::scroll_y(),
                row_gap: px(2),
                padding: px(6),
                border: UiRect::all(px(1)),
                border_radius: px(6),
            }
        ScriptList
            BorderColor::all(Color::srgb(0.25, 0.27, 0.3))
            --
            // direita: o preview + abrir fora
            Node {
                flex_grow: 1.0,
                min_width: px(0),
                flex_direction: FlexDirection::Column,
                row_gap: px(6),
            }
            Children [
                Node {
                    flex_direction: FlexDirection::Row,
                    column_gap: px(12),
                    align_items: AlignItems::Center,
                    flex_wrap: FlexWrap::NoWrap,
                }
                Children [
                    @caption(t(Lang::default(), StrKey::ScriptTitle)) ScriptTitle
                    --
                    @FeathersToolButton {
                        @variant: ButtonVariant::Plain,
                        @caption: bsn! {
                            @icon_caption(
                                fa::FILE_LINES,
                                t(Lang::default(), StrKey::FilesOpenExternal),
                                icons::ICON_DIM
                            )
                        },
                    }
                    ScriptOpenPlaceholder
                    Tooltip(t(Lang::default(), StrKey::FilesOpenExternalTip))
                    on(open_script_button)
                ]
                --
                Node {
                    flex_grow: 1.0,
                    min_height: px(0),
                    overflow: Overflow::scroll_y(),
                    flex_direction: FlexDirection::Column,
                }
                ScriptPreview
                Children []
            ]
        ]
    }
}

/// Where the script list rows go.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ScriptList;

/// Placeholder button replaced per selection (keeps the bsn! spawn simple).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ScriptOpenPlaceholder;

#[derive(Component, Debug, Clone, Copy, Default)]
struct ScriptTitle;

/// Applies `--mode` once (QA/agents).
pub fn apply_startup_mode(
    config: Res<crate::app::EditorConfig>,
    mut ran: Local<bool>,
    mut state: ResMut<EditorModeState>,
) {
    if *ran {
        return;
    }
    *ran = true;
    let Some(wanted) = config.startup_mode.as_deref() else {
        return;
    };
    state.0 = match wanted.to_ascii_lowercase().as_str() {
        "ui" => EditorMode::Ui,
        "script" => EditorMode::Script,
        _ => EditorMode::ThreeD,
    };
}

fn mode_clicked(
    activate: On<Activate>,
    buttons: Query<&ModeButton>,
    mut state: ResMut<EditorModeState>,
) {
    if let Ok(button) = buttons.get(activate.event_target())
        && state.0 != button.0
    {
        state.0 = button.0;
    }
}

/// Highlights the active mode button.
fn refresh_mode_strip(
    state: Res<EditorModeState>,
    lang: Res<Lang>,
    mut buttons: Query<(&ModeButton, &mut ButtonVariant)>,
    strips: Query<(), With<ModeStrip>>,
) {
    let _ = strips;
    if !state.is_changed() && !lang.is_changed() {
        return;
    }
    for (button, mut variant) in &mut buttons {
        let wanted = if state.0 == button.0 {
            ButtonVariant::Primary
        } else {
            ButtonVariant::Plain
        };
        if *variant != wanted {
            *variant = wanted;
        }
    }
}

/// Applies the mode: camera swap + letterbox (UI) and the script panel (Script).
#[allow(clippy::type_complexity)]
fn apply_mode(
    state: Res<EditorModeState>,
    windows: Query<&Window>,
    mut commands: Commands,
    mut editor_cameras: Query<&mut Camera, With<viber::editor_camera::EditorCamera>>,
    mut ui_cameras: Query<
        (Entity, &mut Camera),
        (
            With<crate::viewport::GameUiCamera>,
            Without<viber::editor_camera::EditorCamera>,
        ),
    >,
    mut panels: Query<&mut Node, With<ScriptPanel>>,
) {
    // B4: em modo UI o marker `KeepCameraActive` é assegurado TODOS os frames
    // — o `suppress_game_cameras` da engine desativa TODA a câmara sem
    // `EditorCamera`/`KeepCameraActive` em cada frame, e o `is_active = true`
    // abaixo só é escrito enquanto `state` está "changed" (~2 frames). Sem o
    // marker, da 3.ª frame nenhuma câmara ficava activa → letterbox preto. O
    // insert também cobre câmaras (re)spawnadas depois da troca de modo
    // (rebuild do viewport). Insert de componente existente = no-op barato.
    if state.0 == EditorMode::Ui {
        for (entity, _) in &ui_cameras {
            commands
                .entity(entity)
                .insert(viber::editor_camera::KeepCameraActive);
        }
    }
    if !state.is_changed() {
        return;
    }
    let mode = state.0;
    // UI mode: a câmara de UI do jogo fica ativa, em letterbox 16:9 centrado.
    if let (Ok(window), Ok((ui_entity, mut ui_camera))) =
        (windows.single(), ui_cameras.single_mut())
    {
        match mode {
            EditorMode::Ui => {
                ui_camera.is_active = true;
                let (w, h) = (window.width(), window.height());
                let target = 16.0 / 9.0;
                let (view_w, view_h) = if w / h > target {
                    (h * target, h)
                } else {
                    (w, w / target)
                };
                let (x, y) = ((w - view_w) * 0.5, (h - view_h) * 0.5);
                ui_camera.viewport = Some(bevy::camera::Viewport {
                    physical_position: UVec2::new(x as u32, y as u32),
                    physical_size: UVec2::new(view_w as u32, view_h as u32),
                    ..default()
                });
            }
            EditorMode::ThreeD | EditorMode::Script => {
                // Sai do modo UI: devolve a câmara à regra comum (o suppress
                // da engine volta a desativá-la).
                commands
                    .entity(ui_entity)
                    .remove::<viber::editor_camera::KeepCameraActive>();
                ui_camera.is_active = false;
                ui_camera.viewport = None;
            }
        }
    }
    // A câmara de editor cede a janela em modo UI (a de UI desenha por cima).
    for mut camera in &mut editor_cameras {
        camera.is_active = mode != EditorMode::Ui;
    }
    // O painel de scripts cobre o viewport no modo Script.
    for mut panel in &mut panels {
        panel.display = if mode == EditorMode::Script {
            Display::Flex
        } else {
            Display::None
        };
    }
}

/// The world's `.lua` files (from the Files dock scan) + the selected preview.
fn render_script_panel(
    state: Res<ScriptState>,
    files: Res<FilesState>,
    doc: Res<SceneDoc>,
    mut commands: Commands,
    lists: Query<Entity, With<ScriptList>>,
    previews: Query<Entity, With<ScriptPreview>>,
) {
    if !state.is_changed() && !files.is_changed() {
        return;
    }
    let Ok(list) = lists.single() else {
        return;
    };
    let _ = doc;
    commands.entity(list).despawn_related::<Children>();
    let mut scripts = Vec::new();
    collect_scripts(&files.entries, "", &mut scripts);
    for path in scripts {
        let selected = state.selected.as_deref() == Some(&path);
        let name = path.rsplit('/').next().unwrap_or(&path).to_owned();
        let mut entity = commands.spawn((
            ScriptRow(path.clone()),
            Node {
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: px(6),
                padding: UiRect::vertical(px(2)),
                ..default()
            },
            ChildOf(list),
        ));
        if selected {
            entity.insert(bevy::feathers::theme::ThemeBackgroundColor(
                bevy::feathers::tokens::BUTTON_PRIMARY_BG,
            ));
        }
        entity.observe(select_script).with_children(|children| {
            children.spawn((
                icons::icon_text(fa::TERMINAL, 11.0, icons::ICON_DIM),
                Node {
                    width: px(14),
                    ..default()
                },
                Pickable::IGNORE,
            ));
            children.spawn((
                Text::new(name),
                TextFont::from_font_size(12.0),
                TextColor(Color::srgb(0.88, 0.9, 0.94)),
                Pickable::IGNORE,
            ));
        });
    }
    // o preview do script selecionado
    if let (Ok(preview), Some(path)) = (previews.single(), state.selected.clone()) {
        commands.entity(preview).despawn_related::<Children>();
        let dir = doc.world_path().parent().map(|dir| dir.join(&path));
        let text = dir
            .and_then(|path| std::fs::read_to_string(path).ok())
            .map(|text| text.lines().take(120).collect::<Vec<_>>().join("\n"))
            .unwrap_or_default();
        commands.spawn((
            Text::new(text),
            TextFont::from_font_size(12.0),
            TextColor(Color::srgb(0.82, 0.86, 0.9)),
            Node {
                flex_grow: 1.0,
                ..default()
            },
            ChildOf(preview),
        ));
    }
}

fn collect_scripts(entries: &[FileEntry], prefix: &str, out: &mut Vec<String>) {
    for entry in entries {
        let path = if prefix.is_empty() {
            entry.name.clone()
        } else {
            format!("{prefix}/{}", entry.name)
        };
        if entry.kind == FileKind::Dir {
            collect_scripts(&entry.children, &path, out);
        } else if entry.kind == FileKind::Script {
            out.push(path);
        }
    }
}

fn select_script(click: On<PointerClick>, rows: Query<&ScriptRow>, mut state: ResMut<ScriptState>) {
    if let Ok(row) = rows.get(click.event_target()) {
        state.selected = Some(row.0.clone());
    }
}

fn open_script_button(
    activate: On<Activate>,
    buttons: Query<(), With<ScriptOpenPlaceholder>>,
    mut commands: Commands,
) {
    if !buttons.contains(activate.event_target()) {
        return;
    }
    commands.queue(open_script_clicked);
}

fn open_script_clicked(world: &mut World) {
    let Some(path) = world.resource::<ScriptState>().selected.clone() else {
        return;
    };
    let Some(dir) = world
        .resource::<SceneDoc>()
        .world_path()
        .parent()
        .map(|dir| dir.join(&path))
    else {
        return;
    };
    let lang = *world.resource::<Lang>();
    let opened = std::process::Command::new("xdg-open")
        .arg(&dir)
        .spawn()
        .is_ok();
    world.resource_mut::<EditStatus>().0 = if opened {
        t(lang, StrKey::FilesOpened).to_string()
    } else {
        t(lang, StrKey::FilesOpenFailed).to_string()
    };
}
