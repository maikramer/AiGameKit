//! Godot-style hover tooltips: any entity with a [`Tooltip`] shows a small floating panel
//! near the cursor after a short delay. One shared node follows the pointer.
//!
//! The panel is spawned as the LAST child of the editor root (see [`super::layout`]), so it
//! draws above every panel on the editor's UI camera.

use bevy::picking::hover::Hovered;
use bevy::prelude::*;
use bevy::window::PrimaryWindow;
use std::time::{Duration, Instant};

/// Hover-delay before a tooltip appears, matching desktop-toolkit feel.
const DELAY: Duration = Duration::from_millis(450);

/// The tooltip text of an entity (usually a button).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct Tooltip(pub &'static str);

/// The floating panel root (hidden unless a tooltip is showing).
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct TooltipRoot;

/// The tooltip's single text span.
#[derive(Component, Debug, Clone, Copy, Default)]
pub struct TooltipText;

#[derive(Resource, Debug, Default)]
struct TooltipState {
    target: Option<Entity>,
    hovered_since: Option<Instant>,
}

pub struct TooltipPlugin;

impl Plugin for TooltipPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<TooltipState>()
            .add_systems(Update, (track_tooltip_hover, update_tooltip).chain());
    }
}

/// The floating panel, as a scene (last child of the editor root).
pub fn tooltip_root() -> impl Scene {
    bsn! {
        TooltipRoot
        Node {
            position_type: PositionType::Absolute,
            display: Display::None,
            padding: UiRect::axes(px(8), px(4)),
            border: UiRect::all(px(1)),
            border_radius: px(4),
        }
        BackgroundColor(Color::srgba(0.07, 0.08, 0.1, 0.96))
        BorderColor::all(Color::srgb(0.35, 0.38, 0.44))
        // The panel must never catch the pointer (it would unhover what it explains).
        Pickable::IGNORE
        Children [
            @tooltip_text()
        ]
    }
}

fn tooltip_text() -> impl Scene {
    let placeholder = "";
    bsn! {
        TooltipText
        Text(placeholder)
        TextFont { font_size: {FontSize::Px(12.0)} }
        TextColor(Color::srgb(0.88, 0.9, 0.94))
    }
}

/// Tracks which tooltip-capable entity is hovered, and since when.
fn track_tooltip_hover(
    mut state: ResMut<TooltipState>,
    changed: Query<(Entity, &Hovered, Has<Tooltip>), Changed<Hovered>>,
) {
    for (entity, hovered, capable) in &changed {
        if !capable {
            continue;
        }
        if hovered.0 {
            state.target = Some(entity);
            state.hovered_since = Some(Instant::now());
        } else if state.target == Some(entity) {
            state.target = None;
            state.hovered_since = None;
        }
    }
}

/// Shows, positions and hides the panel. The panel follows the cursor, nudged left when
/// it would cross the window's right edge.
fn update_tooltip(
    state: Res<TooltipState>,
    windows: Query<&Window, With<PrimaryWindow>>,
    tooltips: Query<&Tooltip>,
    mut roots: Query<&mut Node, With<TooltipRoot>>,
    mut texts: Query<&mut Text, With<TooltipText>>,
) {
    let Ok(mut root) = roots.single_mut() else {
        return;
    };
    let Some(target) = state.target.filter(|&target| {
        state
            .hovered_since
            .is_some_and(|since| since.elapsed() >= DELAY)
            && tooltips.get(target).is_ok()
    }) else {
        root.display = Display::None;
        return;
    };
    let Ok(window) = windows.single() else {
        root.display = Display::None;
        return;
    };
    let Some(cursor) = window.cursor_position() else {
        root.display = Display::None;
        return;
    };
    if let Ok(tooltip) = tooltips.get(target)
        && let Ok(mut text) = texts.single_mut()
        && text.0 != tooltip.0
    {
        text.0 = tooltip.0.to_string();
    }
    root.display = Display::Flex;
    root.left = px((cursor.x + 14.0).min(window.width() - 240.0).max(0.0));
    root.top = px(cursor.y + 18.0);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delay_is_short_but_not_instant() {
        assert!(DELAY >= Duration::from_millis(300));
        assert!(DELAY <= Duration::from_millis(800));
    }
}
