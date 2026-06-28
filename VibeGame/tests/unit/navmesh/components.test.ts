import { describe, expect, it } from 'bun:test';
import { NavMeshAgent, NavMeshSurface, NavMeshWalkable } from 'vibegame';

const MAX_ENTITIES = 100000;

describe('NavMeshSurface component', () => {
  it('exposes enabled/generated as MAX_ENTITIES Uint8Arrays, defaulting to enabled & not generated', () => {
    expect(NavMeshSurface.enabled).toBeInstanceOf(Uint8Array);
    expect(NavMeshSurface.generated).toBeInstanceOf(Uint8Array);
    expect(NavMeshSurface.enabled).toHaveLength(MAX_ENTITIES);
    expect(NavMeshSurface.generated).toHaveLength(MAX_ENTITIES);
    expect(NavMeshSurface.enabled[0]).toBe(1);
    expect(NavMeshSurface.generated[0]).toBe(0);
  });
});

describe('NavMeshWalkable component', () => {
  it('is a single enabled flag, on by default for every entity', () => {
    expect(NavMeshWalkable.enabled).toBeInstanceOf(Uint8Array);
    expect(NavMeshWalkable.enabled).toHaveLength(MAX_ENTITIES);
    expect(NavMeshWalkable.enabled[0]).toBe(1);
    expect(NavMeshWalkable.enabled[MAX_ENTITIES - 1]).toBe(1);
  });
});

describe('NavMeshAgent component', () => {
  it('stores agentIndex as a MAX_ENTITIES Int32Array and marks unregistered agents -1', () => {
    expect(NavMeshAgent.agentIndex).toBeInstanceOf(Int32Array);
    expect(NavMeshAgent.agentIndex).toHaveLength(MAX_ENTITIES);
    expect(NavMeshAgent.agentIndex[0]).toBe(-1);
    expect(NavMeshAgent.agentIndex[MAX_ENTITIES - 1]).toBe(-1);
  });

  it('pre-fills body-shape defaults (radius 0.4, height 1.0, enabled)', () => {
    expect(NavMeshAgent.radius).toBeInstanceOf(Float32Array);
    expect(NavMeshAgent.height).toBeInstanceOf(Float32Array);
    expect(NavMeshAgent.radius[0]).toBeCloseTo(0.4, 5);
    expect(NavMeshAgent.height[0]).toBeCloseTo(1.0, 5);
    expect(NavMeshAgent.enabled[0]).toBe(1);
  });

  it('initializes movement state to zeroed arrays', () => {
    expect(NavMeshAgent.speed).toBeInstanceOf(Float32Array);
    expect(NavMeshAgent.targetX).toBeInstanceOf(Float32Array);
    expect(NavMeshAgent.targetY).toBeInstanceOf(Float32Array);
    expect(NavMeshAgent.targetZ).toBeInstanceOf(Float32Array);
    expect(NavMeshAgent.hasTarget).toBeInstanceOf(Uint8Array);
    expect(NavMeshAgent.speed[0]).toBe(0);
    expect(NavMeshAgent.targetX[0]).toBe(0);
    expect(NavMeshAgent.hasTarget[0]).toBe(0);
  });
});
