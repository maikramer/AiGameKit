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
  type Object3D,
} from 'three';
import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';

export interface GltfAnimatorOptions {
  /** Default crossfade duration in seconds. */
  crossfadeDuration?: number;
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

export class GltfAnimator {
  readonly mixer: AnimationMixer;
  readonly clips: Map<string, AnimationClip> = new Map();

  private currentAction: AnimationAction | null = null;
  private currentClipName = '';
  private crossfadeDuration: number;

  private locomotionSets = new Map<string, LocomotionSet>();
  private activeLocomotionSetName = 'default';
  private _overrideLock = false;
  private previousLocomotionClip = '';
  private _jumpState: 'none' | 'start' | 'loop' | 'end' = 'none';

  // Additive overlay (e.g. turn-lean blended on top of locomotion).
  private additiveClips = new Map<string, AnimationClip>();
  private additiveAction: AnimationAction | null = null;
  private additiveClipName = '';
  private additiveWeight = 0;
  private additiveTarget = 0;

  constructor(gltf: GLTF, options: GltfAnimatorOptions = {}) {
    this.mixer = new AnimationMixer(gltf.scene);
    this.crossfadeDuration = options.crossfadeDuration ?? 0.25;

    for (const clip of gltf.animations) {
      this.clips.set(clip.name, clip);
    }
  }

  get root(): Object3D {
    return this.mixer.getRoot() as Object3D;
  }

  get clipNames(): string[] {
    return Array.from(this.clips.keys());
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

  /** Play a clip by name with optional crossfade from the current clip. */
  play(
    clipName: string,
    options?: { crossfade?: number; loop?: boolean }
  ): AnimationAction | null {
    if (clipName === this.currentClipName && this.currentAction?.isRunning()) {
      return this.currentAction;
    }

    const clip = this.clips.get(clipName);
    if (!clip) {
      console.warn(
        `[GltfAnimator] Clip "${clipName}" not found. Available: ${this.clipNames.join(', ')}`
      );
      return null;
    }

    const nextAction = this.mixer.clipAction(clip);
    const fade = options?.crossfade ?? this.crossfadeDuration;

    if (options?.loop === false) {
      nextAction.setLoop(2200, 1); // THREE.LoopOnce
      nextAction.clampWhenFinished = true;
    }

    if (this.currentAction && fade > 0) {
      nextAction.reset().setEffectiveTimeScale(1).setEffectiveWeight(1);
      this.currentAction.crossFadeTo(nextAction, fade, true);
      nextAction.play();
    } else {
      nextAction.reset().play();
    }

    this.currentAction = nextAction;
    this.currentClipName = clipName;
    return nextAction;
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

  /** Current smoothed weight of the additive overlay (0 when none). */
  get additiveOverlayWeight(): number {
    return this.additiveWeight;
  }

  /** Tick the mixer. Call every frame with delta time in seconds. */
  update(deltaTime: number): void {
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
      console.warn(`[GltfAnimator] Locomotion set "${name}" not found`);
      return;
    }
    this.activeLocomotionSetName = name;
  }

  playLocomotion(
    action: keyof LocomotionSet,
    options?: { crossfade?: number }
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
    return this.play(typeof clipName === 'string' ? clipName : '', options);
  }

  playOverride(
    clipName: string,
    options?: { loop?: boolean; crossfade?: number; onFinished?: () => void }
  ): AnimationAction | null {
    this._overrideLock = true;
    const action = this.play(clipName, {
      loop: options?.loop ?? false,
      crossfade: options?.crossfade,
    });

    if (action) {
      const onFinished = options?.onFinished;
      const self = this;
      // The mixer fires 'finished' for ANY LoopOnce action it owns, so filter
      // to this override's action — otherwise an unrelated one-shot finishing
      // first would release the lock early and fire the wrong callback.
      action
        .getMixer()
        .addEventListener(
          'finished',
          function handler(e: { action?: unknown }) {
            if (e.action !== action) return;
            action.getMixer().removeEventListener('finished', handler);
            self._overrideLock = false;
            if (onFinished) onFinished();
          }
        );
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
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.root);
  }
}
