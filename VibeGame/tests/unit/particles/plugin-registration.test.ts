import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { ParticlesPlugin } from '../../../src/plugins/particles/plugin';
import {
  ParticleSystem,
  ParticleBurst,
} from '../../../src/plugins/particles/components';

describe('ParticlesPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(ParticlesPlugin);
  });

  it('should have a recipe named "ParticleSystem" with components ["transform", "particleSystem"]', () => {
    const emitterRecipe = ParticlesPlugin.recipes!.find(
      (r) => r.name === 'ParticleSystem'
    );
    expect(emitterRecipe).toBeDefined();
    expect(emitterRecipe!.components).toEqual(['transform', 'particleSystem']);
  });

  it('should have a recipe named "ParticleBurst" with components ["transform", "particleBurst"]', () => {
    const burstRecipe = ParticlesPlugin.recipes!.find(
      (r) => r.name === 'ParticleBurst'
    );
    expect(burstRecipe).toBeDefined();
    expect(burstRecipe!.components).toEqual(['transform', 'particleBurst']);
  });

  it('should register the particlesEmitter component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, ParticleSystem);
    expect(state.hasComponent(entity, ParticleSystem)).toBe(true);
  });

  it('should register the particlesBurst component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, ParticleBurst);
    expect(state.hasComponent(entity, ParticleBurst)).toBe(true);
  });

  it('should register the particle-emitter recipe', () => {
    const recipe = state.getRecipe('ParticleSystem');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('particleSystem');
  });

  it('should register the particle-burst recipe', () => {
    const recipe = state.getRecipe('ParticleBurst');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('particleBurst');
  });

  it('should have five systems registered', () => {
    expect(ParticlesPlugin.systems).toHaveLength(5);
  });

  it('should have config.defaults for particlesEmitter', () => {
    const defaults = ParticlesPlugin.config!.defaults!.particleSystem;
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
    const defaults = ParticlesPlugin.config!.defaults!.particleBurst;
    expect(defaults).toBeDefined();
    expect(defaults.preset).toBe(2);
    expect(defaults.count).toBeCloseTo(100);
    expect(defaults.triggered).toBe(0);
  });

  it('should have config.enums for particlesEmitter preset with unique values', () => {
    const enums = ParticlesPlugin.config!.enums!.particleSystem;
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
