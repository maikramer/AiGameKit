//! Reusable HUD widgets: stat rows, sparkline graphs and tab controls —
//! the building blocks shared by the menu and the profiler window.

use bevy::prelude::*;

use super::assets::{HudAssets, label};

/// A value line in a stats panel; `kind` discriminates which system writes
/// the text each refresh.
#[derive(Component)]
pub struct StatValue {
    pub kind: usize,
}

/// One bar of a frame-time sparkline; `slot` indexes the ring history.
#[derive(Component)]
pub struct GraphBar {
    pub slot: usize,
}

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

/// Label + value row (the value can be driven per frame via [`StatValue`]).
#[allow(clippy::too_many_arguments)]
pub fn stat_row(
    row: &mut bevy::ecs::hierarchy::ChildSpawner<'_>,
    hud: &HudAssets,
    label_text: &str,
    value_text: &str,
    kind: usize,
    width: f32,
) {
    row.spawn((Node {
        width: Val::Px(width),
        justify_content: JustifyContent::SpaceBetween,
        margin: UiRect::bottom(Val::Px(4.0)),
        ..Default::default()
    },))
        .with_children(|row| {
            row.spawn(label(
                hud,
                label_text,
                12.0,
                Color::srgba(0.85, 0.82, 0.74, 0.9),
            ));
            row.spawn((
                Text::new(value_text.to_string()),
                TextColor(Color::srgb(0.92, 0.94, 0.88)),
                TextFont {
                    font: hud.font.clone().into(),
                    font_size: 13.0.into(),
                    ..Default::default()
                },
                StatValue { kind },
            ));
        });
}

/// Sparkline: `count` vertical bars (frame-time history). Bars carry
/// [`GraphBar`] with their ring slot for per-frame height/color updates by
/// the owning system.
pub fn sparkline(
    row: &mut bevy::ecs::hierarchy::ChildSpawner<'_>,
    count: usize,
    bar_width: f32,
    gap: f32,
    height: f32,
) {
    row.spawn(Node {
        width: Val::Px(count as f32 * (bar_width + gap)),
        height: Val::Px(height),
        align_items: AlignItems::FlexEnd,
        column_gap: Val::Px(gap),
        margin: UiRect::bottom(Val::Px(10.0)),
        ..Default::default()
    })
    .with_children(|graph| {
        for slot in 0..count {
            graph.spawn((
                Node {
                    width: Val::Px(bar_width),
                    height: Val::Px(height * 0.2),
                    border_radius: BorderRadius::all(Val::Px(1.0)),
                    ..Default::default()
                },
                BackgroundColor(Color::srgb(0.36, 0.72, 0.30)),
                GraphBar { slot },
            ));
        }
    });
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
