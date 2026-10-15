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
use bevy::feathers::containers::flex_spacer;
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
            flex_shrink: 0.0,
            flex_direction: FlexDirection::Row,
            column_gap: px(10),
            padding: px(6),
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
use super::problems::problems_body;
use super::profiler::ProfilerBody;
use super::strings::{Lang, LangLabel, LangTooltip, StrKey, t};
use super::theme;
use super::toolbar::toolbar;
use super::tools::tool_strip;
use super::tooltip::{Tooltip, tooltip_root};
use super::worlddoc::{WorldDocBody, WorldDocLines, WorldDocMinimap};
use crate::scene::add_node::{AddNodeBody, AddNodeButton, AddNodePanel, open_add_node};
use crate::scene::inspector::InspectorBody;
use crate::scene::tree::SceneTreeBody;
use crate::viewport::ViewportBody;
use crate::viewport::brush_panel::brush_panel;

/// Default width of the side docks, in pixels (resizable by the splitters).
const SIDE_DOCK_PX: f32 = 280.0;
/// Default height of the bottom dock, in pixels (resizable by its splitter).
const BOTTOM_DOCK_PX: f32 = 220.0;
/// Splitter hit area (the hairline is drawn in its middle).
const SPLITTER_PX: f32 = 6.0;

/// Sizes of the resizable docks, dragged by the [`Splitter`]s.
#[derive(Resource, Debug, Clone, Copy)]
pub struct DockSizes {
    pub left: f32,
    pub right: f32,
    pub bottom: f32,
}

impl Default for DockSizes {
    fn default() -> Self {
        Self {
            left: SIDE_DOCK_PX,
            right: SIDE_DOCK_PX + 20.0,
            bottom: BOTTOM_DOCK_PX,
        }
    }
}

/// Which dock a [`Splitter`] (or a sized node) belongs to.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DockSide {
    #[default]
    Left,
    Right,
    Bottom,
}

/// A node whose size follows [`DockSizes`].
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct DockSized(pub DockSide);

/// A draggable splitter between a dock and the viewport.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct Splitter(pub DockSide);

/// The bottom-dock tab currently shown.
#[derive(Resource, Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ActiveDockTab(pub TabKind);

/// The accent bar under a tab (visible on the active one).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct TabUnderline(pub TabKind);

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
    Problems,
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
            flex_direction: FlexDirection::Column,
        }
        BackgroundColor(theme::BG_WINDOW)
        // Root-level font: every themed label outside a button (dock headers, status,
        // tooltips) uses feathers' Fira Sans at 13px. Buttons set their own closer font.
        InheritableFont {
            font: fonts::REGULAR,
            font_size: {FontSize::Px(theme::FONT_BODY)},
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
                padding: px(6),
            }
            Children [
                @scene_dock()
                --
                @splitter(DockSide::Left)
                --
                @center_column()
                --
                @splitter(DockSide::Right)
                --
                @inspector_dock()
            ]
            --
            // The status bar: game state, cursor XZ, selected count, dirty files.
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

/// A framed dock panel: rounded, bordered, clipped — the header and body live inside.
fn dock_frame() -> impl Scene {
    bsn! {
        Node {
            flex_direction: FlexDirection::Column,
            border: UiRect::all(px(1)),
            border_radius: px(6),
            overflow: Overflow::clip(),
            min_width: px(0),
            min_height: px(0),
        }
        BackgroundColor(theme::BG_PANEL)
        BorderColor::all(theme::BORDER)
    }
}

/// A dock header: icon + caption on the left, actions on the right.
fn dock_header() -> impl Scene {
    bsn! {
        Node {
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(6),
            padding: UiRect::axes(px(10), px(0)),
            height: px(theme::TABBAR_PX),
            flex_shrink: 0.0,
            border: UiRect::bottom(px(1)),
        }
        BackgroundColor(theme::BG_HEADER)
        BorderColor::all(theme::BORDER)
    }
}

/// A vertical splitter (between a side dock and the viewport): drag to resize the dock.
fn splitter(side: DockSide) -> impl Scene {
    bsn! {
        Node {
            width: px(SPLITTER_PX),
            height: percent(100),
            flex_shrink: 0.0,
            justify_content: JustifyContent::Center,
            align_items: AlignItems::Center,
        }
        Splitter(side)
        bevy::picking::cursor::EntityCursor::System(bevy::window::SystemCursorIcon::ColResize)
        on(drag_splitter)
        Children [
            Node { width: px(2), height: px(48), border_radius: px(1) }
            BackgroundColor(theme::BORDER)
            Pickable::IGNORE
        ]
    }
}

/// The horizontal splitter above the bottom dock.
fn splitter_bottom() -> impl Scene {
    bsn! {
        Node {
            width: percent(100),
            height: px(SPLITTER_PX),
            flex_shrink: 0.0,
            justify_content: JustifyContent::Center,
            align_items: AlignItems::Center,
        }
        Splitter(DockSide::Bottom)
        bevy::picking::cursor::EntityCursor::System(bevy::window::SystemCursorIcon::RowResize)
        on(drag_splitter)
        Children [
            Node { width: px(48), height: px(2), border_radius: px(1) }
            BackgroundColor(theme::BORDER)
            Pickable::IGNORE
        ]
    }
}

fn drag_splitter(
    drag: On<bevy::picking::events::PointerDrag>,
    splitters: Query<&Splitter>,
    mut sizes: ResMut<DockSizes>,
) {
    let Ok(splitter) = splitters.get(drag.event_target()) else {
        return;
    };
    let delta = drag.delta;
    match splitter.0 {
        DockSide::Left => sizes.left = (sizes.left + delta.x).clamp(180.0, 560.0),
        DockSide::Right => sizes.right = (sizes.right - delta.x).clamp(200.0, 620.0),
        DockSide::Bottom => sizes.bottom = (sizes.bottom - delta.y).clamp(90.0, 640.0),
    }
}

/// Applies [`DockSizes`] to the sized docks (only when a splitter moved).
pub fn apply_dock_sizes(sizes: Res<DockSizes>, mut nodes: Query<(&DockSized, &mut Node)>) {
    if !sizes.is_changed() {
        return;
    }
    for (sized, mut node) in &mut nodes {
        match sized.0 {
            DockSide::Left => node.width = px(sizes.left),
            DockSide::Right => node.width = px(sizes.right),
            DockSide::Bottom => node.height = px(sizes.bottom),
        }
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
        BackgroundColor(theme::BG_POPOVER)
        BorderColor::all(theme::BORDER_STRONG)
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
        @dock_frame()
        Node { width: px(SIDE_DOCK_PX), flex_shrink: 0.0 }
        DockSized(DockSide::Left)
        Children [
            @dock_header()
            Children [
                @icon(fa::SITEMAP, 12.0, icons::ICON_DIM)
                --
                @caption(t(Lang::default(), StrKey::DockScene)) LangLabel(StrKey::DockScene)
                --
                @flex_spacer()
                --
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! { @icon(fa::PLUS, 12.0, icons::ICON) },
                }
                AddNodeButton
                Tooltip(t(Lang::default(), StrKey::BtnAddNode))
                on(open_add_node)
            ]
            --
            // Search row: magnifier + filter input over the whole dock width.
            Node {
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: px(6),
                padding: UiRect::axes(px(8), px(5)),
                flex_shrink: 0.0,
                border: UiRect::bottom(px(1)),
            }
            BorderColor::all(theme::BORDER)
            Children [
                @icon(fa::MAGNIFYING_GLASS, 11.0, icons::ICON_DIM)
                --
                @FeathersTextInputContainer
                Node { flex_grow: 1.0, min_width: px(0) }
                Children [
                    @FeathersTextInput {
                        @visible_width: 8f32,
                    }
                    TreeSearchInput
                ]
            ]
            --
            Node {
                flex_grow: 1.0,
                min_height: px(0),
                overflow: Overflow::scroll_y(),
                flex_direction: FlexDirection::Column,
                padding: UiRect::axes(px(4), px(4)),
            }
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
        }
        Children [
            Node {
                width: percent(100),
                flex_grow: 1.0,
                min_height: px(0),
                border: UiRect::all(px(1)),
                border_radius: px(6),
                overflow: Overflow::clip(),
            }
            BorderColor::all(theme::BORDER)
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
            @splitter_bottom()
            --
            @dock_tabs()
        ]
    }
}

/// Right: the Inspector.
fn inspector_dock() -> impl Scene {
    bsn! {
        @dock_frame()
        Node { width: px(SIDE_DOCK_PX + 20.0), flex_shrink: 0.0 }
        DockSized(DockSide::Right)
        Children [
            @dock_header()
            Children [
                @icon(fa::SLIDERS, 12.0, icons::ICON_DIM)
                --
                @caption(t(Lang::default(), StrKey::DockInspector))
                LangLabel(StrKey::DockInspector)
            ]
            --
            Node {
                flex_grow: 1.0,
                min_height: px(0),
                overflow: Overflow::scroll_y(),
                flex_direction: FlexDirection::Column,
                row_gap: px(4),
                padding: px(6),
            }
            InspectorBody
            Children []
        ]
    }
}

/// The bottom dock: a tab bar over the active tab's body.
fn dock_tabs() -> impl Scene {
    bsn! {
        @dock_frame()
        Node {
            width: percent(100),
            height: px(BOTTOM_DOCK_PX),
            flex_shrink: 0.0,
        }
        DockSized(DockSide::Bottom)
        Children [
            @dock_header()
            Node { column_gap: px(0), padding: UiRect::horizontal(px(2)), overflow: Overflow::clip_x() }
            Children [
                @editor_tab(TabKind::Console, fa::TERMINAL, StrKey::TabConsole)
                --
                @editor_tab(TabKind::Logs, fa::FILE_LINES, StrKey::TabLogs)
                --
                @editor_tab(TabKind::Problems, fa::TRIANGLE_EXCLAMATION, StrKey::TabProblems)
                --
                @editor_tab(TabKind::Files, fa::FOLDER, StrKey::TabFiles)
                --
                @editor_tab(TabKind::WorldDoc, fa::MAP, StrKey::TabWorldDoc)
                --
                @editor_tab(TabKind::History, fa::ROTATE_LEFT, StrKey::TabHistory)
                --
                @editor_tab(TabKind::Game, fa::GEARS, StrKey::TabGame)
                --
                @editor_tab(TabKind::Profiler, fa::GAUGE_HIGH, StrKey::TabProfiler)
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
            --
            @tab_body(TabKind::Problems)
            Children [ @problems_body() ]
        ]
    }
}

/// The History dock body: the engine's undo/redo stacks, clickable.
fn history_body() -> impl Scene {
    bsn! {
        @theme::pane()
        Children [
            @theme::pane_body()
            Node { overflow: Overflow::scroll_y(), flex_direction: FlexDirection::Column }
        HistoryBody
            Children []
        ]
    }
}

/// The Profiler dock body: the running game's systems, polled live.
fn profiler_body() -> impl Scene {
    bsn! {
        @theme::pane()
        Children [
            @theme::pane_body()
            Node { overflow: Overflow::scroll_y(), flex_direction: FlexDirection::Column }
            ProfilerBody
            Children []
        ]
    }
}

/// The Files dock body: a header (filters + actions) over the scrollable tree.
fn files_body() -> impl Scene {
    bsn! {
        @theme::pane()
        Children [
            @theme::pane_body()
            Node { width: percent(100), row_gap: px(4) }
            Children [
                Node {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: px(4),
                    flex_wrap: FlexWrap::Wrap,
                    flex_shrink: 0.0,
                }
                FilesHeader
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

/// The status bar: the running game's state (dot + text), the world position under the
/// cursor, the selection count, the dirty files and the editor's fps — Godot's bottom line.
fn status_bar() -> impl Scene {
    bsn! {
        Node {
            width: percent(100),
            height: px(theme::STATUS_PX),
            flex_shrink: 0.0,
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(18),
            padding: UiRect::axes(px(10), px(0)),
            border: UiRect::top(px(1)),
            overflow: Overflow::clip(),
        }
        BorderColor::all(theme::BORDER)
        BackgroundColor(theme::BG_HEADER)
        Children [
            @super::toolbar::status_area()
            --
            @flex_spacer()
            --
            @status_cell(fa::LOCATION_CROSSHAIRS)
            Children [ @caption("—") StatusBarCursor Pickable::IGNORE ]
            --
            @status_cell(fa::OBJECT_GROUP)
            Children [ @caption("—") StatusBarSelection Pickable::IGNORE ]
            --
            @status_cell(fa::GAUGE_HIGH)
            Children [ @caption("—") StatusBarDirty Pickable::IGNORE ]
        ]
    }
}

/// A status bar cell: a dim icon before the value.
fn status_cell(glyph: char) -> impl Scene {
    bsn! {
        Node {
            flex_direction: FlexDirection::Row,
            align_items: AlignItems::Center,
            column_gap: px(6),
            flex_shrink: 0.0,
        }
        Pickable::IGNORE
        Children [ @icon(glyph, 10.0, icons::ICON_DIM) ]
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
        BackgroundColor(theme::BG_POPOVER)
        BorderColor::all(theme::BORDER_STRONG)
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
                    column_gap: px(6),
                    align_items: AlignItems::Center,
                    flex_shrink: 0.0,
                }
                Children [
                    @icon(glyph, 11.0, icons::ICON_DIM)
                    --
                    @caption(t(Lang::default(), key)) LangLabel(key)
                ]
            },
        }
        Node {
            flex_shrink: 0.0,
            height: percent(100),
            padding: UiRect::horizontal(px(10)),
            border_radius: BorderRadius::ZERO,
        }
        EditorTab(kind)
        Tooltip(t(Lang::default(), key))
        LangTooltip(key)
        on(select_dock_tab)
        Children [
            Node {
                position_type: PositionType::Absolute,
                left: px(6),
                right: px(6),
                bottom: px(0),
                height: px(2),
                border_radius: px(1),
                display: {if kind == TabKind::Console { Display::Flex } else { Display::None }},
            }
            BackgroundColor(theme::ACCENT)
            TabUnderline(kind)
            Pickable::IGNORE
        ]
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
        @theme::pane()
        Children [
            @theme::pane_body()
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
                    Node { flex_grow: 1.0, min_width: px(0) }
                    Children [
                        @FeathersTextInput {
                            @visible_width: 20f32,
                        }
                        ConsoleInput
                    ]
                ]
                --
                Node {
                    flex_grow: 1.0,
                    min_height: px(0),
                    overflow: Overflow::scroll_y(),
                    flex_direction: FlexDirection::Column,
                }
                ConsoleHistory
                Children []
            ]
        ]
    }
}

fn logs_body() -> impl Scene {
    bsn! {
        @theme::pane()
        Children [
            @theme::pane_body()
            Node { width: percent(100), flex_direction: FlexDirection::Column }
            Children [
                // Debugger: a árvore de entidades da ENGINE A CORRER, a um clique.
                @FeathersToolButton {
                    @variant: ButtonVariant::Plain,
                    @caption: bsn! {
                        @icon_caption(fa::SITEMAP, t(Lang::default(), StrKey::BtnRemoteTree), icons::ICON_DIM)
                    },
                }
                Node { align_self: AlignSelf::FlexStart }
                RemoteTreeButton
                Tooltip(t(Lang::default(), StrKey::TipRemoteTree))
                LangTooltip(StrKey::TipRemoteTree)
                on(dump_remote_tree)
                --
                Node {
                    flex_grow: 1.0,
                    min_height: px(0),
                    overflow: Overflow::scroll_y(),
                    flex_direction: FlexDirection::Column,
                }
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
        @theme::pane()
        Children [
            @theme::pane_body()
            WorldDocBody
            Node { flex_direction: FlexDirection::Row, column_gap: px(10) }
            Children [
                // The minimap fills the dock height (square), the summary scrolls beside it.
                Node {
                    height: percent(100),
                    aspect_ratio: {Some(1.0)},
                    flex_shrink: 0.0,
                }
                WorldDocMinimap
                --
                Node {
                    flex_grow: 1.0,
                    min_width: px(0),
                    min_height: px(0),
                    overflow: Overflow::scroll_y(),
                    flex_direction: FlexDirection::Column,
                    row_gap: px(2),
                }
                WorldDocLines
                Children []
            ]
        ]
    }
}

/// Applies `--tab` once (QA/agents): the named bottom-dock tab opens at startup.
pub fn apply_startup_tab(
    config: Res<crate::app::EditorConfig>,
    mut ran: Local<bool>,
    mut active: ResMut<ActiveDockTab>,
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
        "problems" | "problemas" => TabKind::Problems,
        _ => return,
    };
    active.0 = kind;
}

fn select_dock_tab(
    activate: On<Activate>,
    tabs: Query<&EditorTab>,
    mut active: ResMut<ActiveDockTab>,
) {
    if let Ok(tab) = tabs.get(activate.event_target())
        && active.0 != tab.0
    {
        active.0 = tab.0;
    }
}

/// Shows the active tab's body and its underline; hides the rest.
pub fn apply_active_tab(
    active: Res<ActiveDockTab>,
    mut bodies: Query<(&TabBody, &mut Node), Without<TabUnderline>>,
    mut underlines: Query<(&TabUnderline, &mut Node), Without<TabBody>>,
) {
    if !active.is_changed() {
        return;
    }
    let display = |on: bool| if on { Display::Flex } else { Display::None };
    for (body, mut node) in &mut bodies {
        node.display = display(body.0 == active.0);
    }
    for (line, mut node) in &mut underlines {
        node.display = display(line.0 == active.0);
    }
}
