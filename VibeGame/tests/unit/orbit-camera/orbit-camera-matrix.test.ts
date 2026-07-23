import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { OrbitCamera } from 'vibegame/orbit-camera';
import { OrbitCameraPlugin } from 'vibegame/orbit-camera';

describe('OrbitCamera default field matrix', () => {
  const defaultsBlock = OrbitCameraPlugin.config?.defaults?.['orbit-camera'];
  if (defaultsBlock == null) {
    throw new Error('OrbitCameraPlugin defaults missing');
  }
  const defaults = defaultsBlock;

  for (let i = 0; i < 30; i++) {
    it(`default targetDistance stable (i=${i})`, () => {
      expect(defaults.targetDistance).toBe(4);
    });
  }

  for (let i = 0; i < 30; i++) {
    it(`default smoothness stable (i=${i})`, () => {
      expect(defaults.smoothness).toBe(0.5);
    });
  }
});

describe('OrbitCamera per-entity writes', () => {
  let state: State;
  let eid: number;

  beforeEach(() => {
    state = new State();
    state.registerComponent('orbit-camera', OrbitCamera);
    eid = state.createEntity();
    state.addComponent(eid, OrbitCamera);
  });

  for (const distance of [1, 2, 4, 8, 16, 24]) {
    it(`stores targetDistance ${distance}`, () => {
      OrbitCamera.targetDistance[eid] = distance;
      expect(OrbitCamera.targetDistance[eid]).toBe(distance);
    });
  }

  for (const yaw of [0, 0.5, 1.0, 1.5, Math.PI]) {
    it(`stores targetYaw ${yaw}`, () => {
      OrbitCamera.targetYaw[eid] = yaw;
      expect(OrbitCamera.targetYaw[eid]).toBeCloseTo(yaw, 5);
    });
  }

  for (const pitch of [0, 0.2, 0.5, 1.0, Math.PI / 2]) {
    it(`stores targetPitch ${pitch}`, () => {
      OrbitCamera.targetPitch[eid] = pitch;
      expect(OrbitCamera.targetPitch[eid]).toBeCloseTo(pitch, 5);
    });
  }

  for (const sens of [0.001, 0.007, 0.01, 0.02]) {
    it(`stores sensitivity ${sens}`, () => {
      OrbitCamera.sensitivity[eid] = sens;
      expect(OrbitCamera.sensitivity[eid]).toBeCloseTo(sens, 5);
    });
  }
});

describe('OrbitCamera offset defaults', () => {
  const defaultsBlock = OrbitCameraPlugin.config?.defaults?.['orbit-camera'];
  if (defaultsBlock == null) {
    throw new Error('OrbitCameraPlugin defaults missing');
  }
  const defaults = defaultsBlock;

  it('offsetY default is 1.25', () => {
    expect(defaults.offsetY).toBe(1.25);
  });

  for (let i = 0; i < 10; i++) {
    it(`offsetX default zero (i=${i})`, () => {
      expect(defaults.offsetX).toBe(0);
    });
  }
});
