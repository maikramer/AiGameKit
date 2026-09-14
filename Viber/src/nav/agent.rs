//! Landmass agents: from "walk at this point" to "walk toward this point,
//! around what is in the way, without shouldering the neighbours aside".
//!
//! The contract with the rest of the engine is deliberately narrow. Producers
//! (the Rust FSM, the Luau scripts) keep asking for a velocity through
//! [`crate::ai::AiLocomotion`]; this module intercepts that ask, turns it into
//! a landmass destination, and writes landmass's answer back into the same
//! field. Nothing upstream changes — that is why all seventeen scripts in
//! `examples/simple-rpg` navigate without a line of edit.
//!
//! When there is no navmesh yet (boot, a tile still generating, `VIBER_NAV=0`),
//! the ask passes through untouched and the character beelines exactly as it
//! did before.

use bevy::prelude::*;
use bevy_landmass::prelude::*;
use bevy_landmass::coords::ThreeD;
use bevy_landmass::{Agent3d, AgentTypeIndexCostOverrides, Character};

use crate::ai::AiLocomotion;

/// Type index of a road polygon in the archipelago, mirroring
/// [`super::tile::AREA_ROAD`].
pub const ROAD_TYPE_INDEX: usize = super::tile::AREA_ROAD.0 as usize;
/// Type index of ordinary walkable ground (`AreaType::DEFAULT_WALKABLE`).
pub const GROUND_TYPE_INDEX: usize = 255;

/// What a character is allowed to prefer.
///
/// This is the whole of "prefer roads": one cost multiplier per profile, priced
/// on the archipelago, rather than pathing rules sprinkled through the scripts.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Component)]
pub enum NavProfile {
    /// Townsfolk, guards, merchants: going overland costs
    /// [`super::NavConfig::offroad_cost`] times what the road costs, so they
    /// walk the streets and only cut the corner when the detour is absurd.
    #[default]
    Civil,
    /// Wolves, bandits, anything hunting: ground is ground. A predator that
    /// politely followed the cobble would read as a pedestrian.
    Wild,
}

/// Marks the entity that carries the archipelago and its single island.
#[derive(Resource, Debug, Clone, Copy)]
pub struct NavArchipelago {
    pub archipelago: Entity,
    pub island: Entity,
}

/// The profile a character gets when the world did not state one.
///
/// Hostility is a concept the engine already has — `combat::is_hostile_script`
/// is what decides which scripted creatures get combat vitals — so it decides
/// this too, instead of a second marker to author and keep in sync. A wolf
/// hunts across the ground; a merchant walks the street.
pub fn default_profile(script: Option<&crate::luau::LuaScriptRef>, fsm: bool) -> NavProfile {
    if fsm || script.is_some_and(|script| crate::combat::is_hostile_script(&script.path)) {
        NavProfile::Wild
    } else {
        NavProfile::Civil
    }
}

/// Gives every character an agent, and the hero a character body to avoid.
///
/// Runs every frame because creatures respawn and spawner groups stream in; the
/// query filters on "has locomotion but no agent yet", so the steady-state cost
/// is an empty iteration.
#[allow(clippy::type_complexity)]
pub fn attach_nav_agents(
    mut commands: Commands,
    config: Res<super::NavConfig>,
    archipelago: Option<Res<NavArchipelago>>,
    fresh: Query<
        (
            Entity,
            Option<&NavProfile>,
            Option<&crate::luau::LuaScriptRef>,
            Has<crate::ai::EnemyCreature>,
        ),
        (With<AiLocomotion>, Without<Agent3d>),
    >,
    hero: Query<Entity, (With<crate::player::Player>, Without<Character<ThreeD>>)>,
) {
    let Some(archipelago) = archipelago else {
        return;
    };
    for (entity, profile, script, fsm) in &fresh {
        let profile = profile.copied().unwrap_or_else(|| default_profile(script, fsm));
        let mut overrides = AgentTypeIndexCostOverrides::default();
        if matches!(profile, NavProfile::Civil) {
            overrides.set_type_index_cost(GROUND_TYPE_INDEX, config.offroad_cost);
        }
        commands.entity(entity).insert((
            Agent3dBundle {
                agent: Agent3d::default(),
                settings: AgentSettings {
                    radius: config.agent_radius,
                    desired_speed: 0.0,
                    max_speed: 0.0,
                },
                archipelago_ref: ArchipelagoRef3d::new(archipelago.archipelago),
            },
            // Landmass reads the real velocity to size its avoidance; without
            // it every agent looks motionless to its neighbours.
            Velocity3d::default(),
            AgentTarget3d::None,
            AgentDesiredVelocity3d::default(),
            overrides,
            profile,
        ));
    }
    // The hero is an obstacle to be steered around, never a thing to be
    // pathed: it has its own controller.
    for entity in &hero {
        commands.entity(entity).insert((
            Character3dBundle {
                character: Character::<ThreeD>::default(),
                settings: CharacterSettings {
                    radius: config.agent_radius,
                },
                archipelago_ref: ArchipelagoRef3d::new(archipelago.archipelago),
            },
            Velocity3d::default(),
        ));
    }
}

/// Turns this frame's locomotion ask into a landmass destination.
///
/// Runs after the producers and before landmass itself.
pub fn push_nav_targets(
    config: Res<super::NavConfig>,
    terrain: Option<Res<crate::terrain::runtime::TerrainRuntime>>,
    mut agents: Query<(
        &GlobalTransform,
        &AiLocomotion,
        &mut AgentTarget3d,
        &mut AgentSettings,
        &mut Velocity3d,
    )>,
) {
    if !config.enabled {
        return;
    }
    for (global, loco, mut target, mut settings, mut velocity) in &mut agents {
        let desired = loco.desired();
        let speed = desired.length();
        if speed <= 1e-3 {
            // Standing still: drop the target so landmass stops pathing, but
            // keep the agent registered so others still avoid it.
            *target = AgentTarget3d::None;
            settings.desired_speed = 0.0;
            settings.max_speed = 0.0;
            velocity.velocity = Vec3::ZERO;
            continue;
        }
        let here = global.translation();
        // A stated destination is the real thing to path to — that is the whole
        // point of `drive_to`. Only a producer that gave a bare velocity
        // (`viber.move_by`) falls back to projecting it forward, and then the
        // projection has to reach past the agent's own polygon or the path
        // would never leave it.
        let goal = match loco.goal() {
            // The navmesh follows the GROUND at the destination, which is not
            // the ground the character is standing on. Using the agent's own Y
            // put the target a metre into the air 20 m down a slope, landmass
            // (correctly) reported `TargetNotOnNavMesh`, and the character
            // reverted to the beeline it was supposed to stop taking.
            Some(goal) => {
                let y = terrain
                    .as_deref()
                    .map(|rt| rt.sample_mesh_surface(goal.x, goal.y))
                    .unwrap_or(here.y);
                Vec3::new(goal.x, y, goal.y)
            }
            None => {
                let direction = desired / speed;
                let reach = (speed * config.lookahead_secs).max(config.min_lookahead);
                here + Vec3::new(direction.x, 0.0, direction.y) * reach
            }
        };
        *target = AgentTarget3d::Point(goal);
        settings.desired_speed = speed;
        // Headroom over the ask so the avoidance can sidestep without falling
        // behind; the acceleration ramp in `apply_ai_locomotion` is what keeps
        // that headroom from reading as a lurch.
        settings.max_speed = speed * config.max_speed_factor;
        velocity.velocity = Vec3::new(loco.velocity.x, 0.0, loco.velocity.y);
    }
}

/// Nome estável de um estado de agente — o census do debug bridge
/// (`viber.debug.nav`) e o log de debug partilham os MESMOS rótulos.
pub fn state_name(state: &AgentState) -> &'static str {
    match state {
        AgentState::Idle => "idle",
        AgentState::ReachedTarget => "chegou",
        AgentState::ReachedAnimationLink => "link-chegou",
        AgentState::UsingAnimationLink => "link",
        AgentState::Moving => "a-mover",
        AgentState::AgentNotOnNavMesh => "fora-da-mesh",
        AgentState::TargetNotOnNavMesh => "alvo-fora",
        AgentState::NoPath => "sem-caminho",
        AgentState::Paused => "pausado",
    }
}

/// Writes landmass's answer back into the locomotion ask.
///
/// Runs after landmass. An agent that landmass could not place on the navmesh
/// (off the tile, navmesh still generating) keeps the ask it already had —
/// that is the beeline fallback, and it is the reason navigation can never make
/// the world *worse* than it was.
pub fn pull_nav_velocities(
    config: Res<super::NavConfig>,
    mut agents: Query<(
        &AgentState,
        &AgentDesiredVelocity3d,
        Option<&GlobalTransform>,
        &mut AiLocomotion,
    )>,
) {
    if !config.enabled {
        return;
    }
    let mut census = [0usize; 9];
    let (mut sample_ask, mut sample_nav) = (Vec2::ZERO, Vec2::ZERO);
    let mut sampled = false;
    for (state, desired, global, mut loco) in &mut agents {
        if config.debug && !sampled {
            sampled = true;
            sample_ask = loco.desired();
            let v = desired.velocity();
            sample_nav = Vec2::new(v.x, v.z);
        }
        let position = global.map(|g| g.translation().xz());
        census[match state {
            AgentState::Idle => 0,
            AgentState::ReachedTarget => 1,
            AgentState::ReachedAnimationLink => 2,
            AgentState::UsingAnimationLink => 3,
            AgentState::Moving => 4,
            AgentState::AgentNotOnNavMesh => 5,
            AgentState::TargetNotOnNavMesh => 6,
            AgentState::NoPath => 7,
            AgentState::Paused => 8,
        }] += 1;
        match state {
            // Landmass has a path and an avoidance answer: it wins.
            AgentState::Moving => {
                let velocity = desired.velocity();
                loco.drive(Vec2::new(velocity.x, velocity.z));
            }
            // Landmass says the target is reached. Trust it only when the
            // character really is near the destination it stated: a target that
            // sampled onto the edge of an incomplete navmesh reads as "reached"
            // from a long way off, and obeying that froze the character in the
            // middle of the field.
            AgentState::ReachedTarget => {
                let arrived = match (loco.goal(), position) {
                    (Some(goal), Some(here)) => here.distance(goal) <= config.arrive_distance,
                    // No stated goal: the target WAS the lookahead point, so
                    // reaching it means nothing — keep the producer's ask.
                    _ => false,
                };
                if arrived {
                    loco.halt();
                }
            }
            // No navmesh under the agent, no path to the target, or the target
            // is off the mesh: leave the producer's own ask alone.
            _ => {}
        }
    }
    if config.debug && census.iter().any(|n| *n > 0) {
        debug!(
            "nav: idle {} chegou {} a-andar {} fora-da-mesh {} alvo-fora {} sem-caminho {} pausado {} | 1.º agente pedido ({:.2}, {:.2}) → navegado ({:.2}, {:.2})",
            census[0], census[1], census[4], census[5], census[6], census[7], census[8],
            sample_ask.x, sample_ask.y, sample_nav.x, sample_nav.y
        );
    }
}
