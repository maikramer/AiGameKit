import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { NavMeshSurface, NavMeshAgent } from '../../../src/plugins/navmesh/components';

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
      expect(NavMeshSurface[field]).toBeDefined();
      expect(typeof NavMeshSurface[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, NavMeshSurface);

    for (const field of NAVMESH_FIELDS) {
      expect(NavMeshSurface[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading all fields (roundtrip)', () => {
    state.addComponent(entity, NavMeshSurface);
    NavMeshSurface.loaded[entity] = 1;
    NavMeshSurface.buildFromScene[entity] = 1;

    expect(NavMeshSurface.loaded[entity]).toBe(1);
    expect(NavMeshSurface.buildFromScene[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    const entity2 = state.createEntity();
    state.addComponent(entity, NavMeshSurface);
    state.addComponent(entity2, NavMeshSurface);

    NavMeshSurface.loaded[entity] = 1;
    NavMeshSurface.loaded[entity2] = 0;
    NavMeshSurface.buildFromScene[entity] = 0;
    NavMeshSurface.buildFromScene[entity2] = 1;

    expect(NavMeshSurface.loaded[entity]).toBe(1);
    expect(NavMeshSurface.loaded[entity2]).toBe(0);
    expect(NavMeshSurface.buildFromScene[entity]).toBe(0);
    expect(NavMeshSurface.buildFromScene[entity2]).toBe(1);
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
      expect(NavMeshAgent[field]).toBeDefined();
      expect(typeof NavMeshAgent[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, NavMeshAgent);

    for (const field of NAVAGENT_FIELDS) {
      expect(NavMeshAgent[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading all fields (roundtrip)', () => {
    state.addComponent(entity, NavMeshAgent);
    NavMeshAgent.targetX[entity] = 10.0;
    NavMeshAgent.targetY[entity] = 0.0;
    NavMeshAgent.targetZ[entity] = -5.0;
    NavMeshAgent.speed[entity] = 4.5;
    NavMeshAgent.tolerance[entity] = 0.35;
    NavMeshAgent.status[entity] = 1;

    expect(NavMeshAgent.targetX[entity]).toBeCloseTo(10.0);
    expect(NavMeshAgent.targetY[entity]).toBeCloseTo(0.0);
    expect(NavMeshAgent.targetZ[entity]).toBeCloseTo(-5.0);
    expect(NavMeshAgent.speed[entity]).toBeCloseTo(4.5);
    expect(NavMeshAgent.tolerance[entity]).toBeCloseTo(0.35);
    expect(NavMeshAgent.status[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    const entity2 = state.createEntity();
    state.addComponent(entity, NavMeshAgent);
    state.addComponent(entity2, NavMeshAgent);

    NavMeshAgent.targetX[entity] = 1;
    NavMeshAgent.targetX[entity2] = 99;
    NavMeshAgent.speed[entity] = 3;
    NavMeshAgent.speed[entity2] = 7;
    NavMeshAgent.status[entity] = 0;
    NavMeshAgent.status[entity2] = 2;

    expect(NavMeshAgent.targetX[entity]).toBeCloseTo(1);
    expect(NavMeshAgent.targetX[entity2]).toBeCloseTo(99);
    expect(NavMeshAgent.speed[entity]).toBeCloseTo(3);
    expect(NavMeshAgent.speed[entity2]).toBeCloseTo(7);
    expect(NavMeshAgent.status[entity]).toBe(0);
    expect(NavMeshAgent.status[entity2]).toBe(2);
  });
});
