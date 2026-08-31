//! Enemy AI — deterministic wander + player chase over the carved terrain,
//! plus the respawn queue for dynamic spawner groups.
//!
//! Creatures carry [`EnemyCreature`] (authored stats + current state); the
//! patrol bookkeeping lives in [`WanderState`], auto-inserted by [`enemy_ai`]
//! on the first tick. Heights come straight from [`TerrainRuntime::sample`]
//! every tick (no physics bodies) and facing reuses the player's
//! +Z-forward convention via [`crate::player::facing_rotation`].
//!
//! Deaths are not emitted anywhere yet, so [`respawn_spawners`] mostly idles —
//! it exists and is wired so whoever starts pushing [`RespawnEntry`] into the
//! queue (combat, spawner lifecycle) gets respawns for free.
//!
//! Wiring: `app.add_plugins(crate::ai::AiPlugin);` (see [`AiPlugin`]).

use bevy::math::Vec3;
use bevy::prelude::*;

use crate::terrain::runtime::TerrainRuntime;

/// Wander speed as a fraction of the authored chase speed.
pub const WANDER_SPEED_FRACTION: f32 = 0.4;
/// Chase keeps aggro until this multiple of `aggro_radius` (hysteresis band).
pub const DEAGGRO_HYSTERESIS: f32 = 1.8;
/// Seconds a wanderer keeps the same patrol target before picking a new one.
pub const WANDER_RETARGET_SECS: f32 = 4.0;
/// Patrol targets are picked inside this radius around `home`.
pub const WANDER_RADIUS: f32 = 8.0;
/// "Arrived" distance for a patrol target (triggers an early retarget).
pub const WANDER_ARRIVE_DIST: f32 = 0.5;

/// Behaviour state of one [`EnemyCreature`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum EnemyState {
    /// Patrol around `home`; the [`Default`].
    #[default]
    Wander,
    /// Run at the player while inside the aggro radius.
    Chase,
}

/// A spawned creature: authored stats plus its current behaviour state.
///
/// `Default` is Wander with `home` at the origin — [`enemy_ai`] latches the
/// real home to the spawn position on the first tick.
#[derive(Debug, Clone, Component)]
pub struct EnemyCreature {
    /// Chase speed in m/s (wander runs at [`WANDER_SPEED_FRACTION`] × this).
    pub speed: f32,
    /// Player distance that flips Wander → Chase.
    pub aggro_radius: f32,
    /// Chase stops (attack range) once this close to the player.
    pub attack_radius: f32,
    /// Patrol anchor (XZ); set by [`enemy_ai`] on the first tick.
    pub home: Vec2,
    pub state: EnemyState,
}

impl Default for EnemyCreature {
    fn default() -> Self {
        Self {
            speed: 2.0,
            aggro_radius: 18.0,
            attack_radius: 2.2,
            home: Vec2::ZERO,
            state: EnemyState::Wander,
        }
    }
}

/// Patrol bookkeeping for one wanderer, auto-inserted by [`enemy_ai`].
#[derive(Debug, Clone, Component, Default)]
pub struct WanderState {
    /// Current patrol target (XZ).
    pub target: Vec2,
    /// `Time.elapsed_secs()` when a new target must be picked.
    pub next_pick_at: f32,
    /// Retarget count — feeds the deterministic per-pick seed.
    pub picks: u64,
}

/// Deterministic seed for one retarget of one entity: a hash of the entity
/// index and the pick counter (which advances with arrival/time triggers).
pub fn enemy_seed(entity_index: u32, picks: u64) -> u64 {
    (entity_index as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15)
        ^ picks.wrapping_mul(0xBF58_476D_1CE4_E5B9)
}

/// Patrol target: uniform point in the disc `home ± radius`, via the shared
/// SplitMix64 RNG (`crate::spawner::Rng`) — same seed, same target.
pub fn wander_target(home: Vec2, radius: f32, rng_seed: u64) -> Vec2 {
    let mut rng = crate::spawner::Rng::new(rng_seed);
    home + rng.unit_disc() * radius
}

/// State transition with the deaggro hysteresis band: Wander → Chase inside
/// `aggro`, Chase → Wander only past `deaggro` (> `aggro`, typically
/// `aggro × [`DEAGGRO_HYSTERESIS`]`); in between the state is sticky.
pub fn enemy_next_state(dist: f32, state: EnemyState, aggro: f32, deaggro: f32) -> EnemyState {
    match state {
        EnemyState::Wander if dist < aggro => EnemyState::Chase,
        EnemyState::Chase if dist > deaggro => EnemyState::Wander,
        other => other,
    }
}

/// Clamp an XZ position to the world disc of radius `limit` (Y passes
/// through — heights come from the terrain sampler, not the border).
pub fn clamp_to_world(pos: Vec3, limit: f32) -> Vec3 {
    let dist_sq = pos.x * pos.x + pos.z * pos.z;
    if dist_sq > limit * limit {
        let scale = limit / dist_sq.sqrt();
        Vec3::new(pos.x * scale, pos.y, pos.z * scale)
    } else {
        pos
    }
}

/// One pending creature respawn.
#[derive(Debug, Clone, PartialEq)]
pub struct RespawnEntry {
    /// World position to respawn at.
    pub position: Vec3,
    /// Template of the originating spawner group (kept for future scene picks).
    pub template_index: usize,
    /// Identifier of the originating spawner group.
    pub group_id: u64,
    /// `Time.elapsed_secs()` at which the creature comes back.
    pub respawn_at: f32,
}

/// Creatures waiting to come back. Nothing fills this yet — spawner deaths
/// are not emitted; the queue and [`respawn_spawners`] are ready for whoever
/// starts emitting them.
#[derive(Debug, Default, Resource)]
pub struct RespawnQueue(pub Vec<RespawnEntry>);

/// Drain the entries whose time has come (pure split for testability).
pub fn split_due(queue: &mut Vec<RespawnEntry>, now: f32) -> Vec<RespawnEntry> {
    let (due, pending): (Vec<_>, Vec<_>) = queue.drain(..).partition(|e| now >= e.respawn_at);
    *queue = pending;
    due
}

/// Wander + chase driver for every [`EnemyCreature`].
///
/// Per tick: latch `home` to the spawn position on the first tick, resolve
/// Wander/Chase (hysteresis), move toward the active target (player or patrol
/// point), snap Y to the terrain and face the movement direction.
#[allow(clippy::needless_pass_by_value, clippy::type_complexity)]
pub fn enemy_ai(
    mut commands: Commands,
    time: Res<Time>,
    runtime: Option<Res<TerrainRuntime>>,
    players: Query<&GlobalTransform, With<crate::player::Player>>,
    mut enemies: Query<(
        Entity,
        &mut Transform,
        &mut EnemyCreature,
        Option<&mut WanderState>,
    )>,
) {
    let Some(runtime) = runtime else {
        return; // terrain bootstrap has not published the carved world yet
    };
    let now = time.elapsed_secs();
    let player_xz = players
        .iter()
        .next()
        .map(|gt| Vec2::new(gt.translation().x, gt.translation().z));
    let world_limit = runtime.spec.world_size * 0.5;

    for (entity, mut transform, mut enemy, wander) in &mut enemies {
        // First tick: the patrol anchor is wherever the creature spawned.
        if enemy.home == Vec2::ZERO {
            enemy.home = Vec2::new(transform.translation.x, transform.translation.z);
        }
        let pos_xz = Vec2::new(transform.translation.x, transform.translation.z);
        let player_dist = player_xz
            .map(|p| pos_xz.distance(p))
            .unwrap_or(f32::INFINITY);

        let deaggro = enemy.aggro_radius * DEAGGRO_HYSTERESIS;
        enemy.state = enemy_next_state(player_dist, enemy.state, enemy.aggro_radius, deaggro);

        let (speed, target) = match enemy.state {
            EnemyState::Chase => {
                let target = player_xz.unwrap_or(pos_xz);
                // In attack range: hold ground instead of pushing into the player.
                if player_dist <= enemy.attack_radius {
                    (0.0, target)
                } else {
                    (enemy.speed, target)
                }
            }
            EnemyState::Wander => {
                // Auto-insert / refresh patrol state; retarget on arrival or
                // every WANDER_RETARGET_SECS.
                let Some(mut wander) = wander else {
                    commands.entity(entity).insert(WanderState {
                        target: pos_xz,
                        next_pick_at: now + WANDER_RETARGET_SECS,
                        picks: 0,
                    });
                    continue;
                };
                let arrived = pos_xz.distance(wander.target) < WANDER_ARRIVE_DIST;
                if now >= wander.next_pick_at || arrived {
                    wander.picks += 1;
                    wander.target = wander_target(
                        enemy.home,
                        WANDER_RADIUS,
                        enemy_seed(entity.index().index(), wander.picks),
                    );
                    wander.next_pick_at = now + WANDER_RETARGET_SECS;
                }
                (enemy.speed * WANDER_SPEED_FRACTION, wander.target)
            }
        };

        if speed > 0.0 {
            let offset = target - pos_xz;
            if offset.length_squared() > 1e-8 {
                // NOTE: glam's `Vec3::truncate` drops Z (keeps x,y) — build
                // the XZ pair explicitly instead.
                let dir2 = offset.normalize();
                let dir3 = Vec3::new(dir2.x, 0.0, dir2.y);
                let step = (speed * time.delta_secs()).min(offset.length());
                let moved = pos_xz + dir2 * step;
                let moved = clamp_to_world(
                    Vec3::new(moved.x, transform.translation.y, moved.y),
                    world_limit,
                );
                transform.translation =
                    Vec3::new(moved.x, runtime.sample(moved.x, moved.z), moved.z);
                transform.rotation = crate::player::facing_rotation(dir3);
            }
        }
    }
}

/// Spawn due [`RespawnEntry`] items back into the world with a placeholder
/// visual (red sphere). No-op until something starts queueing respawns.
///
/// Assets are `Option` on purpose: Bevy 0.19 panics on failed param
/// validation, and headless worlds carry no render asset stores — there the
/// creature respawns as a logic entity without the placeholder visual.
#[allow(clippy::needless_pass_by_value)]
pub fn respawn_spawners(
    mut commands: Commands,
    time: Res<Time>,
    mut queue: ResMut<RespawnQueue>,
    meshes: Option<ResMut<Assets<Mesh>>>,
    materials: Option<ResMut<Assets<StandardMaterial>>>,
) {
    if queue.0.is_empty() {
        return;
    }
    let (mut meshes, mut materials) = (meshes, materials);
    for entry in split_due(&mut queue.0, time.elapsed_secs()) {
        let mut entity = commands.spawn((
            Name::new(format!(
                "enemy g{}#{}",
                entry.group_id, entry.template_index
            )),
            Transform::from_translation(entry.position),
            Visibility::Inherited,
            EnemyCreature::default(),
        ));
        if let (Some(meshes), Some(materials)) = (&mut meshes, &mut materials) {
            entity.insert((
                Mesh3d(meshes.add(Mesh::from(bevy::math::primitives::Sphere::new(0.5)))),
                MeshMaterial3d(materials.add(StandardMaterial {
                    base_color: Color::srgb(0.85, 0.2, 0.2),
                    ..StandardMaterial::default()
                })),
            ));
        }
    }
}

/// Registers [`RespawnQueue`] plus the [`enemy_ai`] / [`respawn_spawners`]
/// update systems. One line on top of `main`'s plugin stack.
pub struct AiPlugin;

impl Plugin for AiPlugin {
    fn build(&self, app: &mut App) {
        app.init_resource::<RespawnQueue>()
            .add_systems(Update, (enemy_ai, respawn_spawners));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::terrain::heightmap::HeightMapU16;
    use crate::terrain::spec::TerrainSpec;

    #[test]
    fn test_default_enemy_creature_values() {
        let enemy = EnemyCreature::default();
        assert_eq!(enemy.speed, 2.0);
        assert_eq!(enemy.aggro_radius, 18.0);
        assert_eq!(enemy.attack_radius, 2.2);
        assert_eq!(enemy.home, Vec2::ZERO);
        assert_eq!(enemy.state, EnemyState::Wander);
    }

    #[test]
    fn test_wander_target_is_deterministic_and_bounded() {
        let home = Vec2::new(40.0, -12.0);
        let a = wander_target(home, WANDER_RADIUS, 7);
        let b = wander_target(home, WANDER_RADIUS, 7);
        assert_eq!(a, b, "same seed → same patrol point");
        let c = wander_target(home, WANDER_RADIUS, 8);
        assert_ne!(a, c, "different seed → different patrol point");
        for target in [a, b, c] {
            assert!(
                (target - home).length() <= WANDER_RADIUS + 1e-3,
                "target {target:?} outside the patrol disc around {home:?}"
            );
        }
    }

    #[test]
    fn test_enemy_next_state_hysteresis() {
        let aggro = 18.0;
        let deaggro = aggro * DEAGGRO_HYSTERESIS; // 32.4
        // Inside aggro: either state engages the chase.
        assert_eq!(
            enemy_next_state(5.0, EnemyState::Wander, aggro, deaggro),
            EnemyState::Chase
        );
        assert_eq!(
            enemy_next_state(5.0, EnemyState::Chase, aggro, deaggro),
            EnemyState::Chase
        );
        // Hysteresis band (aggro..deaggro): state is sticky.
        let mid = (aggro + deaggro) * 0.5;
        assert_eq!(
            enemy_next_state(mid, EnemyState::Wander, aggro, deaggro),
            EnemyState::Wander
        );
        assert_eq!(
            enemy_next_state(mid, EnemyState::Chase, aggro, deaggro),
            EnemyState::Chase
        );
        // Past deaggro the chase drops; wander never engages far away.
        assert_eq!(
            enemy_next_state(60.0, EnemyState::Chase, aggro, deaggro),
            EnemyState::Wander
        );
        assert_eq!(
            enemy_next_state(60.0, EnemyState::Wander, aggro, deaggro),
            EnemyState::Wander
        );
        // No player → infinite distance never aggros.
        assert_eq!(
            enemy_next_state(f32::INFINITY, EnemyState::Wander, aggro, deaggro),
            EnemyState::Wander
        );
    }

    #[test]
    fn test_clamp_to_world_scales_xz_and_keeps_y() {
        let inside = clamp_to_world(Vec3::new(5.0, 3.0, -7.0), 100.0);
        assert_eq!(
            inside,
            Vec3::new(5.0, 3.0, -7.0),
            "inside the disc: untouched"
        );
        let outside = clamp_to_world(Vec3::new(30.0, 2.0, 40.0), 50.0);
        assert_eq!(outside.y, 2.0, "height passes through");
        let clamped_xz = Vec2::new(outside.x, outside.z);
        assert!(
            (clamped_xz.length() - 50.0).abs() < 1e-4,
            "XZ rescaled onto the limit: {clamped_xz:?}"
        );
        assert!(
            Vec2::new(30.0, 40.0).angle_to(clamped_xz).abs() < 1e-4,
            "direction preserved"
        );
        let zeroed = clamp_to_world(Vec3::new(9.0, 1.0, 9.0), 0.0);
        assert_eq!(
            Vec2::new(zeroed.x, zeroed.z),
            Vec2::ZERO,
            "limit 0 zeroes XZ"
        );
    }

    #[test]
    fn test_enemy_seed_varies_per_entity_and_pick() {
        assert_eq!(enemy_seed(3, 2), enemy_seed(3, 2), "deterministic");
        assert_ne!(enemy_seed(3, 2), enemy_seed(4, 2));
        assert_ne!(enemy_seed(3, 2), enemy_seed(3, 3));
    }

    #[test]
    fn test_split_due_drains_only_due_entries() {
        let mut queue = vec![
            RespawnEntry {
                position: Vec3::ONE,
                template_index: 0,
                group_id: 1,
                respawn_at: 0.0,
            },
            RespawnEntry {
                position: Vec3::ZERO,
                template_index: 2,
                group_id: 1,
                respawn_at: 9.5,
            },
        ];
        let due = split_due(&mut queue, 4.0);
        assert_eq!(due.len(), 1);
        assert_eq!(due[0].group_id, 1);
        assert_eq!(due[0].respawn_at, 0.0);
        assert_eq!(queue.len(), 1, "not-yet-due entry stays queued");
        assert_eq!(queue[0].respawn_at, 9.5);
        assert!(split_due(&mut Vec::new(), 100.0).is_empty(), "empty queue");
    }

    /// Headless end-to-end: a procedural carved world + [`AiPlugin`], one
    /// wanderer and one player inside the aggro radius — the enemy must
    /// engage the chase, close distance and sit on the terrain height.
    #[test]
    fn test_enemy_ai_chases_player_over_terrain_headless() {
        let mut app = App::new();
        // TimePlugin is disabled: it would overwrite the clock with real
        // (microscopic) deltas every frame; we advance `Time` by hand instead.
        app.add_plugins(MinimalPlugins.build().disable::<bevy::time::TimePlugin>())
            .add_plugins(bevy::transform::TransformPlugin)
            .add_plugins(AiPlugin);
        app.init_resource::<Time>();
        let spec = TerrainSpec {
            world_size: 128.0,
            max_height: 40.0,
            seed: 5,
            ..TerrainSpec::default()
        };
        let map = HeightMapU16::procedural(&spec, spec.resolution.max(1) as usize);
        let grid = crate::terrain::brush::BrushGrid::from_height_map(
            &map,
            spec.world_size,
            spec.max_height,
            spec.height_smoothing,
        )
        .expect("grid builds");
        let runtime = TerrainRuntime {
            spec,
            grid,
            water: vec![],
            roads: vec![],
            pads: vec![],
        };
        app.insert_resource(runtime);

        app.world_mut().spawn((
            crate::player::Player::default(),
            Transform::from_xyz(5.0, 0.0, 0.0),
        ));
        app.world_mut()
            .spawn((EnemyCreature::default(), Transform::from_xyz(0.0, 0.0, 0.0)));

        // 20 × 0.25 s = 5 simulated seconds — enough to close 2.8 m at 2 m/s.
        for _ in 0..20 {
            app.world_mut()
                .resource_mut::<Time>()
                .advance_by(std::time::Duration::from_millis(250));
            app.update();
        }

        let world = app.world_mut();
        let mut q = world.query::<(Entity, &EnemyCreature, &Transform)>();
        let (entity, enemy, transform) = q
            .iter(world)
            .next()
            .expect("enemy alive with its components");
        assert_eq!(enemy.state, EnemyState::Chase, "5 m < aggro 18 m → chase");
        let pos = transform.translation;
        let dist = Vec2::new(pos.x, pos.z).distance(Vec2::new(5.0, 0.0));
        assert!(
            dist < 4.5,
            "enemy closed distance toward the player, now {dist:.2} m"
        );
        // Heights come from the sampler at the exact standing spot.
        let ground_here = world.resource::<TerrainRuntime>().sample(pos.x, pos.z);
        assert!(
            (pos.y - ground_here).abs() < 1e-2,
            "enemy y {:+} snapped to sampled ground {ground_here:+}",
            pos.y
        );
        // Patrol bookkeeping is inserted lazily on the first Wander tick; an
        // enemy that never left Chase never gained one.
        assert!(
            world.get::<WanderState>(entity).is_none(),
            "chasing enemies do not patrol"
        );
    }

    /// Same world, no player: the creature stays a wanderer, moves at the
    /// wander pace and never strays past its patrol radius.
    #[test]
    fn test_enemy_ai_wanders_without_a_player_headless() {
        let mut app = App::new();
        app.add_plugins(MinimalPlugins.build().disable::<bevy::time::TimePlugin>())
            .add_plugins(bevy::transform::TransformPlugin)
            .add_plugins(AiPlugin);
        app.init_resource::<Time>();
        let spec = TerrainSpec {
            world_size: 128.0,
            max_height: 40.0,
            seed: 5,
            ..TerrainSpec::default()
        };
        let map = HeightMapU16::procedural(&spec, spec.resolution.max(1) as usize);
        let grid = crate::terrain::brush::BrushGrid::from_height_map(
            &map,
            spec.world_size,
            spec.max_height,
            spec.height_smoothing,
        )
        .expect("grid builds");
        app.insert_resource(TerrainRuntime {
            spec,
            grid,
            water: vec![],
            roads: vec![],
            pads: vec![],
        });
        let world_limit = 128.0 * 0.5;
        let home = Vec2::new(40.0, -30.0); // well inside the world disc
        // Enemy B patrols around an in-world home…
        app.world_mut().spawn((
            EnemyCreature {
                home,
                ..EnemyCreature::default()
            },
            Transform::from_xyz(home.x, 0.0, home.y),
        ));
        // …enemy A spawns outside the world disc to exercise clamp_to_world:
        // it ends up on the border, which is by design farther than the
        // patrol radius from its home.
        let outside = Vec2::new(200.0, 200.0);
        app.world_mut().spawn((
            EnemyCreature {
                home: outside,
                ..EnemyCreature::default()
            },
            Transform::from_xyz(outside.x, 0.0, outside.y),
        ));

        // 30 × 0.25 s = 7.5 simulated seconds of patrol (two retargets).
        for _ in 0..30 {
            app.world_mut()
                .resource_mut::<Time>()
                .advance_by(std::time::Duration::from_millis(250));
            app.update();
        }

        let world = app.world_mut();
        let mut q = world.query::<(&EnemyCreature, &Transform)>();
        let mut border = None;
        let mut patroller = None;
        for (enemy, transform) in q.iter(world) {
            if enemy.home == outside {
                border = Some((enemy, transform));
            } else {
                patroller = Some((enemy, transform));
            }
        }
        let (border_enemy, border_transform) = border.expect("border enemy alive");
        let (patrol_enemy, patrol_transform) = patroller.expect("patrolling enemy alive");
        assert_eq!(
            border_enemy.state,
            EnemyState::Wander,
            "no player → never chases"
        );
        assert_eq!(
            patrol_enemy.state,
            EnemyState::Wander,
            "no player → never chases"
        );

        // Home latched to each spawn point on the first tick…
        assert_eq!(patrol_enemy.home, home);
        // …and the patrol keeps the creature around it (movement is toward a
        // target inside the patrol disc, so it can only drift that far).
        let pos = Vec2::new(
            patrol_transform.translation.x,
            patrol_transform.translation.z,
        );
        assert!(
            pos.distance(home) <= WANDER_RADIUS + 1.0,
            "wanderer stayed near home: {pos:?} vs {home:?}"
        );

        // The out-of-world spawn is pulled onto the border disc every tick.
        let border_pos = Vec2::new(
            border_transform.translation.x,
            border_transform.translation.z,
        );
        assert!(
            border_pos.length() <= world_limit + 1e-3,
            "clamped inside the world disc: {border_pos:?}"
        );
    }

    /// Respawn queue drains due entries into real entities with the
    /// placeholder visual.
    #[test]
    fn test_respawn_spawners_revives_due_entries_headless() {
        let mut app = App::new();
        app.add_plugins(MinimalPlugins).add_plugins(AiPlugin);
        app.insert_resource(RespawnQueue(vec![RespawnEntry {
            position: Vec3::new(3.0, 1.0, -4.0),
            template_index: 1,
            group_id: 42,
            respawn_at: 0.0, // due on the first update
        }]));

        app.update();
        app.update(); // commands apply on the flush after the system

        assert!(
            app.world()
                .get_resource::<RespawnQueue>()
                .expect("queue kept")
                .0
                .is_empty(),
            "due entry drained"
        );
        let world = app.world_mut();
        let mut enemies = world.query::<(&EnemyCreature, &Transform, &Name)>();
        let (enemy, transform, name) = enemies
            .iter(world)
            .next()
            .expect("respawned creature exists");
        assert_eq!(enemy.state, EnemyState::Wander);
        assert_eq!(transform.translation, Vec3::new(3.0, 1.0, -4.0));
        assert!(name.to_string().contains("g42"), "group id in the name");
    }
}
