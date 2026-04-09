import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { ParticlesPlugin } from '../../../src/plugins/particles/plugin';
import { ParticlesEmitter, ParticlesBurst } from '../../../src/plugins/particles/components';

describe('ParticlesPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(ParticlesPlugin);
  });

  it('should have a recipe named "particle-emitter" with components ["transform", "particlesEmitter"]', () => {
    const emitterRecipe = ParticlesPlugin.recipes!.find((r) => r.name === 'particle-emitter');
    expect(emitterRecipe).toBeDefined();
    expect(emitterRecipe!.components).toEqual(['transform', 'particlesEmitter']);
  });

  it('should have a recipe named "particle-burst" with components ["transform", "particlesBurst"]', () => {
    const burstRecipe = ParticlesPlugin.recipes!.find((r) => r.name === 'particle-burst');
    expect(burstRecipe).toBeDefined();
    expect(burstRecipe!.components).toEqual(['transform', 'particlesBurst']);
  });

  it('should register the particlesEmitter component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, ParticlesEmitter);
    expect(state.hasComponent(entity, ParticlesEmitter)).toBe(true);
  });

  it('should register the particlesBurst component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, ParticlesBurst);
    expect(state.hasComponent(entity, ParticlesBurst)).toBe(true);
  });

  it('should register the particle-emitter recipe', () => {
    const recipe = state.getRecipe('particle-emitter');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('particlesEmitter');
  });

  it('should register the particle-burst recipe', () => {
    const recipe = state.getRecipe('particle-burst');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('particlesBurst');
  });

  it('should have five systems registered', () => {
    expect(ParticlesPlugin.systems).toHaveLength(5);
  });

  it('should have config.defaults for particlesEmitter', () => {
    const defaults = ParticlesPlugin.config!.defaults!.particlesEmitter;
    expect(defaults).toBeDefined();
    expect(defaults.preset).toBe(0);
    expect(defaults.rate).toBeCloseTo(20);
    expect(defaults.lifetime).toBeCloseTo(2);
    expect(defaults.size).toBeCloseTo(0.2);
    expect(defaults.looping).toBe(1);
    expect(defaults.playing).toBe(1);
    expect(defaults.spawned).toBe(0);
  });

  it('should have config.defaults for particlesBurst', () => {
    const defaults = ParticlesPlugin.config!.defaults!.particlesBurst;
    expect(defaults).toBeDefined();
    expect(defaults.preset).toBe(2);
    expect(defaults.count).toBeCloseTo(100);
    expect(defaults.triggered).toBe(0);
  });

  it('should have config.enums for particlesEmitter preset with unique values', () => {
    const enums = ParticlesPlugin.config!.enums!.particlesEmitter;
    expect(enums).toBeDefined();
    expect(enums.preset).toBeDefined();

    const presetValues = Object.values(enums.preset);
    const uniqueValues = new Set(presetValues);
    expect(uniqueValues.size).toBe(presetValues.length);

    expect(enums.preset.fire).toBe(0);
    expect(enums.preset.rain).toBe(4);
    expect(enums.preset.snow).toBe(5);
    expect(enums.preset.custom).toBe(99);
  });
});
