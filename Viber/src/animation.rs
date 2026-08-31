//! glTF animation — clip discovery, state selection and playback.
//!
//! Characters arrive from the asset pipeline as animated GLBs carrying a whole
//! catalogue of clips (the `simple-rpg` hero ships 37: `idle`, `walk`, `run`,
//! `attack`, `death`, …). Bevy loads them but plays nothing on its own, so
//! every character stood in its bind pose — the T-pose in the world.
//!
//! This module does three things:
//!
//! 1. **Bind** — when a glTF scene finishes spawning, build an
//!    [`AnimationGraph`] from every clip in the file and hand it to the
//!    `AnimationPlayer` the glTF loader placed on the scene root.
//! 2. **Resolve** — map a gameplay [`AnimState`] onto whichever clip the file
//!    actually ships. Naming is not consistent across the pipeline: the hero
//!    has `idle` / `walk` / `run`, while retargeted Quaternius rigs have
//!    `Animator3D_BreatheIdle` / `Animator3D_Walk`. [`normalize_clip_name`]
//!    strips the tool prefix and punctuation so both resolve.
//! 3. **Drive** — pick the state from the character's own motion and cross-fade
//!    into it.

use std::collections::HashMap;
use std::time::Duration;

use bevy::animation::AnimationPlayer;
use bevy::animation::graph::{AnimationGraphHandle, AnimationNodeIndex};
use bevy::animation::transition::AnimationTransitions;
use bevy::prelude::*;

/// Cross-fade applied when a character switches clip.
pub const CLIP_BLEND: Duration = Duration::from_millis(180);
/// Below this speed (m/s) a character reads as standing still.
pub const IDLE_SPEED: f32 = 0.15;
/// At or above this speed (m/s) the run clip replaces the walk clip.
pub const RUN_SPEED: f32 = 5.2;

/// A gameplay animation state, independent of what the file calls its clips.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum AnimState {
    #[default]
    Idle,
    Walk,
    Run,
    Jump,
    Fall,
}

impl AnimState {
    /// Clip names to look for, best first.
    ///
    /// Every entry is already normalized (see [`normalize_clip_name`]). The
    /// lists are deliberately broad: the same state is spelled differently by
    /// the hero rig, the retargeted creature rigs and hand-authored props.
    pub fn candidates(self) -> &'static [&'static str] {
        match self {
            AnimState::Idle => &["idle", "breatheidle", "idlebreathe", "stand", "idle01"],
            AnimState::Walk => &["walk", "walkforward", "walking"],
            AnimState::Run => &["run", "sprint", "runforward", "jog"],
            AnimState::Jump => &["jump", "jumpup", "jumpstart"],
            AnimState::Fall => &["fall", "falling", "jumpair", "jump"],
        }
    }

    /// States tried in order when this one has no clip in the file.
    ///
    /// A rig without `run` should jog with `walk` rather than freeze, and a rig
    /// without airborne clips should keep its ground pose.
    pub fn fallbacks(self) -> &'static [AnimState] {
        match self {
            AnimState::Idle => &[],
            AnimState::Walk => &[AnimState::Run, AnimState::Idle],
            AnimState::Run => &[AnimState::Walk, AnimState::Idle],
            AnimState::Jump => &[AnimState::Fall, AnimState::Idle],
            AnimState::Fall => &[AnimState::Jump, AnimState::Idle],
        }
    }
}

/// Lowercases a clip name and drops everything that is not a letter or digit,
/// plus the pipeline's tool prefixes.
///
/// `"Animator3D_BreatheIdle"` and `"breathe idle"` both become `"breatheidle"`,
/// so one candidate list matches every rig the pipeline produces.
pub fn normalize_clip_name(name: &str) -> String {
    let lowered: String = name
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect::<String>()
        .to_ascii_lowercase();
    // Retarget output is prefixed with the tool name; `mixamocom` shows up in
    // third-party rigs that went through the same path.
    for prefix in ["animator3d", "mixamocom", "armature"] {
        if let Some(rest) = lowered.strip_prefix(prefix)
            && !rest.is_empty()
        {
            return rest.to_string();
        }
    }
    lowered
}

/// Chooses the clip index for a state out of a file's clip names.
///
/// Exact normalized matches win over substring matches, so a file containing
/// both `idle` and `swordidle` picks `idle` for [`AnimState::Idle`] instead of
/// whichever happened to be first.
pub fn resolve_clip(names: &[String], state: AnimState) -> Option<usize> {
    let normalized: Vec<String> = names.iter().map(|n| normalize_clip_name(n)).collect();
    for candidate in state.candidates() {
        if let Some(i) = normalized.iter().position(|n| n == candidate) {
            return Some(i);
        }
    }
    for candidate in state.candidates() {
        if let Some(i) = normalized.iter().position(|n| n.contains(candidate)) {
            return Some(i);
        }
    }
    None
}

/// Resolves a state through its fallbacks, so a missing clip degrades instead
/// of leaving the character in its bind pose.
pub fn resolve_with_fallback(names: &[String], state: AnimState) -> Option<usize> {
    resolve_clip(names, state).or_else(|| {
        state
            .fallbacks()
            .iter()
            .find_map(|next| resolve_clip(names, *next))
    })
}

/// Picks the animation state for a character from its own motion.
pub fn state_for_motion(planar_speed: f32, grounded: bool, vertical_speed: f32) -> AnimState {
    if !grounded {
        return if vertical_speed > 0.0 {
            AnimState::Jump
        } else {
            AnimState::Fall
        };
    }
    if planar_speed >= RUN_SPEED {
        AnimState::Run
    } else if planar_speed > IDLE_SPEED {
        AnimState::Walk
    } else {
        AnimState::Idle
    }
}

// ----------------------------------------------------------------- runtime

/// Asks for a glTF's clips to be bound to the `AnimationPlayer` the scene
/// spawns. Placed on the entity that owns the scene; [`bind_animations`]
/// consumes it once the player exists somewhere below.
#[derive(Debug, Component, Clone)]
pub struct AnimatedScene {
    pub gltf: Handle<bevy::gltf::Gltf>,
}

/// A bound character: clip names plus the graph node for each one.
#[derive(Debug, Component)]
pub struct CharacterAnimator {
    /// Clip names as the file spells them, in file order.
    pub clip_names: Vec<String>,
    /// Graph node per clip, parallel to `clip_names`.
    pub nodes: Vec<AnimationNodeIndex>,
    /// The entity carrying the `AnimationPlayer` (a scene descendant).
    pub player: Entity,
    /// State currently playing.
    pub state: Option<AnimState>,
}

impl CharacterAnimator {
    /// Graph node for a state, honouring the fallback chain.
    pub fn node_for(&self, state: AnimState) -> Option<AnimationNodeIndex> {
        resolve_with_fallback(&self.clip_names, state).and_then(|i| self.nodes.get(i).copied())
    }
}

/// Binds loaded glTF clips to the scene's `AnimationPlayer`.
///
/// The glTF loader puts the `AnimationPlayer` on the scene root, which is a
/// descendant of the entity that asked for the scene — so the player is found
/// by walking down, and the resulting [`CharacterAnimator`] lives on the
/// gameplay entity where the movement code can reach it.
pub fn bind_animations(
    mut commands: Commands,
    gltfs: Res<Assets<bevy::gltf::Gltf>>,
    mut graphs: ResMut<Assets<AnimationGraph>>,
    pending: Query<(Entity, &AnimatedScene), Without<CharacterAnimator>>,
    children: Query<&Children>,
    players: Query<(), With<AnimationPlayer>>,
) {
    for (entity, scene) in &pending {
        let Some(gltf) = gltfs.get(&scene.gltf) else {
            continue; // still loading
        };
        if gltf.animations.is_empty() {
            // Nothing to play; stop revisiting this entity.
            commands.entity(entity).remove::<AnimatedScene>();
            continue;
        }
        let Some(player_entity) = find_animation_player(entity, &children, &players) else {
            continue; // scene has not spawned its nodes yet
        };

        let (graph, nodes) = AnimationGraph::from_clips(gltf.animations.iter().cloned());
        let handle = graphs.add(graph);
        // `named_animations` is a map, so file order comes from `animations`
        // and the names are matched back by handle.
        let clip_names = gltf
            .animations
            .iter()
            .map(|clip| {
                gltf.named_animations
                    .iter()
                    .find(|(_, h)| *h == clip)
                    .map(|(name, _)| name.to_string())
                    .unwrap_or_default()
            })
            .collect();

        let animator = CharacterAnimator {
            clip_names,
            nodes,
            player: player_entity,
            state: None,
        };
        // Start every animated character idling. Only the hero has a driver,
        // so without this the NPCs and creatures would stand in their bind
        // pose forever — the same T-pose the hero used to have.
        let mut transitions = AnimationTransitions::new();
        let mut player = AnimationPlayer::default();
        if let Some(node) = animator.node_for(AnimState::Idle) {
            transitions.play(&mut player, node, Duration::ZERO).repeat();
        }
        commands
            .entity(player_entity)
            .insert((AnimationGraphHandle(handle), transitions, player));
        commands
            .entity(entity)
            .remove::<AnimatedScene>()
            .insert(CharacterAnimator {
                state: Some(AnimState::Idle),
                ..animator
            });
    }
}

/// Depth-first search for the `AnimationPlayer` under `root`.
fn find_animation_player(
    root: Entity,
    children: &Query<&Children>,
    players: &Query<(), With<AnimationPlayer>>,
) -> Option<Entity> {
    if players.get(root).is_ok() {
        return Some(root);
    }
    for child in children.get(root).ok()?.iter() {
        if let Some(found) = find_animation_player(child, children, players) {
            return Some(found);
        }
    }
    None
}

/// Plays `state` on a bound character, cross-fading from whatever was playing.
pub fn play_state(
    animator: &mut CharacterAnimator,
    players: &mut Query<(&mut AnimationPlayer, &mut AnimationTransitions)>,
    state: AnimState,
) {
    if animator.state == Some(state) {
        return;
    }
    let Some(node) = animator.node_for(state) else {
        return;
    };
    let Ok((mut player, mut transitions)) = players.get_mut(animator.player) else {
        return;
    };
    transitions.play(&mut player, node, CLIP_BLEND).repeat();
    animator.state = Some(state);
}

/// Drives the hero's clip from the movement the player controller produced.
pub fn drive_player_animation(
    mut heroes: Query<(
        Entity,
        &Transform,
        &crate::player::Player,
        &mut CharacterAnimator,
    )>,
    mut players: Query<(&mut AnimationPlayer, &mut AnimationTransitions)>,
    mut last: Local<HashMap<Entity, Vec3>>,
    time: Res<Time>,
) {
    let dt = time.delta_secs().max(1e-4);
    for (entity, transform, hero, mut animator) in &mut heroes {
        // Speed from actual displacement, not from the input: the character
        // controller may have been blocked by a wall, and a hero pressed into
        // one should stand still rather than run on the spot.
        let previous = last.get(&entity).copied().unwrap_or(transform.translation);
        let delta = transform.translation - previous;
        last.insert(entity, transform.translation);
        let planar = Vec3::new(delta.x, 0.0, delta.z).length() / dt;

        let state = state_for_motion(planar, hero.grounded, hero.vel_y);
        play_state(&mut animator, &mut players, state);
    }
}

/// Registers clip binding and the hero's animation driver.
#[derive(Default)]
pub struct AnimationPlugin;

impl bevy::app::Plugin for AnimationPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.add_systems(
            bevy::app::Update,
            (bind_animations, drive_player_animation).chain(),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The hero GLB's real clip list.
    fn hero_clips() -> Vec<String> {
        [
            "attack",
            "axe",
            "axeidle",
            "chestopen",
            "chop",
            "chopidle",
            "crouchidle",
            "dance",
            "death",
            "fall",
            "fixing",
            "gather",
            "harvest",
            "hit",
            "hithead",
            "idle",
            "interact",
            "jump",
            "jumpland",
            "mine",
            "no",
            "punch",
            "roar",
            "roll",
            "run",
            "spear",
            "spearidle",
            "sprint",
            "sword",
            "sworda",
            "swordb",
            "swordc",
            "swordheavy",
            "swordidle",
            "talk",
            "walk",
            "yes",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect()
    }

    /// The wolf GLB's real clip list — the retargeted naming convention.
    fn wolf_clips() -> Vec<String> {
        [
            "Animator3D_Attack",
            "Animator3D_BreatheIdle",
            "Animator3D_Death",
            "Animator3D_Hit",
            "Animator3D_Jump",
            "Animator3D_Roar",
            "Animator3D_Run",
            "Animator3D_Walk",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect()
    }

    #[test]
    fn test_normalize_strips_tool_prefix_and_punctuation() {
        assert_eq!(normalize_clip_name("Animator3D_BreatheIdle"), "breatheidle");
        assert_eq!(normalize_clip_name("Walk"), "walk");
        assert_eq!(normalize_clip_name("walk forward"), "walkforward");
        assert_eq!(normalize_clip_name("mixamo.com_Run"), "run");
        // A prefix that would leave nothing behind is kept as-is.
        assert_eq!(normalize_clip_name("Armature"), "armature");
    }

    #[test]
    fn test_resolve_prefers_exact_over_substring() {
        let clips = hero_clips();
        // `axeidle`, `chopidle`, `crouchidle` and `swordidle` all contain
        // "idle"; the bare `idle` has to win.
        let idle = resolve_clip(&clips, AnimState::Idle).expect("hero has idle");
        assert_eq!(clips[idle], "idle");
        let walk = resolve_clip(&clips, AnimState::Walk).expect("hero has walk");
        assert_eq!(clips[walk], "walk");
        let run = resolve_clip(&clips, AnimState::Run).expect("hero has run");
        assert_eq!(clips[run], "run");
    }

    #[test]
    fn test_resolve_handles_retargeted_naming() {
        let clips = wolf_clips();
        let idle = resolve_clip(&clips, AnimState::Idle).expect("wolf has an idle");
        assert_eq!(clips[idle], "Animator3D_BreatheIdle");
        let run = resolve_clip(&clips, AnimState::Run).expect("wolf has a run");
        assert_eq!(clips[run], "Animator3D_Run");
    }

    #[test]
    fn test_resolve_falls_back_when_a_clip_is_missing() {
        // A rig with only a walk still moves when asked to run.
        let clips = vec!["Idle".to_string(), "Walk".to_string()];
        let run = resolve_with_fallback(&clips, AnimState::Run).expect("falls back to walk");
        assert_eq!(clips[run], "Walk");
        // No airborne clips: hold the idle pose rather than freeze in bind pose.
        let fall = resolve_with_fallback(&clips, AnimState::Fall).expect("falls back to idle");
        assert_eq!(clips[fall], "Idle");
    }

    #[test]
    fn test_resolve_gives_up_on_an_unrelated_rig() {
        let clips = vec!["ChestOpen".to_string(), "Creak".to_string()];
        assert!(resolve_with_fallback(&clips, AnimState::Walk).is_none());
    }

    #[test]
    fn test_state_for_motion_thresholds() {
        assert_eq!(state_for_motion(0.0, true, 0.0), AnimState::Idle);
        assert_eq!(state_for_motion(0.1, true, 0.0), AnimState::Idle);
        assert_eq!(state_for_motion(3.0, true, 0.0), AnimState::Walk);
        assert_eq!(state_for_motion(6.0, true, 0.0), AnimState::Run);
        // Exactly at the run threshold reads as running.
        assert_eq!(state_for_motion(RUN_SPEED, true, 0.0), AnimState::Run);
    }

    #[test]
    fn test_state_for_motion_airborne_splits_jump_and_fall() {
        assert_eq!(state_for_motion(0.0, false, 5.0), AnimState::Jump);
        assert_eq!(state_for_motion(0.0, false, -5.0), AnimState::Fall);
        // Airborne wins over speed: a running jump is still a jump.
        assert_eq!(state_for_motion(8.0, false, 3.0), AnimState::Jump);
    }
}
