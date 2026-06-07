import { beforeEach, describe, expect, it } from 'bun:test';
import { AnimationClip, Scene } from 'three';
import { GltfAnimator, type LocomotionSet } from 'vibegame';

function makeGltf(clipNames: string[]) {
  return {
    scene: new Scene(),
    animations: clipNames.map((name) => new AnimationClip(name, 1, [])),
  };
}

function makeAnimator(clipNames: string[]) {
  return new GltfAnimator(makeGltf(clipNames) as any);
}

const defaultSet: LocomotionSet = {
  idle: 'idle',
  walk: 'walk',
  run: 'run',
  jump: 'jump',
};

describe('GltfAnimator locomotion', () => {
  let animator: GltfAnimator;

  beforeEach(() => {
    animator = makeAnimator([
      'idle',
      'walk',
      'run',
      'jump',
      'attack',
      'jump_start',
      'jump_loop',
      'jump_end',
    ]);
  });

  it('registers and switches locomotion sets', () => {
    const anim = makeAnimator([
      'idle',
      'walk',
      'run',
      'jump',
      'attack',
      'armed_idle',
      'armed_walk',
      'armed_run',
    ]);
    anim.registerLocomotionSet('default', defaultSet);
    anim.registerLocomotionSet('armed', {
      idle: 'armed_idle',
      walk: 'armed_walk',
      run: 'armed_run',
    });

    anim.playLocomotion('idle');
    expect(anim.activeClipName).toBe('idle');

    anim.switchLocomotionSet('armed');
    anim.playLocomotion('walk');
    expect(anim.activeClipName).toBe('armed_walk');
  });

  it('switchLocomotionSet warns on missing set', () => {
    const warnings: string[] = [];
    const orig = console.warn;
    console.warn = (msg: string) => warnings.push(msg);

    animator.switchLocomotionSet('nonexistent');

    console.warn = orig;
    expect(warnings.length).toBe(1);
    expect(warnings[0]).toContain('nonexistent');
  });

  it('playLocomotion resolves correct clip from active set', () => {
    animator.registerLocomotionSet('default', defaultSet);

    animator.playLocomotion('idle');
    expect(animator.activeClipName).toBe('idle');

    animator.playLocomotion('walk');
    expect(animator.activeClipName).toBe('walk');

    animator.playLocomotion('run');
    expect(animator.activeClipName).toBe('run');
  });

  it('playLocomotion returns null for missing action in set', () => {
    animator.registerLocomotionSet('default', {
      idle: 'idle',
      walk: 'walk',
      run: 'run',
    });

    const result = animator.playLocomotion('jump');
    expect(result).toBeNull();
  });

  it('playLocomotion returns null when no set registered', () => {
    const result = animator.playLocomotion('idle');
    expect(result).toBeNull();
  });

  it('override lock prevents locomotion interruption', () => {
    animator.registerLocomotionSet('default', defaultSet);

    animator.playLocomotion('idle');
    expect(animator.activeClipName).toBe('idle');

    animator.playOverride('attack');
    expect(animator.overrideLock).toBe(true);
    expect(animator.activeClipName).toBe('attack');

    const result = animator.playLocomotion('walk');
    expect(animator.activeClipName).toBe('attack');
    expect(result).not.toBeNull();
  });

  it('override lock releases when animation finishes', () => {
    animator.registerLocomotionSet('default', defaultSet);

    let finishedCalled = false;
    animator.playOverride('attack', {
      onFinished: () => {
        finishedCalled = true;
      },
    });

    expect(animator.overrideLock).toBe(true);

    const mixer = animator.mixer;
    mixer.dispatchEvent({ type: 'finished', action: {} as any, direction: 1 });

    expect(animator.overrideLock).toBe(false);
    expect(finishedCalled).toBe(true);
  });

  it('playOverride with loop=true does not lock', () => {
    animator.registerLocomotionSet('default', defaultSet);

    const result = animator.playOverride('attack', { loop: true });
    expect(result).not.toBeNull();
  });

  it('backward compat: play() still works directly', () => {
    const result = animator.play('walk');
    expect(result).not.toBeNull();
    expect(animator.activeClipName).toBe('walk');

    const result2 = animator.play('run');
    expect(result2).not.toBeNull();
    expect(animator.activeClipName).toBe('run');
  });

  it("default locomotion set is 'default'", () => {
    animator.registerLocomotionSet('default', defaultSet);

    animator.playLocomotion('idle');
    expect(animator.activeClipName).toBe('idle');
  });

  it('resolves turn-in-place clips from the locomotion set', () => {
    const anim = makeAnimator([
      'idle',
      'walk',
      'run',
      'Animator3D_TurnLeft',
      'Animator3D_TurnRight',
    ]);
    anim.registerLocomotionSet('default', {
      idle: 'idle',
      walk: 'walk',
      run: 'run',
      turnLeft: 'Animator3D_TurnLeft',
      turnRight: 'Animator3D_TurnRight',
    });

    anim.playLocomotion('turnLeft');
    expect(anim.activeClipName).toBe('Animator3D_TurnLeft');

    anim.playLocomotion('turnRight');
    expect(anim.activeClipName).toBe('Animator3D_TurnRight');
  });

  it('additive turn overlay ramps in over the base and fades out', () => {
    const anim = makeAnimator(['idle', 'walk', 'run', 'Animator3D_TurnLeft']);
    anim.play('walk');

    expect(anim.additiveOverlayWeight).toBe(0);

    // Curving while walking: base stays 'walk', turn blends on top.
    anim.setAdditive('Animator3D_TurnLeft', 1);
    for (let i = 0; i < 20; i++) anim.update(0.1);
    expect(anim.activeClipName).toBe('walk'); // base unchanged
    expect(anim.additiveOverlayWeight).toBeGreaterThan(0.5);

    // Stop steering: overlay fades back out.
    anim.setAdditive('', 0);
    for (let i = 0; i < 40; i++) anim.update(0.1);
    expect(anim.additiveOverlayWeight).toBeLessThan(0.05);
  });

  it('3-part jump triggers playJumpSequence', () => {
    animator.registerLocomotionSet('default', {
      idle: 'idle',
      walk: 'walk',
      run: 'run',
      jump: { start: 'jump_start', loop: 'jump_loop', end: 'jump_end' },
    });

    animator.playLocomotion('jump');
    expect(animator.activeClipName).toBe('jump_start');
  });
});
