import { beforeEach, describe, expect, it } from 'bun:test';
import { State, Transform, Rigidbody } from 'aigamekit-vibegame';
import {
  SpawnGateComponent,
  SpawnGatePlugin,
  spawnGateRecipe,
  gateEntity,
} from '../../../src/plugins/spawn-gate';

const GATE_FIELDS = ['ready', 'yOffset', 'skinDistance'] as const;

describe('SpawnGateComponent buffers', () => {
  for (const field of GATE_FIELDS) {
    it(`${field} array exists`, () => {
      expect(SpawnGateComponent[field]).toBeDefined();
      expect(SpawnGateComponent[field].length).toBeGreaterThan(1);
    });
  }

  it('ready defaults to 0 at fresh index', () => {
    expect(SpawnGateComponent.ready[500]).toBe(0);
  });
});

describe('spawnGateRecipe', () => {
  it('name is SpawnGate', () => {
    expect(spawnGateRecipe.name).toBe('SpawnGate');
  });

  it('has no default components on recipe entity', () => {
    expect(spawnGateRecipe.components).toEqual([]);
  });

  for (const attr of [
    'target-entity',
    'y-fallback',
    'skin-distance',
  ] as const) {
    it(`parserAttributes includes ${attr}`, () => {
      expect(spawnGateRecipe.parserAttributes).toContain(attr);
    });
  }
});

describe('SpawnGatePlugin', () => {
  it('registers spawn-gate component', () => {
    expect(SpawnGatePlugin.components?.['spawn-gate']).toBe(SpawnGateComponent);
  });

  it('includes spawn gate recipe', () => {
    expect(SpawnGatePlugin.recipes).toContain(spawnGateRecipe);
  });

  it('registers SpawnGate parser', () => {
    expect(SpawnGatePlugin.config?.parsers?.SpawnGate).toBeDefined();
  });

  for (const field of GATE_FIELDS) {
    it(`defaults include spawn-gate.${field}`, () => {
      const defaults = SpawnGatePlugin.config?.defaults?.['spawn-gate'];
      expect(defaults).toBeDefined();
      expect(Object.prototype.hasOwnProperty.call(defaults, field)).toBe(true);
    });
  }

  it('default skinDistance is 0.05', () => {
    expect(SpawnGatePlugin.config?.defaults?.['spawn-gate']?.skinDistance).toBe(
      0.05
    );
  });
});

describe('gateEntity helper', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('spawn-gate', SpawnGateComponent);
    state.registerComponent('rigidbody', Rigidbody);
  });

  it('marks ready as 0', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    Transform.posY[eid] = 12;
    gateEntity(state, eid);
    expect(SpawnGateComponent.ready[eid]).toBe(0);
  });

  it('uses yFallback when provided', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    Transform.posY[eid] = 12;
    gateEntity(state, eid, { yFallback: 99 });
    expect(SpawnGateComponent.yOffset[eid]).toBe(99);
  });

  it('uses transform Y when yFallback omitted', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    Transform.posY[eid] = 33;
    gateEntity(state, eid);
    expect(SpawnGateComponent.yOffset[eid]).toBe(33);
  });

  for (const skin of [0.01, 0.05, 0.1, 0.25]) {
    it(`accepts skinDistance ${skin}`, () => {
      const eid = state.createEntity();
      state.addComponent(eid, Transform);
      gateEntity(state, eid, { skinDistance: skin });
      expect(SpawnGateComponent.skinDistance[eid]).toBeCloseTo(skin, 5);
    });
  }

  for (const y of [0, 10, 50, 100, -5]) {
    it(`stores yFallback ${y}`, () => {
      const eid = state.createEntity();
      state.addComponent(eid, Transform);
      gateEntity(state, eid, { yFallback: y });
      expect(SpawnGateComponent.yOffset[eid]).toBe(y);
    });
  }
});
