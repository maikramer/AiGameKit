import { logger } from '../core/utils/logger';
import { mirrorAnimationClip } from './anim-mirror';
/**
 * Runtime animation controller for GLTF models with embedded clips.
 * Wraps Three.js AnimationMixer with crossfade and state management.
 *
 * Usage:
 *   const gltf = await loader.loadAsync(url);
 *   const animator = new GltfAnimator(gltf);
 *   animator.play('Animator3D_BreatheIdle');
 *   // in render loop: animator.update(deltaTime);
 */
import {
  AdditiveAnimationBlendMode,
  AnimationAction,
  AnimationClip,
  AnimationMixer,
  AnimationUtils,
  LoopOnce,
  LoopRepeat,
  type Object3D,
} from 'three';
import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';

export interface GltfAnimatorOptions {
  /** Default crossfade duration in seconds. */
  crossfadeDuration?: number;
  /**
   * Mixer root. Defaults to ``gltf.scene``. Use a LOD clone (or other
   * instance) so clips drive the visible mesh, not the cached master.
   */
  root?: Object3D;
}

/** Named set of locomotion clip names for state-based animation switching. */
export interface LocomotionSet {
  idle: string;
  walk: string;
  run: string;
  jump?: string | { start: string; loop: string; end: string };
  walkBack?: string;
  leftWalk?: string;
  rightWalk?: string;
  /** Turn-in-place clips, played when the heading changes without translating. */
  turnLeft?: string;
  turnRight?: string;
}

export function matchClipKeyword(clipNames: string[], keyword: string): string {
  if (!keyword || clipNames.length === 0) return '';
  const want = keyword.toLowerCase();
  const lower = clipNames.map((n) => n.toLowerCase());

  const exactIdx = lower.findIndex((n) => n === want);
  if (exactIdx >= 0) return clipNames[exactIdx]!;

  const suffixes = [`_${want}`, `-${want}`, `.${want}`];
  for (let i = 0; i < lower.length; i++) {
    const n = lower[i]!;
    if (suffixes.some((s) => n.endsWith(s))) return clipNames[i]!;
  }

  const prefixes = [`${want}_`, `${want}-`, `${want}.`];
  for (let i = 0; i < lower.length; i++) {
    const n = lower[i]!;
    if (prefixes.some((p) => n.startsWith(p))) return clipNames[i]!;
  }

  let bestIdx = -1;
  let bestLen = Infinity;
  for (let i = 0; i < lower.length; i++) {
    const n = lower[i]!;
    if (n.includes(want) && n.length < bestLen) {
      bestLen = n.length;
      bestIdx = i;
    }
  }
  return bestIdx >= 0 ? clipNames[bestIdx]! : '';
}

export class GltfAnimator {
  readonly mixer: AnimationMixer;
  readonly clips: Map<string, AnimationClip> = new Map();

  private currentAction: AnimationAction | null = null;
  private currentClipName = '';
  private crossfadeDuration: number;
  // False whenever the mixer still owes an evaluation for the current action
  // (fresh play, or never ticked) — the hook that lets distance-culled rigs
  // park on the clip's first frame instead of bind pose.
  private posedOnce = false;

  private locomotionSets = new Map<string, LocomotionSet>();
  private activeLocomotionSetName = 'default';
  private _overrideLock = false;
  private previousLocomotionClip = '';
  private _jumpState: 'none' | 'start' | 'loop' | 'end' = 'none';

  // Cached clip-name arrays. `clips` is populated once in the ctor and never
  // mutated thereafter, so the cache is filled lazily on first read and reused.
  private _clipNamesCache: string[] | null = null;
  private _clipNamesLowerCache: string[] | null = null;

  // Tracks the active playOverride so its 'finished' listener can be removed
  // before registering a new one (and on dispose). Without this the listener
  // leaks across successive overrides, pinning closures and stale actions.
  private _activeOverride: {
    action: AnimationAction;
    handler: (e: { action?: unknown }) => void;
  } | null = null;

  // Additive overlay (e.g. turn-lean blended on top of locomotion).
  private additiveClips = new Map<string, AnimationClip>();
  private additiveAction: AnimationAction | null = null;
  private additiveClipName = '';
  private additiveWeight = 0;
  private additiveTarget = 0;

  // Flinch overlay: a second, independent additive layer with its own
  // attack/release envelope (see `playFlinch`). Kept apart from the lean
  // overlay above so a creature can lean into a turn *and* flinch at once.
  private flinchAction: AnimationAction | null = null;
  private flinchClipName = '';
  private flinch: {
    peak: number;
    attack: number;
    release: number;
    elapsed: number;
  } | null = null;

  /** Clip names already warned as missing (avoid per-frame spam). */
  private missingClipWarned = new Set<string>();

  constructor(gltf: GLTF, options: GltfAnimatorOptions = {}) {
    this.mixer = new AnimationMixer(options.root ?? gltf.scene);
    this.crossfadeDuration = options.crossfadeDuration ?? 0.25;

    for (const clip of gltf.animations) {
      this.clips.set(clip.name, clip);
    }
  }

  /**
   * Ensure a left-right mirrored clip exists ("``<base>_m``" convention — see
   * ``anim-mirror.ts``). Mirrored clips are built on demand from the base clip
   * (bone L/R swap + sagittal reflection), so any swing can alternate hands
   * without shipping extra source clips. Registering invalidates the cached
   * clip-name arrays. No-op (true) when the clip already exists; false when
   * the name has no ``_m`` suffix or the base clip is missing.
   */
  ensureMirroredClip(name: string): boolean {
    if (this.clips.has(name)) return true;
    const suffix = '_m';
    if (!name.endsWith(suffix)) return false;
    const baseClip = this.clips.get(name.slice(0, -suffix.length));
    if (!baseClip) return false;
    this.clips.set(name, mirrorAnimationClip(baseClip, name));
    // Mirrors are exact only for rigs with symmetric rest poses; retargeted
    // rigs (mixed bone conventions) can distort — make on-demand builds
    // visible in the console so a twisted pose is traceable to its clip.
    console.info(
      `[GltfAnimator] built mirrored clip "${name}" on demand (retarget rigs may distort)`
    );
    this._clipNamesCache = null;
    this._clipNamesLowerCache = null;
    return true;
  }

  get root(): Object3D {
    return this.mixer.getRoot() as Object3D;
  }

  get clipNames(): string[] {
    if (this._clipNamesCache === null) {
      this._clipNamesCache = Array.from(this.clips.keys());
    }
    return this._clipNamesCache;
  }

  /** Lowercased clip names (cached); used by player-side fuzzy matching. */
  get clipNamesLower(): string[] {
    if (this._clipNamesLowerCache === null) {
      this._clipNamesLowerCache = this.clipNames.map((n) => n.toLowerCase());
    }
    return this._clipNamesLowerCache;
  }

  get activeClipName(): string {
    return this.currentClipName;
  }

  /** Playback time of the current clip in seconds (0 if none). */
  get currentTime(): number {
    return this.currentAction?.time ?? 0;
  }

  /** Duration of the current clip in seconds (0 if none). */
  get currentClipDuration(): number {
    return this.currentAction?.getClip().duration ?? 0;
  }

  /** Current clip position normalized to 0..1 (0 if none). */
  get currentNormalizedTime(): number {
    const d = this.currentClipDuration;
    if (d <= 0 || !this.currentAction) return 0;
    return (this.currentAction.time % d) / d;
  }

  /**
   * Play a clip by name with optional crossfade from the current clip.
   *
   * `phaseSync` (locomotion cuts): start the incoming clip at the same
   * normalized phase as the outgoing one (`time = phase × nextDuration`) so
   * cyclic gaits blend footfall-to-footfall instead of mid-stride — this is
   * what removes the foot slide/pop on idle↔walk↔run cuts. Only meaningful
   * when both clips loop.
   */
  play(
    clipName: string,
    options?: {
      crossfade?: number;
      loop?: boolean;
      phaseSync?: boolean;
    }
  ): AnimationAction | null {
    if (!clipName) return null;

    const resolved = this.resolveClipName(clipName);
    if (!resolved) {
      // Empty Available usually means animator built on a master without
      // animations (rigged-only / failed load) — warn once per instance.
      if (!this.missingClipWarned.has(clipName)) {
        this.missingClipWarned.add(clipName);
        logger.warn(
          `[GltfAnimator] Clip "${clipName}" not found. Available: ${this.clipNames.join(', ') || '(none)'}`
        );
      }
      return null;
    }
    clipName = resolved;

    if (clipName === this.currentClipName && this.currentAction?.isRunning()) {
      return this.currentAction;
    }

    const clip = this.clips.get(clipName);
    if (!clip) {
      logger.warn(
        `[GltfAnimator] Clip "${clipName}" not found. Available: ${this.clipNames.join(', ')}`
      );
      return null;
    }

    const nextAction = this.mixer.clipAction(clip);
    const fade = options?.crossfade ?? this.crossfadeDuration;

    if (options?.loop === false) {
      nextAction.setLoop(LoopOnce, 1);
      nextAction.clampWhenFinished = true;
    } else {
      // Reset sticky LoopOnce from a prior one-shot (lunge/hit/death) on this
      // same clip — otherwise locomotion freezes on the last keyframe / bind.
      nextAction.setLoop(LoopRepeat, Infinity);
      nextAction.clampWhenFinished = false;
    }

    if (this.currentAction && fade > 0) {
      nextAction.reset().setEffectiveTimeScale(1).setEffectiveWeight(1);
      if (options?.phaseSync) {
        const prev = this.currentAction.getClip().duration;
        const nextDur = clip.duration;
        if (prev > 0 && nextDur > 0) {
          const phase = (this.currentAction.time % prev) / prev;
          nextAction.time = phase * nextDur;
        }
      }
      this.currentAction.crossFadeTo(nextAction, fade, true);
      nextAction.play();
    } else {
      nextAction.reset().setEffectiveTimeScale(1).setEffectiveWeight(1).play();
    }

    this.currentAction = nextAction;
    this.currentClipName = clipName;
    this.posedOnce = false;
    return nextAction;
  }

  /**
   * Evaluate the mixer once without advancing time — parks the rig on the
   * current clip's first frame so distance-frozen entities never sit in bind
   * pose (A-pose). Safe to call every frame: no-op once the pose is applied,
   * and while nothing is playing yet (retries on the next call).
   */
  poseFrozenFrame(): void {
    if (this.posedOnce || !this.currentAction) return;
    this.mixer.update(0);
    this.posedOnce = true;
  }

  /**
   * Resolve a logical clip name (`idle`/`walk`/…) to an actual GLB clip.
   * Exact / separator / shortest-substring first, then locomotion aliases.
   */
  resolveClipName(requested: string): string {
    if (this.clips.has(requested)) return requested;
    // Mirrored variant ("<clip>_m") missing? Build it on demand from the base
    // clip before fuzzy-matching — the mirror IS the clip, not a near-name.
    if (requested.endsWith('_m') && this.ensureMirroredClip(requested)) {
      return requested;
    }
    const matched = matchClipKeyword(this.clipNames, requested);
    if (matched) return matched;

    const want = requested.toLowerCase();
    const names = this.clipNames;
    const lower = this.clipNamesLower;

    const aliases: Record<string, string[]> = {
      idle: ['breathe', 'breath', 'stand', 'rest', 'pose', 'wait', 'hover'],
      walk: [
        'locomotion',
        'stride',
        'jog',
        'walking',
        'move',
        'hover',
        'soar',
        'fly',
      ],
      run: ['sprint', 'running', 'fast', 'soar', 'dive', 'fly'],
      jump: ['leap', 'hop', 'vault', 'jumping', 'dive', 'lunge', 'pounce'],
      death: ['die', 'dead', 'defeat', 'fall', 'land'],
      hit: ['hurt', 'damage', 'react', 'flinch', 'attack', 'dive'],
      attack: ['slash', 'strike', 'swing', 'punch', 'dive'],
      roar: ['growl', 'scream', 'shout'],
      lunge: ['pounce', 'leap', 'jump', 'dive'],
    };
    for (const alt of aliases[want] ?? []) {
      const idx = lower.findIndex((n) => n.includes(alt));
      if (idx >= 0) return names[idx]!;
    }
    return '';
  }

  /**
   * Blend an additive overlay clip (e.g. a turn-lean) on top of whatever the
   * base locomotion is playing. `intensity` in [0,1]; pass an empty clip name
   * (or 0) to fade the overlay out. The weight is smoothed in {@link update}.
   */
  setAdditive(clipName: string, intensity: number): void {
    const target = Math.max(0, Math.min(1, intensity));
    if (!clipName || target <= 0) {
      this.additiveTarget = 0;
      return;
    }
    if (clipName !== this.additiveClipName) {
      const base = this.clips.get(clipName);
      if (!base) {
        this.additiveTarget = 0;
        return;
      }
      if (this.additiveAction) this.additiveAction.stop();
      let additive = this.additiveClips.get(clipName);
      if (!additive) {
        additive = AnimationUtils.makeClipAdditive(base.clone());
        this.additiveClips.set(clipName, additive);
      }
      this.additiveAction = this.mixer.clipAction(
        additive,
        undefined,
        AdditiveAnimationBlendMode
      );
      this.additiveAction.play();
      this.additiveClipName = clipName;
    } else if (this.additiveAction && this.additiveWeight <= 0.001) {
      // Re-engaging the same overlay after it faded out: restart the lean-in.
      this.additiveAction.reset().play();
    }
    this.additiveTarget = target;
  }

  /**
   * Default envelope for {@link playFlinch}. A reaction has to register inside
   * a couple of frames or the blow it answers is already over, so the ramp-in
   * is near-instant and the release carries the recovery.
   */
  static readonly DEFAULT_FLINCH_PEAK = 0.7;
  static readonly DEFAULT_FLINCH_ATTACK = 0.05;
  static readonly DEFAULT_FLINCH_RELEASE = 0.28;

  /**
   * Punch an additive reaction clip on top of whatever is playing, then let it
   * decay — the smooth alternative to swapping the whole body onto a `hit`
   * clip.
   *
   * A full-clip hit reaction has to interrupt the base pose, which means a
   * creature mid-swing snaps out of its attack (and a boss with poise gets no
   * visible reaction at all, because interrupting it is not an option). An
   * additive layer adds the *difference* between the reaction and the rig's
   * rest pose, so the run keeps running, the swing keeps swinging, and the
   * torso still recoils. Nothing here touches {@link play} or the override
   * lock, so a flinch can land in the middle of an attack override.
   *
   * Re-triggering while one is live restarts the envelope and keeps the higher
   * peak, so a flurry of blows reads as sustained recoil instead of a stutter.
   */
  playFlinch(
    clipName: string,
    options?: {
      /** Peak additive weight, 0..1 (default 0.7). */
      weight?: number;
      /** Ramp-in seconds (default 0.05). */
      attack?: number;
      /** Fade-out seconds after the peak (default 0.28). */
      release?: number;
      /** Playback rate of the reaction clip (default 1). */
      timeScale?: number;
    }
  ): boolean {
    const resolved = this.resolveClipName(clipName);
    const base = resolved ? this.clips.get(resolved) : undefined;
    if (!base) return false;

    const peak = Math.max(
      0,
      Math.min(1, options?.weight ?? GltfAnimator.DEFAULT_FLINCH_PEAK)
    );
    if (peak <= 0) return false;

    if (resolved !== this.flinchClipName || !this.flinchAction) {
      if (this.flinchAction) this.flinchAction.stop();
      let additive = this.additiveClips.get(resolved);
      if (!additive) {
        additive = AnimationUtils.makeClipAdditive(base.clone());
        this.additiveClips.set(resolved, additive);
      }
      this.flinchAction = this.mixer.clipAction(
        additive,
        undefined,
        AdditiveAnimationBlendMode
      );
      this.flinchClipName = resolved;
    }

    const action = this.flinchAction;
    action.setLoop(LoopOnce, 1);
    action.clampWhenFinished = true;
    action.reset();
    action.setEffectiveTimeScale(options?.timeScale ?? 1);
    action.setEffectiveWeight(0);
    action.play();

    const carried = this.flinch ? Math.max(peak, this.flinchWeightNow()) : peak;
    this.flinch = {
      peak: carried,
      attack: Math.max(
        0,
        options?.attack ?? GltfAnimator.DEFAULT_FLINCH_ATTACK
      ),
      release: Math.max(
        0.001,
        options?.release ?? GltfAnimator.DEFAULT_FLINCH_RELEASE
      ),
      elapsed: 0,
    };
    return true;
  }

  /** Weight the flinch envelope is at right now (0 when none is live). */
  flinchWeightNow(): number {
    const f = this.flinch;
    if (!f) return 0;
    if (f.elapsed < f.attack) {
      return f.attack > 0 ? f.peak * (f.elapsed / f.attack) : f.peak;
    }
    const decayed = 1 - (f.elapsed - f.attack) / f.release;
    return decayed > 0 ? f.peak * decayed : 0;
  }

  /** True while a flinch overlay is still contributing to the pose. */
  get flinching(): boolean {
    return this.flinch !== null;
  }

  /** Drop the flinch overlay immediately (death, teardown, scene swap). */
  clearFlinch(): void {
    if (this.flinchAction) {
      this.flinchAction.stop();
      this.flinchAction.setEffectiveWeight(0);
    }
    this.flinch = null;
  }

  private updateFlinch(deltaTime: number): void {
    const f = this.flinch;
    if (!f || !this.flinchAction) return;
    f.elapsed += deltaTime;
    const weight = this.flinchWeightNow();
    if (weight <= 0) {
      this.clearFlinch();
      return;
    }
    this.flinchAction.setEffectiveWeight(weight);
  }

  /** Current smoothed weight of the additive overlay (0 when none). */
  get additiveOverlayWeight(): number {
    return this.additiveWeight;
  }

  /** Tick the mixer. Call every frame with delta time in seconds. */
  update(deltaTime: number): void {
    this.posedOnce = true;
    this.updateFlinch(deltaTime);
    if (this.additiveAction) {
      // Smoothly ramp the overlay weight toward its target (~6x/sec).
      const k = Math.min(1, deltaTime * 6);
      this.additiveWeight += (this.additiveTarget - this.additiveWeight) * k;
      if (this.additiveWeight < 0.001 && this.additiveTarget === 0) {
        this.additiveWeight = 0;
      }
      this.additiveAction.setEffectiveWeight(this.additiveWeight);

      // While the overlay is held (target > 0), freeze the clip at its peak
      // lean instead of letting it loop/finish — releasing the key fades the
      // weight out, which returns the pose smoothly to the base locomotion.
      if (this.additiveTarget > 0) {
        const clip = this.additiveAction.getClip();
        const hold = clip.duration * 0.5;
        if (this.additiveAction.time >= hold) {
          this.additiveAction.paused = true;
          this.additiveAction.time = hold;
        }
      } else if (this.additiveAction.paused) {
        this.additiveAction.paused = false;
      }
    }
    this.mixer.update(deltaTime);
  }

  setTimeScale(scale: number): void {
    if (this.currentAction) {
      this.currentAction.setEffectiveTimeScale(scale);
    }
  }

  registerLocomotionSet(name: string, clips: LocomotionSet): void {
    this.locomotionSets.set(name, clips);
  }

  switchLocomotionSet(name: string, _crossfadeDuration?: number): void {
    if (!this.locomotionSets.has(name)) {
      logger.warn(`[GltfAnimator] Locomotion set "${name}" not found`);
      return;
    }
    this.activeLocomotionSetName = name;
  }

  playLocomotion(
    action: keyof LocomotionSet,
    options?: { crossfade?: number; phaseSync?: boolean }
  ): AnimationAction | null {
    if (this._overrideLock) return this.currentAction;

    const set = this.locomotionSets.get(this.activeLocomotionSetName);
    if (!set) return null;

    const clipName = set[action];
    if (clipName === undefined) return null;

    if (action === 'jump' && typeof clipName === 'object') {
      return this.playJumpSequence(clipName);
    }

    this.previousLocomotionClip = typeof clipName === 'string' ? clipName : '';
    // Cyclic gaits cut footfall-to-footfall by default (opt out with
    // `phaseSync: false`); one-shot jump phases never phase-sync.
    const phaseSync = options?.phaseSync ?? action !== 'jump';
    return this.play(typeof clipName === 'string' ? clipName : '', {
      crossfade: options?.crossfade,
      phaseSync,
    });
  }

  /**
   * Default fade-in for one-shot overrides (attacks/hits/gathers). Short on
   * purpose: a 0.25 s blend makes swings read mushy — the action should own
   * the pose almost immediately, then blend back to locomotion on exit.
   */
  static readonly DEFAULT_OVERRIDE_FADE = 0.12;

  playOverride(
    clipName: string,
    options?: {
      loop?: boolean;
      crossfade?: number;
      /** Playback rate (1 = normal). Faster values shorten override lock. */
      timeScale?: number;
      onFinished?: () => void;
    }
  ): AnimationAction | null {
    // A prior override's listener lingers on the mixer until its one-shot
    // 'finished' fires (which may never happen if the clip was interrupted).
    if (this._activeOverride) {
      this.mixer.removeEventListener('finished', this._activeOverride.handler);
      this._activeOverride = null;
    }

    const action = this.play(clipName, {
      loop: options?.loop ?? false,
      crossfade: options?.crossfade ?? GltfAnimator.DEFAULT_OVERRIDE_FADE,
    });

    if (!action) {
      // Clip missing: nothing plays, so the override lock must not arm —
      // a typo'd clip name would otherwise freeze locomotion overrides
      // forever (nothing ever fires 'finished' to release it).
      this._overrideLock = false;
      return null;
    }
    this._overrideLock = true;

    if (action) {
      const scale = options?.timeScale;
      if (scale !== undefined && Number.isFinite(scale) && scale > 0) {
        action.setEffectiveTimeScale(scale);
      }
      const onFinished = options?.onFinished;
      const mixer = action.getMixer();
      // The mixer fires 'finished' for ANY LoopOnce action it owns, so filter
      // to this override's action — otherwise an unrelated one-shot finishing
      // first would release the lock early and fire the wrong callback.
      const handler = (e: { action?: unknown }) => {
        if (e.action !== action) return;
        mixer.removeEventListener('finished', handler);
        this._activeOverride = null;
        this._overrideLock = false;
        if (onFinished) onFinished();
      };
      mixer.addEventListener('finished', handler);
      this._activeOverride = { action, handler };
    }

    return action;
  }

  get overrideLock(): boolean {
    return this._overrideLock;
  }

  get lastLocomotionClip(): string {
    return this.previousLocomotionClip;
  }

  get jumpPhase(): 'none' | 'start' | 'loop' | 'end' {
    return this._jumpState;
  }

  private playJumpSequence(jump: {
    start: string;
    loop: string;
    end: string;
  }): AnimationAction | null {
    this._jumpState = 'start';
    return this.play(jump.start);
  }

  /** Stop all animations and release mixer resources. */
  dispose(): void {
    this.clearFlinch();
    if (this._activeOverride) {
      this.mixer.removeEventListener('finished', this._activeOverride.handler);
      this._activeOverride = null;
    }
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.root);
  }
}
