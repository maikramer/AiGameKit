//! glTF animation — clip discovery, state selection and playback.
//!
//! Characters arrive from the asset pipeline as animated GLBs carrying a whole
//! catalogue of clips (the `simple-rpg` hero ships 37: `idle`, `walk`, `run`,
//! `attack`, `death`, …). Bevy loads them but plays nothing on its own, so
//! every character stood in its bind pose — the T-pose in the world.
//!
//! This module does four things:
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
//! 4. **Arbitrate** — one-shot actions (attack, gesture, death) take the clip
//!    for exactly as long as the clip lasts, and the motion driver is locked
//!    out meanwhile instead of fighting it frame by frame.
//! 5. **Survive scene swaps** — the render-LOD ladder
//!    ([`crate::render_lod`]) replaces a character's `WorldAssetRoot` as it
//!    crosses distance tiers, and that swap despawns the whole old subtree,
//!    bound `AnimationPlayer` included. [`rearm_after_scene_swap`] notices the
//!    swap and re-arms the bind with the new tier's own gltf — clips are
//!    per-gltf assets — while [`bind_animations`] re-binds in place, keeping
//!    the locomotion state so a swap mid-chase reads as a step change and not
//!    as a reset through idle.
//!
//! ## Why the arbitration matters
//!
//! [`AnimationTransitions::play`] calls `AnimationPlayer::start`, which
//! **replays** the target clip — `seek_time` goes back to zero. Calling it once
//! per frame therefore pins a character on frame 0 of its own walk cycle: the
//! rig looks rigid, "stops" while walking, and every threshold flap reads as a
//! discrete pop. Every playback path here is idempotent for that reason: the
//! clip is only (re)started when the *resolved graph node* actually changes,
//! which also means a state change that falls back onto the same clip does not
//! restart it.

use std::time::Duration;

use bevy::animation::graph::{AnimationGraphHandle, AnimationNodeIndex};
use bevy::animation::transition::AnimationTransitions;
use bevy::animation::{AnimationPlayer, RepeatAnimation};
use bevy::prelude::*;

/// Cross-fade applied when a character switches locomotion clip.
pub const CLIP_BLEND: Duration = Duration::from_millis(180);
/// Cross-fade into a one-shot action (attack, gesture) — snappier, the hit has
/// to read on the frame the player pressed the button.
pub const ACTION_BLEND: Duration = Duration::from_millis(80);
/// Cross-fade quando um swing INTERROMPE o anterior (mash do ataque): mais
/// longo que o arranque frio, os dois cortes interpolam em vez de "pular".
pub const ACTION_INTERRUPT_BLEND: Duration = Duration::from_millis(150);
/// Below this speed (m/s) a character reads as standing still.
pub const IDLE_SPEED: f32 = 0.15;
/// At or above this speed (m/s) the run clip replaces the walk clip.
pub const RUN_SPEED: f32 = 5.2;
/// Hysteresis band (m/s) below [`RUN_SPEED`] kept while already running.
///
/// Steering with A/D drops the input magnitude to `SIDE_MOVE_FACTOR` (0.6), so
/// a sprinting hero dips under the bare threshold *while turning*. Without the
/// band the state flapped Run↔Walk every few frames and each flap replayed the
/// clip from zero — the "discrete jumps" when curving.
pub const RUN_HYSTERESIS: f32 = 1.0;
/// Hysteresis band (m/s) below [`IDLE_SPEED`] kept while already moving.
pub const IDLE_HYSTERESIS: f32 = 0.07;
/// [`RUN_HYSTERESIS`] as a fraction of the gate, for rigs whose run gate is
/// smaller than the hero's. `1.0 / 5.2` is the hero's own ratio, so the hero
/// keeps exactly the band it always had.
pub const RUN_HYSTERESIS_FRACTION: f32 = RUN_HYSTERESIS / RUN_SPEED;
/// Same idea for the idle band.
pub const IDLE_HYSTERESIS_FRACTION: f32 = IDLE_HYSTERESIS / IDLE_SPEED;
/// A character has to be off the ground this long (s) before the airborne
/// clips take over.
///
/// The hero's `grounded` flag comes from the Rapier controller's previous
/// resolution and flickers on slopes, stairs and while terrain colliders are
/// still streaming in. One flicker frame used to punch a Fall clip through the
/// walk cycle.
pub const AIRBORNE_GRACE: f32 = 0.10;
/// Air-time mínimo (s) para uma aterragem tocar o one-shot `jumpland` —
/// descidas de escada e flickers de declive não são aterragens.
pub const LANDING_AIR_TIME: f32 = 0.25;
/// Cooldown do hit-react por entidade (s): rajadas de dano no mesmo pack de
/// frames fazem UMA reação de flinch, não um loop de tremores.
pub const HIT_REACT_COOLDOWN: f32 = 0.4;
/// Clips de hit-react por ordem de preferência, em nomes NORMALIZADOS
/// ([`normalize_clip_name`]). `Animator3D_Hit` (rigs retargetados) também
/// normaliza para `hit`, logo as duas entradas cobrem os três spellings
/// `hit` → `hithead` → `Animator3D_Hit`.
const HIT_REACT_CLIPS: [&str; 2] = ["hit", "hithead"];
/// Time constant of the measured-speed low-pass (s).
pub const SPEED_SMOOTH_TAU: f32 = 0.12;
/// Playback-rate clamp so the sync never turns a walk into a slideshow or a
/// blur.
pub const SPEED_SCALE_RANGE: (f32, f32) = (0.55, 1.7);
/// Default yaw rate (rad/s) of a character with no authored profile.
///
/// ~7 rad/s turns 180° in a quarter second: fast enough that a creature still
/// commits to a new target immediately, slow enough that the eye reads a turn
/// instead of a teleported orientation.
pub const DEFAULT_TURN_RATE: f32 = 7.0;
/// Default acceleration (m/s²) toward the desired velocity.
///
/// At 12 m/s² a 4 m/s chase reaches full speed in a third of a second — the
/// ramp is what the animation's own low-pass (`SPEED_SMOOTH_TAU`) was fighting
/// when the AI went from standing to full speed inside one frame.
pub const DEFAULT_ACCEL: f32 = 12.0;

/// Nominal locomotion speeds (m/s) of a rig, used to sync clip playback rate
/// to real motion (kills foot sliding).
///
/// `scale_range` travels with the speeds on purpose: the clamp is only a safety
/// net against *wrong* nominals. A rig whose nominals were measured from the
/// speeds it is actually commanded at (see [`LocomotionProfile`]) sits near 1.0
/// and can afford a much tighter net than one running on the module defaults.
#[derive(Debug, Clone, Copy)]
pub struct MotionSpeeds {
    pub walk: f32,
    pub run: f32,
    /// Playback-rate clamp applied by [`Self::scale_for`].
    pub scale_range: (f32, f32),
}

impl MotionSpeeds {
    /// The hero: `Player::speed` 4.0, sprint ×1.5 (see [`crate::player`]).
    ///
    /// Keeps the wide legacy clamp: strafing drops the input magnitude to
    /// `SIDE_MOVE_FACTOR` (0.6) without changing the nominal, so the hero walks
    /// legitimately down at 0.6×.
    pub const HERO: Self = Self {
        walk: 4.0,
        run: 6.0,
        scale_range: SPEED_SCALE_RANGE,
    };
    /// Creatures with no measured profile yet: wander ~1.5 m/s, chase ~4 m/s.
    pub const CREATURE: Self = Self {
        walk: 1.5,
        run: 4.0,
        scale_range: SPEED_SCALE_RANGE,
    };

    /// Clip playback rate for a state at `speed`.
    pub fn scale_for(self, state: AnimState, speed: f32) -> f32 {
        let nominal = match state {
            AnimState::Run => self.run,
            AnimState::Walk => self.walk,
            _ => return 1.0,
        };
        (speed / nominal.max(1e-3)).clamp(self.scale_range.0, self.scale_range.1)
    }
}

/// The two speed thresholds that pick a locomotion state, as a unit.
///
/// They used to be the module constants [`RUN_SPEED`] / [`IDLE_SPEED`] for
/// every rig in the world, which is exactly how the moonwalk got in: a creature
/// chasing at 4.6 m/s never reached the hero-sized 5.2 m/s run gate, stayed on
/// the walk clip, and asked for a 3.07× playback rate that the clamp cut to
/// 1.7× — 45 % of every stride was the feet sliding over the ground.
#[derive(Debug, Clone, Copy)]
pub struct MotionGates {
    pub run: f32,
    pub idle: f32,
}

impl MotionGates {
    /// The historical, hero-sized thresholds.
    pub const HERO: Self = Self {
        run: RUN_SPEED,
        idle: IDLE_SPEED,
    };

    /// Thresholds that fit a rig's own nominals: run halfway between walk and
    /// run, idle low enough that a genuinely slow walker is not read as still.
    pub fn from_speeds(speeds: MotionSpeeds) -> Self {
        Self {
            run: (speeds.walk + speeds.run) * 0.5,
            idle: IDLE_SPEED.min(speeds.walk * 0.2),
        }
    }
}

/// Playback-rate clamp for a rig whose nominals were measured rather than
/// assumed. Tighter than [`SPEED_SCALE_RANGE`] because the ratio should now sit
/// near 1.0; anything outside it is a real mismatch, not a missing nominal.
pub const CALIBRATED_SCALE_RANGE: (f32, f32) = (0.7, 1.45);

/// Speed below which a commanded step counts as "not moving" for calibration.
const CALIBRATION_FLOOR: f32 = 0.05;

/// A walk nominal this close to the run nominal means we only ever saw ONE
/// speed, so the pair is synthesised instead of measured.
const CALIBRATION_SPREAD: f32 = 0.85;

/// Nominal walk as a fraction of run, when only one speed was ever observed.
const CALIBRATION_WALK_FRACTION: f32 = 0.45;

/// Per-rig locomotion nominals — what "walking" and "running" mean for THIS
/// character, plus how fast it may turn and accelerate.
///
/// Authoring is optional by design (mission premise "ease over knobs"): the
/// component calibrates itself from the speeds the entity is actually commanded
/// at, through [`Self::observe`]. The Luau scripts pass their speed to
/// `viber.move_towards`, and the Rust FSM has it in `EnemyCreature::speed`, so
/// both paths teach it the truth without a single line of authoring. An
/// explicit `locomotion="walk: 1.1; run: 4.6"` (or `viber.set_locomotion`)
/// latches [`Self::authored`] and stops the calibration.
#[derive(Debug, Clone, Copy, Component)]
pub struct LocomotionProfile {
    /// Nominal walking speed (m/s) of the rig's walk clip.
    pub walk: f32,
    /// Nominal running speed (m/s) of the rig's run clip.
    pub run: f32,
    /// Maximum yaw rate (rad/s). The whole reason a creature stops spinning
    /// 180° in one frame when it picks a new wander target.
    pub turn_rate: f32,
    /// Acceleration (m/s²) toward the desired velocity.
    pub accel: f32,
    /// Nominals came from authoring, not from observation.
    pub authored: bool,
    /// Slowest non-zero speed ever commanded (`INFINITY` until the first one).
    observed_min: f32,
    /// Fastest speed ever commanded.
    observed_max: f32,
}

impl Default for LocomotionProfile {
    fn default() -> Self {
        Self {
            walk: MotionSpeeds::CREATURE.walk,
            run: MotionSpeeds::CREATURE.run,
            turn_rate: DEFAULT_TURN_RATE,
            accel: DEFAULT_ACCEL,
            authored: false,
            observed_min: f32::INFINITY,
            observed_max: 0.0,
        }
    }
}

impl LocomotionProfile {
    /// A profile with nominals the world author (or a script) stated outright.
    pub fn authored(walk: f32, run: f32) -> Self {
        Self {
            walk: walk.max(1e-3),
            run: run.max(walk).max(1e-3),
            authored: true,
            ..Self::default()
        }
    }

    /// Feeds one commanded speed into the calibration.
    ///
    /// Returns `true` when the nominals moved, so callers can skip change
    /// detection noise on the component.
    pub fn observe(&mut self, commanded: f32) -> bool {
        if self.authored || !commanded.is_finite() || commanded <= CALIBRATION_FLOOR {
            return false;
        }
        let min = self.observed_min.min(commanded);
        let max = self.observed_max.max(commanded);
        if min == self.observed_min && max == self.observed_max {
            return false;
        }
        self.observed_min = min;
        self.observed_max = max;
        // Two clearly separate speeds (wander vs chase) ARE the two nominals.
        // One speed only — a townsfolk that never runs — gives the walk
        // nominal, and the run nominal is synthesised above it so the rig keeps
        // a headroom instead of flipping to the run clip on its own walk.
        self.run = if min < max * CALIBRATION_SPREAD {
            self.walk = min;
            max
        } else {
            self.walk = max;
            max / CALIBRATION_WALK_FRACTION
        };
        true
    }

    /// True once the profile has something better than the module defaults.
    pub fn is_calibrated(&self) -> bool {
        self.authored || self.observed_max > 0.0
    }

    /// Nominals + clamp for [`MotionSpeeds::scale_for`].
    pub fn speeds(&self) -> MotionSpeeds {
        MotionSpeeds {
            walk: self.walk,
            run: self.run,
            scale_range: if self.is_calibrated() {
                CALIBRATED_SCALE_RANGE
            } else {
                SPEED_SCALE_RANGE
            },
        }
    }

    /// State thresholds that fit these nominals.
    pub fn gates(&self) -> MotionGates {
        MotionGates::from_speeds(self.speeds())
    }
}

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

/// Picks the animation state for a character from its own motion, with no
/// memory of the previous state.
///
/// Prefer [`next_state`] in drivers: the bare thresholds flap when the speed
/// hovers on a boundary.
pub fn state_for_motion(planar_speed: f32, grounded: bool, vertical_speed: f32) -> AnimState {
    next_state(None, planar_speed, grounded, vertical_speed)
}

/// Picks the animation state from motion, keeping the current one through the
/// hysteresis bands around the thresholds.
///
/// `current` is the state the character is already in; passing `None` gives the
/// bare thresholds.
pub fn next_state(
    current: Option<AnimState>,
    planar_speed: f32,
    grounded: bool,
    vertical_speed: f32,
) -> AnimState {
    next_state_with(
        current,
        planar_speed,
        grounded,
        vertical_speed,
        MotionGates::HERO,
    )
}

/// [`next_state`] against a rig's own thresholds instead of the module-wide,
/// hero-sized ones.
///
/// The hysteresis bands scale with the gates: a creature whose run gate sits at
/// 2.75 m/s cannot use the hero's 1.0 m/s band without swallowing its whole
/// walk range, so each band is a fraction of its own gate, capped at the
/// historical constant so the hero's behaviour is bit-for-bit what it was.
pub fn next_state_with(
    current: Option<AnimState>,
    planar_speed: f32,
    grounded: bool,
    vertical_speed: f32,
    gates: MotionGates,
) -> AnimState {
    if !grounded {
        return if vertical_speed > 0.0 {
            AnimState::Jump
        } else {
            AnimState::Fall
        };
    }
    let run_band = RUN_HYSTERESIS.min(gates.run * RUN_HYSTERESIS_FRACTION);
    let idle_band = IDLE_HYSTERESIS.min(gates.idle * IDLE_HYSTERESIS_FRACTION);
    // Widen the band the character is already inside, never the one it is
    // leaving — that is what stops the flapping without adding lag on entry.
    let run_gate = if current == Some(AnimState::Run) {
        gates.run - run_band
    } else {
        gates.run
    };
    let idle_gate = if matches!(current, Some(AnimState::Walk) | Some(AnimState::Run)) {
        gates.idle - idle_band
    } else {
        gates.idle
    };
    if planar_speed >= run_gate {
        AnimState::Run
    } else if planar_speed > idle_gate {
        AnimState::Walk
    } else {
        AnimState::Idle
    }
}

// ----------------------------------------------------------------- runtime

/// Asks for a glTF's clips to be bound to the `AnimationPlayer` the scene
/// spawns. Placed on the entity that owns the scene; [`bind_animations`]
/// consumes it once a *virgin* player exists somewhere below (one without a
/// [`BoundAnimation`] mark).
///
/// Consumed on bind, and re-inserted by [`rearm_after_scene_swap`] whenever
/// something swaps the scene under an already-bound character — today the
/// render-LOD ladder, anything else that mutates `WorldAssetRoot` tomorrow.
#[derive(Debug, Component, Clone)]
pub struct AnimatedScene {
    pub gltf: Handle<bevy::gltf::Gltf>,
}

/// Marks an `AnimationPlayer` that [`bind_animations`] already wired a graph
/// into.
///
/// Right after a scene swap the dying subtree and the fresh one can coexist
/// for a frame or two. The search for a player to bind must only ever pick an
/// unmarked one: binding the old, already-graphed player again is precisely
/// the dangling-animator bug this module defends against.
#[derive(Debug, Component)]
pub struct BoundAnimation;

/// Marks a character whose clip is driven by something other than the built-in
/// motion drivers (a Luau script, a cutscene). [`drive_character_animation`]
/// skips it.
#[derive(Debug, Component, Default)]
pub struct ManualAnimation;

/// A bound character: clip names plus the graph node for each one, and the
/// small amount of per-character playback state the drivers need.
#[derive(Debug, Component)]
pub struct CharacterAnimator {
    /// Clip names as the file spells them, in file order.
    pub clip_names: Vec<String>,
    /// Graph node per clip, parallel to `clip_names`.
    pub nodes: Vec<AnimationNodeIndex>,
    /// Clip length in seconds, parallel to `clip_names`. Drives how long a
    /// one-shot action owns the rig.
    pub durations: Vec<f32>,
    /// The entity carrying the `AnimationPlayer` (a scene descendant).
    pub player: Entity,
    /// Locomotion state the motion driver last asked for. Kept across actions
    /// so the hysteresis in [`next_state`] has memory.
    pub state: Option<AnimState>,
    /// Graph node actually asserted on the player. The clip is only replayed
    /// when this changes, which is what keeps playback continuous.
    pub current: Option<AnimationNodeIndex>,
    /// Seconds left in the one-shot action that owns the rig, if any.
    pub action_time: f32,
    /// A terminal clip (death) holds the last pose forever.
    pub locked: bool,
    /// Seconds spent off the ground (airborne debounce).
    pub air_time: f32,
    /// Low-passed planar speed (m/s) measured from real displacement.
    pub speed: f32,
    /// Previous world position, for that measurement.
    pub last_pos: Option<Vec3>,
}

impl CharacterAnimator {
    /// Graph node for a state, honouring the fallback chain.
    pub fn node_for(&self, state: AnimState) -> Option<AnimationNodeIndex> {
        resolve_with_fallback(&self.clip_names, state).and_then(|i| self.nodes.get(i).copied())
    }

    /// Graph node for the first clip whose name satisfies `pred`, matched
    /// against the *normalized* name.
    pub fn node_matching(&self, pred: impl Fn(&str) -> bool) -> Option<AnimationNodeIndex> {
        self.clip_names
            .iter()
            .position(|name| pred(&normalize_clip_name(name)))
            .and_then(|i| self.nodes.get(i).copied())
    }

    /// Length in seconds of the clip behind `node`.
    pub fn duration_of(&self, node: AnimationNodeIndex) -> f32 {
        self.nodes
            .iter()
            .position(|n| *n == node)
            .and_then(|i| self.durations.get(i).copied())
            .unwrap_or(0.0)
    }

    /// True while a one-shot or terminal clip owns the rig.
    pub fn is_busy(&self) -> bool {
        self.locked || self.action_time > 0.0
    }
}

/// Convenience alias — every playback helper needs the same pair of components
/// off the scene's `AnimationPlayer` entity.
pub type PlayerQuery<'w, 's> = Query<
    'w,
    's,
    (
        &'static mut AnimationPlayer,
        &'static mut AnimationTransitions,
    ),
>;

/// Binds loaded glTF clips to the scene's `AnimationPlayer`.
///
/// The glTF loader puts the `AnimationPlayer` on the scene root, which is a
/// descendant of the entity that asked for the scene — so the player is found
/// by walking down, and the resulting [`CharacterAnimator`] lives on the
/// gameplay entity where the movement code can reach it.
///
/// Runs again after a scene swap (see [`rearm_after_scene_swap`]): when a
/// `CharacterAnimator` already exists it is updated in place — same graph
/// procedure, but the measured speed, position baseline and locomotion state
/// carry over, so the character resumes its gait on the new tier's clips
/// instead of snapping back to idle.
pub fn bind_animations(
    mut commands: Commands,
    gltfs: Res<Assets<bevy::gltf::Gltf>>,
    clips: Res<Assets<AnimationClip>>,
    mut graphs: ResMut<Assets<AnimationGraph>>,
    pending: Query<(Entity, &AnimatedScene, Option<&CharacterAnimator>)>,
    children: Query<&Children>,
    players: Query<(), (With<AnimationPlayer>, Without<BoundAnimation>)>,
) {
    for (entity, scene, previous) in &pending {
        let Some(gltf) = gltfs.get(&scene.gltf) else {
            continue; // still loading
        };
        if gltf.animations.is_empty() {
            // Nothing to play; stop revisiting this entity.
            commands.entity(entity).remove::<AnimatedScene>();
            continue;
        }
        let Some(player_entity) = find_animation_player(entity, &children, &players) else {
            continue; // the fresh subtree has not spawned its player yet
        };

        let (graph, nodes) = AnimationGraph::from_clips(gltf.animations.iter().cloned());
        let handle = graphs.add(graph);
        // `named_animations` is a map, so file order comes from `animations`
        // and the names are matched back by handle.
        let clip_names: Vec<String> = gltf
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
        // Clip lengths: a one-shot has to hand the rig back exactly when the
        // clip ends, not on a hard-coded timer.
        let durations: Vec<f32> = gltf
            .animations
            .iter()
            .map(|clip| clips.get(clip).map(|c| c.duration()).unwrap_or(0.0))
            .collect();

        // Measured-motion state survives a re-bind: the entity itself never
        // moved during the swap, so the speed low-pass and the position
        // baseline stay valid.
        let (resume_state, speed, last_pos, air_time) = previous
            .map_or((None, 0.0, None, 0.0), |old| {
                (old.state, old.speed, old.last_pos, old.air_time)
            });
        // Locomotion clip the character was in, resolved against the NEW file.
        // LOD tiers ship the same clip catalogue, so a swap mid-walk finds
        // `walk` again and resumes instead of dipping through idle.
        let resume_node = resume_state
            .and_then(|state| resolve_with_fallback(&clip_names, state))
            .and_then(|i| nodes.get(i).copied());
        let start = resume_node.or_else(|| {
            resolve_with_fallback(&clip_names, AnimState::Idle).and_then(|i| nodes.get(i).copied())
        });
        // `state` records what the rig is actually doing: the resumed state
        // when it resolved, otherwise whatever the fallback started (idle).
        let state = if resume_node.is_some() {
            resume_state
        } else {
            start.map(|_| AnimState::Idle)
        };

        let animator = CharacterAnimator {
            clip_names,
            nodes,
            durations,
            player: player_entity,
            state,
            current: start,
            action_time: 0.0,
            locked: false,
            air_time,
            speed,
            last_pos,
        };
        // Start the rig on `start` right away. Only the hero has a movement
        // driver, so without this the NPCs and creatures would stand in their
        // bind pose forever — the same T-pose the hero used to have.
        let mut transitions = AnimationTransitions::new();
        let mut player = AnimationPlayer::default();
        if let Some(node) = start {
            transitions.play(&mut player, node, Duration::ZERO).repeat();
        }
        commands.entity(player_entity).insert((
            BoundAnimation,
            AnimationGraphHandle(handle),
            transitions,
            player,
        ));
        commands
            .entity(entity)
            .remove::<AnimatedScene>()
            .insert(animator);
    }
}

/// Depth-first search for an *unbound* `AnimationPlayer` under `root`.
///
/// Players already wired by a previous bind carry [`BoundAnimation`] and are
/// skipped — during a scene swap the dying subtree's player can still be in
/// the hierarchy for a frame, and binding it again would re-create the
/// dangling animator the swap just armed against. Descendants are still
/// searched, so a fresh player nested below a dying one is found.
fn find_animation_player(
    root: Entity,
    children: &Query<&Children>,
    players: &Query<(), (With<AnimationPlayer>, Without<BoundAnimation>)>,
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

/// Re-arms the animation bind after something replaced an entity's glTF scene.
///
/// Today that is the render-LOD ladder ([`crate::render_lod::MeshLod`]),
/// which swaps `WorldAssetRoot` to trade mesh detail for distance; tomorrow,
/// any other scene-swapping mechanism gets this for free off the same change
/// detection. The swap despawns the whole old subtree — the bound
/// `AnimationPlayer` included — and the fresh one spawns with a player no
/// graph is attached to, while `CharacterAnimator` keeps pointing at the dead
/// one: playback fails silently and the character freezes in bind pose.
/// Re-inserting [`AnimatedScene`] sends the entity back through
/// [`bind_animations`], which re-binds in place.
///
/// The new tier's own gltf handle is mandatory — animation clips are
/// per-gltf assets, so a graph built for tier 0 does not drive tier 1 — and
/// travels on the ladder (`MeshLod::gltf_tiers`).
///
/// Characters holding a terminal clip (`locked` — corpses) are skipped on
/// purpose: their death pose is lost to the swap either way, and re-arming
/// would stand the corpse back up into `idle`.
#[allow(clippy::type_complexity)]
fn rearm_after_scene_swap(
    mut commands: Commands,
    swapped: Query<
        (
            Entity,
            &crate::render_lod::MeshLod,
            Option<&AnimatedScene>,
            Option<&CharacterAnimator>,
        ),
        Changed<bevy::world_serialization::WorldAssetRoot>,
    >,
) {
    for (entity, lod, animated, animator) in &swapped {
        if animated.is_none() && animator.is_none() {
            continue; // not an animated character (props, vegetation, rocks)
        }
        if animator.is_some_and(|a| a.locked) {
            continue; // a terminal clip owns the rig for good
        }
        let Some(gltf) = lod.gltf_tiers.get(lod.current as usize) else {
            continue; // no gltf known for the tier now resident
        };
        if animated.is_some_and(|a| a.gltf.id() == gltf.id()) {
            continue; // already armed for this tier (e.g. the first spawn)
        }
        commands
            .entity(entity)
            .insert(AnimatedScene { gltf: gltf.clone() });
    }
}

/// Plays `state` on a bound character, cross-fading from whatever was playing.
///
/// No-op while a one-shot action owns the rig, and — crucially — a no-op when
/// the state resolves to the clip already playing. Re-asserting the same state
/// every frame therefore leaves playback untouched instead of pinning the rig
/// on frame 0.
pub fn play_state(animator: &mut CharacterAnimator, players: &mut PlayerQuery, state: AnimState) {
    if animator.is_busy() {
        return;
    }
    let Some(node) = animator.node_for(state) else {
        return;
    };
    animator.state = Some(state);
    if animator.current == Some(node) {
        return;
    }
    let Ok((mut player, mut transitions)) = players.get_mut(animator.player) else {
        return;
    };
    transitions
        .play(&mut player, node, CLIP_BLEND)
        .set_repeat(RepeatAnimation::Forever)
        .set_speed(1.0);
    animator.current = Some(node);
}

/// Plays a one-shot clip that owns the rig until it ends.
///
/// `terminal` keeps the last pose forever (death). Returns `false` when the rig
/// is already locked or the node is unknown.
pub fn play_action(
    animator: &mut CharacterAnimator,
    players: &mut PlayerQuery,
    node: AnimationNodeIndex,
    blend: Duration,
    terminal: bool,
) -> bool {
    play_action_scaled(animator, players, node, blend, terminal, 1.0)
}

/// One-shot com playback speed — o ataque do herói corre a 1.4× (o clip base
/// de ~1.5 s leria lento para golpes a 0.36 s de cadência). `action_time`
/// divide por `speed` para o rig voltar ao driver no fim real do clip.
pub fn play_action_scaled(
    animator: &mut CharacterAnimator,
    players: &mut PlayerQuery,
    node: AnimationNodeIndex,
    blend: Duration,
    terminal: bool,
    speed: f32,
) -> bool {
    if animator.locked {
        return false;
    }
    let speed = if speed.is_finite() && speed > 0.05 {
        speed
    } else {
        1.0
    };
    let Ok((mut player, mut transitions)) = players.get_mut(animator.player) else {
        return false;
    };
    transitions
        .play(&mut player, node, blend)
        .set_repeat(RepeatAnimation::Never)
        .set_speed(speed);
    animator.current = Some(node);
    animator.locked = terminal;
    animator.action_time = if terminal {
        f32::INFINITY
    } else {
        // A clip with no measurable length would lock the rig forever; fall
        // back to a short beat so the driver always gets the rig back.
        let d = animator.duration_of(node);
        let d = if d > 1e-3 { d } else { 0.25 };
        d / speed
    };
    true
}

/// Ticks one-shot actions down and hands the rig back to the motion drivers.
pub fn tick_animator_actions(time: Res<Time>, mut animators: Query<&mut CharacterAnimator>) {
    let dt = time.delta_secs();
    for mut animator in &mut animators {
        if animator.locked || animator.action_time <= 0.0 {
            continue;
        }
        animator.action_time -= dt;
        if animator.action_time <= 0.0 {
            animator.action_time = 0.0;
            // Clearing `current` (and not `state`) makes the next driver tick
            // re-assert the locomotion clip with a proper cross-fade while the
            // hysteresis keeps its memory.
            animator.current = None;
        }
    }
}

// ── hit-react (flinch ao levar dano) ────────────────────────────────────

/// Pedido de hit-react: inserido pelas vias de dano (golpe de melee no land,
/// `PlayerHurt` físico no herói). Consumido por [`hit_react_system`], que o
/// remove SEMPRE — um pedido órfão nunca re-dispara mais tarde.
#[derive(Debug, Component)]
pub struct HitReact;

/// Cooldown do hit-react (s restantes, decrementado por frame).
#[derive(Debug, Component)]
pub struct HitReactCooldown {
    pub timer: f32,
}

/// Guard puro do hit-react: sem cooldown aberto E sem one-shot/clip terminal
/// a possuir o rig (ataques em curso e corpses `locked` não flincham).
pub fn hit_react_allowed(on_cooldown: bool, busy: bool) -> bool {
    !on_cooldown && !busy
}

/// Nó do hit-react com a cadeia de fallback `hit` → `hithead` →
/// `Animator3D_Hit` (os nomes comparam-se NORMALIZADOS; ver
/// [`HIT_REACT_CLIPS`]). `None` = o rig não tem clip de reação — no-op
/// silencioso.
pub fn hit_react_node(animator: &CharacterAnimator) -> Option<AnimationNodeIndex> {
    HIT_REACT_CLIPS
        .iter()
        .find_map(|name| animator.node_matching(|n| n == *name))
}

/// Hit-react: one-shot de flinch com o blend de INTERRUPÇÃO (150 ms — o golpe
/// em curso é cortado a meio, igual ao mash do ataque). Guard de
/// [`HIT_REACT_COOLDOWN`] por entidade + respeito por `is_busy()`; o cooldown
/// só se arma quando o clip chega a tocar. O animator é OPCIONAL de propósito:
/// um pedido num personagem ainda não bindado (GLB a chegar) é consumido sem
/// tentar tocar — sem isto, o marcador órfão disparava um flinch atrasado
/// mal o bind acontecesse.
#[allow(clippy::type_complexity)]
pub fn hit_react_system(
    mut pending: Query<
        (
            Entity,
            Option<&mut CharacterAnimator>,
            Option<&mut HitReactCooldown>,
        ),
        With<HitReact>,
    >,
    // Disjunto por construção: quem tem o pedido é processado acima; o resto
    // só precisa do tick do timer.
    mut cooling: Query<&mut HitReactCooldown, Without<HitReact>>,
    mut players: PlayerQuery,
    time: Res<Time>,
    mut commands: Commands,
) {
    let dt = time.delta_secs();
    for mut cooldown in &mut cooling {
        cooldown.timer = (cooldown.timer - dt).max(0.0);
    }
    for (entity, animator, cooldown) in &mut pending {
        commands.entity(entity).remove::<HitReact>();
        let Some(mut animator) = animator else {
            continue;
        };
        let on_cooldown = cooldown.as_deref().is_some_and(|c| c.timer > 0.0);
        if !hit_react_allowed(on_cooldown, animator.is_busy()) {
            continue;
        }
        let Some(node) = hit_react_node(&animator) else {
            continue;
        };
        if play_action(
            &mut animator,
            &mut players,
            node,
            ACTION_INTERRUPT_BLEND,
            false,
        ) {
            if let Some(mut cooldown) = cooldown {
                cooldown.timer = HIT_REACT_COOLDOWN;
            } else {
                commands.entity(entity).insert(HitReactCooldown {
                    timer: HIT_REACT_COOLDOWN,
                });
            }
        }
    }
}

/// True no frame em que o personagem volta ao chão depois de ar suficiente
/// ([`LANDING_AIR_TIME`]). `was_airborne` é o air-time acumulado ANTES do
/// reset do frame grounded — no driver, o valor lido antes de
/// [`advance_motion`].
pub fn is_landing(was_airborne: f32, grounded: bool) -> bool {
    grounded && was_airborne > LANDING_AIR_TIME
}

/// Updates the smoothed speed / airborne debounce from a new world position and
/// returns the locomotion state to play.
fn advance_motion(
    animator: &mut CharacterAnimator,
    position: Vec3,
    grounded: bool,
    vertical_speed: f32,
    dt: f32,
    gates: MotionGates,
) -> AnimState {
    // Speed from actual displacement, not from the input: the character
    // controller may have been blocked by a wall, and a hero pressed into one
    // should stand still rather than run on the spot.
    let raw = match animator.last_pos {
        Some(previous) => {
            let delta = position - previous;
            Vec3::new(delta.x, 0.0, delta.z).length() / dt
        }
        // First tick (or first tick after a freeze): no baseline, no speed.
        None => 0.0,
    };
    animator.last_pos = Some(position);
    // Low-pass: on a frame hitch the delta reads ~0 and the animation "stopped"
    // mid-walk (Idle↔Walk flip on every stutter).
    let alpha = 1.0 - (-dt / SPEED_SMOOTH_TAU).exp();
    animator.speed += (raw - animator.speed) * alpha;

    if grounded {
        animator.air_time = 0.0;
    } else {
        animator.air_time += dt;
    }
    let airborne = animator.air_time >= AIRBORNE_GRACE;
    next_state_with(
        animator.state,
        animator.speed,
        !airborne,
        vertical_speed,
        gates,
    )
}

/// Matches the clip's playback rate to the character's real speed, so the feet
/// stay planted instead of sliding.
fn sync_clip_speed(
    animator: &CharacterAnimator,
    players: &mut PlayerQuery,
    state: AnimState,
    speeds: MotionSpeeds,
) {
    if animator.is_busy() {
        return;
    }
    let Some(node) = animator.current else {
        return;
    };
    let Ok((mut player, _)) = players.get_mut(animator.player) else {
        return;
    };
    if let Some(active) = player.animation_mut(node) {
        active.set_speed(speeds.scale_for(state, animator.speed));
    }
}

/// Drives the hero's clip from the movement the player controller produced.
pub fn drive_player_animation(
    mut heroes: Query<(&Transform, &crate::player::Player, &mut CharacterAnimator)>,
    mut players: PlayerQuery,
    time: Res<Time>,
) {
    let dt = time.delta_secs().max(1e-4);
    for (transform, hero, mut animator) in &mut heroes {
        // `advance_motion` zera o air_time no primeiro frame grounded, pelo que
        // o valor ANTES da chamada é o voo acumulado até à aterragem.
        let was_airborne = animator.air_time;
        let state = advance_motion(
            &mut animator,
            transform.translation,
            hero.grounded,
            hero.vel_y,
            dt,
            MotionGates::HERO,
        );
        play_state(&mut animator, &mut players, state);
        // Aterragem: one-shot `jumpland` após voo real (rigs sem o clip fazem
        // no-op silencioso). Não interrompe one-shots em curso (um swing a
        // terminar tem prioridade sobre o "poussada") e não toca a histerese
        // — o `play_state` acima já fez a passagem de locomoção.
        if is_landing(was_airborne, hero.grounded) && !animator.is_busy() {
            if let Some(node) = animator.node_matching(|n| n == "jumpland") {
                play_action(&mut animator, &mut players, node, ACTION_BLEND, false);
            }
        }
        sync_clip_speed(&animator, &mut players, state, MotionSpeeds::HERO);
    }
}

/// Drives every other animated character (NPCs, scripted creatures, the Rust
/// FSM enemies) from its own displacement.
///
/// Creatures further than their [`crate::luau::ScriptActivation`] radius are
/// skipped: their clip keeps looping (it costs nothing extra once bound) but the
/// driver does no work and, importantly, drops the position baseline so the
/// character does not read as teleporting when it comes back in range.
#[allow(clippy::type_complexity)]
pub fn drive_character_animation(
    mut characters: Query<
        (
            &GlobalTransform,
            &mut CharacterAnimator,
            Option<&crate::luau::ScriptActivation>,
            Option<&LocomotionProfile>,
        ),
        (Without<crate::player::Player>, Without<ManualAnimation>),
    >,
    hero: Query<&GlobalTransform, With<crate::player::Player>>,
    mut players: PlayerQuery,
    time: Res<Time>,
) {
    let dt = time.delta_secs().max(1e-4);
    let hero_pos = hero.single().ok().map(|g| g.translation());
    for (transform, mut animator, activation, profile) in &mut characters {
        if animator.locked {
            continue; // dead: the terminal clip holds the pose
        }
        let position = transform.translation();
        if let (Some(hero_pos), Some(activation)) = (hero_pos, activation) {
            if position.distance(hero_pos) > activation.radius {
                // Drop the baseline so the first tick back in range measures a
                // frame, not the whole trip.
                animator.last_pos = None;
                continue;
            }
        }
        // A rig with no profile yet falls back to the module-wide creature
        // nominals — the behaviour this driver always had.
        let (speeds, gates) = match profile {
            Some(p) => (p.speeds(), p.gates()),
            None => (
                MotionSpeeds::CREATURE,
                MotionGates::from_speeds(MotionSpeeds::CREATURE),
            ),
        };
        // Creatures are always grounded as far as the clip picker is concerned;
        // none of the rigs in the pipeline ship airborne clips for them.
        let state = advance_motion(&mut animator, position, true, 0.0, dt, gates);
        play_state(&mut animator, &mut players, state);
        sync_clip_speed(&animator, &mut players, state, speeds);
    }
}

/// Registers clip binding, action arbitration and the motion drivers.
#[derive(Default)]
pub struct AnimationPlugin;

impl bevy::app::Plugin for AnimationPlugin {
    fn build(&self, app: &mut bevy::app::App) {
        app.add_systems(
            bevy::app::Update,
            (
                rearm_after_scene_swap,
                bind_animations,
                tick_animator_actions,
                // O flinch corre antes dos drivers: um one-shot que ele toca
                // bloqueia o `play_state` do driver no mesmo frame (`is_busy`).
                hit_react_system,
                // The hero driver reads `Player::grounded` / `vel_y`, which the
                // controller refreshes in `player_movement`; without the order
                // the airborne state was a frame stale at random.
                drive_player_animation.after(crate::player::player_movement),
                drive_character_animation,
            )
                .chain(),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Fraction of a stride the feet slide over the ground: the ratio between
    /// what the body travels and what the clip, at its current playback rate,
    /// says it travels. Zero is planted feet; the moonwalk was ~0.45.
    fn foot_slide(profile: &LocomotionProfile, speed: f32) -> f32 {
        let state = next_state_with(None, speed, true, 0.0, profile.gates());
        let scale = profile.speeds().scale_for(state, speed);
        let nominal = match state {
            AnimState::Run => profile.run,
            AnimState::Walk => profile.walk,
            other => panic!("{speed} m/s should be locomotion, got {other:?}"),
        };
        (speed - scale * nominal).abs() / speed
    }

    /// The bug, frozen. A wolf chasing at 4.6 m/s never reached the hero-sized
    /// 5.2 m/s run gate, so it stayed on the walk clip and asked for a 3.07×
    /// playback rate that the clamp cut to 1.7× — 45 % of every stride was the
    /// feet sliding. This is what the per-rig profile exists to fix.
    #[test]
    fn test_hero_sized_gates_are_what_made_a_chasing_wolf_moonwalk() {
        let chase = 4.6_f32;
        let state = next_state(None, chase, true, 0.0);
        assert_eq!(
            state,
            AnimState::Walk,
            "4.6 m/s is below the hero run gate of {RUN_SPEED}"
        );
        let scale = MotionSpeeds::CREATURE.scale_for(state, chase);
        assert_eq!(scale, SPEED_SCALE_RANGE.1, "the rate request hits the clamp");
        let slide = (chase - scale * MotionSpeeds::CREATURE.walk).abs() / chase;
        assert!(
            slide > 0.4,
            "the legacy path slides {:.0} % of every stride",
            slide * 100.0
        );
    }

    /// Every creature in the example, calibrated from the speeds its script
    /// actually commands, keeps its feet planted at every speed it is driven
    /// at. The 0.15 bar is the acceptance criterion for this work.
    #[test]
    fn test_a_calibrated_profile_keeps_the_feet_planted() {
        // (name, commanded speeds) straight out of examples/simple-rpg/scripts.
        let rigs = [
            ("wolf", vec![2.2_f32, 4.6]),
            ("bandit", vec![1.8, 3.8]),
            ("shade", vec![1.4, 4.2]),
            ("slime", vec![0.7, 2.0]),
            ("witch", vec![1.4, 3.6]),
            ("sand-worm", vec![1.2, 4.0]),
            ("townsfolk", vec![1.1]),
            ("interior-folk", vec![0.6]),
        ];
        for (name, speeds) in rigs {
            let mut profile = LocomotionProfile::default();
            for speed in &speeds {
                profile.observe(*speed);
            }
            assert!(profile.is_calibrated(), "{name} calibrated from its speeds");
            for speed in &speeds {
                let slide = foot_slide(&profile, *speed);
                assert!(
                    slide < 0.15,
                    "{name} at {speed} m/s slides {:.0} % of a stride",
                    slide * 100.0
                );
            }
        }
    }

    /// Two clearly separated speeds ARE the two nominals; a single speed gives
    /// the walk nominal and synthesises headroom above it, so a rig that only
    /// ever strolls never flips itself onto the run clip.
    #[test]
    fn test_calibration_reads_two_speeds_as_walk_and_run() {
        let mut two = LocomotionProfile::default();
        two.observe(2.2);
        two.observe(4.6);
        assert_eq!((two.walk, two.run), (2.2, 4.6));
        assert_eq!(
            next_state_with(None, 4.6, true, 0.0, two.gates()),
            AnimState::Run
        );
        assert_eq!(
            next_state_with(None, 2.2, true, 0.0, two.gates()),
            AnimState::Walk
        );

        let mut one = LocomotionProfile::default();
        one.observe(1.1);
        assert_eq!(one.walk, 1.1);
        assert!(one.run > 1.1, "run nominal sits above the only speed seen");
        assert_eq!(
            next_state_with(None, 1.1, true, 0.0, one.gates()),
            AnimState::Walk,
            "a townsfolk walking at its own only speed stays on the walk clip"
        );
    }

    /// Authoring wins over observation, and observation never touches it.
    #[test]
    fn test_authored_nominals_ignore_observation() {
        let mut profile = LocomotionProfile::authored(1.1, 4.6);
        assert!(!profile.observe(99.0), "authored profiles do not calibrate");
        assert_eq!((profile.walk, profile.run), (1.1, 4.6));
    }

    /// The hero's thresholds and clamp are untouched by the per-rig work.
    #[test]
    fn test_hero_gates_and_clamp_are_unchanged() {
        assert_eq!(MotionGates::HERO.run, RUN_SPEED);
        assert_eq!(MotionGates::HERO.idle, IDLE_SPEED);
        assert_eq!(MotionSpeeds::HERO.scale_range, SPEED_SCALE_RANGE);
        // Strafing drops the hero to 0.6× without changing the nominal — the
        // wide legacy clamp has to keep allowing it.
        assert_eq!(MotionSpeeds::HERO.scale_for(AnimState::Walk, 4.0 * 0.6), 0.6);
    }

    /// The hysteresis band scales with the gate instead of swallowing a
    /// creature's whole walk range, and the hero keeps exactly its old band.
    #[test]
    fn test_hysteresis_band_scales_with_the_gate() {
        let dipped = RUN_SPEED - RUN_HYSTERESIS * 0.5;
        assert_eq!(
            next_state_with(Some(AnimState::Run), dipped, true, 0.0, MotionGates::HERO),
            AnimState::Run,
            "the hero band is bit-for-bit what it was"
        );
        let mut profile = LocomotionProfile::default();
        profile.observe(0.7);
        profile.observe(2.0);
        let gates = profile.gates();
        assert!(
            gates.run - RUN_HYSTERESIS < profile.walk,
            "a hero-sized band would drop the run gate below this rig's own \
             walk nominal, so it would never come back off the run clip"
        );
        assert_eq!(
            next_state_with(Some(AnimState::Run), 0.7, true, 0.0, gates),
            AnimState::Walk,
            "the scaled band still lets a slime drop back to its walk"
        );
    }

    use bevy::ecs::system::SystemState;

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

    #[test]
    fn test_next_state_keeps_run_through_the_hysteresis_band() {
        // Steering while sprinting dips the speed under the bare threshold;
        // a running character stays running instead of flapping to Walk.
        let dipped = RUN_SPEED - RUN_HYSTERESIS * 0.5;
        assert_eq!(
            next_state(Some(AnimState::Run), dipped, true, 0.0),
            AnimState::Run
        );
        // The same speed does NOT promote a walker to Run.
        assert_eq!(
            next_state(Some(AnimState::Walk), dipped, true, 0.0),
            AnimState::Walk
        );
        // Past the band it does drop to Walk.
        assert_eq!(
            next_state(
                Some(AnimState::Run),
                RUN_SPEED - RUN_HYSTERESIS - 0.1,
                true,
                0.0
            ),
            AnimState::Walk
        );
    }

    #[test]
    fn test_next_state_keeps_walk_through_the_idle_band() {
        let dipped = IDLE_SPEED - IDLE_HYSTERESIS * 0.5;
        assert_eq!(
            next_state(Some(AnimState::Walk), dipped, true, 0.0),
            AnimState::Walk
        );
        assert_eq!(
            next_state(Some(AnimState::Idle), dipped, true, 0.0),
            AnimState::Idle
        );
    }

    #[test]
    fn test_hysteresis_kills_the_flap_at_the_threshold() {
        // A speed oscillating by ±0.4 m/s across RUN_SPEED used to switch clip
        // on every sample; with the band it settles after the first crossing.
        let samples = [5.3f32, 4.9, 5.3, 4.8, 5.1, 4.85, 5.25];
        let mut state = Some(AnimState::Walk);
        let mut switches = 0;
        for s in samples {
            let next = next_state(state, s, true, 0.0);
            if Some(next) != state {
                switches += 1;
            }
            state = Some(next);
        }
        assert_eq!(switches, 1, "only the promotion to Run should happen");
    }

    #[test]
    fn test_motion_speed_scale_tracks_real_speed() {
        let s = MotionSpeeds::HERO;
        // At the nominal walk speed the clip plays at 1×.
        assert!((s.scale_for(AnimState::Walk, 4.0) - 1.0).abs() < 1e-5);
        // Half speed slows the clip (down to the clamp).
        assert!(s.scale_for(AnimState::Walk, 2.0) < 1.0);
        // Non-locomotion states never rescale.
        assert_eq!(s.scale_for(AnimState::Idle, 12.0), 1.0);
        // The clamp keeps playback sane at absurd speeds.
        assert_eq!(s.scale_for(AnimState::Run, 1000.0), SPEED_SCALE_RANGE.1);
    }

    /// Builds an animator with the given clip names, all one second long.
    fn animator(names: &[&str]) -> CharacterAnimator {
        let clip_names: Vec<String> = names.iter().map(|s| s.to_string()).collect();
        let nodes = (0..names.len())
            .map(|i| AnimationNodeIndex::new(i + 1))
            .collect();
        CharacterAnimator {
            durations: vec![1.0; names.len()],
            clip_names,
            nodes,
            player: Entity::PLACEHOLDER,
            state: None,
            current: None,
            action_time: 0.0,
            locked: false,
            air_time: 0.0,
            speed: 0.0,
            last_pos: None,
        }
    }

    #[test]
    fn test_advance_motion_debounces_a_one_frame_ground_flicker() {
        let mut a = animator(&["idle", "walk", "run", "fall"]);
        let dt = 1.0 / 60.0;
        // Walk in a straight line for a while.
        let mut x = 0.0f32;
        let mut state = AnimState::Idle;
        for _ in 0..60 {
            x += 3.0 * dt;
            state = advance_motion(&mut a, Vec3::new(x, 0.0, 0.0), true, 0.0, dt, MotionGates::HERO);
            a.state = Some(state);
        }
        assert_eq!(state, AnimState::Walk);
        // A single frame reporting "not grounded" must not punch a Fall in.
        x += 3.0 * dt;
        let flick = advance_motion(&mut a, Vec3::new(x, 0.0, 0.0), false, -1.0, dt, MotionGates::HERO);
        assert_eq!(flick, AnimState::Walk, "one flicker frame is debounced");
        // Genuinely leaving the ground does switch.
        for _ in 0..12 {
            x += 3.0 * dt;
            state = advance_motion(&mut a, Vec3::new(x, 0.0, 0.0), false, -4.0, dt, MotionGates::HERO);
            a.state = Some(state);
        }
        assert_eq!(state, AnimState::Fall);
    }

    #[test]
    fn test_advance_motion_does_not_spike_after_a_freeze() {
        let mut a = animator(&["idle", "walk", "run"]);
        let dt = 1.0 / 60.0;
        a.last_pos = None; // as left by the activation-radius freeze
        // Coming back 400 m away must not read as a 24 km/h sprint.
        let state = advance_motion(&mut a, Vec3::new(400.0, 0.0, 0.0), true, 0.0, dt, MotionGates::HERO);
        assert_eq!(state, AnimState::Idle);
        assert_eq!(a.speed, 0.0);
    }

    #[test]
    fn test_duration_of_and_busy_gate() {
        let mut a = animator(&["idle", "walk", "attack"]);
        let attack = a.node_matching(|n| n == "attack").expect("attack clip");
        assert_eq!(a.duration_of(attack), 1.0);
        assert!(!a.is_busy());
        a.action_time = 0.5;
        assert!(a.is_busy());
        a.action_time = 0.0;
        a.locked = true;
        assert!(a.is_busy(), "a terminal clip keeps the rig");
    }

    /// Spawns an `AnimationPlayer` entity plus a bound animator, and returns a
    /// world ready to run the playback helpers against.
    fn world_with_animator(names: &[&str]) -> (World, Entity) {
        let mut world = World::new();
        world.insert_resource(Time::<()>::default());
        let player = world
            .spawn((AnimationPlayer::default(), AnimationTransitions::new()))
            .id();
        let mut animator = animator(names);
        animator.player = player;
        let owner = world.spawn(animator).id();
        (world, owner)
    }

    /// Runs `f` with the animator and the player query, the way a driver does.
    fn with_animator<R>(
        world: &mut World,
        owner: Entity,
        f: impl FnOnce(&mut CharacterAnimator, &mut PlayerQuery) -> R,
    ) -> R {
        let mut state: SystemState<(Query<&mut CharacterAnimator>, PlayerQuery)> =
            SystemState::new(world);
        let (mut animators, mut players) = state.get_mut(world).expect("system state");
        let mut animator = animators.get_mut(owner).expect("animator");
        let out = f(&mut animator, &mut players);
        state.apply(world);
        out
    }

    /// Seek time of the clip the animator currently asserts.
    fn current_seek(world: &mut World, owner: Entity) -> f32 {
        let animator = world.get::<CharacterAnimator>(owner).expect("animator");
        let node = animator.current.expect("a clip is playing");
        world
            .get::<AnimationPlayer>(animator.player)
            .expect("player")
            .animation(node)
            .expect("active")
            .seek_time()
    }

    #[test]
    fn test_play_state_does_not_restart_the_clip_it_is_already_playing() {
        // THE regression: `AnimationTransitions::play` replays from zero, so a
        // driver that re-asserts the same state every frame used to pin the rig
        // on frame 0 — "engessada", stopping mid-walk.
        let (mut world, owner) = world_with_animator(&["idle", "walk", "run"]);
        with_animator(&mut world, owner, |a, p| {
            play_state(a, p, AnimState::Walk);
        });
        // Pretend the clip advanced a third of a second.
        let node = world
            .get::<CharacterAnimator>(owner)
            .unwrap()
            .current
            .unwrap();
        let player_entity = world.get::<CharacterAnimator>(owner).unwrap().player;
        world
            .get_mut::<AnimationPlayer>(player_entity)
            .unwrap()
            .animation_mut(node)
            .unwrap()
            .seek_to(0.33);
        // Re-asserting the same state 10× must not touch playback.
        for _ in 0..10 {
            with_animator(&mut world, owner, |a, p| {
                play_state(a, p, AnimState::Walk);
            });
        }
        assert!(
            (current_seek(&mut world, owner) - 0.33).abs() < 1e-6,
            "the walk clip was replayed"
        );
    }

    #[test]
    fn test_play_state_does_not_restart_when_two_states_share_a_clip() {
        // A rig with no `run` resolves Run through the fallback onto `walk`;
        // flipping Walk↔Run must not restart that single clip.
        let (mut world, owner) = world_with_animator(&["idle", "walk"]);
        with_animator(&mut world, owner, |a, p| {
            play_state(a, p, AnimState::Walk);
        });
        let node = world
            .get::<CharacterAnimator>(owner)
            .unwrap()
            .current
            .unwrap();
        let player_entity = world.get::<CharacterAnimator>(owner).unwrap().player;
        world
            .get_mut::<AnimationPlayer>(player_entity)
            .unwrap()
            .animation_mut(node)
            .unwrap()
            .seek_to(0.5);
        with_animator(&mut world, owner, |a, p| {
            play_state(a, p, AnimState::Run);
        });
        assert!((current_seek(&mut world, owner) - 0.5).abs() < 1e-6);
        assert_eq!(
            world.get::<CharacterAnimator>(owner).unwrap().state,
            Some(AnimState::Run),
            "the gameplay state still tracks Run"
        );
    }

    #[test]
    fn test_action_owns_the_rig_then_hands_it_back() {
        let (mut world, owner) = world_with_animator(&["idle", "walk", "attack"]);
        with_animator(&mut world, owner, |a, p| {
            play_state(a, p, AnimState::Walk);
        });
        let attack = world
            .get::<CharacterAnimator>(owner)
            .unwrap()
            .node_matching(|n| n == "attack")
            .unwrap();
        with_animator(&mut world, owner, |a, p| {
            assert!(play_action(a, p, attack, ACTION_BLEND, false));
        });
        // While the swing runs the motion driver cannot steal the clip back.
        with_animator(&mut world, owner, |a, p| {
            play_state(a, p, AnimState::Walk);
        });
        assert_eq!(
            world.get::<CharacterAnimator>(owner).unwrap().current,
            Some(attack)
        );
        // The clip is one second long and plays exactly once.
        let player_entity = world.get::<CharacterAnimator>(owner).unwrap().player;
        assert_eq!(
            world
                .get::<AnimationPlayer>(player_entity)
                .unwrap()
                .animation(attack)
                .unwrap()
                .repeat_mode(),
            RepeatAnimation::Never
        );
        // Tick past the clip's duration: the rig comes back to the driver.
        world
            .get_mut::<CharacterAnimator>(owner)
            .unwrap()
            .action_time = 0.0;
        world.get_mut::<CharacterAnimator>(owner).unwrap().current = None;
        with_animator(&mut world, owner, |a, p| {
            play_state(a, p, AnimState::Walk);
        });
        let walk = world
            .get::<CharacterAnimator>(owner)
            .unwrap()
            .node_for(AnimState::Walk)
            .unwrap();
        assert_eq!(
            world.get::<CharacterAnimator>(owner).unwrap().current,
            Some(walk)
        );
    }

    #[test]
    fn test_terminal_action_holds_the_pose_forever() {
        let (mut world, owner) = world_with_animator(&["idle", "walk", "death"]);
        let death = world
            .get::<CharacterAnimator>(owner)
            .unwrap()
            .node_matching(|n| n == "death")
            .unwrap();
        with_animator(&mut world, owner, |a, p| {
            assert!(play_action(a, p, death, ACTION_BLEND, true));
        });
        // No driver and no second death can move it.
        with_animator(&mut world, owner, |a, p| {
            play_state(a, p, AnimState::Run);
            assert!(!play_action(a, p, death, ACTION_BLEND, true));
        });
        assert_eq!(
            world.get::<CharacterAnimator>(owner).unwrap().current,
            Some(death)
        );
        assert!(world.get::<CharacterAnimator>(owner).unwrap().locked);
    }

    #[test]
    fn test_tick_animator_actions_releases_only_finished_actions() {
        let mut world = World::new();
        let mut time = Time::<()>::default();
        time.advance_by(Duration::from_millis(100));
        world.insert_resource(time);
        let mut running = animator(&["idle", "attack"]);
        running.action_time = 0.3;
        running.current = Some(AnimationNodeIndex::new(2));
        let running = world.spawn(running).id();
        let mut ending = animator(&["idle", "attack"]);
        ending.action_time = 0.05;
        ending.current = Some(AnimationNodeIndex::new(2));
        let ending = world.spawn(ending).id();
        let mut dead = animator(&["idle", "death"]);
        dead.locked = true;
        dead.current = Some(AnimationNodeIndex::new(2));
        let dead = world.spawn(dead).id();

        let mut state: SystemState<(Res<Time>, Query<&mut CharacterAnimator>)> =
            SystemState::new(&mut world);
        let (time, animators) = state.get_mut(&mut world).expect("system state");
        tick_animator_actions(time, animators);

        assert!(world.get::<CharacterAnimator>(running).unwrap().action_time > 0.0);
        assert!(
            world
                .get::<CharacterAnimator>(running)
                .unwrap()
                .current
                .is_some()
        );
        assert_eq!(
            world.get::<CharacterAnimator>(ending).unwrap().action_time,
            0.0
        );
        assert!(
            world
                .get::<CharacterAnimator>(ending)
                .unwrap()
                .current
                .is_none(),
            "a finished action releases the clip so the driver re-asserts it"
        );
        assert!(world.get::<CharacterAnimator>(dead).unwrap().locked);
        assert!(
            world
                .get::<CharacterAnimator>(dead)
                .unwrap()
                .current
                .is_some()
        );
    }

    #[test]
    fn test_node_matching_uses_normalized_names() {
        let a = animator(&["Animator3D_Death", "Animator3D_Walk"]);
        assert_eq!(
            a.node_matching(|n| n == "death"),
            Some(AnimationNodeIndex::new(1))
        );
        assert_eq!(a.node_matching(|n| n == "attack"), None);
    }

    // ------------------------------------------------------------ hit-react

    #[test]
    fn test_hit_react_guard_cooldown_and_busy() {
        assert!(hit_react_allowed(false, false), "livre: flincha");
        assert!(
            !hit_react_allowed(true, false),
            "cooldown aberto: sem flinch"
        );
        assert!(
            !hit_react_allowed(false, true),
            "rig ocupado (swing/death): sem flinch"
        );
        assert!(!hit_react_allowed(true, true));
    }

    #[test]
    fn test_hit_react_node_fallback_chain() {
        // Herói: `hit` ganha ao `hithead` (ordem da cadeia).
        let hero = animator(&["idle", "hit", "hithead"]);
        assert_eq!(hit_react_node(&hero), Some(AnimationNodeIndex::new(2)));
        // Rig retargetado: `Animator3D_Hit` normaliza para `hit`.
        let wolf = animator(&["Animator3D_BreatheIdle", "Animator3D_Hit"]);
        assert_eq!(hit_react_node(&wolf), Some(AnimationNodeIndex::new(2)));
        // Sem `hit`, o `hithead` entra na cadeia.
        let headed = animator(&["idle", "hithead"]);
        assert_eq!(hit_react_node(&headed), Some(AnimationNodeIndex::new(2)));
        // Rig sem clip de reação: None (no-op silencioso).
        assert_eq!(hit_react_node(&animator(&["idle", "walk"])), None);
    }

    #[test]
    fn test_hit_react_cooldown_expires_and_replays() {
        // 0.4 s de cooldown: a 0.3 s ainda bloqueia, a 0.41 s deixa.
        let mut cooldown = HitReactCooldown {
            timer: HIT_REACT_COOLDOWN,
        };
        let dt = 1.0 / 60.0;
        for _ in 0..18 {
            cooldown.timer = (cooldown.timer - dt).max(0.0);
        }
        assert!(!hit_react_allowed(cooldown.timer > 0.0, false));
        while cooldown.timer > 0.0 {
            cooldown.timer = (cooldown.timer - dt).max(0.0);
        }
        assert!(hit_react_allowed(cooldown.timer > 0.0, false));
    }

    // -------------------------------------------------------------- landing

    #[test]
    fn test_is_landing_threshold_and_flicker() {
        let mut a = animator(&["idle", "jumpland"]);
        let dt = 1.0 / 60.0;
        // 18 frames no ar ≈ 0.3 s de voo.
        for _ in 0..18 {
            advance_motion(&mut a, Vec3::ZERO, false, -3.0, dt, MotionGates::HERO);
        }
        let was_airborne = a.air_time;
        assert!(
            was_airborne > LANDING_AIR_TIME,
            "voo acumulado {was_airborne}"
        );
        advance_motion(&mut a, Vec3::ZERO, true, 0.0, dt, MotionGates::HERO);
        assert!(is_landing(was_airborne, true), "aterragem detetada");
        assert_eq!(a.air_time, 0.0, "air_time zerou no frame grounded");
        // Flicker de 1 frame (0.017 s) NÃO é aterragem.
        let mut b = animator(&["idle", "jumpland"]);
        advance_motion(&mut b, Vec3::ZERO, false, -1.0, dt, MotionGates::HERO);
        let flick = b.air_time;
        advance_motion(&mut b, Vec3::ZERO, true, 0.0, dt, MotionGates::HERO);
        assert!(!is_landing(flick, true), "flicker debounced");
        // Ainda no ar: nunca é aterragem.
        assert!(!is_landing(0.3, false));
        // Exatamente no limiar (0.25 s) ainda não conta ("> 0.25").
        assert!(!is_landing(LANDING_AIR_TIME, true));
    }

    // ---------------------------------------------------- scene-swap rebind

    /// Minimal app: asset stores, the world-instance spawner, the render-LOD
    /// ladder and the re-arm/bind pair. No motion drivers — the test wants the
    /// animator's state exactly as it left it.
    fn swap_test_app() -> App {
        let mut app = App::new();
        app.add_plugins((
            bevy::MinimalPlugins,
            bevy::asset::AssetPlugin::default(),
            bevy::world_serialization::WorldSerializationPlugin,
            bevy::animation::AnimationPlugin,
            crate::render_lod::RenderLodPlugin,
        ));
        app.init_asset::<bevy::gltf::Gltf>();
        app.register_type::<Children>()
            .register_type::<ChildOf>()
            .register_type::<AnimationPlayer>();
        app.add_systems(
            bevy::app::Update,
            (rearm_after_scene_swap, bind_animations).chain(),
        );
        // the camera the LOD ladder measures distance against
        app.world_mut()
            .spawn((Camera3d::default(), GlobalTransform::default()));
        app
    }

    /// A synthetic gltf asset: `names` clips plus a default scene whose root
    /// entity carries the `AnimationPlayer` the gltf loader would place.
    fn add_fake_gltf(
        app: &mut App,
        names: &[&str],
    ) -> (
        Handle<bevy::gltf::Gltf>,
        Handle<bevy::world_serialization::WorldAsset>,
    ) {
        let mut animations = Vec::new();
        let mut named_animations =
            <bevy::platform::collections::HashMap<Box<str>, Handle<AnimationClip>>>::default();
        for name in names {
            let clip = app
                .world_mut()
                .resource_mut::<Assets<AnimationClip>>()
                .add(AnimationClip::default());
            named_animations.insert(Box::<str>::from(*name), clip.clone());
            animations.push(clip);
        }
        let mut scene_world = World::new();
        scene_world.spawn(AnimationPlayer::default());
        let scene = app
            .world_mut()
            .resource_mut::<Assets<bevy::world_serialization::WorldAsset>>()
            .add(bevy::world_serialization::WorldAsset { world: scene_world });
        let gltf = bevy::gltf::Gltf {
            scenes: vec![scene.clone()],
            named_scenes: Default::default(),
            meshes: Vec::new(),
            named_meshes: Default::default(),
            materials: Vec::new(),
            named_materials: Default::default(),
            nodes: Vec::new(),
            named_nodes: Default::default(),
            skins: Vec::new(),
            named_skins: Default::default(),
            default_scene: Some(scene.clone()),
            animations,
            named_animations,
            source: None,
        };
        let handle = app
            .world_mut()
            .resource_mut::<Assets<bevy::gltf::Gltf>>()
            .add(gltf);
        (handle, scene)
    }

    #[test]
    fn test_lod_swap_rebinds_and_keeps_the_locomotion_state() {
        let mut app = swap_test_app();
        let (gltf0, scene0) = add_fake_gltf(&mut app, &["idle", "walk"]);
        let (gltf1, scene1) = add_fake_gltf(&mut app, &["idle", "walk"]);

        let instance = app
            .world_mut()
            .spawn((
                GlobalTransform::from_translation(Vec3::X * 10.0),
                Visibility::Inherited,
                bevy::world_serialization::WorldAssetRoot(scene0.clone()),
                AnimatedScene {
                    gltf: gltf0.clone(),
                },
                crate::render_lod::MeshLod {
                    tiers: vec![scene0, scene1],
                    gltf_tiers: vec![gltf0, gltf1],
                    near: 18.0,
                    mid: 55.0,
                    current: 0,
                    no_shadows: false,
                },
            ))
            .id();

        for _ in 0..6 {
            app.update();
        }
        let first = {
            let animator = app
                .world()
                .get::<CharacterAnimator>(instance)
                .expect("bound on first spawn");
            assert_eq!(animator.state, Some(AnimState::Idle));
            animator.player
        };
        assert!(
            app.world().get::<BoundAnimation>(first).is_some(),
            "the first player is marked as bound"
        );
        assert!(app.world().get::<AnimationGraphHandle>(first).is_some());

        // Mid-chase: the driver's last state is Walk when the ladder swaps.
        app.world_mut()
            .get_mut::<CharacterAnimator>(instance)
            .unwrap()
            .state = Some(AnimState::Walk);

        // Cross the `mid` threshold (camera sits at the origin).
        app.world_mut()
            .entity_mut(instance)
            .insert(GlobalTransform::from_translation(Vec3::X * 60.0));
        for _ in 0..8 {
            app.update();
        }

        let animator = app
            .world()
            .get::<CharacterAnimator>(instance)
            .expect("re-bound after the swap");
        assert_ne!(
            animator.player, first,
            "the animator points at the fresh tier's player"
        );
        assert!(
            app.world().get_entity(first).is_err(),
            "the swapped-out subtree — old player included — despawned"
        );
        assert!(app.world().get::<BoundAnimation>(animator.player).is_some());
        assert!(
            app.world()
                .get::<AnimationGraphHandle>(animator.player)
                .is_some(),
            "the fresh player carries a graph"
        );
        assert_eq!(
            animator.state,
            Some(AnimState::Walk),
            "the locomotion state survived the swap"
        );
        assert_eq!(
            animator.current,
            animator.node_for(AnimState::Walk),
            "the walk clip is what resumes on the new tier"
        );
    }

    #[test]
    fn test_find_animation_player_skips_bound_players() {
        let mut world = World::new();
        let bound = world
            .spawn((AnimationPlayer::default(), BoundAnimation))
            .id();
        let fresh = world.spawn(AnimationPlayer::default()).id();
        let root = world.spawn_empty().id();
        world.entity_mut(root).add_child(bound);
        world.entity_mut(bound).add_child(fresh);

        let mut state: SystemState<(
            Query<&Children>,
            Query<(), (With<AnimationPlayer>, Without<BoundAnimation>)>,
        )> = SystemState::new(&mut world);
        {
            let (children, players) = state.get(&mut world).expect("system state");
            assert_eq!(
                find_animation_player(root, &children, &players),
                Some(fresh),
                "the fresh player is found below the dying, already-bound one"
            );
        }
        world.entity_mut(fresh).insert(BoundAnimation);
        {
            let (children, players) = state.get(&mut world).expect("system state");
            assert_eq!(
                find_animation_player(root, &children, &players),
                None,
                "every player already carries a graph — nothing to bind into"
            );
        }
    }

    #[test]
    fn test_rearm_after_scene_swap_targets_only_characters() {
        let mut app = App::new();
        app.add_plugins(bevy::asset::AssetPlugin::default());
        app.init_asset::<AnimationClip>()
            .init_asset::<bevy::gltf::Gltf>()
            .init_asset::<bevy::world_serialization::WorldAsset>();
        app.add_systems(Update, rearm_after_scene_swap);
        let (g0, _) = add_fake_gltf(&mut app, &["idle"]);
        let (g1, _) = add_fake_gltf(&mut app, &["idle"]);

        let ladder = |current: u8| crate::render_lod::MeshLod {
            tiers: vec![Handle::default(), Handle::default()],
            gltf_tiers: vec![g0.clone(), g1.clone()],
            near: 18.0,
            mid: 55.0,
            current,
            no_shadows: false,
        };
        fn walk_animator() -> CharacterAnimator {
            CharacterAnimator {
                clip_names: vec!["idle".into()],
                nodes: vec![AnimationNodeIndex::new(1)],
                durations: vec![1.0],
                player: Entity::PLACEHOLDER,
                state: Some(AnimState::Walk),
                current: None,
                action_time: 0.0,
                locked: false,
                air_time: 0.0,
                speed: 0.0,
                last_pos: None,
            }
        }

        // A bound character (AnimatedScene consumed by the last bind).
        let bound = app
            .world_mut()
            .spawn((
                bevy::world_serialization::WorldAssetRoot(Handle::default()),
                ladder(0),
                walk_animator(),
            ))
            .id();
        // A bind-pending character whose arm predates the swap.
        let pending = app
            .world_mut()
            .spawn((
                bevy::world_serialization::WorldAssetRoot(Handle::default()),
                ladder(0),
                AnimatedScene { gltf: g0.clone() },
            ))
            .id();
        // A corpse: a terminal clip owns the rig for good.
        let corpse = app
            .world_mut()
            .spawn((
                bevy::world_serialization::WorldAssetRoot(Handle::default()),
                ladder(0),
                CharacterAnimator {
                    locked: true,
                    ..walk_animator()
                },
            ))
            .id();
        // A plain prop — no animation components.
        let prop = app
            .world_mut()
            .spawn((
                bevy::world_serialization::WorldAssetRoot(Handle::default()),
                ladder(0),
            ))
            .id();

        // The insertion itself is a change; the ladder still sits on tier 0,
        // so the re-arm lands on tier 0's gltf and nothing else moves.
        app.update();
        assert_eq!(
            app.world().get::<AnimatedScene>(bound).map(|a| a.gltf.id()),
            Some(g0.id()),
            "a bound character without an arm re-arms against the resident tier"
        );
        assert!(
            app.world().get::<CharacterAnimator>(bound).is_some(),
            "the animator stays until bind re-binds it in place"
        );
        assert!(
            app.world().get::<AnimatedScene>(corpse).is_none(),
            "locked animators are never re-armed"
        );
        assert!(
            app.world().get::<AnimatedScene>(prop).is_none(),
            "props are not characters"
        );

        // Swap every entity to tier 1: bump `current` like `update_mesh_lod`
        // does and touch the root so change detection fires.
        for entity in [bound, pending, corpse, prop] {
            app.world_mut()
                .get_mut::<crate::render_lod::MeshLod>(entity)
                .unwrap()
                .current = 1;
            app.world_mut()
                .entity_mut(entity)
                .insert(bevy::world_serialization::WorldAssetRoot(Handle::default()));
        }
        app.update();

        assert_eq!(
            app.world().get::<AnimatedScene>(bound).map(|a| a.gltf.id()),
            Some(g1.id()),
            "re-armed with the tier now resident"
        );
        assert_eq!(
            app.world()
                .get::<AnimatedScene>(pending)
                .map(|a| a.gltf.id()),
            Some(g1.id()),
            "a stale arm follows the swap"
        );
        assert!(
            app.world().get::<AnimatedScene>(corpse).is_none(),
            "the corpse is still not re-armed"
        );
        assert!(
            app.world().get::<AnimatedScene>(prop).is_none(),
            "the prop is still not a character"
        );
    }
}
