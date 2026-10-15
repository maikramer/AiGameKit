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
use super::theme;
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
            .init_resource::<ScriptReloadTask>()
            .add_systems(
                Update,
                (
                    refresh_mode_strip,
                    apply_mode,
                    render_script_panel,
                    open_script_clicked,
                    poll_script_reload,
                ),
            );
    }
}

/// The strip, as a scene (child of the viewport container, top-center).
pub fn mode_strip() -> impl Scene {
    bsn! {
        // Full-width carrier so the strip centers itself (left: 50% put its LEFT edge at
        // the center); the carrier never catches the pointer.
        Node {
            position_type: PositionType::Absolute,
            left: px(0),
            right: px(0),
            top: px(10),
            justify_content: JustifyContent::Center,
        }
        GlobalZIndex(11)
        Pickable::IGNORE
        Children [
            ModeStrip
            Node {
                flex_direction: FlexDirection::Row,
                column_gap: px(2),
                padding: px(3),
                border: UiRect::all(px(1)),
                border_radius: px(7),
            }
            BackgroundColor(theme::BG_OVERLAY)
            BorderColor::all(theme::BORDER)
            Children [
                @mode_button(EditorMode::ThreeD, fa::CUBES, StrKey::Mode3D)
                --
                @mode_button(EditorMode::Ui, fa::DISPLAY, StrKey::ModeUi)
                --
                @mode_button(EditorMode::Script, fa::TERMINAL, StrKey::ModeScript)
            ]
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
            padding: UiRect::new(px(10), px(10), px(52), px(10)),
        }
        BackgroundColor(theme::BG_WINDOW)
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
            BorderColor::all(theme::BORDER)
            BackgroundColor(theme::BG_PANEL)
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
                    @caption(t(Lang::default(), StrKey::ScriptTitle))
                    Node { flex_grow: 1.0, flex_shrink: 1.0, min_width: px(0) }
                    ScriptTitle
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
                    --
                    @FeathersToolButton {
                        @variant: ButtonVariant::Plain,
                        @caption: bsn! {
                            @icon_caption(
                                fa::ROTATE_RIGHT,
                                t(Lang::default(), StrKey::ScriptReload),
                                icons::ICON_DIM
                            )
                        },
                    }
                    ScriptReloadButton
                    Tooltip(t(Lang::default(), StrKey::ScriptReloadTip))
                    on(script_reload_button)
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

/// "Recarregar" — pede ao JOGO a correr para recarregar o script selecionado.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct ScriptReloadButton;

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
    let _ = (strips, lang);
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
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
fn apply_mode(
    state: Res<EditorModeState>,
    image: Res<crate::viewport::ViewportImage>,
    images: Res<Assets<Image>>,
    mut commands: Commands,
    mut editor_cameras: Query<&mut Camera, With<viber::editor_camera::EditorCamera>>,
    mut ui_cameras: Query<
        (Entity, &mut Camera),
        (
            With<crate::viewport::GameUiCamera>,
            Without<viber::editor_camera::EditorCamera>,
        ),
    >,
    mut clear_cameras: Query<
        (Entity, &mut Camera),
        (
            With<crate::viewport::LetterboxClearCamera>,
            Without<crate::viewport::GameUiCamera>,
            Without<viber::editor_camera::EditorCamera>,
        ),
    >,
    mut panels: Query<&mut Node, (With<ScriptPanel>, Without<super::tools::ToolStrip>)>,
    mut strips: Query<&mut Node, (With<super::tools::ToolStrip>, Without<ScriptPanel>)>,
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
        for (entity, _) in &clear_cameras {
            commands
                .entity(entity)
                .insert(viber::editor_camera::KeepCameraActive);
        }
    }
    // UI mode: a câmara de UI do jogo desenha na imagem do viewport, em letterbox 16:9
    // centrado — recalculado todos os frames (os splitters mudam o tamanho da imagem).
    if state.0 == EditorMode::Ui
        && let Some(size) = images.get(&image.0).map(|image| image.size())
        && let Ok((_, mut ui_camera)) = ui_cameras.single_mut()
    {
        let viewport = letterbox(size);
        let same = ui_camera.viewport.as_ref().is_some_and(|current| {
            current.physical_position == viewport.physical_position
                && current.physical_size == viewport.physical_size
        });
        if !same {
            ui_camera.viewport = Some(viewport);
        }
    }
    if !state.is_changed() {
        return;
    }
    let mode = state.0;
    for (entity, mut camera) in &mut clear_cameras {
        camera.is_active = mode == EditorMode::Ui;
        if mode != EditorMode::Ui {
            commands
                .entity(entity)
                .remove::<viber::editor_camera::KeepCameraActive>();
        }
    }
    // As ferramentas de gizmo 3D só fazem sentido no modo 3D.
    for mut strip in &mut strips {
        strip.display = if mode == EditorMode::ThreeD {
            Display::Flex
        } else {
            Display::None
        };
    }
    if let Ok((ui_entity, mut ui_camera)) = ui_cameras.single_mut() {
        match mode {
            EditorMode::Ui => ui_camera.is_active = true,
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

/// The 16:9 rect centered in an image of `size` physical px.
fn letterbox(size: UVec2) -> bevy::camera::Viewport {
    let (w, h) = (size.x.max(1) as f32, size.y.max(1) as f32);
    let target = 16.0 / 9.0;
    let (view_w, view_h) = if w / h > target {
        (h * target, h)
    } else {
        (w, w / target)
    };
    bevy::camera::Viewport {
        physical_position: UVec2::new(((w - view_w) * 0.5) as u32, ((h - view_h) * 0.5) as u32),
        physical_size: UVec2::new(view_w.max(1.0) as u32, view_h.max(1.0) as u32),
        ..default()
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

/// Task do reload em voo (o resultado vira linha de status).
#[derive(Resource, Debug, Default)]
pub struct ScriptReloadTask(
    pub Option<bevy::tasks::Task<Result<serde_json::Value, crate::bridge::BrpClientError>>>,
);

fn script_reload_button(
    activate: On<Activate>,
    buttons: Query<(), With<ScriptReloadButton>>,
    state: Res<ScriptState>,
    bridge: Res<crate::bridge::ViberBridge>,
    mut task: ResMut<ScriptReloadTask>,
    lang: Res<Lang>,
) {
    if !buttons.contains(activate.event_target()) {
        return;
    }
    let Some(path) = state.selected.clone() else {
        return;
    };
    if !matches!(bridge.status, crate::bridge::EngineStatus::Connected { .. }) {
        return;
    }
    let _ = lang;
    task.0 = Some(crate::bridge::methods::scripts_reload(
        &bridge.client,
        &path,
    ));
}

/// Drena o task do reload e escreve o resultado no status.
pub fn poll_script_reload(
    bridge: Res<crate::bridge::ViberBridge>,
    mut task: ResMut<ScriptReloadTask>,
    mut status: ResMut<crate::scene::EditStatus>,
    lang: Res<Lang>,
) {
    let Some(current) = task.0.as_mut() else {
        return;
    };
    if let Some(result) = bevy::tasks::block_on(bevy::tasks::poll_once(current)) {
        task.0 = None;
        status.0 = match result {
            Ok(value) => {
                let count = value.get("entities").and_then(|v| v.as_u64()).unwrap_or(0);
                t(*lang, StrKey::ScriptReloaded).replace("{count}", &count.to_string())
            }
            Err(error) => format!("{error}"),
        };
    }
    let _ = bridge;
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
