//! Reusable HUD widgets: stat rows, sparkline graphs and tab controls —
//! the building blocks shared by the menu and the profiler window.

use bevy::prelude::*;
// Bevy 0.20: `Interaction`/`Button` do prelude são aliases de tipos privados.
use crate::ui::interaction::{Button, Interaction};

use super::assets::{HudAssets, label};

/// A value line in a stats panel; `kind` discriminates which system writes
/// A clickable tab button.
#[derive(Component)]
pub struct TabButton {
    pub tab: usize,
}

/// A tab content frame; only the active tab is visible.
#[derive(Component)]
pub struct TabContent {
    pub tab: usize,
}

/// Tab button row: one [`TabButton`] per label.
pub fn tab_buttons(
    row: &mut bevy::ecs::hierarchy::ChildSpawner<'_>,
    hud: &HudAssets,
    labels: &[&str],
) {
    row.spawn(Node {
        column_gap: Val::Px(6.0),
        margin: UiRect::bottom(Val::Px(10.0)),
        ..Default::default()
    })
    .with_children(|row| {
        for (tab, label_text) in labels.iter().enumerate() {
            row.spawn((
                Node {
                    padding: UiRect::axes(Val::Px(12.0), Val::Px(5.0)),
                    border_radius: BorderRadius::all(Val::Px(8.0)),
                    ..Default::default()
                },
                Button,
                Interaction::default(),
                BackgroundColor(if tab == 0 {
                    Color::srgba(0.9, 0.7, 0.2, 0.85)
                } else {
                    Color::srgba(0.16, 0.15, 0.13, 0.85)
                }),
                label(
                    hud,
                    *label_text,
                    13.0,
                    if tab == 0 {
                        Color::srgb(0.25, 0.17, 0.03)
                    } else {
                        Color::srgb(0.95, 0.93, 0.85)
                    },
                ),
                TabButton { tab },
            ));
        }
    });
}

/// One controls row: key hint in gold, action in warm light.
pub fn controls_row(
    row: &mut bevy::ecs::hierarchy::ChildSpawner<'_>,
    hud: &HudAssets,
    key: &str,
    action: &str,
    width: f32,
) {
    row.spawn(Node {
        width: Val::Px(width),
        justify_content: JustifyContent::SpaceBetween,
        margin: UiRect::bottom(Val::Px(6.0)),
        ..Default::default()
    })
    .with_children(|row| {
        row.spawn(label(hud, key, 13.0, Color::srgb(0.95, 0.78, 0.28)));
        row.spawn(label(hud, action, 13.0, Color::srgb(0.88, 0.86, 0.8)));
    });
}
