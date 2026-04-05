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

export class GltfAnimator {
  readonly mixer: AnimationMixer;
  readonly clips: Map<string, AnimationClip> = new Map();

  private currentAction: AnimationAction | null = null;
  private currentClipName = '';
  private crossfadeDuration: number;

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

  /** Stop all animations and release mixer resources. */
  dispose(): void {
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.root);
  }
}
