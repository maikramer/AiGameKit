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
  AnimationAction,
  AnimationClip,
  AnimationMixer,
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

  /** Tick the mixer. Call every frame with delta time in seconds. */
  update(deltaTime: number): void {
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
      action.getMixer().addEventListener('finished', function handler() {
        action.getMixer().removeEventListener('finished', handler);
        self._overrideLock = false;
        if (onFinished) onFinished();
      });
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
