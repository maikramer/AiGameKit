//! Health and XP bar dynamics: fill nodes mirror the player's [`Health`] /
//! [`Xp`] every frame (VibeGame `healthbar`/`xpbar` parity).

use bevy::prelude::*;

use crate::vitals::{Health, Xp, health_fraction, xp_fraction};

/// Marker for the healthbar fill node (width mirrors the player's `Health`).
#[derive(Component)]
pub struct HudHealthFill;

/// Marker for the healthbar label node ("100/100").
#[derive(Component)]
pub struct HudHealthLabel;

/// Marker for the xpbar fill node (width mirrors the player's `Xp`).
#[derive(Component)]
pub struct HudXpFill;

/// Marker for the xpbar label node ("0/100", dim, right-aligned).
#[derive(Component)]
pub struct HudXpLabel;

/// Label text for the healthbar ("{cur}/{max}", hp rounded for display).
pub fn health_label_text(current: f32, max: f32) -> String {
    format!("{}/{}", current.round() as i32, max.round() as i32)
}

/// Label text for the xpbar ("{cur}/{next}").
pub fn xp_label_text(current: u32, next: u32) -> String {
    format!("{current}/{next}")
}

/// Mirror the hero's [`Health`] into the `healthbar` fill width and label.
/// Without a `Health` on the player it shows the default 100/100.
pub fn hud_health_sync(
    players: Query<&Health, With<crate::player::Player>>,
    mut fills: Query<&mut Node, With<HudHealthFill>>,
    mut labels: Query<&mut Text, With<HudHealthLabel>>,
) {
    let (current, max) = players
        .iter()
        .next()
        .map(|h| (h.current, h.max))
        .unwrap_or((100.0, 100.0));
    let percent = health_fraction(current, max) * 100.0;
    for mut node in &mut fills {
        node.width = Val::Percent(percent);
    }
    let text = health_label_text(current, max);
    for mut label in &mut labels {
        if label.0 != text {
            label.0 = text.clone();
        }
    }
}

/// Mirror the hero's [`Xp`] into the `xpbar` fill width and label.
/// Without an `Xp` on the player it shows the default 0/100.
pub fn hud_xp_sync(
    players: Query<&Xp, With<crate::player::Player>>,
    mut fills: Query<&mut Node, With<HudXpFill>>,
    mut labels: Query<&mut Text, With<HudXpLabel>>,
) {
    let (current, next) = players
        .iter()
        .next()
        .map(|x| (x.current, x.next))
        .unwrap_or((0, 100));
    let percent = xp_fraction(current, next) * 100.0;
    for mut node in &mut fills {
        node.width = Val::Percent(percent);
    }
    let text = xp_label_text(current, next);
    for mut label in &mut labels {
        if label.0 != text {
            label.0 = text.clone();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_health_label_text() {
        assert_eq!(health_label_text(100.0, 100.0), "100/100");
        assert_eq!(health_label_text(87.5, 100.0), "88/100"); // rounds
        assert_eq!(health_label_text(0.0, 100.0), "0/100");
    }

    #[test]
    fn test_xp_label_text() {
        assert_eq!(xp_label_text(0, 100), "0/100");
        assert_eq!(xp_label_text(30, 150), "30/150");
    }
}
