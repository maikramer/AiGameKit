//! `<PlayerGLTF>` runtime: third-person character controller ported from the
//! VibeGame `player` plugin — gamepad-style, no mouse. A/D steer the camera
//! yaw (`CAMERA_TURN_SPEED`) while nudging the character sideways
//! (`SIDE_MOVE_FACTOR`), W/S walk/sprint along the camera axis, Space jumps
//! with a 100 ms input buffer, 100 ms coyote time and a 0.2 s cooldown. The
//! camera itself (decoupled follow, terrain collision) lives in
//! [`crate::camera`].

use bevy::math::Quat;
use bevy::math::Vec3;
use bevy::prelude::*;

use crate::recipes::spawn::{DialogueNpc, OrbitCamera};
use crate::terrain::runtime::TerrainRuntime;

/// Gravity magnitude (m/s²) — `DEFAULT_GRAVITY = -60` in the VibeGame
/// physics utils; the jump velocity derives from it.
pub const GRAVITY: f32 = 60.0;
/// A/D move at this fraction of the walk speed while steering the camera
/// (VibeGame `SIDE_MOVE_FACTOR`), so turning carves an arc instead of
/// pivoting in place.
pub const SIDE_MOVE_FACTOR: f32 = 0.6;
/// Camera yaw turn rate while steering with A/D (rad/s, VibeGame
/// `CAMERA_TURN_SPEED`).
pub const CAMERA_TURN_SPEED: f32 = 2.5;
/// Jump input buffer window (VibeGame `INPUT_CONFIG.bufferWindow` = 100 ms).
pub const JUMP_BUFFER: f32 = 0.1;
/// Coyote time: jumps still land within this window after leaving the ground
/// (VibeGame `INPUT_CONFIG.gracePeriods.coyoteTime` = 100 ms).
pub const COYOTE_TIME: f32 = 0.1;
/// Cooldown between jumps (VibeGame `JUMP_CONSTANTS.cooldown` = 0.2 s).
pub const JUMP_COOLDOWN: f32 = 0.2;

/// The controllable hero (VibeGame `PlayerController` subset).
#[derive(Debug, Component)]
pub struct Player {
    /// Walk speed in m/s (VibeGame default 4).
    pub speed: f32,
    /// Sprint multiplier when Shift is held (VibeGame default 1.5).
    pub sprint_multiplier: f32,
    /// Jump apex height in meters (VibeGame default 2.3).
    pub jump_height: f32,
    /// Facing turn rate in rad/s (VibeGame default 10).
    pub rotation_speed: f32,
    /// Vertical velocity (jump / gravity integration).
    pub vel_y: f32,
    /// True while standing on the terrain surface.
    pub grounded: bool,
    /// Jump allowed again once the cooldown elapses (VibeGame `canJump`).
    pub can_jump: bool,
    /// True between the jump impulse and the next landing (VibeGame
    /// `isJumping`; gates landing momentum reset).
    pub is_jumping: bool,
    /// Seconds left before [`Self::can_jump`] is restored.
    pub jump_cooldown: f32,
    /// Time of the last grounded frame — drives coyote time.
    pub last_grounded_time: f32,
    /// Time of the last jump press — drives the input buffer.
    pub jump_buffer_time: f32,
}

impl Default for Player {
    fn default() -> Self {
        Self {
            speed: 4.0,
            sprint_multiplier: 1.5,
            jump_height: 2.3,
            rotation_speed: 10.0,
            vel_y: 0.0,
            grounded: true,
            can_jump: true,
            is_jumping: false,
            jump_cooldown: 0.0,
            last_grounded_time: f32::NEG_INFINITY,
            jump_buffer_time: f32::NEG_INFINITY,
        }
    }
}

/// Jump velocity that reaches `jump_height` under [`GRAVITY`]:
/// `sqrt(2 · g · h)` (VibeGame `calculateJumpVelocity`).
pub fn jump_velocity(jump_height: f32) -> f32 {
    (2.0 * GRAVITY * jump_height.max(0.0)).sqrt()
}

/// VibeGame `canPerformJump`: the press is inside the buffer window, the
/// cooldown is done, and we are grounded — or left the ground within coyote
/// time.
pub fn can_perform_jump(
    now: f32,
    buffer_time: f32,
    last_grounded_time: f32,
    can_jump: bool,
    grounded: bool,
) -> bool {
    now - buffer_time <= JUMP_BUFFER
        && can_jump
        && (grounded || now - last_grounded_time <= COYOTE_TIME)
}

/// Horizontal input direction (world space) — VibeGame `processInput`:
/// `(strafe, 0, -forward)` rotated by the camera yaw. Normalized; the input
/// magnitude is applied separately by the caller.
pub fn process_input(forward: f32, strafe: f32, yaw_deg: f32) -> Vec3 {
    let (sin, cos) = yaw_deg.to_radians().sin_cos();
    let dir = Vec3::new(
        strafe * cos - forward * sin,
        0.0,
        -strafe * sin - forward * cos,
    );
    if dir.length_squared() > 0.0 {
        dir.normalize()
    } else {
        dir
    }
}

/// Input magnitude — VibeGame re-applies `min(1, hypot(forward, strafe))`
/// after normalizing, so A/D alone (strafe 0.6) nudges at 60 % speed.
pub fn input_magnitude(forward: f32, strafe: f32) -> f32 {
    (forward * forward + strafe * strafe).sqrt().min(1.0)
}

/// Face a movement direction (models from the pipeline face +Z).
pub fn facing_rotation(dir: Vec3) -> Quat {
    Quat::from_rotation_y(dir.x.atan2(dir.z))
}

/// Slerp factor for facing: `rotation_speed · dt` converted to a 0..1 t,
/// mirroring the VibeGame `calculateSlerpFactor` clamp.
pub fn facing_slerp_factor(current: Quat, target: Quat, rotation_speed: f32, dt: f32) -> f32 {
    let max_radians = rotation_speed * dt;
    let angle = current.angle_between(target);
    if angle <= f32::EPSILON {
        1.0
    } else {
        (max_radians / angle).clamp(0.0, 1.0)
    }
}

/// Gamepad-style movement over the terrain: A/D steer the third-person
/// camera (which the character follows), W/S walk/sprint, Space jumps with
/// buffer + coyote + cooldown. Ground snap via `TerrainRuntime`; walls stop
/// the hero through the Rapier character controller.
#[allow(clippy::type_complexity)]
pub fn player_movement(
    keys: Res<ButtonInput<KeyCode>>,
    time: Res<Time>,
    runtime: Option<Res<TerrainRuntime>>,
    mut cameras: Query<&mut OrbitCamera>,
    mut players: Query<
        (
            &mut Transform,
            &mut Player,
            Option<&mut bevy_rapier3d::prelude::KinematicCharacterController>,
            Option<&bevy_rapier3d::prelude::KinematicCharacterControllerOutput>,
        ),
        Without<Camera>,
    >,
) {
    let Some(runtime) = runtime else {
        return; // terrain bootstrap has not run yet — hero waits airborne
    };
    let dt = time.delta_secs();
    let now = time.elapsed_secs();
    let (w, s, a, d) = (
        keys.pressed(KeyCode::KeyW) || keys.pressed(KeyCode::ArrowUp),
        keys.pressed(KeyCode::KeyS) || keys.pressed(KeyCode::ArrowDown),
        keys.pressed(KeyCode::KeyA) || keys.pressed(KeyCode::ArrowLeft),
        keys.pressed(KeyCode::KeyD) || keys.pressed(KeyCode::ArrowRight),
    );
    let move_x = (d as i32 - a as i32) as f32;
    let move_forward = (w as i32 - s as i32) as f32;
    let sprint = keys.pressed(KeyCode::ShiftLeft) || keys.pressed(KeyCode::ShiftRight);
    let jump_held = keys.pressed(KeyCode::Space);

    for (mut transform, mut player, mut controller, output) in &mut players {
        // Grounded state update first (VibeGame PlayerGroundedSystem runs
        // before movement): refresh the coyote clock and clear the jumping
        // flag on landing.
        if player.grounded {
            player.last_grounded_time = now;
            player.is_jumping = false;
        }
        if player.jump_cooldown > 0.0 {
            player.jump_cooldown = (player.jump_cooldown - dt).max(0.0);
            if player.jump_cooldown == 0.0 {
                player.can_jump = true;
            }
        }

        // Steering: A/D turn the camera; the character heading follows it.
        // The yaw itself is smoothed by the camera system (turnLag).
        let mut camera_yaw_deg = 0.0f32;
        if let Some(mut cam) = cameras.iter_mut().next() {
            cam.yaw_deg -= move_x * CAMERA_TURN_SPEED * dt;
            camera_yaw_deg = cam.yaw_deg;
        }
        let strafe = move_x * SIDE_MOVE_FACTOR;

        let dir = process_input(move_forward, strafe, camera_yaw_deg);
        let input_mag = input_magnitude(move_forward, strafe);
        let sprint_mult = if sprint {
            player.sprint_multiplier
        } else {
            1.0
        };
        let desired = dir * player.speed * sprint_mult * input_mag;
        let mut motion = desired * dt;

        // Facing: slerp toward the move heading at `rotation_speed` rad/s,
        // only while moving (VibeGame rotation mode 1 — idle keeps facing).
        if dir.length_squared() > 0.0 {
            let target = facing_rotation(dir);
            let factor = facing_slerp_factor(transform.rotation, target, player.rotation_speed, dt);
            transform.rotation = transform.rotation.slerp(target, factor);
        }

        // Jump: buffered press, coyote grace, cooldown gate.
        if jump_held {
            player.jump_buffer_time = now;
        }
        if can_perform_jump(
            now,
            player.jump_buffer_time,
            player.last_grounded_time,
            player.can_jump,
            player.grounded,
        ) {
            player.vel_y = jump_velocity(player.jump_height);
            player.is_jumping = true;
            player.can_jump = false;
            player.jump_cooldown = JUMP_COOLDOWN;
            player.jump_buffer_time = f32::NEG_INFINITY;
        }

        // Ground height under the player.
        let ground = runtime.sample(transform.translation.x, transform.translation.z);

        // Vertical integration. Falls faster than it rises feels right
        // (gravity is already twice the jump-fair value).
        player.vel_y -= GRAVITY * dt;
        motion.y += player.vel_y * dt;

        match controller.as_deref_mut() {
            // With a character controller Rapier resolves the motion against
            // the world's colliders, so walls and props actually stop the hero
            // instead of the transform being written straight through them.
            Some(controller) => {
                controller.translation = Some(motion);
                // `grounded` comes from the previous frame's resolution; the
                // heightfield is the floor of last resort while the terrain
                // colliders around the hero are still streaming in.
                let on_collider = output.is_some_and(|out| out.grounded);
                if on_collider {
                    player.grounded = true;
                    if player.vel_y < 0.0 {
                        player.vel_y = 0.0;
                    }
                } else if transform.translation.y <= ground {
                    transform.translation.y = ground;
                    player.vel_y = 0.0;
                    player.grounded = true;
                } else {
                    player.grounded = false;
                }
            }
            // No controller (headless tests, or a hero spawned before the
            // physics plugin): keep the original direct-move behaviour.
            None => {
                transform.translation += motion;
                if transform.translation.y <= ground {
                    transform.translation.y = ground;
                    player.vel_y = 0.0;
                    player.grounded = true;
                } else {
                    player.grounded = false;
                }
            }
        }
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

// ------------------------------------------------------ character controller

/// Capsule radius of the hero's collider (meters).
pub const HERO_RADIUS: f32 = 0.35;
/// Capsule half-height between the cap centres (meters) — a ~1.8 m character.
pub const HERO_HALF_HEIGHT: f32 = 0.55;
/// Steps up to this height are climbed instead of blocking (stairs, kerbs).
pub const HERO_STEP_HEIGHT: f32 = 0.4;
/// Slopes up to this angle are walkable.
pub const HERO_MAX_SLOPE_DEG: f32 = 55.0;

/// Height of the capsule's centre above the entity origin.
///
/// The pipeline exports characters with y = 0 at the feet, and the character
/// controller drives the entity transform — so the entity origin *is* the
/// hero's footprint. A bare `capsule_y` is centred on its own origin, which
/// would bury half the capsule underground and leave the hero floating a
/// radius-plus-half-height above the floor.
pub const HERO_CAPSULE_CENTER: f32 = HERO_HALF_HEIGHT + HERO_RADIUS;

/// The hero's collision shape, with its base at the entity origin.
pub fn hero_collider() -> bevy_rapier3d::prelude::Collider {
    use bevy_rapier3d::prelude::Collider;
    Collider::compound(vec![(
        Vec3::new(0.0, HERO_CAPSULE_CENTER, 0.0),
        Quat::IDENTITY,
        Collider::capsule_y(HERO_HALF_HEIGHT, HERO_RADIUS),
    )])
}

/// Rapier character controller tuned to the VibeGame hero.
pub fn hero_controller() -> bevy_rapier3d::prelude::KinematicCharacterController {
    use bevy_rapier3d::prelude::*;
    KinematicCharacterController {
        up: Vec3::Y,
        // A small skin keeps the capsule from resting exactly on a face, which
        // otherwise flickers between grounded and airborne.
        offset: CharacterLength::Absolute(0.02),
        max_slope_climb_angle: HERO_MAX_SLOPE_DEG.to_radians(),
        min_slope_slide_angle: (HERO_MAX_SLOPE_DEG + 10.0).to_radians(),
        autostep: Some(CharacterAutostep {
            max_height: CharacterLength::Absolute(HERO_STEP_HEIGHT),
            min_width: CharacterLength::Absolute(HERO_RADIUS * 0.5),
            include_dynamic_bodies: false,
        }),
        snap_to_ground: Some(CharacterLength::Absolute(0.3)),
        ..KinematicCharacterController::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-4
    }

    #[test]
    fn test_process_input_cardinal() {
        // yaw 0 → camera behind the target on +Z → W walks into -Z
        let f = process_input(1.0, 0.0, 0.0);
        assert!(approx(f.z, -1.0) && approx(f.x, 0.0));
        // strafe-only input is rotated by the camera yaw too
        let r = process_input(0.0, 1.0, 0.0);
        assert!(approx(r.x, 1.0) && approx(r.z, 0.0));
        // no input → zero
        assert_eq!(process_input(0.0, 0.0, 0.0), Vec3::ZERO);
        // forward at yaw 90° (camera swung to +X side) walks into -X
        let f = process_input(1.0, 0.0, 90.0);
        assert!(approx(f.x, -1.0) && approx(f.z, 0.0));
        // output is always normalized
        let diag = process_input(1.0, 0.6, 37.0);
        assert!(approx(diag.length(), 1.0));
    }

    #[test]
    fn test_input_magnitude_matches_vibegame() {
        // W full, W+D saturates at 1, A/D alone nudges at the side factor.
        assert!(approx(input_magnitude(1.0, 0.0), 1.0));
        assert!(approx(input_magnitude(1.0, 0.6), 1.0));
        assert!(approx(input_magnitude(0.0, 0.6), 0.6));
        assert!(approx(input_magnitude(0.0, 0.0), 0.0));
    }

    #[test]
    fn test_facing_rotation_faces_move_dir() {
        let q = facing_rotation(Vec3::new(0.0, 0.0, 1.0));
        let faced = q * Vec3::Z;
        assert!(approx(faced.z, 1.0));
    }

    #[test]
    fn test_jump_velocity_matches_vibegame() {
        // √(2 · 60 · 2.3) ≈ 16.61 m/s reaches a 2.3 m apex.
        let v = jump_velocity(2.3);
        assert!(approx(v, 16.6132), "{v}");
        assert_eq!(jump_velocity(0.0), 0.0);
    }

    #[test]
    fn test_jump_apex_reaches_height() {
        // integrate: with v0 = jump_velocity, apex ≈ jump_height
        let v0 = jump_velocity(2.3);
        let mut y = 0.0f32;
        let mut vy = v0;
        let dt = 1.0 / 240.0;
        let mut apex = 0.0f32;
        for _ in 0..600 {
            vy -= GRAVITY * dt;
            y += vy * dt;
            apex = apex.max(y);
        }
        assert!((apex - 2.3).abs() < 0.05, "apex {apex}");
    }

    #[test]
    fn test_facing_slerp_factor_clamps() {
        let id = Quat::IDENTITY;
        let quarter = Quat::from_rotation_y(std::f32::consts::FRAC_PI_2);
        // 10 rad/s × 0.016 s = 0.16 rad max — quarter turn (1.57 rad) needs more
        let f = facing_slerp_factor(id, quarter, 10.0, 0.016);
        assert!(f < 0.15, "small dt → small factor: {f}");
        // big dt reaches the target exactly
        assert_eq!(facing_slerp_factor(id, quarter, 10.0, 5.0), 1.0);
    }

    #[test]
    fn test_can_perform_jump_buffer_and_coyote() {
        let now = 10.0;
        // Press inside the buffer window + grounded → jump.
        assert!(can_perform_jump(now, now - 0.05, now - 5.0, true, true));
        // Press older than the buffer window → no jump.
        assert!(!can_perform_jump(now, now - 0.2, now, true, true));
        // Airborne but within coyote time → still jumps.
        assert!(can_perform_jump(now, now - 0.02, now - 0.08, true, false));
        // Airborne past coyote time → blocked.
        assert!(!can_perform_jump(now, now - 0.02, now - 0.5, true, false));
        // Cooldown gate (can_jump false) blocks even a fresh press.
        assert!(!can_perform_jump(now, now - 0.02, now, false, true));
    }

    #[test]
    fn test_player_defaults_mirror_vibegame() {
        let p = Player::default();
        assert_eq!(p.speed, 4.0);
        assert_eq!(p.sprint_multiplier, 1.5);
        assert_eq!(p.jump_height, 2.3);
        assert_eq!(p.rotation_speed, 10.0);
        assert!(p.grounded && p.can_jump);
        assert!(!p.is_jumping);
        assert!(p.last_grounded_time.is_infinite());
    }
}
