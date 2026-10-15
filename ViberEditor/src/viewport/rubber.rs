//! Rubber-band multi-selection (Fase A1): drag over empty viewport space in
//! Select mode to marquee-select every node with an entity inside the rect.
//! A real drag suppresses the click-pick (the same courtesy `FeatureGrab`
//! gets), and Shift extends the current selection.

use bevy::prelude::*;
use viber::scene::SceneSource;
use viber::scene::doc::SceneDoc;

use super::ViewportHover;
use super::gizmo::{GizmoTool, Mode, ViewportCursor};
use crate::scene::Selection;
use viber::editor_camera::EditorCamera;

/// Drag threshold in viewport px (below it, the press stays a click → pick).
const DRAG_PX: f32 = 6.0;

/// True while the rubber-band owns the pointer (the pick stands down).
#[derive(Resource, Debug, Default)]
pub struct RubberGrab;

#[derive(Resource, Debug, Default, Clone, Copy)]
pub struct RubberBand {
    pub active: bool,
    pub start: Vec2,
    pub now: Vec2,
}

/// The marquee rectangle overlay (spawned by the layout, positioned here).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct RubberRect;

pub struct RubberPlugin;

impl Plugin for RubberPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<RubberBand>()
            .add_systems(Update, rubber_interaction.after(super::gizmo::GizmoSet));
    }
}

fn rubber_interaction(world: &mut World) {
    let mode = world.resource::<GizmoTool>().mode;
    let cursor = *world.resource::<ViewportCursor>();
    let buttons = world.resource::<ButtonInput<MouseButton>>().clone();
    let hover = world.resource::<ViewportHover>().0;
    let mut band = *world.resource::<RubberBand>();

    if mode != Mode::Select {
        band.active = false;
        world.remove_resource::<RubberGrab>();
        *world.resource_mut::<RubberBand>() = band;
        return;
    }
    let Some(screen) = cursor.screen else {
        band.active = false;
        world.remove_resource::<RubberGrab>();
        *world.resource_mut::<RubberBand>() = band;
        position_rect(world);
        return;
    };
    if buttons.just_pressed(MouseButton::Left) && hover {
        band.start = screen;
        band.now = screen;
        band.active = false; // só vira rubber depois do threshold
    }
    if buttons.pressed(MouseButton::Left) && band.start != Vec2::ZERO {
        band.now = screen;
        if !band.active && band.now.distance(band.start) > DRAG_PX {
            band.active = true;
            world.insert_resource(RubberGrab);
        }
    }
    if band.active && buttons.just_released(MouseButton::Left) {
        band.active = false;
        world.remove_resource::<RubberGrab>();
        select_in_rect(
            world,
            band,
            world.resource::<ButtonInput<KeyCode>>().clone(),
        );
        band.start = Vec2::ZERO;
        band.now = Vec2::ZERO;
    }
    *world.resource_mut::<RubberBand>() = band;
    position_rect(world);
}

/// Positions (or hides) the marquee overlay from the current band.
fn position_rect(world: &mut World) {
    let band = *world.resource::<RubberBand>();
    let mut rects = world.query_filtered::<&mut Node, With<RubberRect>>();
    let Ok(mut node) = rects.single_mut(world) else {
        return;
    };
    node.display = if band.active {
        Display::Flex
    } else {
        Display::None
    };
    if band.active {
        let min = band.start.min(band.now);
        let size = (band.now - band.start).abs();
        node.position_type = PositionType::Absolute;
        node.left = Val::Px(min.x);
        node.top = Val::Px(min.y);
        node.width = Val::Px(size.x.max(1.0));
        node.height = Val::Px(size.y.max(1.0));
    }
}

/// Every node with an entity inside the viewport-space rect joins the
/// selection (dedup, roots out, ordered by node for stability).
fn select_in_rect(world: &mut World, band: RubberBand, keys: ButtonInput<KeyCode>) {
    let Some(mut cameras) =
        world.try_query_filtered::<(&Camera, &GlobalTransform), With<EditorCamera>>()
    else {
        return;
    };
    let Ok((camera, camera_global)) = cameras.single(world).map(|(c, g)| (c.clone(), *g)) else {
        return;
    };
    let rect_min = band.start.min(band.now);
    let rect_max = band.start.max(band.now);
    let doc = world.resource::<SceneDoc>().clone();
    let mut sources = world.query_filtered::<(&GlobalTransform, &SceneSource), ()>();
    let mut picked: Vec<viber::scene::doc::NodeRef> = Vec::new();
    for (global, source) in sources.iter(world) {
        let Ok(screen) = camera.world_to_viewport(&camera_global, global.translation()) else {
            continue;
        };
        if screen.x < rect_min.x
            || screen.y < rect_min.y
            || screen.x > rect_max.x
            || screen.y > rect_max.y
        {
            continue;
        }
        if let Some(node) = doc.node_ref_for(&source.0)
            && !node.is_root()
            && !picked.contains(&node)
        {
            picked.push(node);
        }
    }
    picked.sort();
    let shift = keys.any_pressed([KeyCode::ShiftLeft, KeyCode::ShiftRight]);
    let mut selection = world.resource_mut::<Selection>();
    if shift {
        for node in picked {
            selection.extend(&node);
        }
    } else if !picked.is_empty() {
        selection.0 = picked;
    }
}
