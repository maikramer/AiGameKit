import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  YukaAgentComponent,
  YUKA_BEHAVIOR_FLEE,
  YUKA_BEHAVIOR_SEEK,
  YUKA_BEHAVIOR_WANDER,
} from '../../../src/plugins/ai-yuka/components';

const FIELDS = [
  'active',
  'behavior',
  'maxSpeed',
  'maxForce',
  'targetEid',
  'faction',
  'targetX',
  'targetZ',
] as const;

describe('YukaAgentComponent', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('exposes SoA fields', () => {
    for (const field of FIELDS) {
      expect(YukaAgentComponent[field]).toBeDefined();
      expect(typeof YukaAgentComponent[field][entity]).toBe('number');
    }
  });

  it('starts at zero before plugin defaults are applied', () => {
    state.addComponent(entity, YukaAgentComponent);
    for (const field of FIELDS) {
      expect(YukaAgentComponent[field][entity]).toBe(0);
    }
  });

  it('allows writing and reading fields', () => {
    state.addComponent(entity, YukaAgentComponent);
    YukaAgentComponent.behavior[entity] = YUKA_BEHAVIOR_WANDER;
    YukaAgentComponent.maxSpeed[entity] = 5.5;
    YukaAgentComponent.maxForce[entity] = 12.0;
    YukaAgentComponent.active[entity] = 1;
    YukaAgentComponent.targetX[entity] = 10;
    YukaAgentComponent.targetZ[entity] = -3;

    expect(YukaAgentComponent.behavior[entity]).toBe(YUKA_BEHAVIOR_WANDER);
    expect(YukaAgentComponent.maxSpeed[entity]).toBeCloseTo(5.5);
    expect(YukaAgentComponent.maxForce[entity]).toBeCloseTo(12.0);
    expect(YukaAgentComponent.active[entity]).toBe(1);
    expect(YukaAgentComponent.targetX[entity]).toBeCloseTo(10);
    expect(YukaAgentComponent.targetZ[entity]).toBeCloseTo(-3);
  });

  it('keeps per-entity values independent', () => {
    const entity2 = state.createEntity();
    state.addComponent(entity, YukaAgentComponent);
    state.addComponent(entity2, YukaAgentComponent);

    YukaAgentComponent.behavior[entity] = YUKA_BEHAVIOR_SEEK;
    YukaAgentComponent.behavior[entity2] = YUKA_BEHAVIOR_FLEE;
    YukaAgentComponent.maxSpeed[entity] = 3;
    YukaAgentComponent.maxSpeed[entity2] = 7;

    expect(YukaAgentComponent.behavior[entity]).toBe(YUKA_BEHAVIOR_SEEK);
    expect(YukaAgentComponent.behavior[entity2]).toBe(YUKA_BEHAVIOR_FLEE);
    expect(YukaAgentComponent.maxSpeed[entity]).toBeCloseTo(3);
    expect(YukaAgentComponent.maxSpeed[entity2]).toBeCloseTo(7);
  });
});
