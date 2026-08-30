import { describe, expect, it } from 'bun:test';
import {
  AnimationClip,
  NumberKeyframeTrack,
  Object3D,
  Scene,
  VectorKeyframeTrack,
} from 'three';
import { GltfAnimator } from 'vibegame';

/** A clip with one real track — `makeClipAdditive` needs a first keyframe. */
function clip(name: string, target: string): AnimationClip {
  return new AnimationClip(name, 1, [
    new VectorKeyframeTrack(`${target}.position`, [0, 1], [0, 0, 0, 0, 0.5, 0]),
    new NumberKeyframeTrack(`${target}.scale[x]`, [0, 1], [1, 1.2]),
  ]);
}

function makeAnimator(names: string[]) {
  const scene = new Scene();
  const bone = new Object3D();
  bone.name = 'Torso';
  scene.add(bone);
  return new GltfAnimator({
    scene,
    animations: names.map((n) => clip(n, 'Torso')),
  } as never);
}

describe('GltfAnimator.playFlinch', () => {
  it('ignores a clip the rig does not have', () => {
    const animator = makeAnimator(['idle', 'walk']);

    expect(animator.playFlinch('nosuchclip')).toBe(false);
    expect(animator.flinching).toBe(false);
  });

  it('ramps in over the attack window and decays to nothing', () => {
    const animator = makeAnimator(['idle', 'hit']);
    animator.play('idle');

    expect(
      animator.playFlinch('hit', { weight: 0.8, attack: 0.1, release: 0.2 })
    ).toBe(true);
    expect(animator.flinchWeightNow()).toBe(0);

    animator.update(0.05); // half the ramp
    expect(animator.flinchWeightNow()).toBeCloseTo(0.4, 3);

    animator.update(0.05); // peak
    expect(animator.flinchWeightNow()).toBeCloseTo(0.8, 3);

    animator.update(0.1); // half the release
    expect(animator.flinchWeightNow()).toBeCloseTo(0.4, 3);

    animator.update(0.15); // past the release
    expect(animator.flinchWeightNow()).toBe(0);
    expect(animator.flinching).toBe(false);
  });

  it('leaves the base clip playing — a flinch never interrupts locomotion', () => {
    const animator = makeAnimator(['run', 'hit']);
    animator.play('run');

    animator.playFlinch('hit');
    animator.update(0.1);

    expect(animator.activeClipName).toBe('run');
  });

  it('does not arm the override lock (a flinch can land mid-swing)', () => {
    const animator = makeAnimator(['idle', 'attack', 'hit']);
    animator.playOverride('attack', { loop: false });

    animator.playFlinch('hit');
    animator.update(0.05);

    expect(animator.overrideLock).toBe(true);
    expect(animator.activeClipName).toBe('attack');
    expect(animator.flinching).toBe(true);
  });

  it('re-triggering keeps the higher peak instead of dropping the pose', () => {
    const animator = makeAnimator(['idle', 'hit']);
    animator.play('idle');

    animator.playFlinch('hit', { weight: 0.9, attack: 0, release: 0.4 });
    animator.update(0.2); // decayed to half
    expect(animator.flinchWeightNow()).toBeCloseTo(0.45, 3);

    // A weaker follow-up blow must not snap the recoil down to its own peak.
    animator.playFlinch('hit', { weight: 0.3, attack: 0, release: 0.4 });
    expect(animator.flinchWeightNow()).toBeCloseTo(0.45, 3);
  });

  it('resolves fuzzy clip names like the rest of the animator', () => {
    const animator = makeAnimator(['Animator3D_Hit', 'Animator3D_Walk']);

    expect(animator.playFlinch('hit')).toBe(true);
  });

  it('clearFlinch drops the overlay at once (death / teardown)', () => {
    const animator = makeAnimator(['idle', 'hit']);
    animator.playFlinch('hit', { attack: 0 });
    animator.update(0.02);
    expect(animator.flinching).toBe(true);

    animator.clearFlinch();

    expect(animator.flinching).toBe(false);
    expect(animator.flinchWeightNow()).toBe(0);
  });

  it('a zero weight is a no-op rather than a stuck overlay', () => {
    const animator = makeAnimator(['idle', 'hit']);

    expect(animator.playFlinch('hit', { weight: 0 })).toBe(false);
    expect(animator.flinching).toBe(false);
  });
});
