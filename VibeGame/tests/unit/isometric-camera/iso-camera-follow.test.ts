import { beforeEach, describe, expect, it } from 'bun:test';
import {
  ISO_PITCH,
  IsometricCamera,
  IsometricCameraInputSystem,
  IsometricCameraPlugin,
  IsometricCameraSystem,
  State,
  clearShadowFocusEntity,
  getShadowFocusEntity,
} from 'aigamekit-vibegame';
import { InputState, InputPlugin } from 'aigamekit-vibegame/input';
import { MainCamera, RenderingPlugin } from 'aigamekit-vibegame/rendering';
import {
  Transform,
  TransformsPlugin,
  WorldTransform,
} from 'aigamekit-vibegame/transforms';

/** Drive the draw-group pose system a fixed number of times. */
function tick(state: State, frames: number, dt = 1 / 60): void {
  for (let n = 0; n < frames; n++) {
    state.time.deltaTime = dt;
    IsometricCameraSystem.update!(state);
  }
}

describe('IsometricCameraSystem', () => {
  let state: State;
  let cam: number;
  let target: number;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(RenderingPlugin);
    state.registerPlugin(InputPlugin);
    state.registerPlugin(IsometricCameraPlugin);

    target = state.createEntity();
    state.addComponent(target, Transform);
    state.addComponent(target, WorldTransform);

    cam = state.createEntity();
    state.addComponent(cam, IsometricCamera);
    state.addComponent(cam, Transform);
    state.addComponent(cam, MainCamera);
    IsometricCamera.target[cam] = target;
  });

  function setTarget(x: number, y: number, z: number): void {
    WorldTransform.posX[target] = x;
    WorldTransform.posY[target] = y;
    WorldTransform.posZ[target] = z;
  }

  describe('follow', () => {
    it('snaps onto the target on the first frame (no startup swoop)', () => {
      setTarget(10, 3, -4);
      tick(state, 1);

      expect(IsometricCamera.followX[cam]).toBeCloseTo(10, 6);
      expect(IsometricCamera.followY[cam]).toBeCloseTo(3, 6);
      expect(IsometricCamera.followZ[cam]).toBeCloseTo(-4, 6);
      expect(IsometricCamera.initialized[cam]).toBe(1);
    });

    it('lags behind a teleport instead of snapping', () => {
      setTarget(0, 0, 0);
      tick(state, 1);
      setTarget(100, 0, 0);
      tick(state, 1);

      expect(IsometricCamera.followX[cam]).toBeGreaterThan(0);
      expect(IsometricCamera.followX[cam]).toBeLessThan(100);
    });

    it('catches up given enough frames', () => {
      setTarget(0, 0, 0);
      tick(state, 1);
      setTarget(100, 0, 0);
      tick(state, 200);

      expect(IsometricCamera.followX[cam]).toBeCloseTo(100, 3);
    });

    it('damps vertical harder than horizontal (swallows step bob)', () => {
      setTarget(0, 0, 0);
      tick(state, 1);
      setTarget(10, 10, 0);
      tick(state, 3);

      const xProgress = IsometricCamera.followX[cam] / 10;
      const yProgress = IsometricCamera.followY[cam] / 10;
      expect(yProgress).toBeLessThan(xProgress);
    });

    it('does nothing without a target', () => {
      IsometricCamera.target[cam] = 0;
      tick(state, 5);
      expect(IsometricCamera.initialized[cam]).toBe(0);
    });

    it('does nothing when the target has no WorldTransform', () => {
      const bare = state.createEntity();
      IsometricCamera.target[cam] = bare;
      tick(state, 5);
      expect(IsometricCamera.initialized[cam]).toBe(0);
    });
  });

  describe('pose', () => {
    it('places the eye at the arm length above and behind the look point', () => {
      setTarget(0, 0, 0);
      IsometricCamera.offsetY[cam] = 0;
      tick(state, 1);

      const d = IsometricCamera.distance[cam];
      const dist = Math.hypot(
        Transform.posX[cam],
        Transform.posY[cam],
        Transform.posZ[cam]
      );
      expect(dist).toBeCloseTo(d, 4);
    });

    it('keeps the camera above the target', () => {
      setTarget(0, 5, 0);
      tick(state, 1);
      expect(Transform.posY[cam]).toBeGreaterThan(5);
    });

    it('uses the fixed isometric pitch', () => {
      setTarget(0, 0, 0);
      IsometricCamera.offsetY[cam] = 0;
      tick(state, 1);

      const horiz = Math.hypot(Transform.posX[cam], Transform.posZ[cam]);
      const pitch = Math.atan2(Transform.posY[cam], horiz);
      expect(pitch).toBeCloseTo(ISO_PITCH, 5);
    });

    it('writes a normalized look-at quaternion', () => {
      setTarget(3, 1, 7);
      tick(state, 1);

      const mag = Math.hypot(
        Transform.rotX[cam],
        Transform.rotY[cam],
        Transform.rotZ[cam],
        Transform.rotW[cam]
      );
      expect(mag).toBeCloseTo(1, 5);
    });

    it('marks the transform dirty so the hierarchy picks it up', () => {
      setTarget(0, 0, 0);
      Transform.dirty[cam] = 0;
      tick(state, 1);
      expect(Transform.dirty[cam]).toBe(1);
    });

    it('follows the target laterally', () => {
      setTarget(0, 0, 0);
      tick(state, 1);
      const x0 = Transform.posX[cam];
      setTarget(50, 0, 0);
      tick(state, 200);
      expect(Transform.posX[cam] - x0).toBeCloseTo(50, 2);
    });
  });

  describe('yaw sweep', () => {
    it('rotates towards the target yaw over time', () => {
      setTarget(0, 0, 0);
      tick(state, 1);
      IsometricCamera.targetYaw[cam] =
        IsometricCamera.targetYaw[cam] + Math.PI / 2;
      tick(state, 1);

      expect(IsometricCamera.yaw[cam]).toBeGreaterThan(Math.PI / 4);
      expect(IsometricCamera.yaw[cam]).toBeLessThan(Math.PI / 4 + Math.PI / 2);
    });

    it('settles exactly on the target yaw', () => {
      setTarget(0, 0, 0);
      tick(state, 1);
      const goal = IsometricCamera.targetYaw[cam] + Math.PI / 2;
      IsometricCamera.targetYaw[cam] = goal;
      tick(state, 300);

      expect(IsometricCamera.yaw[cam]).toBeCloseTo(goal, 3);
    });

    it('takes the short way round when the target crosses ±π', () => {
      setTarget(0, 0, 0);
      IsometricCamera.yaw[cam] = Math.PI - 0.1;
      IsometricCamera.targetYaw[cam] = Math.PI - 0.1;
      tick(state, 1);

      // Target just past +π; the smoothed yaw must move forwards (a wrapped
      // implementation would swing nearly a full turn backwards instead).
      IsometricCamera.targetYaw[cam] = Math.PI + 0.1;
      const before = IsometricCamera.yaw[cam];
      tick(state, 1);
      const step = IsometricCamera.yaw[cam] - before;

      expect(step).toBeGreaterThan(0);
      expect(Math.abs(step)).toBeLessThan(0.2);
    });

    it('does not sweep on the very first frame', () => {
      setTarget(0, 0, 0);
      IsometricCamera.targetYaw[cam] = 3;
      tick(state, 1);
      expect(IsometricCamera.yaw[cam]).toBeCloseTo(3, 6);
    });
  });

  describe('zoom plumbing', () => {
    it('mirrors the smoothed ortho size onto MainCamera in the simulation pass', () => {
      IsometricCamera.targetOrthoSize[cam] = 30;
      state.time.deltaTime = 1;
      for (let n = 0; n < 60; n++) IsometricCameraInputSystem.update!(state);

      expect(IsometricCamera.orthoSize[cam]).toBeCloseTo(30, 3);
      expect(MainCamera.orthoSize[cam]).toBeCloseTo(
        IsometricCamera.orthoSize[cam],
        6
      );
    });

    it('reads scroll from the bound input source', () => {
      const src = state.createEntity();
      state.addComponent(src, InputState);
      IsometricCamera.inputSource[cam] = src;
      InputState.scrollDelta[src] = 1;

      state.time.deltaTime = 1 / 60;
      IsometricCameraInputSystem.update!(state);

      // Positive deltaY = wheel down = zoom out (target grows).
      expect(IsometricCamera.targetOrthoSize[cam]).toBeGreaterThan(22);
    });

    it('auto-resolves an input source when none is bound', () => {
      const src = state.createEntity();
      state.addComponent(src, InputState);
      IsometricCamera.inputSource[cam] = 0;

      state.time.deltaTime = 1 / 60;
      IsometricCameraInputSystem.update!(state);

      expect(IsometricCamera.inputSource[cam]).toBe(src);
    });
  });

  describe('shadow focus', () => {
    it('points the shadow frustum at the follow target', () => {
      clearShadowFocusEntity(state);
      setTarget(0, 0, 0);
      tick(state, 1);
      expect(getShadowFocusEntity(state)).toBe(target);
    });

    it('follows a target swap', () => {
      const other = state.createEntity();
      state.addComponent(other, Transform);
      state.addComponent(other, WorldTransform);

      setTarget(0, 0, 0);
      tick(state, 1);
      IsometricCamera.target[cam] = other;
      tick(state, 1);

      expect(getShadowFocusEntity(state)).toBe(other);
    });
  });
});
