
import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';

const MAX_ENTITIES = 100000;

describe('State Recipe Methods', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  it('should create entity using state.createFromRecipe method', () => {
    const entity = state.createFromRecipe('GameObject');
    expect(entity).toBeGreaterThanOrEqual(0);
    expect(state.exists(entity)).toBe(true);
  });

  it('should create entity with attributes using state.createFromRecipe', () => {
    const Transform = { posX: new Float32Array(MAX_ENTITIES), posY: new Float32Array(MAX_ENTITIES), posZ: new Float32Array(MAX_ENTITIES) };

    state.registerComponent('transform', Transform);
    state.registerRecipe({
      name: 'test-entity',
      components: ['transform'],
    });

    const entity = state.createFromRecipe('test-entity', {
      pos: '10 20 30',
    });

    expect(state.hasComponent(entity, Transform)).toBe(true);
    expect(Transform.posX[entity]).toBe(10);
    expect(Transform.posY[entity]).toBe(20);
    expect(Transform.posZ[entity]).toBe(30);
  });

  it('should create entity with mixed attributes and overrides', () => {
    const Health = { current: new Float32Array(MAX_ENTITIES), max: new Float32Array(MAX_ENTITIES) };

    state.registerComponent('health', Health);
    state.registerRecipe({
      name: 'character',
      components: ['health'],
      overrides: {
        'health.max': 100,
      },
    });

    const entity = state.createFromRecipe('character', {
      health: 'current: 50',
    });

    expect(state.hasComponent(entity, Health)).toBe(true);
    expect(Health.current[entity]).toBe(50);
    expect(Health.max[entity]).toBe(100);
  });

  it('should throw error for unknown recipe', () => {
    expect(() => {
      state.createFromRecipe('unknown-recipe');
    }).toThrow(/Unknown element <unknown-recipe>/);
  });
});
