//! Viewport navigation over the engine's [`EditorCamState`] (target + yaw/pitch/distance).
//!
//! Orbit / pan / zoom reuse the engine's own pure operations (`viber::editor_camera`); free look
//! keeps the *eye* fixed and swings the target around it, so the same state drives both modes.

use bevy::input::mouse::{AccumulatedMouseMotion, AccumulatedMouseScroll, MouseScrollUnit};
use bevy::prelude::*;
use viber::camera::camera_offset;
use viber::editor_camera::{self, EditorCamState};

/// Degrees of orbit per pixel of middle drag.
const ORBIT_DEG_PER_PX: f32 = 0.3;
/// Degrees of free look per pixel of right drag.
const LOOK_DEG_PER_PX: f32 = 0.2;
/// Zoom factor per wheel line.
const ZOOM_PER_LINE: f32 = 1.12;
/// Pixels per wheel "line" for touchpads reporting pixel deltas.
const PX_PER_LINE: f32 = 40.0;
/// Free look may tilt further up than the orbit camera (which keeps the horizon in frame).
const LOOK_MIN_PITCH_DEG: f32 = -80.0;
const LOOK_MAX_PITCH_DEG: f32 = editor_camera::MAX_PITCH_DEG;
/// Fly speed bounds (m/s); the wheel scales it while flying.
const FLY_SPEED_MIN: f32 = 2.0;
const FLY_SPEED_MAX: f32 = 400.0;
/// Shift multiplies the fly speed.
const FLY_BOOST: f32 = 4.0;

/// Fly-mode state: the speed persists between flights, like Godot.
#[derive(Resource, Debug, Clone, Copy)]
pub struct FlyState {
    pub speed: f32,
    /// A middle/right drag began over the viewport (input keeps flowing if it leaves).
    pub dragging: bool,
}

impl Default for FlyState {
    fn default() -> Self {
        Self {
            speed: 20.0,
            dragging: false,
        }
    }
}

/// Middle drag orbits, Shift + middle drag pans, the wheel zooms (unless flying).
pub fn orbit_pan_zoom(
    buttons: Res<ButtonInput<MouseButton>>,
    keys: Res<ButtonInput<KeyCode>>,
    motion: Res<AccumulatedMouseMotion>,
    scroll: Res<AccumulatedMouseScroll>,
    mut fly: ResMut<FlyState>,
    state: Option<ResMut<EditorCamState>>,
) {
    let Some(mut state) = state else {
        return;
    };
    let shift = keys.any_pressed([KeyCode::ShiftLeft, KeyCode::ShiftRight]);
    if buttons.pressed(MouseButton::Middle) && motion.delta != Vec2::ZERO {
        fly.dragging = true;
        let delta = motion.delta;
        let _ = if shift {
            editor_camera::pan(&mut state, delta.x, delta.y)
        } else {
            editor_camera::orbit(
                &mut state,
                -delta.x * ORBIT_DEG_PER_PX,
                delta.y * ORBIT_DEG_PER_PX,
            )
        };
    }
    if !buttons.pressed(MouseButton::Right) {
        let lines = scroll_lines(&scroll);
        if lines != 0.0 {
            let _ = editor_camera::zoom(&mut state, ZOOM_PER_LINE.powf(lines));
        }
    }
    if !buttons.any_pressed([MouseButton::Middle, MouseButton::Right]) {
        fly.dragging = false;
    }
}

/// Right drag looks around; WASD/QE fly while it is held.
pub fn fly(
    time: Res<Time<Real>>,
    buttons: Res<ButtonInput<MouseButton>>,
    keys: Res<ButtonInput<KeyCode>>,
    motion: Res<AccumulatedMouseMotion>,
    scroll: Res<AccumulatedMouseScroll>,
    mut fly: ResMut<FlyState>,
    state: Option<ResMut<EditorCamState>>,
) {
    let Some(mut state) = state else {
        return;
    };
    if !buttons.pressed(MouseButton::Right) {
        return;
    }
    fly.dragging = true;
    let lines = scroll_lines(&scroll);
    if lines != 0.0 {
        fly.speed = (fly.speed * 1.2_f32.powf(lines)).clamp(FLY_SPEED_MIN, FLY_SPEED_MAX);
    }
    if motion.delta != Vec2::ZERO {
        free_look(
            &mut state,
            -motion.delta.x * LOOK_DEG_PER_PX,
            -motion.delta.y * LOOK_DEG_PER_PX,
        );
    }
    let mut local = Vec3::ZERO;
    for (key, axis) in [
        (KeyCode::KeyW, Vec3::NEG_Z),
        (KeyCode::KeyS, Vec3::Z),
        (KeyCode::KeyA, Vec3::NEG_X),
        (KeyCode::KeyD, Vec3::X),
        (KeyCode::KeyE, Vec3::Y),
        (KeyCode::KeyQ, Vec3::NEG_Y),
    ] {
        if keys.pressed(key) {
            local += axis;
        }
    }
    if local != Vec3::ZERO {
        let boost = if keys.any_pressed([KeyCode::ShiftLeft, KeyCode::ShiftRight]) {
            FLY_BOOST
        } else {
            1.0
        };
        fly_move(
            &mut state,
            local.normalize() * fly.speed * boost * time.delta_secs(),
        );
    }
}

/// Rotates the view around the eye: yaw/pitch change, the eye stays, the target follows.
pub fn free_look(state: &mut EditorCamState, dyaw_deg: f32, dpitch_deg: f32) {
    let eye = state.eye();
    state.yaw_deg += dyaw_deg;
    // Looking up means the camera sits *below* its target: pitch goes negative.
    state.pitch_deg = (state.pitch_deg - dpitch_deg).clamp(LOOK_MIN_PITCH_DEG, LOOK_MAX_PITCH_DEG);
    state.target = eye - camera_offset(state.yaw_deg, state.pitch_deg, state.distance, 0.0);
}

/// Moves eye and target together by `local` metres in view space (-Z forward, +X right, +Y up).
pub fn fly_move(state: &mut EditorCamState, local: Vec3) {
    let forward = (state.target - state.eye()).normalize_or_zero();
    let right = forward.cross(Vec3::Y).normalize_or_zero();
    let up = Vec3::Y;
    state.target += right * local.x + up * local.y - forward * local.z;
}

fn scroll_lines(scroll: &AccumulatedMouseScroll) -> f32 {
    match scroll.unit {
        MouseScrollUnit::Line => scroll.delta.y,
        MouseScrollUnit::Pixel => scroll.delta.y / PX_PER_LINE,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state() -> EditorCamState {
        EditorCamState {
            active: true,
            target: Vec3::new(10.0, 0.0, 0.0),
            yaw_deg: 30.0,
            pitch_deg: 35.0,
            distance: 50.0,
        }
    }

    #[test]
    fn free_look_keeps_the_eye_in_place() {
        let mut cam = state();
        let eye = cam.eye();
        free_look(&mut cam, 25.0, -10.0);
        assert!(cam.eye().distance(eye) < 1e-3, "{:?} vs {eye:?}", cam.eye());
        assert!((cam.yaw_deg - 55.0).abs() < 1e-4);
        assert!((cam.pitch_deg - 45.0).abs() < 1e-4);
    }

    #[test]
    fn free_look_clamps_pitch_both_ways() {
        let mut cam = state();
        free_look(&mut cam, 0.0, 500.0);
        assert!((cam.pitch_deg - LOOK_MIN_PITCH_DEG).abs() < 1e-4);
        free_look(&mut cam, 0.0, -500.0);
        assert!((cam.pitch_deg - LOOK_MAX_PITCH_DEG).abs() < 1e-4);
    }

    #[test]
    fn fly_forward_moves_along_the_view_direction() {
        let mut cam = state();
        let eye = cam.eye();
        let forward = (cam.target - eye).normalize();
        fly_move(&mut cam, Vec3::new(0.0, 0.0, -5.0));
        let moved = cam.eye() - eye;
        assert!(moved.distance(forward * 5.0) < 1e-3, "{moved:?}");
    }

    #[test]
    fn fly_strafe_and_lift_are_horizontal_and_vertical() {
        let mut cam = state();
        let eye = cam.eye();
        fly_move(&mut cam, Vec3::new(3.0, 0.0, 0.0));
        let strafe = cam.eye() - eye;
        assert!(strafe.y.abs() < 1e-4 && (strafe.length() - 3.0).abs() < 1e-3);
        let eye = cam.eye();
        fly_move(&mut cam, Vec3::new(0.0, 2.0, 0.0));
        assert!((cam.eye() - eye).distance(Vec3::Y * 2.0) < 1e-4);
    }
}
