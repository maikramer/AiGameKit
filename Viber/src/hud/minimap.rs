//! Circular minimap: the player arrow rotates with the camera heading and
//! numbered quest dots plot nearby NPCs north-up.

use bevy::math::Rot2;
use bevy::prelude::*;

use crate::player::Player;
use crate::recipes::spawn::{DialogueNpc, OrbitCamera};

/// The minimap player arrow (rotation mirrors the camera heading).
#[derive(Component)]
pub struct MinimapArrow;

/// A quest dot on the minimap (positioned at a nearby NPC's world spot).
#[derive(Component)]
pub struct MinimapDot;

/// Radius of the minimap in world meters (authored `range` attribute).
#[derive(Component)]
pub struct MinimapRange(pub f32);

/// Minimap position (UI px offsets from the map center) for a world delta
/// `(dx, dz)` from the player, north-up: camera yaw rotates the plot so the
/// character's facing feels stable. Clamped to the map radius.
pub fn minimap_xy(dx: f32, dz: f32, yaw_deg: f32, range_m: f32, radius_px: f32) -> (f32, f32) {
    let yaw = yaw_deg.to_radians();
    let (sin, cos) = yaw.sin_cos();
    // Camera forward (fx, fz), right (rx, rz) — matches player::process_input.
    let (fx, fz) = (-sin, -cos);
    let (rx, rz) = (cos, -sin);
    let scale = radius_px / range_m.max(1.0);
    let x = (dx * rx + dz * rz) * scale;
    let y_up = (dx * fx + dz * fz) * scale;
    let len = (x * x + y_up * y_up).sqrt();
    let clamp = radius_px * 0.92;
    let (x, y_up) = if len > clamp {
        (x / len * clamp, y_up / len * clamp)
    } else {
        (x, y_up)
    };
    // UI y grows downward.
    (x, -y_up)
}

/// Minimap arrow rotation (radians, clockwise) for a camera yaw — the arrow
/// points where the camera faces, on a north-up map.
pub fn arrow_rotation_rad(yaw_deg: f32) -> f32 {
    -yaw_deg.to_radians()
}

/// Animate the minimap: arrow rotation + quest dots at nearby NPC spots.
#[allow(clippy::type_complexity)]
pub fn hud_minimap_update(
    cameras: Query<&OrbitCamera>,
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<&GlobalTransform, With<DialogueNpc>>,
    maps: Query<(&Node, &MinimapRange), (Without<MinimapArrow>, Without<MinimapDot>)>,
    mut arrow: Query<&mut UiTransform, (With<MinimapArrow>, Without<MinimapDot>)>,
    mut dots: Query<(&mut Node, &mut UiTransform, &mut Visibility), With<MinimapDot>>,
) {
    let Some(cam) = cameras.iter().next() else {
        return;
    };
    let Some(mapper) = players.iter().next() else {
        return;
    };
    let player_pos = mapper.translation();
    if let Ok(mut transform) = arrow.single_mut() {
        *transform = UiTransform::from_rotation(Rot2::radians(arrow_rotation_rad(cam.yaw_deg)));
    }
    let Ok((map_node, range)) = maps.single() else {
        return;
    };
    // Node height is authored px (148) — derive the inner radius from it.
    let radius = match map_node.height {
        Val::Px(h) => h / 2.0 - 6.0,
        _ => 68.0,
    };
    let range_m = range.0;
    let mut npcs: Vec<_> = npcs
        .iter()
        .map(|t| t.translation())
        .map(|p| (p.x - player_pos.x, p.z - player_pos.z))
        .collect();
    npcs.sort_by(|a, b| {
        (a.0 * a.0 + a.1 * a.1)
            .partial_cmp(&(b.0 * b.0 + b.1 * b.1))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    for (index, (mut node, mut transform, mut visibility)) in dots.iter_mut().enumerate() {
        if let Some(&(dx, dz)) = npcs.get(index % npcs.len().max(1)) {
            let (x, y) = minimap_xy(dx, dz, cam.yaw_deg, range_m, radius);
            node.left = Val::Percent(50.0);
            node.top = Val::Percent(50.0);
            *transform = UiTransform::from_translation(Val2::new(Val::Px(x), Val::Px(y)));
            *visibility = Visibility::Visible;
        } else {
            *visibility = Visibility::Hidden;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-4
    }

    #[test]
    fn test_minimap_xy_north_up() {
        let radius = 68.0;
        // NPC directly north (−Z) of the player, camera facing north:
        // dot sits straight up on the map (UI y negative).
        let (x, y) = minimap_xy(0.0, -30.0, 0.0, 60.0, radius);
        assert!(approx(x, 0.0) && y < 0.0 && approx(y, -34.0), "({x},{y})");
        // NPC to the east: dot to the right regardless of camera yaw.
        let (x, _) = minimap_xy(30.0, 0.0, 0.0, 60.0, radius);
        assert!(x > 0.0);
        // Camera swung 180°: the same north NPC now plots below center.
        let (_, y) = minimap_xy(0.0, -30.0, 180.0, 60.0, radius);
        assert!(y > 0.0);
        // Beyond range clamps inside the circle.
        let (x, y) = minimap_xy(0.0, -500.0, 0.0, 60.0, radius);
        assert!((x * x + y * y).sqrt() <= radius * 0.93);
    }

    #[test]
    fn test_arrow_rotation_points_facing() {
        // Facing north → arrow up; facing east (yaw −90) → rotated +90°.
        assert!(approx(arrow_rotation_rad(0.0), 0.0));
        assert!(approx(
            arrow_rotation_rad(-90.0),
            std::f32::consts::FRAC_PI_2
        ));
    }
}
