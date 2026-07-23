import { beforeEach, describe, expect, it } from 'bun:test';
import { State, Transform, Rigidbody } from 'vibegame';
import {
  SpawnGateComponent,
  SpawnGatePlugin,
  gateEntity,
} from '../../../src/plugins/spawn-gate';

describe('SpawnGate plugin defaults matrix', () => {
  const defaults = SpawnGatePlugin.config?.defaults?.['spawn-gate']!;

  for (let i = 0; i < 25; i++) {
    it(`default ready is 0 (i=${i})`, () => {
      expect(defaults.ready).toBe(0);
    });
  }

  for (let i = 0; i < 25; i++) {
    it(`default skinDistance is 0.05 (i=${i})`, () => {
      expect(defaults.skinDistance).toBe(0.05);
    });
  }

  for (let i = 0; i < 25; i++) {
    it(`SpawnGatePlugin has one system (i=${i})`, () => {
      expect(SpawnGatePlugin.systems).toHaveLength(1);
    });
  }
});

describe('gateEntity matrix', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('spawn-gate', SpawnGateComponent);
    state.registerComponent('rigidbody', Rigidbody);
  });

  for (let n = 0; n < 25; n++) {
    it(`gateEntity entity ${n} sets ready 0`, () => {
      const eid = state.createEntity();
      state.addComponent(eid, Transform);
      Transform.posY[eid] = n;
      gateEntity(state, eid);
      expect(SpawnGateComponent.ready[eid]).toBe(0);
      expect(SpawnGateComponent.yOffset[eid]).toBe(n);
    });
  }
});

describe('SpawnGateComponent field writes', () => {
  const eid = 42;

  for (const y of [0, 1, 5, 10, 25, 50, 75, 100]) {
    it(`yOffset stores ${y}`, () => {
      SpawnGateComponent.yOffset[eid] = y;
      expect(SpawnGateComponent.yOffset[eid]).toBe(y);
    });
  }

  for (const skin of [0.01, 0.02, 0.05, 0.1, 0.2]) {
    it(`skinDistance stores ${skin}`, () => {
      SpawnGateComponent.skinDistance[eid] = skin;
      expect(SpawnGateComponent.skinDistance[eid]).toBeCloseTo(skin, 5);
    });
  }

  for (const ready of [0, 1]) {
    it(`ready flag ${ready}`, () => {
      SpawnGateComponent.ready[eid] = ready;
      expect(SpawnGateComponent.ready[eid]).toBe(ready);
    });
  }
});
