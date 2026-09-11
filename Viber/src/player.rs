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

use crate::camera::low_pass_factor;
use crate::recipes::spawn::{DialogueNpc, OrbitCamera};
use crate::terrain::runtime::TerrainRuntime;

/// Gravity magnitude (m/s²) — `DEFAULT_GRAVITY = -60` in the VibeGame
/// physics utils; the jump velocity derives from it.
pub const GRAVITY: f32 = 60.0;
/// A/D move at this fraction of the walk speed while steering the camera
/// (VibeGame `SIDE_MOVE_FACTOR`), so turning carves an arc instead of
/// pivoting in place.
pub const SIDE_MOVE_FACTOR: f32 = 0.6;
/// Probe acima dos pés do herói ao perguntar `surface_below` pelo chão.
///
/// Tem de exceder a skin do character controller (0.02): um herói em repouso
/// assenta a skin acima do collider, e a sonda tem de começar em AR.
pub const GROUND_PROBE: f32 = 0.05;
/// Camera yaw turn rate while steering with A/D (rad/s, VibeGame
/// `CAMERA_TURN_SPEED`).
pub const CAMERA_TURN_SPEED: f32 = 2.5;

/// Terminal fall speed (m/s).
///
/// Gravity used to integrate `vel_y` without a bound. A long enough fall then
/// asks the character controller to move tens of meters in one frame, which
/// outruns its shape-cast: the hero passes THROUGH the terrain collider and
/// keeps accelerating, and because the collider streaming follows the hero the
/// ground never comes back. Reproduced live at `y = -123675` after a debug
/// teleport. 55 m/s is well past any reachable jump/fall speed in the game and
/// still lands inside one 1 m voxel at 60 fps.
pub const TERMINAL_VELOCITY: f32 = 55.0;

/// How far below the topmost surface counts as "fell out of the world" (m).
///
/// Deeper than any authored cave or the whole `max-height` of a world, so a
/// legitimate underground hero is never teleported; only one that already
/// tunnelled is rescued.
pub const VOID_RESCUE_DEPTH: f32 = 250.0;

/// Clamps the fall speed to [`TERMINAL_VELOCITY`]. Rising is never clamped —
/// the jump apex is tuned against `GRAVITY` alone.
pub fn clamp_fall_speed(vel_y: f32) -> f32 {
    vel_y.max(-TERMINAL_VELOCITY)
}

/// True when the hero is so far under the topmost surface that no cave
/// explains it — they tunnelled and are falling through the void.
pub fn fell_out_of_world(player_y: f32, top: f32) -> bool {
    player_y < top - VOID_RESCUE_DEPTH
}

pub const JUMP_BUFFER: f32 = 0.1;
/// Coyote time: jumps still land within this window after leaving the ground
/// (VibeGame `INPUT_CONFIG.gracePeriods.coyoteTime` = 100 ms).
pub const COYOTE_TIME: f32 = 0.1;
/// Cooldown between jumps (VibeGame `JUMP_CONSTANTS.cooldown` = 0.2 s).
pub const JUMP_COOLDOWN: f32 = 0.2;
/// Time constant to REACH the input velocity (acceleration feel). Larger =
/// heavier, more inertia ramping up.
pub const ACCEL_TAU: f32 = 0.08;
/// Time constant to bleed the velocity back to zero when input stops
/// (drag / skid feel). Larger = more drift on release.
pub const DECEL_TAU: f32 = 0.14;
/// Time constant while airborne: jumps keep their momentum (drifty arcs)
/// instead of tracking input instantly.
pub const AIR_TAU: f32 = 0.3;

// ── passos (passe de juice r1) ──────────────────────────────────────────

/// Comprimento de passada (m): o intervalo dos passos deriva da velocidade
/// REAL (o low-pass que o movimento já calcula) — andar a 4 m/s soa a
/// ~0.55 s, sprint a 6 m/s a ~0.37 s. Passada constante, cadência variável.
pub const STEP_STRIDE_M: f32 = 2.2;
/// Velocidade mínima (m/s) para haver passos — parado ou a roçar o chão
/// não há som.
pub const STEP_MIN_SPEED: f32 = 0.8;

/// Intervalo entre passos a `speed` m/s (`None` = sem passos).
pub fn step_interval(speed: f32) -> Option<f32> {
    if !speed.is_finite() || speed < STEP_MIN_SPEED {
        return None;
    }
    Some(STEP_STRIDE_M / speed)
}

/// Decisão de um passo (pura; o sistema só escreve o SFX): decrementa o
/// timer e devolve `Some(intervalo)` no frame em que o passo DEVE soar.
/// Parado/airborne reinicia o timer — o 1.º passo ao arrancar soa logo.
pub fn footstep_due(step_timer: &mut f32, grounded: bool, speed: f32, dt: f32) -> Option<f32> {
    *step_timer -= dt;
    match step_interval(speed).filter(|_| grounded) {
        Some(interval) if *step_timer <= 0.0 => {
            *step_timer = interval;
            Some(interval)
        }
        _ => {
            if !grounded || speed < STEP_MIN_SPEED {
                *step_timer = 0.0;
            }
            None
        }
    }
}

/// Smoothed-velocity time constant for the current motion state.
pub fn movement_tau(grounded: bool, input_active: bool) -> f32 {
    if !grounded {
        AIR_TAU
    } else if input_active {
        ACCEL_TAU
    } else {
        DECEL_TAU
    }
}

/// Whether gravity integrates this frame. Never while resting on the ground:
/// a grounded `vel_y` is reset to zero every frame, so ticking gravity there
/// handed the character controller a constant downward push that it turns
/// into a downhill slide on any slope (the hero kept creeping after input
/// stopped); walking downhill stays glued through `snap_to_ground` instead.
/// Airborne or rising (jump) always takes gravity — `grounded` lags the
/// controller output by a frame, and skipping the jump frame overshot the apex.
pub fn gravity_applies(grounded: bool, vel_y: f32) -> bool {
    !grounded || vel_y > 0.0
}

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
    /// Smoothed horizontal velocity (m/s) — carries the inertia/drag feel;
    /// the auto-camera reads it for the movement heading.
    pub vel_x: f32,
    pub vel_z: f32,
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
    /// Time of the last A/D steering frame — the auto-camera hands control
    /// back to the player for a grace period after manual steering.
    pub last_steer_time: f32,
    /// Time of the last frame the hero was actually moving (speed above the
    /// walk gate) — starts at +∞ so the camera's idle settle only engages
    /// after the character has moved and then stopped.
    pub last_moving_time: f32,
    /// Timer dos passos (s até ao próximo SFX; ver [`footstep_due`]).
    pub step_timer: f32,
}

impl Default for Player {
    fn default() -> Self {
        Self {
            speed: 4.0,
            sprint_multiplier: 1.5,
            jump_height: 2.3,
            rotation_speed: 10.0,
            vel_y: 0.0,
            vel_x: 0.0,
            vel_z: 0.0,
            grounded: true,
            can_jump: true,
            is_jumping: false,
            jump_cooldown: 0.0,
            last_grounded_time: f32::NEG_INFINITY,
            jump_buffer_time: f32::NEG_INFINITY,
            last_steer_time: f32::NEG_INFINITY,
            last_moving_time: f32::INFINITY,
            step_timer: 0.0,
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
#[allow(clippy::too_many_arguments)]
pub fn player_movement(
    keys: Res<ButtonInput<KeyCode>>,
    menus: Res<crate::menus::MenusOpen>,
    time: Res<Time>,
    runtime: Option<Res<TerrainRuntime>>,
    collision: Option<Res<crate::physics::TerrainCollisionStatus>>,
    mut cameras: Query<&mut OrbitCamera>,
    mut sfx: MessageWriter<crate::ambient::SfxEvent>,
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
    // Modal aberto consome o teclado: WASD/Espaço não movem/saltam por
    // trás da loja/menu (W/S navegavam E andavam). O `translation` PENDENTE
    // do character controller limpa-se TAMBÉM — no bevy_rapier3d 0.36 ele
    // persiste e é reaplicado em todos os steps (o herói deslizava com o
    // menu aberto e, se estava em salto, subia indefinidamente).
    if menus.any() {
        for (_, mut player, controller, _) in &mut players {
            player.vel_y = 0.0;
            if let Some(mut controller) = controller {
                controller.translation = Some(Vec3::ZERO);
            }
        }
        return;
    }
    let dt = time.delta_secs();
    let now = time.elapsed_secs();
    // Há chão de collider carregado sob o herói? (streaming de colunas)
    let terrain_ready = collision.is_some_and(|status| status.ready);
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
        // The yaw itself is smoothed by the camera system (turnLag), and the
        // auto-follow hands control back after a grace period.
        let mut camera_yaw_deg = 0.0f32;
        if let Some(mut cam) = cameras.iter_mut().next() {
            cam.yaw_deg -= move_x * CAMERA_TURN_SPEED * dt;
            camera_yaw_deg = cam.yaw_deg;
        }
        if move_x != 0.0 {
            player.last_steer_time = now;
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

        // Inertia & drag: the actual velocity chases the input velocity
        // through a state-dependent low-pass (accel ramp, release skid,
        // drifty airborne arcs) — instant velocity reads as "stiff".
        let tau = movement_tau(player.grounded, dir.length_squared() > 0.0);
        let a = low_pass_factor(dt, tau);
        player.vel_x += (desired.x - player.vel_x) * a;
        player.vel_z += (desired.z - player.vel_z) * a;
        if player.vel_x * player.vel_x + player.vel_z * player.vel_z > 0.5 * 0.5 {
            player.last_moving_time = now;
        }
        let mut motion = Vec3::new(player.vel_x, 0.0, player.vel_z) * dt;

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
            sfx.write(crate::ambient::SfxEvent {
                clip: crate::ambient::SfxClip::Jump,
                position: Some(transform.translation),
            });
        }

        // Ground height under the player — the floor of last resort while the
        // real colliders stream in. Under an overhang or inside a cave the
        // top of the world is the WRONG floor and must not snap the hero up
        // through the roof — see [`last_resort_ground`].
        //
        // FORA DA PEGADA não há terreno: as amostras saturam na orla e
        // devolvem-me a cota da borda, que aqui dentro seria uma "superfície"
        // a 2 km de distância usada para resgatar o herói — o salto de metro
        // que uma cena de interior fora do mapa levava. Fora do campo as duas
        // rede de segurança desligam-se e manda o collider.
        let in_field = runtime.in_field(transform.translation.x, transform.translation.z);
        let top = if in_field {
            runtime.sample(transform.translation.x, transform.translation.z)
        } else {
            f32::NEG_INFINITY
        };

        // Void rescue: a hero this far under the surface tunnelled through the
        // terrain collider (see [`fell_out_of_world`]). Put them back on the
        // surface with no inertia instead of letting them fall forever.
        if fell_out_of_world(transform.translation.y, top) {
            warn!(
                "player fell out of the world at y={:.1} (surface {:.1}) — rescued to the surface",
                transform.translation.y, top
            );
            transform.translation.y = top;
            player.vel_y = 0.0;
            player.grounded = true;
        }
        // O chão é a superfície SÓLIDA SOB o herói (o zero do SDF por baixo
        // dos pés), não o topo do mundo — sob um overhang ou dentro de uma
        // gruta o topo é teto, não chão. É só a rede de segurança de
        // "sem collider carregado": com chão de collider debaixo do herói
        // (`TerrainCollisionStatus.ready`), o collider é a autoridade.
        let ground = if in_field {
            runtime
                .surface_below(
                    transform.translation.x,
                    transform.translation.z,
                    transform.translation.y + GROUND_PROBE,
                )
                .unwrap_or(f32::NEG_INFINITY)
        } else {
            f32::NEG_INFINITY
        };

        // Vertical integration. Falls faster than it rises feels right
        // (gravity is already twice the jump-fair value); see
        // [`gravity_applies`] for why it skips resting frames.
        if gravity_applies(player.grounded, player.vel_y) {
            player.vel_y -= GRAVITY * dt;
        }
        // Bounded fall: one frame of motion must stay small enough for the
        // controller's shape-cast to see the ground — see [`clamp_fall_speed`].
        player.vel_y = clamp_fall_speed(player.vel_y);
        motion.y += player.vel_y * dt;

        match controller.as_deref_mut() {
            // With a character controller Rapier resolves the motion against
            // the world's colliders, so walls and props actually stop the hero
            // instead of the transform being written straight through them.
            Some(controller) => {
                controller.translation = Some(motion);
                // Collider authority: `grounded` vem da resolução do Rapier
                // contra o trimesh da coluna. O chão analítico só entra
                // quando NÃO há chão de collider carregado (arranque,
                // teleporte para coluna ainda por assar) — com chão carregado,
                // não-grounded é airborne legítimo, não desculpa para snap.
                let on_collider = output.is_some_and(|out| out.grounded);
                if on_collider {
                    player.grounded = true;
                    if player.vel_y < 0.0 {
                        player.vel_y = 0.0;
                    }
                } else if !terrain_ready && transform.translation.y <= ground {
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

        // Passos: cadência pela velocidade REAL (o mesmo low-pass que a
        // animação/câmara leem) — parado/airborne não soa. Em água a variante
        // `FootstepWater` substitui o passo seco.
        let planar = (player.vel_x * player.vel_x + player.vel_z * player.vel_z).sqrt();
        let grounded = player.grounded;
        if footstep_due(&mut player.step_timer, grounded, planar, dt).is_some() {
            let clip = if runtime.in_water(transform.translation.x, transform.translation.z) {
                crate::ambient::SfxClip::FootstepWater
            } else {
                crate::ambient::SfxClip::Footstep
            };
            sfx.write(crate::ambient::SfxEvent {
                clip,
                position: Some(transform.translation),
            });
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
    // Todo o custo (varrer os NPC e medir distâncias) só existe no frame em que
    // [E] é premido. Fora disso o resultado era descartado (`let _ = near`),
    // portanto era N `distance` (sqrt) + um `min_by` por frame sem leitor.
    if !keys.just_pressed(KeyCode::KeyE) {
        return;
    }
    let Ok(player) = players.single() else {
        return;
    };
    let player_pos = player.translation();
    // O MAIS PRÓXIMO em alcance, não o primeiro da ordem de query — com dois
    // NPC em alcance, `.next()` devolvia o id errado (order-dependent).
    let nearest = npcs
        .iter()
        .filter(|(t, _)| t.translation().distance_squared(player_pos) < 3.5 * 3.5)
        .min_by(|(a, _), (b, _)| {
            a.translation()
                .distance_squared(player_pos)
                .partial_cmp(&b.translation().distance_squared(player_pos))
                .unwrap_or(std::cmp::Ordering::Equal)
        })
        .map(|(_, npc)| npc.dialogue_id.as_str());
    match nearest {
        Some(id) => bevy::log::info!("interaction: dialogue {} available", id),
        None => bevy::log::info!("interaction: nothing nearby"),
    }
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
    fn test_ground_probe_starts_above_the_controller_skin() {
        // O herói em repouso assenta a SKIN do controller (0.02, ver
        // `hero_controller`) acima do collider; a sonda tem de começar em ar.
        // Valores em jogo: GROUND_PROBE e a skin, à data deste teste.
        let probe_ok = GROUND_PROBE > 0.02 && GROUND_PROBE < 0.2;
        assert!(
            probe_ok,
            "GROUND_PROBE={GROUND_PROBE} saiu da janela (skin, degrau)"
        );
    }

    #[test]
    fn test_fall_speed_is_bounded_but_rising_is_not() {
        // Uma queda longa integrava `vel_y` sem limite: a 60 fps um `vel_y` de
        // -1000 pede 16 m num frame e o shape-cast do controller não vê o chão.
        assert_eq!(clamp_fall_speed(-1000.0), -TERMINAL_VELOCITY);
        assert_eq!(clamp_fall_speed(-TERMINAL_VELOCITY), -TERMINAL_VELOCITY);
        // Um frame à velocidade terminal tem de caber num voxel de LOD 0 (1 m)
        // a 60 fps, senão o clamp não resolve nada.
        let frame_drop = TERMINAL_VELOCITY / 60.0;
        assert!(
            frame_drop < 1.0,
            "queda de {frame_drop} m/frame salta o voxel"
        );
        // Subir (salto) nunca é cortado — o apex está afinado contra GRAVITY.
        assert_eq!(clamp_fall_speed(0.0), 0.0);
        assert_eq!(clamp_fall_speed(1000.0), 1000.0);
    }

    #[test]
    fn test_void_rescue_only_fires_below_any_real_cave() {
        // Na superfície, debaixo de um teto, ou numa gruta funda: não é void.
        assert!(!fell_out_of_world(30.0, 30.0));
        assert!(!fell_out_of_world(-40.0, 30.0));
        assert!(!fell_out_of_world(30.0 - VOID_RESCUE_DEPTH, 30.0));
        // Atravessou o terreno e continua a cair: resgate.
        assert!(fell_out_of_world(30.0 - VOID_RESCUE_DEPTH - 0.01, 30.0));
        assert!(fell_out_of_world(-123675.0, 38.75));
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
    fn test_movement_tau_states() {
        assert_eq!(movement_tau(true, true), ACCEL_TAU);
        assert_eq!(movement_tau(true, false), DECEL_TAU);
        assert_eq!(movement_tau(false, true), AIR_TAU);
        assert_eq!(movement_tau(false, false), AIR_TAU);
    }

    #[test]
    fn test_gravity_skips_resting_frames_only() {
        // Parado ou a andar numa encosta (grounded, vel_y a zero): sem tick —
        // era este empurrão que o controller convertia em escorregar.
        assert!(!gravity_applies(true, 0.0));
        // Airborne (a cair ou no topo do arco): gravidade sempre.
        assert!(gravity_applies(false, 0.0));
        assert!(gravity_applies(false, -3.0));
        // Frame do salto: `grounded` ainda é o output stale do frame anterior,
        // mas vel_y > 0 — a gravidade tem de ticar ou o apex passa dos 2.3 m.
        assert!(gravity_applies(true, jump_velocity(2.3)));
    }

    #[test]
    fn test_inertia_ramp_and_drag() {
        let dt = 1.0 / 60.0;
        // Acceleration: ~90 % of walk speed after tau·ln(10) ≈ 0.18 s
        // (≈ 11 frames at 60 fps) — a perceptible ramp, not instant.
        let mut v = 0.0f32;
        let mut frames_to_90 = 0u32;
        for i in 1..=120 {
            v += (4.0 - v) * low_pass_factor(dt, movement_tau(true, true));
            if frames_to_90 == 0 && v >= 3.6 {
                frames_to_90 = i;
            }
        }
        assert!(
            (10..=14).contains(&frames_to_90),
            "90 % speed after {frames_to_90} frames"
        );
        // Drag on release: ~10 % left after tau·ln(10) ≈ 0.32 s (≈ 19 frames)
        // — a skid, not a stop.
        let mut v = 4.0f32;
        let mut frames_to_10 = 0u32;
        for i in 1..=120 {
            v += (0.0 - v) * low_pass_factor(dt, movement_tau(true, false));
            if frames_to_10 == 0 && v <= 0.4 {
                frames_to_10 = i;
            }
        }
        assert!(
            (17..=22).contains(&frames_to_10),
            "10 % speed after {frames_to_10} frames"
        );
        // Airborne: much floatier (momentum through jump arcs).
        assert!(movement_tau(false, true) > movement_tau(true, true) * 2.0);
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
        assert_eq!(p.step_timer, 0.0);
    }

    // ── passos (passe de juice r1) ──────────────────────────────────────

    #[test]
    fn test_step_interval_run_vs_walk() {
        // Andar 4 m/s → ~0.55 s; sprint 6 m/s → ~0.37 s (passada fixa).
        let walk = step_interval(4.0).unwrap();
        let run = step_interval(6.0).unwrap();
        assert!((walk - 0.55).abs() < 1e-4, "walk={walk}");
        assert!(run > 0.33 && run < 0.42, "run={run}");
        assert!(run < walk, "sprint tem cadência mais rápida");
        // Passada constante: o dobro da velocidade = metade do intervalo.
        assert!((step_interval(8.0).unwrap() * 2.0 - walk).abs() < 1e-4);
    }

    #[test]
    fn test_footsteps_silent_when_still_and_airborne() {
        let mut timer = 5.0;
        // Parado: sem passos e timer REINICIADO (o 1.º passo ao arrancar
        // soa logo, não espera um intervalo velho).
        assert!(footstep_due(&mut timer, true, 0.0, 0.016).is_none());
        assert_eq!(timer, 0.0);
        // Deriva lenta (abaixo do gate): sem passos.
        assert!(footstep_due(&mut timer, true, 0.3, 0.016).is_none());
        // Airborne: sem passos mesmo com velocidade.
        assert!(footstep_due(&mut timer, false, 4.0, 0.016).is_none());
        // Lixo numérico: sem passos, sem pânico.
        assert!(footstep_due(&mut timer, true, f32::NAN, 0.016).is_none());
        assert!(footstep_due(&mut timer, true, f32::INFINITY, 0.016).is_none());
    }

    #[test]
    fn test_footstep_cadence_run_vs_walk_sequence() {
        let dt = 1.0 / 60.0;
        // 1.º passo ao arrancar soa de imediato; depois ao intervalo —
        // NÃO em todos os frames.
        let mut timer = 0.0;
        let interval = footstep_due(&mut timer, true, 4.0, dt).expect("1.º passo");
        assert!((interval - 0.55).abs() < 1e-4);
        // Frame seguinte: não há passo novo (o timer está a correr).
        assert!(footstep_due(&mut timer, true, 4.0, dt).is_none());
        // 10 s a andar: ~10/0.55 ≈ 18 passos (1.º incluído).
        let mut walk_steps = 1;
        for _ in 1..600 {
            if footstep_due(&mut timer, true, 4.0, dt).is_some() {
                walk_steps += 1;
            }
        }
        assert!(
            (17..=21).contains(&walk_steps),
            "andar 10 s → ~18 passos: {walk_steps}"
        );
        // Em sprint a cadência aperta: mais passos no mesmo tempo.
        let mut timer = 0.0;
        let mut run_steps = 1;
        for _ in 1..600 {
            if footstep_due(&mut timer, true, 6.0, dt).is_some() {
                run_steps += 1;
            }
        }
        assert!(
            run_steps > walk_steps,
            "sprint {run_steps} > andar {walk_steps}"
        );
    }
}
