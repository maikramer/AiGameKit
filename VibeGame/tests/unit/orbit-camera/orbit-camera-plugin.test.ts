import { beforeEach, describe, expect, it } from 'bun:test';
import { State, defineQuery } from 'vibegame';
import {
  OrbitCameraPlugin,
  OrbitCamera,
  orbitCameraRecipe,
} from 'vibegame/orbit-camera';
import {
  getCameraControls,
  removeCameraControls,
  setCameraControls,
} from '../../../src/plugins/orbit-camera/registry';
import {
  OrbitCameraInputSystem,
  OrbitCameraSetupSystem,
  OrbitCameraSystem,
} from '../../../src/plugins/orbit-camera/systems';

const ORBIT_FIELDS = [
  'target',
  'inputSource',
  'currentYaw',
  'currentPitch',
  'currentDistance',
  'targetYaw',
  'targetPitch',
  'targetDistance',
  'minDistance',
  'maxDistance',
  'minPitch',
  'maxPitch',
  'smoothness',
  'offsetX',
  'offsetY',
  'offsetZ',
  'sensitivity',
  'zoomSensitivity',
] as const;

describe('orbitCameraRecipe', () => {
  it('name is OrbitCamera', () => {
    expect(orbitCameraRecipe.name).toBe('OrbitCamera');
  });

  it('merge is true', () => {
    expect(orbitCameraRecipe.merge).toBe(true);
  });

  it('includes orbit-camera, transform, main-camera', () => {
    expect(orbitCameraRecipe.components).toEqual([
      'orbit-camera',
      'transform',
      'main-camera',
    ]);
  });
});

describe('OrbitCameraPlugin', () => {
  it('registers three systems in order', () => {
    expect(OrbitCameraPlugin.systems?.[0]).toBe(OrbitCameraSetupSystem);
    expect(OrbitCameraPlugin.systems?.[1]).toBe(OrbitCameraInputSystem);
    expect(OrbitCameraPlugin.systems?.[2]).toBe(OrbitCameraSystem);
  });

  it('registers OrbitCamera component', () => {
    expect(OrbitCameraPlugin.components?.OrbitCamera).toBe(OrbitCamera);
  });

  it('initialize hook is defined', () => {
    expect(typeof OrbitCameraPlugin.initialize).toBe('function');
  });

  for (const field of ORBIT_FIELDS) {
    it(`defaults include orbit-camera.${field}`, () => {
      const defaults = OrbitCameraPlugin.config?.defaults?.['orbit-camera'];
      expect(defaults).toBeDefined();
      expect(Object.prototype.hasOwnProperty.call(defaults, field)).toBe(true);
    });
  }

  it('default targetDistance is 4', () => {
    expect(
      OrbitCameraPlugin.config?.defaults?.['orbit-camera']?.targetDistance
    ).toBe(4);
  });

  it('default maxDistance is 25', () => {
    expect(
      OrbitCameraPlugin.config?.defaults?.['orbit-camera']?.maxDistance
    ).toBe(25);
  });
});

describe('OrbitCamera component fields', () => {
  const eid = 2;

  for (const field of ORBIT_FIELDS) {
    it(`${field} typed array is accessible`, () => {
      expect(OrbitCamera[field]).toBeDefined();
      expect(
        Number.isFinite(OrbitCamera[field][eid]) ||
          OrbitCamera[field][eid] === 0
      ).toBe(true);
    });
  }
});

describe('orbit camera registry', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  it('getCameraControls returns undefined when unset', () => {
    expect(getCameraControls(state, 1)).toBeUndefined();
  });

  it('setCameraControls stores mock controls reference', () => {
    const fake = { dispose: () => {} } as import('camera-controls').default;
    setCameraControls(state, 5, fake);
    expect(getCameraControls(state, 5)).toBe(fake);
  });

  it('removeCameraControls disposes and clears', () => {
    let disposed = false;
    const fake = {
      dispose: () => {
        disposed = true;
      },
    } as import('camera-controls').default;
    setCameraControls(state, 7, fake);
    removeCameraControls(state, 7);
    expect(disposed).toBe(true);
    expect(getCameraControls(state, 7)).toBeUndefined();
  });

  for (const eid of [1, 2, 3, 4, 5]) {
    it(`registry isolates entity ${eid}`, () => {
      const fake = { dispose: () => {} } as import('camera-controls').default;
      setCameraControls(state, eid, fake);
      expect(getCameraControls(state, eid)).toBe(fake);
    });
  }
});

describe('OrbitCamera defaults numeric sanity', () => {
  const defaults = OrbitCameraPlugin.config?.defaults?.['orbit-camera']!;

  for (const field of [
    'minDistance',
    'maxDistance',
    'smoothness',
    'offsetY',
    'sensitivity',
    'zoomSensitivity',
  ] as const) {
    it(`${field} default is finite`, () => {
      expect(Number.isFinite(defaults[field])).toBe(true);
    });
  }

  it('minDistance is less than maxDistance', () => {
    expect(defaults.minDistance).toBeLessThan(defaults.maxDistance);
  });

  it('minPitch is less than maxPitch', () => {
    expect(defaults.minPitch).toBeLessThan(defaults.maxPitch);
  });
});

describe('OrbitCamera ECS query', () => {
  it('defineQuery finds orbit camera entities', () => {
    const state = new State();
    state.registerComponent('orbit-camera', OrbitCamera);
    const eid = state.createEntity();
    state.addComponent(eid, OrbitCamera);
    const q = defineQuery([OrbitCamera])(state.world);
    expect(q).toContain(eid);
  });
});

describe('orbit camera systems metadata', () => {
  for (const [name, system] of [
    ['OrbitCameraSetupSystem', OrbitCameraSetupSystem],
    ['OrbitCameraInputSystem', OrbitCameraInputSystem],
    ['OrbitCameraSystem', OrbitCameraSystem],
  ] as const) {
    it(`${name} has update`, () => {
      expect(typeof system.update).toBe('function');
    });
  }

  it('setup system is setup group', () => {
    expect(OrbitCameraSetupSystem.group).toBe('setup');
  });

  it('input system is simulation group', () => {
    expect(OrbitCameraInputSystem.group).toBe('simulation');
  });
});
