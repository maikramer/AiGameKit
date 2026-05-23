import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { FollowCamera } from '../../../../src/plugins/follow-camera/components';
import { Transform, WorldTransform } from '../../../../src/plugins/transforms';
import { FollowCameraSpringSystem } from '../../../../src/plugins/follow-camera/systems';

describe('FollowCameraSpringSystem', () => {
  let state: State;
  let cam: number;
  let target: number;

  beforeEach(() => {
    state = new State();

    cam = state.createEntity();
    target = state.createEntity();

    state.addComponent(cam, FollowCamera);
    state.addComponent(cam, Transform, { scaleX: 1, scaleY: 1, scaleZ: 1 });
    state.addComponent(target, WorldTransform);

    FollowCamera.target[cam] = target;
    FollowCamera.currentDistance[cam] = 5;
    FollowCamera.currentPitch[cam] = 0;
    FollowCamera.currentYaw[cam] = 0;
  });

  describe('useSpring = 0 (disabled)', () => {
    it('should skip entities when useSpring is 0', () => {
      FollowCamera.useSpring[cam] = 0;
      Transform.posX[cam] = 99;
      Transform.posY[cam] = 99;
      Transform.posZ[cam] = 99;

      FollowCameraSpringSystem.update(state);

      expect(Transform.posX[cam]).toBe(99);
      expect(Transform.posY[cam]).toBe(99);
      expect(Transform.posZ[cam]).toBe(99);
    });
  });

  describe('useSpring = 1 (enabled)', () => {
    it('should snap to target on first frame and zero velocities', () => {
      FollowCamera.useSpring[cam] = 1;
      FollowCamera.smoothedTargetInit[cam] = 0;

      WorldTransform.posX[target] = 10;
      WorldTransform.posY[target] = 5;
      WorldTransform.posZ[target] = -3;

      FollowCamera.springVelocityX[cam] = 100;
      FollowCamera.springVelocityY[cam] = -50;
      FollowCamera.springVelocityZ[cam] = 25;

      FollowCameraSpringSystem.update(state);

      expect(FollowCamera.smoothedTargetX[cam]).toBeCloseTo(10, 5);
      expect(FollowCamera.smoothedTargetY[cam]).toBeCloseTo(5, 5);
      expect(FollowCamera.smoothedTargetZ[cam]).toBeCloseTo(-3, 5);
      expect(FollowCamera.springVelocityX[cam]).toBe(0);
      expect(FollowCamera.springVelocityY[cam]).toBe(0);
      expect(FollowCamera.springVelocityZ[cam]).toBe(0);
      expect(FollowCamera.smoothedTargetInit[cam]).toBe(1);
    });

    it('should converge smoothed target toward actual position over time', () => {
      FollowCamera.useSpring[cam] = 1;
      FollowCamera.smoothedTargetInit[cam] = 1;
      FollowCamera.springTime[cam] = 0.15;

      FollowCamera.smoothedTargetX[cam] = 0;
      FollowCamera.smoothedTargetY[cam] = 0;
      FollowCamera.smoothedTargetZ[cam] = 0;

      WorldTransform.posX[target] = 10;
      WorldTransform.posY[target] = 0;
      WorldTransform.posZ[target] = 0;

      const dt = 0.016;
      const steps = Math.ceil((3 * 0.15) / dt);

      for (let i = 0; i < steps; i++) {
        state.time.deltaTime = dt;
        FollowCameraSpringSystem.update(state);
      }

      expect(Math.abs(FollowCamera.smoothedTargetX[cam] - 10)).toBeLessThan(
        10 * 0.05,
      );
    });

    it('should store spring velocity in component arrays after each step', () => {
      FollowCamera.useSpring[cam] = 1;
      FollowCamera.smoothedTargetInit[cam] = 1;
      FollowCamera.springTime[cam] = 0.15;

      FollowCamera.smoothedTargetX[cam] = 0;
      FollowCamera.smoothedTargetY[cam] = 0;
      FollowCamera.smoothedTargetZ[cam] = 0;

      WorldTransform.posX[target] = 10;
      WorldTransform.posY[target] = 0;
      WorldTransform.posZ[target] = 0;

      FollowCameraSpringSystem.update(state);

      expect(FollowCamera.springVelocityX[cam]).not.toBe(0);
      expect(FollowCamera.springVelocityY[cam]).toBe(0);
      expect(FollowCamera.springVelocityZ[cam]).toBe(0);
    });

    it('should converge faster with smaller springTime', () => {
      FollowCamera.useSpring[cam] = 1;
      FollowCamera.smoothedTargetInit[cam] = 1;

      WorldTransform.posX[target] = 10;
      WorldTransform.posY[target] = 0;
      WorldTransform.posZ[target] = 0;

      FollowCamera.springTime[cam] = 0.05;
      FollowCamera.smoothedTargetX[cam] = 0;
      FollowCamera.springVelocityX[cam] = 0;

      state.time.deltaTime = 0.016;
      FollowCameraSpringSystem.update(state);
      const fastProgress = Math.abs(FollowCamera.smoothedTargetX[cam]);

      FollowCamera.springTime[cam] = 0.5;
      FollowCamera.smoothedTargetX[cam] = 0;
      FollowCamera.springVelocityX[cam] = 0;

      FollowCameraSpringSystem.update(state);
      const slowProgress = Math.abs(FollowCamera.smoothedTargetX[cam]);

      expect(fastProgress).toBeGreaterThan(slowProgress);
    });

    it('should respect offset when computing target position', () => {
      FollowCamera.useSpring[cam] = 1;
      FollowCamera.smoothedTargetInit[cam] = 0;

      WorldTransform.posX[target] = 10;
      WorldTransform.posY[target] = 5;
      WorldTransform.posZ[target] = -3;

      FollowCamera.offsetX[cam] = 1;
      FollowCamera.offsetY[cam] = 2;
      FollowCamera.offsetZ[cam] = -1;

      FollowCameraSpringSystem.update(state);

      expect(FollowCamera.smoothedTargetX[cam]).toBeCloseTo(11, 5);
      expect(FollowCamera.smoothedTargetY[cam]).toBeCloseTo(7, 5);
      expect(FollowCamera.smoothedTargetZ[cam]).toBeCloseTo(-4, 5);
    });
  });
});
