import { beforeEach, describe, expect, it } from 'bun:test';
import { AnimationClip, Scene } from 'three';
import { GltfAnimator, type LocomotionSet } from 'vibegame';
import { InputState } from '../../../../src/plugins/input/components';
import { PlayerController, PlayerGltfConfig } from '../../../../src/plugins/player/components';

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
};

describe('PlayerGltfConfig animation system', () => {
  let entity: number;
  let animator: GltfAnimator;

  beforeEach(() => {
    entity = 1;

    PlayerGltfConfig.loaded[entity] = 0;
    PlayerGltfConfig.animatorRegistryIndex[entity] = 0;
    PlayerGltfConfig.overrideLock[entity] = 0;
    PlayerGltfConfig.overrideClipIndex[entity] = 0;
    PlayerGltfConfig.idleClipIndex[entity] = 0;
    PlayerGltfConfig.walkClipIndex[entity] = 0;
    PlayerGltfConfig.runClipIndex[entity] = 0;
    PlayerGltfConfig.jumpClipIndex[entity] = 0;

    InputState.moveX[entity] = 0;
    InputState.moveY[entity] = 0;
    InputState.jump[entity] = 0;

    animator = makeAnimator(['idle', 'walk', 'run', 'jump', 'attack']);
  });

  describe('overrideLock', () => {
    it('should prevent clip switching when overrideLock is set', () => {
      animator.registerLocomotionSet('default', defaultSet);
      animator.playLocomotion('idle');
      expect(animator.activeClipName).toBe('idle');

      animator.playOverride('attack');
      expect(animator.overrideLock).toBe(true);
      expect(animator.activeClipName).toBe('attack');

      animator.playLocomotion('walk');
      expect(animator.activeClipName).toBe('attack');
    });

    it('should release override lock when non-looping animation finishes', () => {
      animator.registerLocomotionSet('default', defaultSet);
      animator.playOverride('attack');

      expect(animator.overrideLock).toBe(true);

      animator.mixer.dispatchEvent({
        type: 'finished',
        action: {} as any,
        direction: 1,
      });

      expect(animator.overrideLock).toBe(false);
    });

    it('should call onFinished callback when override completes', () => {
      let finishedCalled = false;
      animator.playOverride('attack', {
        onFinished: () => {
          finishedCalled = true;
        },
      });

      animator.mixer.dispatchEvent({
        type: 'finished',
        action: {} as any,
        direction: 1,
      });

      expect(finishedCalled).toBe(true);
    });

    it('should not lock when override plays with loop=true', () => {
      const result = animator.playOverride('attack', { loop: true });
      expect(result).not.toBeNull();
    });
  });

  describe('InputState-driven movement detection', () => {
    it('should detect walking via InputState.moveX', () => {
      InputState.moveX[entity] = 0.5;
      InputState.moveY[entity] = 0;

      const moving =
        Math.abs(InputState.moveX[entity]) > 0.01 ||
        Math.abs(InputState.moveY[entity]) > 0.01;
      expect(moving).toBe(true);
    });

    it('should detect walking via InputState.moveY', () => {
      InputState.moveX[entity] = 0;
      InputState.moveY[entity] = -0.8;

      const moving =
        Math.abs(InputState.moveX[entity]) > 0.01 ||
        Math.abs(InputState.moveY[entity]) > 0.01;
      expect(moving).toBe(true);
    });

    it('should not detect movement below threshold', () => {
      InputState.moveX[entity] = 0.005;
      InputState.moveY[entity] = 0.005;

      const moving =
        Math.abs(InputState.moveX[entity]) > 0.01 ||
        Math.abs(InputState.moveY[entity]) > 0.01;
      expect(moving).toBe(false);
    });

    it('should not detect movement at zero', () => {
      InputState.moveX[entity] = 0;
      InputState.moveY[entity] = 0;

      const moving =
        Math.abs(InputState.moveX[entity]) > 0.01 ||
        Math.abs(InputState.moveY[entity]) > 0.01;
      expect(moving).toBe(false);
    });
  });

  describe('locomotion state transitions via InputState', () => {
    it('should play walk when moving', () => {
      animator.registerLocomotionSet('default', defaultSet);

      const result = animator.playLocomotion('walk');
      expect(result).not.toBeNull();
      expect(animator.activeClipName).toBe('walk');
    });

    it('should play idle when not moving', () => {
      animator.registerLocomotionSet('default', defaultSet);

      const result = animator.playLocomotion('idle');
      expect(result).not.toBeNull();
      expect(animator.activeClipName).toBe('idle');
    });

    it('should play jump when jump input is set', () => {
      animator.registerLocomotionSet('default', { ...defaultSet, jump: 'jump' });

      InputState.jump[entity] = 1;

      const result = animator.playLocomotion('jump');
      expect(result).not.toBeNull();
      expect(animator.activeClipName).toBe('jump');
    });

    it('should play run when moving with run modifier', () => {
      animator.registerLocomotionSet('default', defaultSet);

      const result = animator.playLocomotion('run');
      expect(result).not.toBeNull();
      expect(animator.activeClipName).toBe('run');
    });
  });

  describe('PlayerGltfConfig component arrays', () => {
    it('should store clip indices in component arrays', () => {
      PlayerGltfConfig.idleClipIndex[entity] = 0;
      PlayerGltfConfig.walkClipIndex[entity] = 1;
      PlayerGltfConfig.runClipIndex[entity] = 2;
      PlayerGltfConfig.jumpClipIndex[entity] = 3;

      expect(PlayerGltfConfig.idleClipIndex[entity]).toBe(0);
      expect(PlayerGltfConfig.walkClipIndex[entity]).toBe(1);
      expect(PlayerGltfConfig.runClipIndex[entity]).toBe(2);
      expect(PlayerGltfConfig.jumpClipIndex[entity]).toBe(3);
    });

    it('should track loaded state', () => {
      expect(PlayerGltfConfig.loaded[entity]).toBe(0);

      PlayerGltfConfig.loaded[entity] = 1;
      expect(PlayerGltfConfig.loaded[entity]).toBe(1);
    });

    it('should track animator registry index', () => {
      expect(PlayerGltfConfig.animatorRegistryIndex[entity]).toBe(0);

      PlayerGltfConfig.animatorRegistryIndex[entity] = 42;
      expect(PlayerGltfConfig.animatorRegistryIndex[entity]).toBe(42);
    });
  });

  describe('override via playOverride', () => {
    it('should play override clip and set lock', () => {
      animator.registerLocomotionSet('default', defaultSet);
      animator.playLocomotion('idle');

      const result = animator.playOverride('attack');
      expect(result).not.toBeNull();
      expect(animator.activeClipName).toBe('attack');
      expect(animator.overrideLock).toBe(true);
    });

    it('should allow override to interrupt another override', () => {
      animator.playOverride('attack');
      expect(animator.activeClipName).toBe('attack');

      animator.playOverride('jump');
      expect(animator.activeClipName).toBe('jump');
    });
  });
});
