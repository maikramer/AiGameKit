//! The editor window layout, Godot-style:
//!
//! ```text
//! ┌ Toolbar: Save · ▶ Play / ⏸ Pause / ⏹ Stop · status ──────────────────┐
//! ├ Objects ──┬ Viewport (editor camera, game UI inside) ──┬ Inspector ───┤
//! │ (world    │  ▣ tool strip floating top-left            │ (the node's  │
//! │  tree)    ├ Console | Logs | World Overview ───────────┤  attributes) │
//! └───────────┴────────────────────────────────────────────┴──────────────┘
//! ```
//!
//! Every editor node targets the editor's own UI camera, so the game's UI (which follows the
//! default UI camera — the viewport camera) never lands on the editor panels.
//!
//! Dock captions and tab labels are bilingual: spawned with [`t`] plus a [`LangLabel`] /
//! [`LangTooltip`] marker and rewritten in place by [`super::strings::sync_lang_labels`]
//! when the language changes.

use crate::scene::tree::TreeSearchInput;
use bevy::feathers::constants::fonts;
use bevy::feathers::containers::{flex_spacer, subpane, subpane_body, subpane_header};
use bevy::feathers::controls::{
    ButtonVariant, FeathersTextInput, FeathersTextInputContainer, FeathersToolButton,
};
use bevy::feathers::display::caption;
use bevy::feathers::font_styles::InheritableFont;
use bevy::prelude::*;
use bevy::text::{FontSize, FontWeight};
use bevy::ui_widgets::Activate;

use super::canvas::canvas_overlay_scene;
use super::console::{ConsoleBody, ConsoleHistory, ConsoleInput};
use super::context_menu::{ContextBody, ContextPanel};
use super::files::{FilesBody, FilesHeader};
/// The Biomes editor: preview + wedges for the selected <Terrain>.
fn biomes_section() -> impl Scene {
    bsn! {
        Node {
            width: percent(100),
            flex_direction: FlexDirection::Row,
            column_gap: px(10),
            padding: px(8),
        }
        BiomesBody
        Children []
    }
}

use super::biomes::BiomesBody;
use super::game::game_body;
use super::history::HistoryBody;
use super::icons::{self, fa, icon, icon_caption};
use super::logs::LogsBody;
use super::mode_strip::{mode_strip, script_panel};
use super::profiler::ProfilerBody;
use super::strings::{Lang, LangLabel, LangTooltip, StrKey, t};
use super::toolbar::toolbar;
use super::tools::tool_strip;
use super::tooltip::{Tooltip, tooltip_root};
use super::worlddoc::{WorldDocBody, WorldDocLines, WorldDocMinimap};
use crate::scene::add_node::{AddNodeBody, AddNodeButton, AddNodePanel, open_add_node};
use crate::scene::inspector::InspectorBody;
use crate::scene::tree::SceneTreeBody;
use crate::viewport::ViewportBody;
use crate::viewport::brush_panel::brush_panel;

/// Width of the side docks, in pixels.
const SIDE_DOCK_PX: f32 = 280.0;
/// Height of the bottom dock, in pixels.
const BOTTOM_DOCK_PX: f32 = 220.0;

/// The camera that draws the editor's own panels (never the game's).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct EditorUiCamera;

/// Which dock tab a header button or body belongs to.
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct EditorTab(pub TabKind);

/// Marks a dock tab body so tab selection can show and hide it.
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct TabBody(pub TabKind);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum TabKind {
    #[default]
    Console,
    Logs,
    Profiler,
    WorldDoc,
    Files,
    History,
    Game,
}

pub(crate) fn camera() -> impl Scene {
    bsn! {
        Camera2d
        Camera { order: 10 }
        EditorUiCamera
        viber::editor_camera::KeepCameraActive
    }
}

/// Points the root at the editor camera (spawned as separate scenes, so linked here).
pub fn target_editor_camera(
    mut commands: Commands,
    cameras: Query<Entity, Added<EditorUiCamera>>,
    roots: Query<Entity, With<crate::ui::EditorRoot>>,
) {
    let Ok(camera) = cameras.single() else {
        return;
    };
    for root in &roots {
        commands.entity(root).insert(UiTargetCamera(camera));
    }
}

pub(crate) fn editor_root() -> impl Scene {
    bsn! {
        crate::ui::EditorRoot
        Name::new("Viber Editor")
        Node {
            position_type: PositionType::Absolute,
            width: percent(100),
            height: percent(100),
            padding: px(6),
            flex_direction: FlexDirection::Column,
            row_gap: px(6),
        }
        BackgroundColor(Color::srgb(0.11, 0.12, 0.14))
        // Root-level font: every themed label outside a button (dock headers, status,
        // tooltips) uses feathers' Fira Sans at 13px. Buttons set their own closer font.
        InheritableFont {
            font: fonts::REGULAR,
            font_size: {FontSize::Px(13.0)},
            weight: {FontWeight::NORMAL},
        }
        Children [
            @toolbar()
            --
            Node {
                width: percent(100),
                flex_grow: 1.0,
                min_height: px(0),
                flex_direction: FlexDirection::Row,
                column_gap: px(6),
            }
            Children [
                @scene_dock()
                --
                @center_column()
                --
                @inspector_dock()
            ]
            --
            // The status bar: cursor XZ, selected count, dirty files.
            @status_bar()
            --
            // The view options popover (Vista button on the toolbar).
            @super::view_options::view_panel()
            --
            // The context menu floats near the cursor (right-click).
            @context_panel()
            --
            // The UI-canvas overlay (selection rect + handles + chips) sits
            // under the dialog and the tooltip.
            @canvas_overlay_scene()
            --
            // Above the docks, under the tooltip: the Add node dialog (modal).
            @add_node_panel()
            --
            // Last child: the hover tooltip floats above every panel.
            @tooltip_root()
        ]
    }
}

/// The Add node dialog: a centered modal card over a full-window catcher.
fn add_node_panel() -> impl Scene {
    bsn! {
        AddNodePanel
        Node {
            position_type: PositionType::Absolute,
            width: percent(100),
            height: percent(100),
            align_items: AlignItems::Center,
            justify_content: JustifyContent::Center,
            display: Display::None,
        }
        BackgroundColor(Color::srgba(0.0, 0.0, 0.0, 0.45))
        Node {
            width: px(340),
            height: px(380),
            flex_direction: FlexDirection::Column,
            padding: px(10),
            border: UiRect::all(px(1)),
            border_radius: px(8),
        }
        BackgroundColor(Color::srgb(0.1, 0.11, 0.13))
        BorderColor::all(Color::srgb(0.35, 0.38, 0.44))
        Children [
            @caption(t(Lang::default(), StrKey::AddNodeTitle)) LangLabel(StrKey::AddNodeTitle)
            --
            Node {
                flex_grow: 1.0,
                min_height: px(0),
                overflow: Overflow::scroll_y(),
                flex_direction: FlexDirection::Column,
            }
            AddNodeBody
            Children []
        ]
    }
}

/// Left: the world object tree.
fn scene_dock() -> impl Scene {
    bsn! {
        @subpane()
        Node { width: px(SIDE_DOCK_PX), flex_shrink: 0.0 }
        Children [
            @subpane_header()
            Children [
                @icon(fa::SITEMAP, 12.0, icons::ICON_DIM)
                --
                @caption(t(Lang::default(), StrKey::DockScene)) LangLabel(StrKey::DockScene)
                --
                @flex_spacer()
                --
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! { @icon(fa::PLUS, 13.0, icons::ICON) },
                }
                AddNodeButton
                Tooltip(t(Lang::default(), StrKey::BtnAddNode))
                on(open_add_node)
                --
                @FeathersTextInputContainer
                Node { width: px(90) }
                Children [
                    @FeathersTextInput {
                        @visible_width: 8f32,
                    }
                    TreeSearchInput
                ]
            ]
            --
            @subpane_body()
            Node { overflow: Overflow::scroll_y(), flex_direction: FlexDirection::Column }
            SceneTreeBody
            Children []
        ]
    }
}

/// Center: the viewport on top, the bottom dock below.
fn center_column() -> impl Scene {
    bsn! {
        Node {
            flex_grow: 1.0,
            min_width: px(0),
            flex_direction: FlexDirection::Column,
            row_gap: px(6),
        }
        Children [
            Node {
                width: percent(100),
                flex_grow: 1.0,
                min_height: px(0),
                border: UiRect::all(px(1)),
            }
            BorderColor::all(Color::srgb(0.25, 0.27, 0.3))
            ViewportBody
            Children [
                // The tool strip floats above the viewport image (z_index in the strip).
                @tool_strip()
                --
                // The rubber-band marquee (drag in Select mode).
                Node {
                    position_type: PositionType::Absolute,
                    display: Display::None,
                    border: UiRect::all(px(1)),
                }
                GlobalZIndex(10)
                crate::viewport::rubber::RubberRect
                BorderColor::all(Color::srgba(0.45, 0.85, 1.0, 0.9))
                BackgroundColor(Color::srgba(0.45, 0.85, 1.0, 0.12))
                Pickable::IGNORE
                --
                // The terrain brush settings float top-right (only with B).
                @brush_panel()
                --
                // The 3D|UI|Script selector floats top-center; the script panel
                // covers the viewport in Script mode.
                @mode_strip()
                --
                @script_panel()
            ]
            --
            @dock_tabs()
        ]
    }
}

/// Right: the Inspector.
fn inspector_dock() -> impl Scene {
    bsn! {
        @subpane()
        Node { width: px(SIDE_DOCK_PX), flex_shrink: 0.0 }
        Children [
            @subpane_header()
            Children [
                @icon(fa::SLIDERS, 12.0, icons::ICON_DIM)
                --
                @caption(t(Lang::default(), StrKey::DockInspector))
                LangLabel(StrKey::DockInspector)
            ]
            --
            @subpane_body()
            Node { overflow: Overflow::scroll_y(), flex_direction: FlexDirection::Column }
            InspectorBody
            Children []
        ]
    }
}

/// The bottom dock: Console | Logs | World Overview.
fn dock_tabs() -> impl Scene {
    bsn! {
        Node {
            width: percent(100),
            height: px(BOTTOM_DOCK_PX),
            flex_shrink: 0.0,
            flex_direction: FlexDirection::Column,
        }
        Children [
            Node {
                flex_direction: FlexDirection::Row,
                column_gap: px(2),
                min_width: px(0),
                overflow: Overflow::clip_x(),
            }
            Children [
                @editor_tab(TabKind::Console, fa::TERMINAL, StrKey::TabConsole)
                --
                @editor_tab(TabKind::Logs, fa::FILE_LINES, StrKey::TabLogs)
                --
                @editor_tab(TabKind::Profiler, fa::GAUGE_HIGH, StrKey::TabProfiler)
                --
                @editor_tab(TabKind::WorldDoc, fa::MAP, StrKey::TabWorldDoc)
                --
                @editor_tab(TabKind::Files, fa::FOLDER, StrKey::TabFiles)
                --
                @editor_tab(TabKind::History, fa::ROTATE_LEFT, StrKey::TabHistory)
                --
                @editor_tab(TabKind::Game, fa::GEARS, StrKey::TabGame)
            ]
            --
            @tab_body(TabKind::Console)
            Children [ @console_body() ]
            --
            @tab_body(TabKind::Logs)
            Children [ @logs_body() ]
            --
            @tab_body(TabKind::WorldDoc)
            Children [
                @worlddoc_body()
                --
                @biomes_section()
            ]
            --
            @tab_body(TabKind::Files)
            Children [ @files_body() ]
            --
            @tab_body(TabKind::Profiler)
            Children [ @profiler_body() ]
            --
            @tab_body(TabKind::History)
            Children [ @history_body() ]
            --
            @tab_body(TabKind::Game)
            Children [ @game_body() ]
        ]
    }
}

/// The History dock body: the engine's undo/redo stacks, clickable.
fn history_body() -> impl Scene {
    bsn! {
        @subpane()
        Node { width: percent(100) }
        Children [
            @subpane_body()
            Node { overflow: Overflow::scroll_y(), flex_direction: FlexDirection::Column }
        HistoryBody
            Children []
        ]
    }
}

/// The Profiler dock body: the running game's systems, polled live.
fn profiler_body() -> impl Scene {
    bsn! {
        @subpane()
        Node { width: percent(100) }
        Children [
            @subpane_body()
            Node { overflow: Overflow::scroll_y(), flex_direction: FlexDirection::Column }
            ProfilerBody
            Children []
        ]
    }
}

/// The Files dock body: a header (filters + actions) over the scrollable tree.
fn files_body() -> impl Scene {
    bsn! {
        @subpane()
        Node { width: percent(100) }
        Children [
            @subpane_body()
            Node { width: percent(100), row_gap: px(4) }
            Children [
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(4),
                    flex_wrap: FlexWrap::Wrap,
                }
                Children [ FilesHeader ]
                --
                // O corpo é o próprio nó de scroll (o padrão do SceneTreeBody):
                // as linhas penduram diretamente nele.
                Node {
                    flex_grow: 1.0,
                    min_height: px(0),
                    overflow: Overflow::scroll_y(),
                    flex_direction: FlexDirection::Column,
                    width: percent(100),
                }
                FilesBody
                Children []
            ]
        ]
    }
}

/// The context menu: a small bordered column at the cursor.
/// The status bar: the world position under the cursor, the selection count,
/// the dirty files and the editor's fps — Godot's bottom line.
fn status_bar() -> impl Scene {
    bsn! {
        Node {
            width: percent(100),
            min_height: px(20),
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(16),
            padding: UiRect::axes(px(8), px(2)),
            border: UiRect::top(px(1)),
        }
        BorderColor::all(Color::srgb(0.25, 0.27, 0.3))
        BackgroundColor(Color::srgb(0.08, 0.09, 0.11))
        Children [
            @caption("—") StatusBarCursor Pickable::IGNORE
            --
            @caption("—") StatusBarSelection Pickable::IGNORE
            --
            @flex_spacer()
            --
            @caption("—") StatusBarDirty Pickable::IGNORE
        ]
    }
}

/// Markers for the status bar's text spans.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct StatusBarCursor;
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct StatusBarSelection;
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct StatusBarDirty;

/// Refreshes the status bar from the live state.
#[allow(clippy::type_complexity)]
#[allow(clippy::too_many_arguments)]
pub fn update_status_bar(
    cursor: Res<crate::viewport::gizmo::ViewportCursor>,
    runtime: Option<Res<viber::terrain::runtime::TerrainRuntime>>,
    selection: Res<crate::scene::Selection>,
    doc: Res<viber::scene::doc::SceneDoc>,
    time: Res<Time>,
    mut cursor_label: Query<&mut Text, With<StatusBarCursor>>,
    mut selection_label: Query<&mut Text, (With<StatusBarSelection>, Without<StatusBarCursor>)>,
    mut dirty_label: Query<
        &mut Text,
        (
            With<StatusBarDirty>,
            Without<StatusBarCursor>,
            Without<StatusBarSelection>,
        ),
    >,
) {
    // cursor XZ no mundo (o ray do viewport + o terreno)
    let at = cursor.ray.and_then(|ray| {
        runtime.as_deref().and_then(|runtime| {
            viber::scene::terrain::ray_ground(ray.origin, *ray.direction, 4000.0, |x, z| {
                runtime.sample(x, z)
            })
        })
    });
    let cursor_text = at.map_or_else(
        || "—".to_owned(),
        |at| format!("X {:.0}  Z {:.0}  Y {:.1}", at.x, at.z, at.y),
    );
    if let Ok(mut text) = cursor_label.single_mut()
        && text.0 != cursor_text
    {
        text.0 = cursor_text;
    }
    let count = selection.0.len();
    let selection_text = if count == 0 {
        "—".to_owned()
    } else {
        format!("{count} node(s)")
    };
    if let Ok(mut text) = selection_label.single_mut()
        && text.0 != selection_text
    {
        text.0 = selection_text;
    }
    let dirty = doc.dirty_paths();
    let dirty_text = if dirty.is_empty() {
        // média móvel (um delta de frame faz 100000 fps)
        let dt = time.delta_secs();
        let smoothed = 1.0 / dt.max(1e-4);
        format!("{:.0} fps", smoothed.clamp(0.0, 999.0))
    } else {
        format!("{} file(s) dirty", dirty.len())
    };
    if let Ok(mut text) = dirty_label.single_mut()
        && text.0 != dirty_text
    {
        text.0 = dirty_text;
    }
}

fn context_panel() -> impl Scene {
    bsn! {
        ContextPanel
        Node {
            position_type: PositionType::Absolute,
            display: Display::None,
            flex_direction: FlexDirection::Column,
            padding: px(4),
            border: UiRect::all(px(1)),
            border_radius: px(6),
            min_width: px(150),
        }
        BackgroundColor(Color::srgb(0.1, 0.11, 0.13))
        BorderColor::all(Color::srgb(0.35, 0.38, 0.44))
        Children [
            Node {
                flex_direction: FlexDirection::Column,
            }
        ContextBody
            Children []
        ]
    }
}

fn editor_tab(kind: TabKind, glyph: char, key: StrKey) -> impl Scene {
    bsn! {
        @FeathersToolButton {
            @variant: ButtonVariant::Plain,
            @caption: bsn! {
                Node {
                    column_gap: px(4),
                    align_items: AlignItems::Center,
                    flex_shrink: 0.0,
                    overflow: Overflow::clip(),
                }
                Children [
                    @icon(glyph, 12.0, icons::ICON_DIM)
                    --
                    @caption(t(Lang::default(), key)) LangLabel(key)
                ]
            },
        }
        Node {
            flex_shrink: 0.0,
            min_width: px(0),
            overflow: Overflow::clip(),
        }
        EditorTab(kind)
        Tooltip(t(Lang::default(), key))
        LangTooltip(key)
        on(select_dock_tab)
    }
}

fn tab_body(kind: TabKind) -> impl Scene {
    bsn! {
        Node {
            width: percent(100),
            flex_grow: 1.0,
            min_height: px(0),
            display: {
                if kind == TabKind::Console { Display::Flex } else { Display::None }
            },
        }
        TabBody(kind)
    }
}

/// The Luau REPL against the running game: a line input plus the scrolling history.
fn console_body() -> impl Scene {
    bsn! {
        @subpane()
        Node { width: percent(100) }
        Children [
            @subpane_body()
            ConsoleBody
            Node { row_gap: px(6) }
            Children [
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(6),
                }
                Children [
                    @icon(fa::ANGLE_RIGHT, 12.0, icons::ICON_DIM)
                    --
                    @FeathersTextInputContainer
                    Node { flex_grow: 1.0 }
                    Children [
                        @FeathersTextInput {
                            @visible_width: 120f32,
                        }
                        ConsoleInput
                    ]
                ]
                --
                Node {
                    flex_grow: 1.0,
                    min_height: px(0),
                    overflow: Overflow::scroll_y(),
                }
                ConsoleHistory
                Children []
            ]
        ]
    }
}

fn logs_body() -> impl Scene {
    bsn! {
        @subpane()
        Node { width: percent(100) }
        Children [
            @subpane_body()
            Node { width: percent(100), flex_direction: FlexDirection::Column }
            Children [
                // Debugger: a árvore de entidades da ENGINE A CORRER, a um clique.
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! {
                        @icon_caption(fa::SITEMAP, t(Lang::default(), StrKey::BtnRemoteTree), icons::ICON_DIM)
                    },
                }
                RemoteTreeButton
                Tooltip(t(Lang::default(), StrKey::TipRemoteTree))
                LangTooltip(StrKey::TipRemoteTree)
                on(dump_remote_tree)
                --
                Node { flex_grow: 1.0, min_height: px(0), overflow: Overflow::scroll_y() }
                LogsBody
                Children []
            ]
        ]
    }
}

/// O marcador do botão e o observador que pede a árvore à engine a correr.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct RemoteTreeButton;

fn dump_remote_tree(
    activate: On<Activate>,
    buttons: Query<(), With<RemoteTreeButton>>,
    mut commands: Commands,
) {
    if buttons.contains(activate.event_target()) {
        commands.queue(super::logs::dump_tree);
    }
}

/// The baked world document: minimap on top, feature summary below.
fn worlddoc_body() -> impl Scene {
    bsn! {
        @subpane()
        Node { width: percent(100) }
        Children [
            @subpane_body()
            WorldDocBody
            Node {
                overflow: Overflow::scroll_y(),
                row_gap: px(2),
            }
            Children [
                Node {
                    width: percent(100),
                    align_items: AlignItems::Center,
                    padding: px(6),
                }
                WorldDocMinimap
                --
                Node {
                    width: percent(100),
                    flex_direction: FlexDirection::Column,
                    row_gap: px(2),
                }
                WorldDocLines
                Children []
            ]
        ]
    }
}

/// Shows the body matching the clicked dock tab.
/// Applies `--tab` once (QA/agents): the named bottom-dock tab opens at startup.
pub fn apply_startup_tab(
    config: Res<crate::app::EditorConfig>,
    mut ran: Local<bool>,
    mut bodies: Query<(&TabBody, &mut Node)>,
) {
    if *ran {
        return;
    }
    *ran = true;
    let Some(wanted) = config.startup_tab.as_deref() else {
        return;
    };
    let kind = match wanted.to_ascii_lowercase().as_str() {
        "console" => TabKind::Console,
        "logs" => TabKind::Logs,
        "worlddoc" | "world" | "doc" => TabKind::WorldDoc,
        "files" | "ficheiros" => TabKind::Files,
        "profiler" | "prof" => TabKind::Profiler,
        "history" | "hist" => TabKind::History,
        "game" | "jogo" => TabKind::Game,
        _ => return,
    };
    for (body, mut node) in &mut bodies {
        node.display = if body.0 == kind {
            Display::Flex
        } else {
            Display::None
        };
    }
}

fn select_dock_tab(
    activate: On<Activate>,
    tabs: Query<&EditorTab>,
    mut bodies: Query<(&TabBody, &mut Node)>,
) {
    let Ok(tab) = tabs.get(activate.event_target()) else {
        return;
    };
    for (body, mut node) in &mut bodies {
        node.display = if body.0 == tab.0 {
            Display::Flex
        } else {
            Display::None
        };
    }
}
