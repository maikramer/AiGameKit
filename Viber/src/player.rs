//! `<PlayerGLTF>` runtime: WASD movement over the terrain plus the
//! third-person orbit camera driven by mouse drag and scroll.

use bevy::math::Quat;
use bevy::math::Vec3;
use bevy::prelude::*;

use crate::recipes::spawn::DialogueNpc;
use crate::terrain::runtime::TerrainRuntime;

/// The controllable hero.
#[derive(Debug, Component)]
pub struct Player {
    /// Meters per second (Shift sprints ×1.8).
    pub speed: f32,
}

/// Camera-to-target offset for an orbit camera: `pitch` 0 keeps the camera
/// on the horizontal ring, +90° puts it straight above the target.
pub fn camera_offset(yaw_deg: f32, pitch_deg: f32, distance: f32) -> Vec3 {
    let yaw = yaw_deg.to_radians();
    let pitch = pitch_deg.to_radians();
    Vec3::new(
        yaw.sin() * pitch.cos(),
        pitch.sin(),
        yaw.cos() * pitch.cos(),
    ) * distance
}

/// Horizontal input direction (world space) from WASD/arrows given the
/// camera yaw; normalized, `Vec3::ZERO` when no key is pressed.
pub fn move_direction(w: bool, s: bool, a: bool, d: bool, yaw_deg: f32) -> Vec3 {
    let yaw = yaw_deg.to_radians();
    // The camera sits at target + offset(yaw,…) and looks at the target, so
    // "forward" is the horizontal opposite of the offset ring direction.
    let forward = Vec3::new(-yaw.sin(), 0.0, -yaw.cos());
    let right = forward.cross(Vec3::Y);
    let mut dir = Vec3::ZERO;
    if w {
        dir += forward;
    }
    if s {
        dir -= forward;
    }
    if d {
        dir += right;
    }
    if a {
        dir -= right;
    }
    if dir != Vec3::ZERO {
        dir.normalize()
    } else {
        dir
    }
}

/// Face a movement direction (models from the pipeline face +Z).
pub fn facing_rotation(dir: Vec3) -> Quat {
    Quat::from_rotation_y(dir.x.atan2(dir.z))
}

/// WASD/arrows movement snapped to the terrain surface.
pub fn player_movement(
    keys: Res<ButtonInput<KeyCode>>,
    time: Res<Time>,
    runtime: Option<Res<TerrainRuntime>>,
    cameras: Query<&crate::recipes::spawn::OrbitCamera>,
    mut players: Query<(&mut Transform, &Player), Without<Camera>>,
) {
    let Some(runtime) = runtime else {
        return; // terrain bootstrap has not run yet — hero waits airborne
    };
    let Ok(camera) = cameras.single() else {
        return;
    };
    let (w, s, a, d) = (
        keys.pressed(KeyCode::KeyW) || keys.pressed(KeyCode::ArrowUp),
        keys.pressed(KeyCode::KeyS) || keys.pressed(KeyCode::ArrowDown),
        keys.pressed(KeyCode::KeyA) || keys.pressed(KeyCode::ArrowLeft),
        keys.pressed(KeyCode::KeyD) || keys.pressed(KeyCode::ArrowRight),
    );
    let dir = move_direction(w, s, a, d, camera.yaw_deg);
    let sprint = if keys.pressed(KeyCode::ShiftLeft) || keys.pressed(KeyCode::ShiftRight) {
        1.8
    } else {
        1.0
    };
    for (mut transform, player) in &mut players {
        if dir != Vec3::ZERO {
            let step = dir * player.speed * sprint * time.delta_secs();
            transform.translation += step;
            transform.rotation = facing_rotation(dir);
        }
        // Seat on the ground every frame (also drops the authored y=150 spawn).
        transform.translation.y = runtime.sample(transform.translation.x, transform.translation.z);
    }
}

/// Proximity interaction: when the player stands near a `<DialogueNPC>`,
/// pressing E logs the dialogue target (dialogue UI lands with the HUD phase;
/// the bridge's `viber debug logs` shows the same lines).
pub fn dialogue_interaction(
    keys: Res<ButtonInput<KeyCode>>,
    players: Query<&GlobalTransform, With<Player>>,
    npcs: Query<(&GlobalTransform, &DialogueNpc)>,
) {
    let Ok(player) = players.single() else {
        return;
    };
    let player_pos = player.translation();
    let nearest = npcs
        .iter()
        .filter(|(t, _)| t.translation().distance(player_pos) < 3.5)
        .map(|(_, npc)| npc.dialogue_id.as_str())
        .next();
    let near = nearest.is_some();
    if keys.just_pressed(KeyCode::KeyE) {
        match nearest {
            Some(id) => bevy::log::info!("interaction: dialogue {} available", id),
            None => bevy::log::info!("interaction: nothing nearby"),
        }
    }
    let _ = near; // prompt UI lands with the HUD phase
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-4
    }

    #[test]
    fn test_camera_offset_ring_and_top() {
        // pitch 0: horizontal ring, distance preserved
        let o = camera_offset(0.0, 0.0, 10.0);
        assert!(approx(o.z, 10.0) && approx(o.y, 0.0) && approx(o.x, 0.0));
        // pitch 90: straight above
        let o = camera_offset(0.0, 90.0, 10.0);
        assert!(approx(o.y, 10.0));
        assert!(approx(o.length(), 10.0), "distance preserved: {o:?}");
        // yaw rotates the ring point: yaw 90 → +X
        let o = camera_offset(90.0, 0.0, 10.0);
        assert!(approx(o.x, 10.0) && approx(o.z, 0.0));
    }

    #[test]
    fn test_move_direction_cardinal() {
        // yaw 0 → camera behind the target on +Z → W walks into -Z
        let f = move_direction(true, false, false, false, 0.0);
        assert!(approx(f.z, -1.0) && approx(f.x, 0.0));
        // D walks screen-right = +X at yaw 0
        let r = move_direction(false, false, false, true, 0.0);
        assert!(approx(r.x, 1.0) && approx(r.z, 0.0));
        // no keys → zero
        assert_eq!(move_direction(false, false, false, false, 0.0), Vec3::ZERO);
        // W+D diagonal is normalized
        let diag = move_direction(true, false, false, true, 0.0);
        assert!(approx(diag.length(), 1.0));
    }

    #[test]
    fn test_facing_rotation_faces_move_dir() {
        let q = facing_rotation(Vec3::new(0.0, 0.0, 1.0));
        let faced = q * Vec3::Z;
        assert!(approx(faced.z, 1.0));
    }
}
