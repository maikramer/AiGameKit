//! The UI-mode canvas tools (Fase 4): the selected `<UiRoot>` subtree's
//! computed rect overlaid on the letterboxed game-UI canvas, with MOVE (drag
//! the body) and RESIZE (drag a corner) handles for nodes with explicit
//! `left`/`top`/`width`/`height`, a Normal/Hover/Pressed state preview (the
//! engine's real `UiPreviewState` — `:hover`/`:active` cascade rules apply),
//! and a `menu_data` info line for `<UiList>` selections. Every commit is ONE
//! canonical `set` op — the same edit an agent makes with `viber scene set`.

use super::theme;
use crate::viewport::EditorViewport as EditorViewportAlias;
use bevy::feathers::controls::{ButtonVariant, FeathersToolButton};
use bevy::feathers::display::caption;
use bevy::prelude::*;
use bevy::ui_widgets::Activate;
use viber::scene::doc::{NodeRef, SceneDoc};
use viber::scene::ops::EditOp;
use viber::scene::pose::format_num;
use viber::ui::runtime::{StyleState, UiPreviewState};

use super::mode_strip::{EditorMode, EditorModeState};
use super::strings::{Lang, StrKey, t};
use crate::scene::{PendingEdits, Selection};

/// Grab radius, window px.
const GRAB_PX: f32 = 14.0;

/// The overlay root (shown in UI mode with a selection that maps to UI).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct CanvasOverlay;

/// The selection rectangle (border only).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct CanvasRect;

/// One of the four corner handles (`0` top-left, clockwise).
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct CanvasCorner(pub usize);

/// A state-preview chip.
#[derive(Component, Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct StateChip(pub UiStatePreview);

/// The info line (dims + menu_data).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct CanvasInfo;

/// The chips+info block, positioned inside the VIEWPORT (not the window).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct CanvasChips;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum UiStatePreview {
    #[default]
    Normal,
    Hover,
    Pressed,
}

impl UiStatePreview {
    fn state(self) -> Option<StyleState> {
        match self {
            UiStatePreview::Normal => None,
            UiStatePreview::Hover => Some(StyleState::Hover),
            UiStatePreview::Pressed => Some(StyleState::Active),
        }
    }
}

/// The drag in progress.
#[derive(Resource, Debug, Default, Clone)]
pub struct CanvasDrag {
    grabbed: bool,
    resize: bool,
    /// Rect at press time, game-UI logical px.
    start: Rect,
    /// Cursor at press / now, window physical px.
    cursor: Vec2,
    now: Vec2,
}

/// The forced state preview (chips); reverts with the chip back to Normal.
#[derive(Resource, Debug, Default)]
pub struct CanvasPreview(pub UiStatePreview);

pub struct CanvasPlugin;

impl Plugin for CanvasPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<CanvasDrag>()
            .init_resource::<CanvasPreview>()
            .add_systems(
                Update,
                (
                    canvas_overlay,
                    canvas_interaction,
                    canvas_state_preview,
                    canvas_menu_info,
                )
                    .chain(),
            );
    }
}

/// The overlay, as a scene (child of the editor root; positioned by systems).
pub fn canvas_overlay_scene() -> impl Scene {
    bsn! {
        CanvasOverlay
        GlobalZIndex(11)
        Node {
            position_type: PositionType::Absolute,
            display: Display::None,
            width: percent(100),
            height: percent(100),
        }
        Pickable::IGNORE
        Children [
            Node {
                position_type: PositionType::Absolute,
                border: UiRect::all(px(1)),
                border_radius: px(2),
            }
        CanvasRect
            BorderColor::all(Color::srgb(0.45, 0.85, 1.0))
            Pickable::IGNORE
            --
            @corner(0)
            --
            @corner(1)
            --
            @corner(2)
            --
            @corner(3)
            --
            Node {
                position_type: PositionType::Absolute,
                left: px(16),
                top: px(48),
                flex_direction: FlexDirection::Column,
                row_gap: px(4),
                padding: px(6),
                border: UiRect::all(px(1)),
                border_radius: px(6),
            }
            BackgroundColor(theme::BG_OVERLAY)
            BorderColor::all(theme::BORDER_STRONG)
        CanvasChips
            Children [
                Node {
                    flex_direction: FlexDirection::Row,
                    column_gap: px(4),
                }
                Children [
                    @state_chip(UiStatePreview::Normal, StrKey::StateNormal)
                    --
                    @state_chip(UiStatePreview::Hover, StrKey::StateHover)
                    --
                    @state_chip(UiStatePreview::Pressed, StrKey::StatePressed)
                ]
                --
                @caption("") CanvasInfo
            ]
        ]
    }
}

fn corner(index: usize) -> impl Scene {
    bsn! {
        Node {
            position_type: PositionType::Absolute,
            width: px(10),
            height: px(10),
            border: UiRect::all(px(1)),
            border_radius: px(2),
        }
        BackgroundColor(Color::srgb(0.09, 0.1, 0.12))
        CanvasCorner(index)
        BorderColor::all(Color::srgb(1.0, 0.75, 0.3))
        Pickable::IGNORE
    }
}

fn state_chip(state: UiStatePreview, key: StrKey) -> impl Scene {
    bsn! {
        @FeathersToolButton {
            @variant: ButtonVariant::Plain,
            @caption: bsn! { @caption(t(Lang::default(), key)) },
        }
        StateChip(state)
        on(state_chip_clicked)
    }
}

fn state_chip_clicked(
    activate: On<Activate>,
    chips: Query<&StateChip>,
    mut preview: ResMut<CanvasPreview>,
) {
    if let Ok(chip) = chips.get(activate.event_target()) {
        preview.0 = chip.0;
    }
}

/// The selected node's UI rect in GAME-UI logical px (the units the XML's
/// `left/top/width/height` speak), via `SceneSource` provenance.
fn selected_ui_rect(world: &mut World) -> Option<Rect> {
    let node = world.resource::<Selection>().primary().cloned()?;
    let origin = world.resource::<SceneDoc>().origin_of(&node);
    let scale = world
        .query::<&Window>()
        .iter(world)
        .next()
        .map(|window| window.scale_factor())
        .unwrap_or(1.0);
    let mut query = world.query_filtered::<(
        &ComputedNode,
        &UiGlobalTransform,
        &viber::scene::SceneSource,
    ), Without<CanvasOverlay>>();
    let mut rect: Option<Rect> = None;
    for (node_size, transform, source) in query.iter(world) {
        if *source.0 != origin {
            continue;
        }
        let size = node_size.size() / scale;
        if size.x < 1.0 || size.y < 1.0 {
            continue;
        }
        let center = transform.translation / scale;
        let half = size / 2.0;
        let candidate = Rect::new(
            center.x - half.x,
            center.y - half.y,
            center.x + half.x,
            center.y + half.y,
        );
        // o MENOR rect do ramo ganha: o elemento mais específico sob o nó
        rect = Some(match rect {
            Some(existing)
                if existing.width() * existing.height()
                    < candidate.width() * candidate.height() =>
            {
                existing
            }
            _ => candidate,
        });
    }
    rect
}

/// The selected node's UI entities (for the state preview).
fn selected_ui_entities(world: &mut World) -> Vec<Entity> {
    let Some(node) = world.resource::<Selection>().primary().cloned() else {
        return Vec::new();
    };
    let origin = world.resource::<SceneDoc>().origin_of(&node);
    let mut query = world.query_filtered::<Entity, With<viber::scene::SceneSource>>();
    query
        .iter(world)
        .filter(|entity| {
            world
                .get::<viber::scene::SceneSource>(*entity)
                .is_some_and(|source| *source.0 == origin)
        })
        .collect()
}

/// Maps a game-UI rect to WINDOW logical px: the game's UI camera draws into the viewport
/// image (letterboxed 16:9 inside it), and that image is shown by the viewport node — so
/// the canvas origin is the node's top-left plus the letterbox offset.
fn canvas_to_window(world: &mut World, rect: Rect) -> Option<Rect> {
    let mut cameras = world.try_query_filtered::<&Camera, With<crate::viewport::GameUiCamera>>()?;
    let camera = cameras.single(world).ok()?;
    let viewport = camera.viewport.clone()?;
    let scale = world
        .query::<&Window>()
        .iter(world)
        .next()
        .map(|window| window.scale_factor())
        .unwrap_or(1.0);
    let mut nodes =
        world.query_filtered::<(&ComputedNode, &UiGlobalTransform), With<EditorViewportAlias>>();
    let (node, transform) = nodes.single(world).ok()?;
    // UI transforms/sizes are physical px; the game rect is logical px from the canvas'
    // top-left (y down), so it only needs the canvas origin added.
    let node_origin = transform.translation - node.size() * 0.5;
    let origin = (node_origin + viewport.physical_position.as_vec2()) / scale;
    Some(Rect::new(
        origin.x + rect.min.x,
        origin.y + rect.min.y,
        origin.x + rect.max.x,
        origin.y + rect.max.y,
    ))
}

/// Shows/positions the overlay (with the live drag preview applied).
fn canvas_overlay(world: &mut World) {
    let active = world.resource::<EditorModeState>().0 == EditorMode::Ui;
    let rect = if active {
        selected_ui_rect(world)
    } else {
        None
    };
    {
        let mut overlays = world.query_filtered::<&mut Node, With<CanvasOverlay>>();
        let Ok(mut overlay) = overlays.single_mut(world) else {
            return;
        };
        overlay.display = if rect.is_some() {
            Display::Flex
        } else {
            Display::None
        };
    }
    let Some(game_rect) = rect else {
        return;
    };
    let Some(mut window_rect) = canvas_to_window(world, game_rect) else {
        return;
    };
    // preview do arrasto em curso
    let drag = world.resource::<CanvasDrag>().clone();
    let scale = world
        .query::<&Window>()
        .iter(world)
        .next()
        .map(|window| window.scale_factor())
        .unwrap_or(1.0);
    if drag.grabbed {
        let delta = (drag.now - drag.cursor) / scale;
        if drag.resize {
            window_rect.max += delta;
        } else {
            window_rect.min += delta;
            window_rect.max += delta;
        }
    }
    let size = (window_rect.max - window_rect.min).max(Vec2::splat(4.0));
    let corners = [
        window_rect.min,
        Vec2::new(window_rect.max.x, window_rect.min.y),
        window_rect.max,
        Vec2::new(window_rect.min.x, window_rect.max.y),
    ];
    // chips/info: o canto SUPERIOR ESQUERDO do viewport (o overlay vive na
    // janela; sem isto os chips caem sobre o dock Objects).
    let mut viewports =
        world.query_filtered::<(&ComputedNode, &UiGlobalTransform), With<EditorViewportAlias>>();
    if let Ok((node, transform)) = viewports.single_mut(world) {
        let vsize = node.size();
        let top_left = (transform.translation - vsize * 0.5) / scale;
        let mut chips = world.query_filtered::<&mut Node, With<CanvasChips>>();
        if let Ok(mut chip_node) = chips.single_mut(world) {
            // canto inferior esquerdo do viewport (o topo é do HUD do jogo)
            chip_node.left = Val::Px(top_left.x + 12.0);
            chip_node.top = Val::Px(top_left.y + vsize.y / scale - 82.0);
        }
    }
    let mut rects = world.query_filtered::<&mut Node, With<CanvasRect>>();
    if let Ok(mut node) = rects.single_mut(world) {
        node.position_type = PositionType::Absolute;
        node.left = Val::Px(window_rect.min.x);
        node.top = Val::Px(window_rect.min.y);
        node.width = Val::Px(size.x);
        node.height = Val::Px(size.y);
    }
    let mut handles = world.query::<(&CanvasCorner, &mut Node)>();
    for (marker, mut node) in handles.iter_mut(world) {
        let corner = corners[marker.0.min(3)];
        node.position_type = PositionType::Absolute;
        node.left = Val::Px(corner.x - 5.0);
        node.top = Val::Px(corner.y - 5.0);
    }
}

/// Press near a corner → resize; press inside the rect → move; release →
/// ONE `set` op (px, canonical). Nodes without explicit attrs stay read-only.
fn canvas_interaction(world: &mut World) {
    if world.resource::<EditorModeState>().0 != EditorMode::Ui {
        world.resource_mut::<CanvasDrag>().grabbed = false;
        return;
    }
    let Some(node) = world.resource::<Selection>().primary().cloned() else {
        return;
    };
    let Some(window) = world.query::<&Window>().iter(world).next().cloned() else {
        return;
    };
    let Some(cursor_window) = window.cursor_position() else {
        return;
    };
    let cursor = cursor_window * window.scale_factor();
    let buttons = world.resource::<ButtonInput<MouseButton>>().clone();
    let mut drag = world.resource::<CanvasDrag>().clone();

    let Some(game_rect) = selected_ui_rect(world) else {
        return;
    };
    let Some(window_rect) = canvas_to_window(world, game_rect) else {
        return;
    };
    let min = window_rect.min * window.scale_factor();
    let max = window_rect.max * window.scale_factor();
    if buttons.just_pressed(MouseButton::Left) {
        let corners = [min, Vec2::new(max.x, min.y), max, Vec2::new(min.x, max.y)];
        let near_corner = corners
            .iter()
            .any(|corner| corner.distance(cursor) < GRAB_PX);
        let inside =
            cursor.x >= min.x && cursor.x <= max.x && cursor.y >= min.y && cursor.y <= max.y;
        if near_corner || inside {
            drag.grabbed = true;
            drag.resize = near_corner;
            drag.cursor = cursor;
            drag.now = cursor;
            drag.start = game_rect;
        }
    }
    if drag.grabbed {
        drag.now = cursor;
    }
    if drag.grabbed && buttons.just_released(MouseButton::Left) {
        drag.grabbed = false;
        let delta = (drag.now - drag.cursor) / window.scale_factor();
        commit(world, &node, drag.start, delta, drag.resize);
    }
    *world.resource_mut::<CanvasDrag>() = drag;
}

/// Writes the moved/resized geometry back as ONE `set` op (game-UI px).
fn commit(world: &mut World, node: &NodeRef, start: Rect, delta: Vec2, resize: bool) {
    let Some(xml) = world.resource::<SceneDoc>().node(node).cloned() else {
        return;
    };
    let mut attrs: Vec<(String, Option<String>)> = Vec::new();
    if resize {
        if xml.attr("width").is_some() {
            attrs.push((
                "width".into(),
                Some(format_num((start.width() + delta.x).max(4.0))),
            ));
        }
        if xml.attr("height").is_some() {
            attrs.push((
                "height".into(),
                Some(format_num((start.height() + delta.y).max(4.0))),
            ));
        }
    } else {
        if xml.attr("left").is_some() {
            attrs.push(("left".into(), Some(format_num(start.min.x + delta.x))));
        }
        if xml.attr("top").is_some() {
            attrs.push(("top".into(), Some(format_num(start.min.y + delta.y))));
        }
    }
    if attrs.is_empty() {
        // nó sem posição/tamanho explícitos: o canvas mostra-o (read-only)
        return;
    }
    let selector = world.resource::<SceneDoc>().selector(node);
    world.resource_mut::<PendingEdits>().push(
        "ui canvas".to_string(),
        vec![EditOp::Set {
            node: selector,
            attrs: attrs.into_iter().collect(),
        }],
    );
}

/// Applies the chips' state preview to the node's UI entities (the engine's
/// cascade honors `UiPreviewState`; a `Normal` chip removes it).
fn canvas_state_preview(world: &mut World) {
    let preview = world.resource::<CanvasPreview>().0;
    for entity in selected_ui_entities(world) {
        match preview.state() {
            Some(state) => {
                if world
                    .get::<UiPreviewState>(entity)
                    .is_none_or(|current| current.0 != state)
                {
                    world.entity_mut(entity).insert(UiPreviewState(state));
                }
            }
            None => {
                if world.get::<UiPreviewState>(entity).is_some() {
                    world.entity_mut(entity).remove::<UiPreviewState>();
                }
            }
        }
    }
}

/// The info line: the rect's size in game-UI px + the resolved `menu_data`
/// rows when the selection is (or contains) a `<UiList>`.
fn canvas_menu_info(world: &mut World) {
    let mode = world.resource::<EditorModeState>().0;
    let Some(node) = world.resource::<Selection>().primary().cloned() else {
        clear_info(world, mode);
        return;
    };
    let Some(xml) = world.resource::<SceneDoc>().node(&node).cloned() else {
        clear_info(world, mode);
        return;
    };
    if mode != EditorMode::Ui {
        clear_info(world, mode);
        return;
    }
    let lang = *world.resource::<Lang>();
    let Some(rect) = selected_ui_rect(world) else {
        clear_info(world, mode);
        return;
    };
    let mut parts = vec![format!(
        "{} {}×{}",
        t(lang, StrKey::CanvasDims),
        format_num(rect.width()),
        format_num(rect.height()),
    )];
    if let Some(source) = list_source(&xml) {
        let rows = world
            .get_resource::<viber::ui::list::UiLists>()
            .map(|lists| lists.rows(&source).to_vec())
            .unwrap_or_default();
        let labels: Vec<String> = rows
            .iter()
            .take(6)
            .map(|row| {
                row.get("label")
                    .cloned()
                    .or_else(|| row.values().next().cloned())
                    .unwrap_or_default()
            })
            .filter(|label| !label.is_empty())
            .collect();
        parts.push(format!(
            "{} \"{source}\": {}",
            t(lang, StrKey::CanvasMenu),
            if labels.is_empty() {
                "—".into()
            } else {
                labels.join(" · ")
            }
        ));
    }
    let wanted = parts.join("  |  ");
    let mut infos = world.query_filtered::<&mut Text, With<CanvasInfo>>();
    if let Ok(mut info) = infos.single_mut(world)
        && info.0 != wanted
    {
        info.0 = wanted;
    }
}

fn clear_info(world: &mut World, mode: EditorMode) {
    if mode != EditorMode::Ui {
        let mut infos = world.query_filtered::<&mut Text, With<CanvasInfo>>();
        if let Ok(mut info) = infos.single_mut(world)
            && !info.0.is_empty()
        {
            info.0.clear();
        }
    }
}

/// The first `<UiList bind>` attr at or under the node. The engine reads
/// `bind` (`Viber/src/ui/tree.rs`) — `source` is accepted as a legacy alias
/// from the VibeGame spelling, but the canonical key is `bind`.
fn list_source(xml: &viber::xml::XmlNode) -> Option<String> {
    let is_list = |node: &viber::xml::XmlNode| {
        node.tag
            .eq_ignore_ascii_case("uilist")
            .then(|| {
                node.attr("bind")
                    .or_else(|| node.attr("source"))
                    .map(str::to_owned)
            })
            .flatten()
    };
    if let Some(source) = is_list(xml) {
        return Some(source);
    }
    let mut stack: Vec<viber::xml::XmlNode> = xml.children.clone();
    while let Some(node) = stack.pop() {
        if let Some(source) = is_list(&node) {
            return Some(source);
        }
        stack.extend(node.children.clone());
    }
    None
}
