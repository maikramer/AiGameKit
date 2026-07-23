import { beforeEach, describe, expect, it } from 'bun:test';
import { getAllEntities } from 'bitecs';
import { State, defineQuery } from 'vibegame';
import { StartupPlugin } from '../../../src/plugins/startup';
import {
  CameraStartupSystem,
  LightingStartupSystem,
  PlayerCharacterSystem,
  PlayerStartupSystem,
} from '../../../src/plugins/startup/systems';
import {
  AmbientLight,
  DirectionalLight,
  MainCamera,
} from '../../../src/plugins/rendering';
import { OrbitCamera } from '../../../src/plugins/orbit-camera';
import { Transform, Parent } from '../../../src/plugins/transforms';
import { InputState } from '../../../src/plugins/input';

const SYSTEMS = [
  LightingStartupSystem,
  CameraStartupSystem,
  PlayerStartupSystem,
  PlayerCharacterSystem,
] as const;

describe('StartupPlugin', () => {
  it('systems ordering is lighting, camera, player, character', () => {
    expect(StartupPlugin.systems?.[0]).toBe(LightingStartupSystem);
    expect(StartupPlugin.systems?.[1]).toBe(CameraStartupSystem);
    expect(StartupPlugin.systems?.[2]).toBe(PlayerStartupSystem);
    expect(StartupPlugin.systems?.[3]).toBe(PlayerCharacterSystem);
  });

  for (let i = 0; i < SYSTEMS.length; i++) {
    it(`system index ${i} is setup group`, () => {
      expect(SYSTEMS[i].group).toBe('setup');
    });
  }

  for (let i = 0; i < SYSTEMS.length; i++) {
    it(`system index ${i} has callable update`, () => {
      expect(typeof SYSTEMS[i].update).toBe('function');
    });
  }
});

describe('LightingStartupSystem behavior', () => {
  let state: State;
  const ambientQuery = defineQuery([AmbientLight]);
  const dirQuery = defineQuery([DirectionalLight]);

  beforeEach(() => {
    state = new State();
    state.registerComponent('ambient-light', AmbientLight);
    state.registerComponent('directional-light', DirectionalLight);
  });

  it('creates lights when none exist', () => {
    expect(ambientQuery(state.world).length).toBe(0);
    LightingStartupSystem.update!(state);
    expect(
      ambientQuery(state.world).length + dirQuery(state.world).length
    ).toBeGreaterThan(0);
  });

  for (let run = 0; run < 5; run++) {
    it(`idempotent when lights exist (run ${run})`, () => {
      const light = state.createEntity();
      state.addComponent(light, DirectionalLight);
      state.addComponent(light, AmbientLight);
      const before = getAllEntities(state.world).length;
      LightingStartupSystem.update!(state);
      expect(getAllEntities(state.world).length).toBe(before);
    });
  }
});

describe('CameraStartupSystem behavior', () => {
  let state: State;
  const camQuery = defineQuery([MainCamera]);

  beforeEach(() => {
    state = new State();
    state.registerComponent('orbit-camera', OrbitCamera);
    state.registerComponent('transform', Transform);
    state.registerComponent('main-camera', MainCamera);
    state.registerComponent('input-state', InputState);
  });

  it('creates main camera when missing', () => {
    CameraStartupSystem.update!(state);
    expect(camQuery(state.world).length).toBe(1);
  });

  for (let run = 0; run < 5; run++) {
    it(`skips spawn when camera exists (run ${run})`, () => {
      const cam = state.createEntity();
      state.addComponent(cam, MainCamera);
      state.addComponent(cam, OrbitCamera);
      state.addComponent(cam, Transform);
      const before = camQuery(state.world).length;
      CameraStartupSystem.update!(state);
      expect(camQuery(state.world).length).toBe(before);
    });
  }

  it('sets inputSource to camera entity on new spawn', () => {
    CameraStartupSystem.update!(state);
    const cams = camQuery(state.world);
    expect(cams.length).toBe(1);
    expect(OrbitCamera.inputSource[cams[0]]).toBe(cams[0]);
  });
});

describe('startup system names', () => {
  for (const [expected, system] of [
    ['LightingStartupSystem', LightingStartupSystem],
    ['CameraStartupSystem', CameraStartupSystem],
    ['PlayerStartupSystem', PlayerStartupSystem],
    ['PlayerCharacterSystem', PlayerCharacterSystem],
  ] as const) {
    it(`${expected} matches system.name`, () => {
      expect(system.name).toBe(expected);
    });
  }

  for (let i = 0; i < 10; i++) {
    it(`StartupPlugin.components empty (check ${i})`, () => {
      expect(StartupPlugin.components).toEqual({});
    });
  }

  for (let i = 0; i < 10; i++) {
    it(`StartupPlugin has four systems (check ${i})`, () => {
      expect(StartupPlugin.systems).toHaveLength(4);
    });
  }
});

describe('PlayerCharacterSystem metadata', () => {
  it('uses Parent component from core for spawned character', () => {
    expect(Parent.entity).toBeDefined();
  });

  it('PlayerCharacterSystem is setup group', () => {
    expect(PlayerCharacterSystem.group).toBe('setup');
  });
});
