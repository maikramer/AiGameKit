import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { AiSteeringPlugin } from '../../../src/plugins/ai-steering/plugin';
import { SteeringAgent, SteeringTarget } from '../../../src/plugins/ai-steering/components';

describe('AiSteeringPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(AiSteeringPlugin);
  });

  it('should have a recipe named "npc" with components ["transform", "steeringAgent", "steeringTarget"]', () => {
    expect(AiSteeringPlugin.recipes!).toHaveLength(1);
    expect(AiSteeringPlugin.recipes![0].name).toBe('npc');
    expect(AiSteeringPlugin.recipes![0].components).toEqual(['transform', 'steeringAgent', 'steeringTarget']);
  });

  it('should register the steeringAgent component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, SteeringAgent);
    expect(state.hasComponent(entity, SteeringAgent)).toBe(true);
  });

  it('should register the steeringTarget component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, SteeringTarget);
    expect(state.hasComponent(entity, SteeringTarget)).toBe(true);
  });

  it('should register the npc recipe', () => {
    const recipe = state.getRecipe('npc');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('steeringAgent');
    expect(recipe?.components).toContain('steeringTarget');
  });

  it('should have one system registered (SteeringSyncSystem)', () => {
    expect(AiSteeringPlugin.systems).toHaveLength(1);
  });

  it('should have config.defaults for steeringAgent', () => {
    const defaults = AiSteeringPlugin.config!.defaults!.steeringAgent;
    expect(defaults).toBeDefined();
    expect(defaults.behavior).toBe(0);
    expect(defaults.maxSpeed).toBeCloseTo(3);
    expect(defaults.maxForce).toBeCloseTo(10);
    expect(defaults.active).toBe(1);
  });

  it('should have config.defaults for steeringTarget', () => {
    const defaults = AiSteeringPlugin.config!.defaults!.steeringTarget;
    expect(defaults).toBeDefined();
    expect(defaults.targetEntity).toBe(0);
    expect(defaults.targetX).toBe(0);
    expect(defaults.targetY).toBe(0);
    expect(defaults.targetZ).toBe(0);
  });

  it('should have config.enums for steeringAgent behavior', () => {
    const enums = AiSteeringPlugin.config!.enums!.steeringAgent;
    expect(enums).toBeDefined();
    expect(enums.behavior).toBeDefined();
    expect(enums.behavior.seek).toBe(0);
    expect(enums.behavior.wander).toBe(1);
    expect(enums.behavior.flee).toBe(2);
  });
});
