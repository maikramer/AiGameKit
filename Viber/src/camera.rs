//! Third-person camera ported from the VibeGame `player-controller` plugin
//! (`ThirdPersonCameraSystem`): a decoupled follow point chases the character
//! through a frame-rate-independent low-pass, the orbit yaw trails the steered
//! heading through a second low-pass, and the desired position is pulled in
//! along its view ray when the terrain blocks it. A final ~50 ms low-pass
//! absorbs collision pops so the intentional lag stays the only lag.
//!
//! There is no mouse: the camera is steered by A/D in [`crate::player`]
//! (gamepad-style automatic camera, like the VibeGame simple-rpg).

use bevy::math::Vec3;
use bevy::prelude::*;

use crate::player::Player;
use crate::recipes::spawn::OrbitCamera;
use crate::terrain::runtime::TerrainRuntime;

/// The camera looks at the follow point plus this height (VibeGame
/// `lookTargetY = targetY + 1.5` — slightly above the feet).
pub const LOOK_PIVOT_HEIGHT: f32 = 1.5;
/// Eye height above the follow point where the collision rays start
/// (VibeGame `eyeY = targetY + 2.0`).
pub const COLLISION_EYE_HEIGHT: f32 = 2.0;
/// Time constant of the final position low-pass that absorbs collision pops
/// (VibeGame `1 - exp(-dt / 0.05)`).
pub const POP_SMOOTH_TAU: f32 = 0.05;
/// Ray-march resolution for the terrain probe (heightfield samples are
/// cheap, so every ray is marched every frame).
const RAY_STEPS: usize = 16;

/// Default follow-point time constant (VibeGame plugin default 0.18 s; the
/// simple-rpg world overrides with 0.18).
pub const DEFAULT_FOLLOW_LAG: f32 = 0.18;
/// Default yaw-trail time constant (simple-rpg world: 0.38 s).
pub const DEFAULT_TURN_LAG: f32 = 0.38;
/// Default terrain clearance (VibeGame plugin default `minTerrainDistance`).
pub const DEFAULT_MIN_TERRAIN_DISTANCE: f32 = 1.0;

// --- true automatic camera (auto-follow behind the movement heading) ---
/// Movement speed (m/s) below which the camera stays where it is.
pub const AUTO_FOLLOW_MIN_SPEED: f32 = 1.0;
/// Seconds after manual A/D steering before the auto-follow takes over.
pub const AUTO_FOLLOW_GRACE: f32 = 0.2;
/// Time constant of the auto-follow swing (larger = lazier repositioning).
pub const AUTO_FOLLOW_TAU: f32 = 0.45;
/// Cap on the auto-follow swing (deg/s) — a 180° turnaround is a graceful
/// sweep, not a whip.
pub const AUTO_FOLLOW_MAX_RATE: f32 = 140.0;
/// Auto-follow dead zone (deg) — small enough that the settle error stays
/// imperceptible; the low-pass already decelerates near the target.
pub const AUTO_FOLLOW_DEAD_ZONE: f32 = 2.0;
/// Fast-follow heading/camera-forward alignment gate (dot product): running
/// forward or diagonally swings the camera; back-pedaling does not.
pub const AUTO_FOLLOW_ALIGN_MIN: f32 = 0.0;
/// Continuous settle: whenever the player isn't steering, the camera drifts
/// behind the character's back at this rate — back-pedaling included, no
/// stop required.
pub const AUTO_SETTLE_TAU: f32 = 0.9;
pub const AUTO_SETTLE_MAX_RATE: f32 = 60.0;

/// Camera-to-target offset for a third-person rig, VibeGame formula: the
/// pitch ring sits `height` ABOVE the follow point (`desiredY = followY +
/// height + sin(pitch)·dist`) — the authored `height` is additive framing,
/// not the pivot. Pitch 0 keeps the ring horizontal; +90° is straight above.
pub fn camera_offset(yaw_deg: f32, pitch_deg: f32, distance: f32, height: f32) -> Vec3 {
    let yaw = yaw_deg.to_radians();
    let pitch = pitch_deg.to_radians();
    Vec3::new(
        yaw.sin() * pitch.cos() * distance,
        height + pitch.sin() * distance,
        yaw.cos() * pitch.cos() * distance,
    )
}

/// Frame-rate-independent low-pass blend factor for a time constant `tau`:
/// `1 - exp(-dt / tau)` (VibeGame `followLag`/`turnLag`, floored at 1e-4).
pub fn low_pass_factor(dt: f32, tau: f32) -> f32 {
    1.0 - (-dt / tau.max(1e-4)).exp()
}

/// Signed shortest angular delta in degrees from `from` to `to` (±180°),
/// so the yaw trail never spins the long way around.
pub fn shortest_angle_delta_deg(from: f32, to: f32) -> f32 {
    let delta = (to - from) % 360.0;
    if delta > 180.0 {
        delta - 360.0
    } else if delta < -180.0 {
        delta + 360.0
    } else {
        delta
    }
}

/// Distance along the view ray where the terrain first violates the
/// `min_dist` clearance — `None` when the whole ray is clear.
///
/// Ports the VibeGame three-ray probe (center + two side rays offset by
/// `radius`) against the heightfield: a ray marches from `eye` and returns
/// the first sample whose height comes within `min_dist` of the probe. The
/// side rays fan out to `full_dist + radius` so geometry just off-axis still
/// pulls the camera in.
pub fn terrain_safe_distance(
    terrain_y: impl Fn(f32, f32) -> f32,
    eye: Vec3,
    dir: Vec3,
    full_dist: f32,
    radius: f32,
    min_dist: f32,
) -> Option<f32> {
    let march = |ray_dir: Vec3, length: f32| -> Option<f32> {
        for step in 1..=RAY_STEPS {
            let t = length * step as f32 / RAY_STEPS as f32;
            let p = eye + ray_dir * t;
            if p.y < terrain_y(p.x, p.z) + min_dist {
                return Some(t);
            }
        }
        None
    };
    let mut min_safe = full_dist;
    let mut has_hit = false;
    if let Some(t) = march(dir, full_dist) {
        min_safe = min_safe.min(t);
        has_hit = true;
    }
    let right = Vec3::new(dir.z, 0.0, -dir.x);
    if right.length_squared() > 1e-8 {
        let right = right.normalize();
        let end = full_dist + radius;
        for side in [-1.0, 1.0] {
            let probe = (dir * end + right * radius * side).normalize();
            if let Some(t) = march(probe, end) {
                min_safe = min_safe.min(t);
                has_hit = true;
            }
        }
    }
    has_hit.then_some(min_safe)
}

/// Yaw that puts the camera BEHIND a character moving along heading
/// `(x, z)`: the camera forward is `-(sin yaw, cos yaw)`, so behind a run
/// along `h` is `atan2(-h.x, -h.z)`.
pub fn behind_yaw_deg(vel_x: f32, vel_z: f32) -> f32 {
    (-vel_x).atan2(-vel_z).to_degrees()
}

/// One low-passed, rate-limited yaw step toward `target`.
fn swing_yaw(yaw_deg: f32, target: f32, tau: f32, max_rate: f32, dt: f32) -> f32 {
    let delta = shortest_angle_delta_deg(yaw_deg, target);
    if delta.abs() < AUTO_FOLLOW_DEAD_ZONE {
        return yaw_deg;
    }
    let step = (delta * low_pass_factor(dt, tau)).clamp(-max_rate * dt, max_rate * dt);
    yaw_deg + step
}

/// Player-derived inputs for [`auto_camera_yaw`] (a bundle keeps the arg
/// count under clippy's limit).
pub struct AutoCameraInput {
    /// Unit movement heading, present while the hero has velocity.
    pub heading: Option<(f32, f32)>,
    /// Horizontal speed (m/s).
    pub speed: f32,
    /// `dot(heading, camera forward)` — back-pedal is negative.
    pub heading_alignment: f32,
    /// Yaw that sits behind the character's back (settle target).
    pub behind_facing_yaw: f32,
    /// The hero has moved at least once (the settle never relocates a
    /// camera that is still on its authored boot framing).
    pub settle_allowed: bool,
    /// Seconds since the last A/D steering frame.
    pub seconds_since_steer: f32,
}

/// Auto-camera step (two regimes), after the steering grace:
///
/// - **Fast follow**: running (speed ≥ [`AUTO_FOLLOW_MIN_SPEED`]) with the
///   heading not pointing backwards (alignment ≥ [`AUTO_FOLLOW_ALIGN_MIN`])
///   swings the yaw behind the movement heading at up to
///   [`AUTO_FOLLOW_MAX_RATE`].
/// - **Continuous settle**: otherwise the yaw drifts behind the CHARACTER'S
///   BACK at [`AUTO_SETTLE_MAX_RATE`] — walking backwards contours the rig
///   around while you move, instead of staring at the hero's face forever.
pub fn auto_camera_yaw(yaw_deg: f32, input: AutoCameraInput, dt: f32) -> f32 {
    if input.seconds_since_steer < AUTO_FOLLOW_GRACE {
        return yaw_deg; // steering owns the yaw
    }
    match input.heading {
        Some((hx, hz))
            if input.speed >= AUTO_FOLLOW_MIN_SPEED
                && input.heading_alignment >= AUTO_FOLLOW_ALIGN_MIN =>
        {
            swing_yaw(
                yaw_deg,
                behind_yaw_deg(hx, hz),
                AUTO_FOLLOW_TAU,
                AUTO_FOLLOW_MAX_RATE,
                dt,
            )
        }
        _ if input.settle_allowed => swing_yaw(
            yaw_deg,
            input.behind_facing_yaw,
            AUTO_SETTLE_TAU,
            AUTO_SETTLE_MAX_RATE,
            dt,
        ),
        _ => yaw_deg,
    }
}

/// Cheap seeded 1-D value noise in [-1, 1] — smooth (smoothstep lattice),
/// deterministic, no external crate. Feeds the organic camera sway.
pub fn noise_pm1(t: f32, seed: u32) -> f32 {
    fn hash01(i: u32) -> f32 {
        let mut x = i.wrapping_mul(0x9E37_79B9);
        x ^= x >> 16;
        x = x.wrapping_mul(0x85EB_CA6B);
        x ^= x >> 13;
        (x & 0x00FF_FFFF) as f32 / 0x00FF_FFFF as f32
    }
    let x = t + seed as f32 * 17.31;
    let i = x.floor();
    let f = x - i;
    let u = f * f * (3.0 - 2.0 * f);
    let (i, i1) = (i as i64 as u32, (i as i64 + 1) as u32);
    hash01(i) * (1.0 - u) + hash01(i1) * u
}

/// Decoupled third-person follow for target-less `<OrbitCamera>`s (worlds
/// with a player). Cameras with an explicit `target` stay on the rigid
/// follow in [`crate::recipes::spawn::orbit_camera_follow`].
///
/// Order matters: run AFTER [`crate::player::player_movement`], which steers
/// `OrbitCamera::yaw_deg` with A/D — this system auto-follows the movement
/// heading and trails everything smoothly.
#[allow(clippy::type_complexity)]
pub fn third_person_camera(
    time: Res<Time>,
    mut cameras: Query<(&mut Transform, &mut OrbitCamera)>,
    players: Query<(&GlobalTransform, &Player), With<Player>>,
    runtime: Option<Res<TerrainRuntime>>,
) {
    let Some((target, player)) = players.iter().next() else {
        return;
    };
    let target_pos = target.translation();
    let dt = time.delta_secs();
    let now = time.elapsed_secs();
    for (mut transform, mut cam) in &mut cameras {
        if cam.target.is_some() {
            continue; // named-target camera: rigid follow owns it
        }
        // TRUE automatic camera (two regimes — see auto_camera_yaw): fast
        // swing behind the movement heading while running; slow drift behind
        // the character's back once they stop.
        let speed = (player.vel_x * player.vel_x + player.vel_z * player.vel_z).sqrt();
        let heading = if speed > 1e-3 {
            Some((player.vel_x / speed, player.vel_z / speed))
        } else {
            None
        };
        let (fx, fz) = cam.yaw_deg.to_radians().sin_cos();
        let alignment = heading.map_or(0.0, |(hx, hz)| hx * -fx + hz * -fz);
        // Model forward is +Z (pipeline convention, matches facing_rotation).
        let facing = target.rotation() * Vec3::Z;
        let behind_facing = behind_yaw_deg(facing.x, facing.z);
        let settle_allowed = player.last_moving_time.is_finite();
        cam.yaw_deg = auto_camera_yaw(
            cam.yaw_deg,
            AutoCameraInput {
                heading,
                speed,
                heading_alignment: alignment,
                behind_facing_yaw: behind_facing,
                settle_allowed,
                seconds_since_steer: now - player.last_steer_time,
            },
            dt,
        );
        // First frame: snap the smoothed state onto the target (no startup
        // swoop from the world origin).
        if !cam.initialized {
            cam.follow_point = target_pos;
            cam.smooth_yaw_deg = cam.yaw_deg;
            cam.current_pos = target_pos
                + camera_offset(
                    cam.smooth_yaw_deg,
                    cam.pitch_state_deg,
                    cam.distance,
                    cam.height,
                );
            cam.initialized = true;
        }

        // Decoupled follow: the follow point chases the character with a time
        // constant, so it lags on a sprint then catches up, and per-frame
        // character jitter can't reach the view. Vertical is damped harder to
        // swallow step/bob bounce (VibeGame uses ×1.8).
        let axz = low_pass_factor(dt, cam.follow_lag);
        let ay = low_pass_factor(dt, cam.follow_lag * 1.8);
        cam.follow_point.x += (target_pos.x - cam.follow_point.x) * axz;
        cam.follow_point.z += (target_pos.z - cam.follow_point.z) * axz;
        cam.follow_point.y += (target_pos.y - cam.follow_point.y) * ay;

        // Yaw trails the steered heading, slower than the player turns,
        // along the shortest angular path.
        let ayaw = low_pass_factor(dt, cam.turn_lag);
        cam.smooth_yaw_deg += shortest_angle_delta_deg(cam.smooth_yaw_deg, cam.yaw_deg) * ayaw;

        let desired = cam.follow_point
            + camera_offset(
                cam.smooth_yaw_deg,
                cam.pitch_state_deg,
                cam.distance,
                cam.height,
            );

        // Terrain collision applied to the desired position FIRST: the final
        // smoothing always chases a safe target, never a blocked one.
        let mut safe = desired;
        if cam.min_terrain_distance > 0.0 {
            if let Some(rt) = runtime.as_deref() {
                let eye = cam.follow_point + Vec3::Y * COLLISION_EYE_HEIGHT;
                let delta = desired - eye;
                let full_dist = delta.length();
                if full_dist > 0.01 {
                    let dir = delta / full_dist;
                    let radius = cam.min_terrain_distance.max(0.5);
                    let hit = terrain_safe_distance(
                        |x, z| rt.sample(x, z),
                        eye,
                        dir,
                        full_dist,
                        radius,
                        cam.min_terrain_distance,
                    );
                    match hit {
                        Some(t) => {
                            let safe_dist = (t - radius).max(0.01);
                            safe = eye + dir * safe_dist;
                        }
                        None => {
                            // Clear line of sight: still enforce the floor
                            // above the terrain at the desired spot.
                            let min_y = rt.sample(desired.x, desired.z) + cam.min_terrain_distance;
                            if desired.y < min_y {
                                safe.y = min_y;
                            }
                        }
                    }
                }
            }
        }

        // Final micro-smoothing toward the SAFE position (~50 ms): absorbs
        // collision pops without adding a second, floaty lag.
        let sf = low_pass_factor(dt, POP_SMOOTH_TAU);
        let cur = cam.current_pos;
        cam.current_pos = cur + (safe - cur) * sf;

        // Organic sway (handheld breathing): two octave layers of slow value
        // noise on the RENDERED position only — `current_pos` stays clean, so
        // the noise never feeds back or accumulates.
        let sway = Vec3::new(
            noise_pm1(now * 0.35, 1) * 0.05 + noise_pm1(now * 1.1, 7) * 0.02,
            noise_pm1(now * 0.3, 2) * 0.04,
            noise_pm1(now * 0.35, 3) * 0.05 + noise_pm1(now * 1.1, 8) * 0.02,
        );
        transform.translation = cam.current_pos + sway;
        transform.look_at(cam.follow_point + Vec3::Y * LOOK_PIVOT_HEIGHT, Vec3::Y);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-4
    }

    #[test]
    fn test_camera_offset_adds_height_above_pitch_ring() {
        // VibeGame formula: y = height + sin(pitch)·dist — the authored
        // height stacks on top of the pitch ring.
        let o = camera_offset(0.0, 0.0, 3.3, 1.55);
        assert!(approx(o.z, 3.3) && approx(o.y, 1.55) && approx(o.x, 0.0));
        // simple-rpg values: 18.3° over 3.3 m → ring 1.04 + height 1.55
        let o = camera_offset(0.0, 18.3, 3.3, 1.55);
        assert!(
            approx(o.y, 1.55 + 18.3f32.to_radians().sin() * 3.3),
            "{o:?}"
        );
        assert!(approx(o.z, 18.3f32.to_radians().cos() * 3.3));
    }

    #[test]
    fn test_low_pass_factor_frame_rate_independent() {
        // Same tau: two 8 ms steps ≈ one 16 ms step (exponential property).
        let single = low_pass_factor(0.016, 0.18);
        let double =
            1.0 - (1.0 - low_pass_factor(0.008, 0.18)) * (1.0 - low_pass_factor(0.008, 0.18));
        assert!((single - double).abs() < 1e-3, "{single} vs {double}");
        // Tiny tau saturates (snappy); huge tau barely moves (floaty).
        assert!(low_pass_factor(0.016, 1e-5) > 0.99);
        assert!(low_pass_factor(0.016, 10.0) < 0.01);
    }

    #[test]
    fn test_shortest_angle_delta_wraps() {
        assert!(approx(shortest_angle_delta_deg(350.0, 10.0), 20.0));
        assert!(approx(shortest_angle_delta_deg(10.0, 350.0), -20.0));
        assert!(approx(shortest_angle_delta_deg(0.0, 180.0), 180.0));
        assert!(approx(shortest_angle_delta_deg(0.0, -180.0), -180.0));
        assert!(approx(shortest_angle_delta_deg(45.0, 45.0), 0.0));
    }

    #[test]
    fn test_terrain_safe_distance_flat_ray_clear() {
        // Flat ground at y=0, eye 3 m up, horizontal ray: the whole ray
        // keeps 3 m of clearance → None → the system uses the fallback clamp.
        let hit = terrain_safe_distance(|_, _| 0.0, Vec3::Y * 3.0, Vec3::NEG_Z, 3.3, 1.0, 1.0);
        assert_eq!(hit, None);
    }

    #[test]
    fn test_terrain_safe_distance_pulls_in_before_hill() {
        // Wall of terrain (y=8) starting 4 m ahead; the ray at y=0 from the
        // ground plane violates clearance immediately on any ray crossing it.
        let terrain = |_x: f32, z: f32| if (-6.0..=-4.0).contains(&z) { 8.0 } else { 0.0 };
        let eye = Vec3::ZERO;
        let dir = Vec3::NEG_Z;
        let hit = terrain_safe_distance(terrain, eye, dir, 3.3, 1.0, 1.0);
        // The 3.3 m center ray never reaches z=-4 — but the SIDE rays fan to
        // full_dist + radius = 4.3 m and cross into the wall. Any hit before
        // full distance proves the fan-out probe works.
        assert!(hit.is_some(), "side rays must catch the wall");
        if let Some(t) = hit {
            assert!(t < 3.3, "hit must be before full distance: {t}");
        }
    }

    #[test]
    fn test_terrain_safe_distance_center_ray_over_valley() {
        // Camera high above a flat field with a raised plateau ahead: center
        // ray at y=5 clears plateau (y=2) everywhere → no center hit, and
        // side rays at the same height also clear → None → fallback clamp.
        let terrain = |x: f32, z: f32| {
            let _ = x;
            if (-8.0..=-2.0).contains(&z) { 2.0 } else { 0.0 }
        };
        let hit = terrain_safe_distance(terrain, Vec3::Y * 5.0, Vec3::NEG_Z, 10.0, 1.0, 1.0);
        assert_eq!(
            hit, None,
            "5 m altitude clears a 2 m plateau with 1 m clearance"
        );
    }

    #[test]
    fn test_yaw_trail_converges_to_steered_yaw() {
        // Simulate: steered yaw jumps to 90°, the smooth yaw must approach it
        // along the shortest path and settle.
        let mut smooth = 0.0f32;
        let steered = 90.0f32;
        let dt = 1.0 / 60.0;
        for _ in 0..240 {
            smooth += shortest_angle_delta_deg(smooth, steered) * low_pass_factor(dt, 0.38);
        }
        assert!(
            (smooth - steered).abs() < 0.5,
            "smooth yaw settled at {smooth}"
        );
        // And it approaches monotonically from below (never overshoots 90°).
        let mut smooth = 0.0f32;
        let mut max_val = 0.0f32;
        for _ in 0..60 {
            smooth += shortest_angle_delta_deg(smooth, steered) * low_pass_factor(dt, 0.38);
            max_val = max_val.max(smooth);
        }
        assert!(max_val <= steered + 1e-3, "no overshoot: {max_val}");
    }

    #[test]
    fn test_behind_yaw_matches_heading() {
        // Running into -Z: the camera must sit on +Z behind the character.
        let yaw = behind_yaw_deg(0.0, -4.0);
        assert!(approx(yaw, 0.0), "run -Z → yaw 0: {yaw}");
        // Running along +X: camera swings to -X side.
        let yaw = behind_yaw_deg(4.0, 0.0);
        assert!(approx(shortest_angle_delta_deg(yaw, -90.0), 0.0), "{yaw}");
    }

    #[test]
    fn test_auto_follow_swallows_behind_heading() {
        // Camera at yaw 0 looks -Z; the character runs along +X (heading is
        // perpendicular): the yaw must sweep to -90° (behind the run).
        let dt = 1.0 / 60.0;
        let mut yaw = 0.0f32;
        for _ in 0..600 {
            yaw = auto_camera_yaw(
                yaw,
                AutoCameraInput {
                    heading: Some((1.0, 0.0)),
                    speed: 4.0,
                    heading_alignment: 0.0,
                    behind_facing_yaw: 180.0,
                    settle_allowed: false,
                    seconds_since_steer: 10.0,
                },
                dt,
            );
        }
        assert!(
            (shortest_angle_delta_deg(yaw, -90.0)).abs() < 2.5,
            "auto yaw settled at {yaw}"
        );
        // Rate limit: the first frame moves well under the 140°/s ceiling.
        let first = auto_camera_yaw(
            0.0,
            AutoCameraInput {
                heading: Some((1.0, 0.0)),
                speed: 4.0,
                heading_alignment: 0.0,
                behind_facing_yaw: 180.0,
                settle_allowed: false,
                seconds_since_steer: 10.0,
            },
            dt,
        );
        assert!(first < 0.0 && first.abs() < 140.0 * dt + 1e-3, "{first}");
    }

    #[test]
    fn test_auto_camera_gates() {
        let dt = 1.0 / 60.0;
        // Steering 0.2 s ago (< grace): nothing moves the yaw.
        assert!(approx(
            auto_camera_yaw(
                30.0,
                AutoCameraInput {
                    heading: Some((1.0, 0.0)),
                    speed: 4.0,
                    heading_alignment: 0.0,
                    behind_facing_yaw: 180.0,
                    settle_allowed: false,
                    seconds_since_steer: 0.1
                },
                dt
            ),
            30.0
        ));
        // Back-pedal (heading opposite the camera forward, alignment < 0):
        // the fast swing must NOT engage.
        assert!(approx(
            auto_camera_yaw(
                30.0,
                AutoCameraInput {
                    heading: Some((0.0, 1.0)),
                    speed: 4.0,
                    heading_alignment: -1.0,
                    behind_facing_yaw: 180.0,
                    settle_allowed: false,
                    seconds_since_steer: 10.0
                },
                dt
            ),
            30.0
        ));
        // Never moved: the settle never engages, so a slow misaligned
        // drift does nothing either.
        assert!(approx(
            auto_camera_yaw(
                30.0,
                AutoCameraInput {
                    heading: Some((1.0, 0.0)),
                    speed: 0.5,
                    heading_alignment: 0.5,
                    behind_facing_yaw: 180.0,
                    settle_allowed: false,
                    seconds_since_steer: 10.0
                },
                dt
            ),
            30.0
        ));
        // Character never moved (settle not allowed even at rest): no
        // settle at boot.
        assert!(approx(
            auto_camera_yaw(
                0.0,
                AutoCameraInput {
                    heading: None,
                    speed: 0.0,
                    heading_alignment: 0.0,
                    behind_facing_yaw: 180.0,
                    settle_allowed: false,
                    seconds_since_steer: 10.0
                },
                dt
            ),
            0.0
        ));
    }

    #[test]
    fn test_idle_settle_goes_behind_the_back() {
        // After a back-pedal the character faces the camera (behind-facing
        // target = 180°); idle for > 1 s, the yaw must creep there at ≤
        // 45°/s — a slow contour, not a whip.
        let dt = 1.0 / 60.0;
        let mut yaw = 0.0f32;
        for _ in 0..60 {
            yaw = auto_camera_yaw(
                yaw,
                AutoCameraInput {
                    heading: None,
                    speed: 0.0,
                    heading_alignment: 0.0,
                    behind_facing_yaw: 180.0,
                    settle_allowed: true,
                    seconds_since_steer: 10.0,
                },
                dt,
            );
        }
        // ~1 s of settle at 60°/s ≈ 57°.
        assert!((50.0..=70.0).contains(&yaw), "settled to {yaw} after 1 s");
        // ...and it keeps going until it reaches the back.
        for _ in 0..600 {
            yaw = auto_camera_yaw(
                yaw,
                AutoCameraInput {
                    heading: None,
                    speed: 0.0,
                    heading_alignment: 0.0,
                    behind_facing_yaw: 180.0,
                    settle_allowed: true,
                    seconds_since_steer: 10.0,
                },
                dt,
            );
        }
        assert!((yaw - 180.0).abs() < 2.5, "final yaw {yaw}");
    }

    #[test]
    fn test_noise_bounded_deterministic_and_smooth() {
        // Bounded in [-1, 1] and deterministic.
        for i in 0..200 {
            let t = i as f32 * 0.037;
            let n = noise_pm1(t, 3);
            assert!((-1.0..=1.0).contains(&n), "t={t} n={n}");
            assert_eq!(noise_pm1(t, 3), n, "deterministic");
        }
        // Smooth: consecutive samples differ by far less than the range.
        let mut prev = noise_pm1(0.0, 5);
        for i in 1..400 {
            let t = i as f32 * 1.0 / 60.0;
            let n = noise_pm1(t, 5);
            assert!((n - prev).abs() < 0.2, "jump at t={t}: {prev}→{n}");
            prev = n;
        }
        // Different seeds are uncorrelated enough to use as separate axes.
        assert!((noise_pm1(1.234, 1) - noise_pm1(1.234, 2)).abs() > 1e-3);
    }

    #[test]
    fn test_follow_point_converges_and_lags() {
        // A sprinting character: the follow point must lag behind, then catch up.
        let mut follow = Vec3::ZERO;
        let dt = 1.0 / 60.0;
        let mut at_step10 = 0.0f32;
        for step in 1..=600 {
            let target = Vec3::Z * (step as f32 * 4.0 * dt); // 4 m/s run
            let a = low_pass_factor(dt, 0.18);
            follow.x += (target.x - follow.x) * a;
            follow.z += (target.z - follow.z) * a;
            if step == 10 {
                at_step10 = follow.z;
            }
        }
        // After 10 frames the target is at 0.667 m; the lagging follow must be
        // behind it but moving.
        assert!(at_step10 > 0.01 && at_step10 < 0.667, "lagged: {at_step10}");
        // Converged: after 10 s of running the follow point trails by the
        // steady-state lag (speed · tau), not by metres.
        let steady_gap = 4.0 * 0.18;
        let target_z = 600.0f32 * 4.0 * dt;
        assert!(
            (target_z - follow.z - steady_gap).abs() < 0.15,
            "steady-state lag ≈ speed·tau: gap {}",
            target_z - follow.z
        );
    }

    fn test_camera(target: Option<&str>) -> OrbitCamera {
        OrbitCamera {
            target: target.map(str::to_string),
            distance: 3.3,
            height: 1.55,
            pitch_deg: Some(18.3),
            pitch_state_deg: 18.3,
            yaw_deg: 0.0,
            mouse_sensitivity: 0.0,
            min_distance: 2.0,
            max_distance: 80.0,
            follow_lag: DEFAULT_FOLLOW_LAG,
            turn_lag: DEFAULT_TURN_LAG,
            min_terrain_distance: 0.0, // headless: no TerrainRuntime anyway
            follow_point: Vec3::ZERO,
            smooth_yaw_deg: 0.0,
            current_pos: Vec3::ZERO,
            initialized: false,
        }
    }

    #[test]
    fn test_third_person_camera_headless_follow() {
        // Headless app: the camera must converge BEHIND the player at the
        // authored offset (ring 3.3·cos18.3° on +Z, height 1.55 + ring y),
        // not stay at its spawn transform.
        let mut app = App::new();
        app.add_plugins(bevy::app::TaskPoolPlugin::default())
            .add_plugins(bevy::time::TimePlugin);
        app.add_systems(bevy::app::Update, third_person_camera);
        app.world_mut().spawn((
            crate::player::Player::default(),
            Transform::from_xyz(4.0, 10.0, 0.0),
            // No TransformPlugin in this minimal app: seed it explicitly (the
            // system reads GlobalTransform, not Transform).
            GlobalTransform::from_xyz(4.0, 10.0, 0.0),
        ));
        let cam = app
            .world_mut()
            .spawn((
                test_camera(None),
                Transform::IDENTITY,
                GlobalTransform::IDENTITY,
            ))
            .id();
        for _ in 0..180 {
            app.update();
        }
        let cam_t = app.world().get::<Transform>(cam).unwrap();
        let expected = Vec3::new(
            4.0,
            10.0 + 1.55 + 18.3f32.to_radians().sin() * 3.3,
            18.3f32.to_radians().cos() * 3.3,
        );
        assert!(
            cam_t.translation.distance(expected) < 0.1,
            "camera settled at {:?}, expected ~{:?}",
            cam_t.translation,
            expected
        );
        // A named-target camera is left alone by this system (rigid follow
        // owns it): it must not have been dragged to the player.
        let named = app
            .world_mut()
            .spawn((
                test_camera(Some("props")),
                Transform::IDENTITY,
                GlobalTransform::IDENTITY,
            ))
            .id();
        app.update();
        let named_t = app.world().get::<Transform>(named).unwrap();
        assert_eq!(named_t.translation, Vec3::ZERO, "named camera untouched");
    }
}
