import { beforeEach, describe, expect, it } from 'bun:test';
import { AnimationClip, LoopRepeat, Scene } from 'three';
import { GltfAnimator, type LocomotionSet } from 'vibegame';
import { matchClipKeyword } from '../../../src/extras/gltf-animator';

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
    const action = animator.playOverride('attack', {
      onFinished: () => {
        finishedCalled = true;
      },
    });

    expect(animator.overrideLock).toBe(true);

    const mixer = animator.mixer;
    // Must be this override's own action — the mixer fires 'finished' for any
    // LoopOnce action it owns, and the lock should only release for ours.
    mixer.dispatchEvent({ type: 'finished', action: action!, direction: 1 });

    expect(animator.overrideLock).toBe(false);
    expect(finishedCalled).toBe(true);
  });

  it('override lock ignores finished events from other actions', () => {
    animator.registerLocomotionSet('default', defaultSet);

    let finishedCalled = false;
    animator.playOverride('attack', {
      onFinished: () => {
        finishedCalled = true;
      },
    });
    expect(animator.overrideLock).toBe(true);

    // A different LoopOnce action finishing must not release our lock.
    animator.mixer.dispatchEvent({
      type: 'finished',
      action: {} as any,
      direction: 1,
    });

    expect(animator.overrideLock).toBe(true);
    expect(finishedCalled).toBe(false);
  });

  it('playOverride with loop=true does not lock', () => {
    animator.registerLocomotionSet('default', defaultSet);

    const result = animator.playOverride('attack', { loop: true });
    expect(result).not.toBeNull();
  });

  it('playOverride applies timeScale', () => {
    const action = animator.playOverride('attack', {
      loop: false,
      timeScale: 1.85,
    });
    expect(action).not.toBeNull();
    expect(action!.getEffectiveTimeScale()).toBeCloseTo(1.85, 5);
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

  it('play() resolves logical names to Animator3D_* clips', () => {
    const anim = makeAnimator([
      'Animator3D_Attack',
      'Animator3D_BreatheIdle',
      'Animator3D_Roar',
      'Animator3D_Run',
      'Animator3D_Walk',
    ]);
    expect(anim.resolveClipName('idle')).toBe('Animator3D_BreatheIdle');
    expect(anim.resolveClipName('walk')).toBe('Animator3D_Walk');
    expect(anim.resolveClipName('run')).toBe('Animator3D_Run');
    const played = anim.play('walk');
    expect(played).not.toBeNull();
    expect(anim.activeClipName).toBe('Animator3D_Walk');
  });

  it('resolves flying-pack aliases (mosquito Hover/Soar/Dive)', () => {
    const anim = makeAnimator([
      'Animator3D_BreatheIdle',
      'Animator3D_Dive',
      'Animator3D_Hover',
      'Animator3D_Land',
      'Animator3D_Soar',
    ]);
    expect(anim.resolveClipName('idle')).toBe('Animator3D_BreatheIdle');
    expect(anim.resolveClipName('walk')).toBe('Animator3D_Hover');
    expect(anim.resolveClipName('run')).toBe('Animator3D_Soar');
    expect(anim.resolveClipName('jump')).toBe('Animator3D_Dive');
    expect(anim.resolveClipName('death')).toBe('Animator3D_Land');
    expect(anim.play('walk')).not.toBeNull();
    expect(anim.activeClipName).toBe('Animator3D_Hover');
  });

  it('resets LoopOnce when later playing a looping clip', () => {
    const anim = makeAnimator(['Animator3D_Jump', 'Animator3D_Walk']);
    anim.play('jump', { loop: false });
    const walk = anim.play('walk');
    expect(walk).not.toBeNull();
    expect(walk!.loop).toBe(LoopRepeat);
    expect(walk!.clampWhenFinished).toBe(false);
  });

  it('matchClipKeyword prefers exact idle over axeidle/swordidle', () => {
    const names = ['axeidle', 'chopidle', 'idle', 'swordidle'];
    expect(matchClipKeyword(names, 'idle')).toBe('idle');
    expect(matchClipKeyword(names, 'swordidle')).toBe('swordidle');
    expect(matchClipKeyword(['walk', 'run', 'idle'], 'walk')).toBe('walk');
    const anim = makeAnimator(names);
    expect(anim.resolveClipName('idle')).toBe('idle');
  });
});
