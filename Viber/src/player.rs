//! `<PlayerGLTF>` runtime: third-person character controller ported from the
//! VibeGame `player` plugin (walk 4 m/s, sprint ×1.5, jump 2.3 m under
//! gravity −60, side-move ×0.6, slerped facing at `rotationSpeed` 10 rad/s)
//! plus the third-person orbit camera driven by mouse drag and scroll.

use bevy::math::Quat;
use bevy::math::Vec3;
use bevy::prelude::*;

use crate::recipes::spawn::DialogueNpc;
use crate::terrain::runtime::TerrainRuntime;

/// Gravity magnitude (m/s²) — `DEFAULT_GRAVITY = -60` in the VibeGame
/// physics utils; the jump velocity derives from it.
pub const GRAVITY: f32 = 60.0;
/// A/D move at this fraction of the walk speed (VibeGame `SIDE_MOVE_FACTOR`).
pub const SIDE_MOVE_FACTOR: f32 = 0.6;
/// Camera yaw turn speed for gamepad-style input (rad/s) — unused for mouse.
pub const CAMERA_TURN_SPEED: f32 = 2.5;

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
        }
    }
}

/// Jump velocity that reaches `jump_height` under [`GRAVITY`]:
/// `sqrt(2 · g · h)` (VibeGame `handleJump`).
pub fn jump_velocity(jump_height: f32) -> f32 {
    (2.0 * GRAVITY * jump_height.max(0.0)).sqrt()
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

/// WASD/arrows movement over the terrain: camera-relative walk/sprint,
/// Space jump (`√2gh` under gravity −60), ground snap via `TerrainRuntime`.
#[allow(clippy::type_complexity)]
pub fn player_movement(
    keys: Res<ButtonInput<KeyCode>>,
    time: Res<Time>,
    runtime: Option<Res<TerrainRuntime>>,
    cameras: Query<&crate::recipes::spawn::OrbitCamera>,
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
    let Ok(camera) = cameras.single() else {
        return;
    };
    let dt = time.delta_secs();
    let (w, s, a, d) = (
        keys.pressed(KeyCode::KeyW) || keys.pressed(KeyCode::ArrowUp),
        keys.pressed(KeyCode::KeyS) || keys.pressed(KeyCode::ArrowDown),
        keys.pressed(KeyCode::KeyA) || keys.pressed(KeyCode::ArrowLeft),
        keys.pressed(KeyCode::KeyD) || keys.pressed(KeyCode::ArrowRight),
    );
    let sprint = keys.pressed(KeyCode::ShiftLeft) || keys.pressed(KeyCode::ShiftRight);
    let jump = keys.pressed(KeyCode::Space);

    for (mut transform, mut player, mut controller, output) in &mut players {
        let dir = move_direction(w, s, a, d, camera.yaw_deg);
        // A/D alone moves at the side factor (carves arcs instead of
        // strafing at full speed) — VibeGame `SIDE_MOVE_FACTOR`.
        let side_damped = d || a;
        let dir = if side_damped && !w && !s {
            dir * SIDE_MOVE_FACTOR
        } else {
            dir
        };

        // Horizontal walk.
        let mut motion = Vec3::ZERO;
        if dir != Vec3::ZERO {
            let sprint_mult = if sprint {
                player.sprint_multiplier
            } else {
                1.0
            };
            motion += dir * player.speed * sprint_mult * dt;
            // Facing: slerp toward the move heading at `rotation_speed` rad/s.
            let target = facing_rotation(dir);
            let factor = facing_slerp_factor(transform.rotation, target, player.rotation_speed, dt);
            transform.rotation = transform.rotation.slerp(target, factor);
        }

        // Ground height under the player.
        let ground = runtime.sample(transform.translation.x, transform.translation.z);

        // Jump: only while grounded (VibeGame `canJump`).
        if jump && player.grounded {
            player.vel_y = jump_velocity(player.jump_height);
            player.grounded = false;
        }

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
}
