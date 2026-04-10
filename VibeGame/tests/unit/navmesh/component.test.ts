import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { NavMesh, NavAgent } from '../../../src/plugins/navmesh/components';

const NAVMESH_FIELDS = ['loaded', 'buildFromScene'] as const;

const NAVAGENT_FIELDS = [
  'targetX',
  'targetY',
  'targetZ',
  'speed',
  'tolerance',
  'status',
] as const;

describe('NavMesh Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 2 fields defined', () => {
    for (const field of NAVMESH_FIELDS) {
      expect(NavMesh[field]).toBeDefined();
      expect(typeof NavMesh[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, NavMesh);

    for (const field of NAVMESH_FIELDS) {
      expect(NavMesh[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading all fields (roundtrip)', () => {
    state.addComponent(entity, NavMesh);
    NavMesh.loaded[entity] = 1;
    NavMesh.buildFromScene[entity] = 1;

    expect(NavMesh.loaded[entity]).toBe(1);
    expect(NavMesh.buildFromScene[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    const entity2 = state.createEntity();
    state.addComponent(entity, NavMesh);
    state.addComponent(entity2, NavMesh);

    NavMesh.loaded[entity] = 1;
    NavMesh.loaded[entity2] = 0;
    NavMesh.buildFromScene[entity] = 0;
    NavMesh.buildFromScene[entity2] = 1;

    expect(NavMesh.loaded[entity]).toBe(1);
    expect(NavMesh.loaded[entity2]).toBe(0);
    expect(NavMesh.buildFromScene[entity]).toBe(0);
    expect(NavMesh.buildFromScene[entity2]).toBe(1);
  });
});

describe('NavAgent Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 6 fields defined', () => {
    for (const field of NAVAGENT_FIELDS) {
      expect(NavAgent[field]).toBeDefined();
      expect(typeof NavAgent[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, NavAgent);

    for (const field of NAVAGENT_FIELDS) {
      expect(NavAgent[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading all fields (roundtrip)', () => {
    state.addComponent(entity, NavAgent);
    NavAgent.targetX[entity] = 10.0;
    NavAgent.targetY[entity] = 0.0;
    NavAgent.targetZ[entity] = -5.0;
    NavAgent.speed[entity] = 4.5;
    NavAgent.tolerance[entity] = 0.35;
    NavAgent.status[entity] = 1;

    expect(NavAgent.targetX[entity]).toBeCloseTo(10.0);
    expect(NavAgent.targetY[entity]).toBeCloseTo(0.0);
    expect(NavAgent.targetZ[entity]).toBeCloseTo(-5.0);
    expect(NavAgent.speed[entity]).toBeCloseTo(4.5);
    expect(NavAgent.tolerance[entity]).toBeCloseTo(0.35);
    expect(NavAgent.status[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    const entity2 = state.createEntity();
    state.addComponent(entity, NavAgent);
    state.addComponent(entity2, NavAgent);

    NavAgent.targetX[entity] = 1;
    NavAgent.targetX[entity2] = 99;
    NavAgent.speed[entity] = 3;
    NavAgent.speed[entity2] = 7;
    NavAgent.status[entity] = 0;
    NavAgent.status[entity2] = 2;

    expect(NavAgent.targetX[entity]).toBeCloseTo(1);
    expect(NavAgent.targetX[entity2]).toBeCloseTo(99);
    expect(NavAgent.speed[entity]).toBeCloseTo(3);
    expect(NavAgent.speed[entity2]).toBeCloseTo(7);
    expect(NavAgent.status[entity]).toBe(0);
    expect(NavAgent.status[entity2]).toBe(2);
  });
});
