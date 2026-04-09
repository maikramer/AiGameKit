import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { SteeringAgent, SteeringTarget } from '../../../src/plugins/ai-steering/components';

const STEERING_AGENT_FIELDS = [
  'behavior',
  'maxSpeed',
  'maxForce',
  'active',
] as const;

const STEERING_TARGET_FIELDS = [
  'targetEntity',
  'targetX',
  'targetY',
  'targetZ',
] as const;

describe('SteeringAgent Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 4 fields defined', () => {
    for (const field of STEERING_AGENT_FIELDS) {
      expect(SteeringAgent[field]).toBeDefined();
      expect(typeof SteeringAgent[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, SteeringAgent);

    for (const field of STEERING_AGENT_FIELDS) {
      expect(SteeringAgent[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading all fields (roundtrip)', () => {
    state.addComponent(entity, SteeringAgent);
    SteeringAgent.behavior[entity] = 1;
    SteeringAgent.maxSpeed[entity] = 5.5;
    SteeringAgent.maxForce[entity] = 12.0;
    SteeringAgent.active[entity] = 1;

    expect(SteeringAgent.behavior[entity]).toBe(1);
    expect(SteeringAgent.maxSpeed[entity]).toBeCloseTo(5.5);
    expect(SteeringAgent.maxForce[entity]).toBeCloseTo(12.0);
    expect(SteeringAgent.active[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    const entity2 = state.createEntity();
    state.addComponent(entity, SteeringAgent);
    state.addComponent(entity2, SteeringAgent);

    SteeringAgent.behavior[entity] = 0;
    SteeringAgent.behavior[entity2] = 2;
    SteeringAgent.maxSpeed[entity] = 3;
    SteeringAgent.maxSpeed[entity2] = 7;

    expect(SteeringAgent.behavior[entity]).toBe(0);
    expect(SteeringAgent.behavior[entity2]).toBe(2);
    expect(SteeringAgent.maxSpeed[entity]).toBeCloseTo(3);
    expect(SteeringAgent.maxSpeed[entity2]).toBeCloseTo(7);
  });
});

describe('SteeringTarget Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all 4 fields defined', () => {
    for (const field of STEERING_TARGET_FIELDS) {
      expect(SteeringTarget[field]).toBeDefined();
      expect(typeof SteeringTarget[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, SteeringTarget);

    for (const field of STEERING_TARGET_FIELDS) {
      expect(SteeringTarget[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading all fields (roundtrip)', () => {
    state.addComponent(entity, SteeringTarget);
    SteeringTarget.targetEntity[entity] = 42;
    SteeringTarget.targetX[entity] = 10.0;
    SteeringTarget.targetY[entity] = 5.0;
    SteeringTarget.targetZ[entity] = -3.0;

    expect(SteeringTarget.targetEntity[entity]).toBe(42);
    expect(SteeringTarget.targetX[entity]).toBeCloseTo(10.0);
    expect(SteeringTarget.targetY[entity]).toBeCloseTo(5.0);
    expect(SteeringTarget.targetZ[entity]).toBeCloseTo(-3.0);
  });

  it('should support multiple entities with independent values', () => {
    const entity2 = state.createEntity();
    state.addComponent(entity, SteeringTarget);
    state.addComponent(entity2, SteeringTarget);

    SteeringTarget.targetX[entity] = 1;
    SteeringTarget.targetX[entity2] = 99;
    SteeringTarget.targetEntity[entity] = 5;
    SteeringTarget.targetEntity[entity2] = 10;

    expect(SteeringTarget.targetX[entity]).toBeCloseTo(1);
    expect(SteeringTarget.targetX[entity2]).toBeCloseTo(99);
    expect(SteeringTarget.targetEntity[entity]).toBe(5);
    expect(SteeringTarget.targetEntity[entity2]).toBe(10);
  });
});
