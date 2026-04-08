import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { FogPlugin } from '../../../src/plugins/fog/plugin';
import { Fog } from '../../../src/plugins/fog/components';

describe('FogPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(FogPlugin);
  });

  it('should have a recipe named "fog" with components ["fog"]', () => {
    expect(FogPlugin.recipes).toHaveLength(1);
    expect(FogPlugin.recipes[0].name).toBe('fog');
    expect(FogPlugin.recipes[0].components).toEqual(['fog']);
  });

  it('should register the fog component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, Fog);
    expect(state.hasComponent(entity, Fog)).toBe(true);
  });

  it('should register the fog recipe', () => {
    const recipe = state.getRecipe('fog');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('fog');
  });

  it('should have two systems registered (FogSystem + FogEffectSystem)', () => {
    expect(FogPlugin.systems).toHaveLength(2);
  });

  it('should have config.defaults with all 12 fog fields', () => {
    const defaults = FogPlugin.config.defaults.fog;
    expect(defaults).toBeDefined();
    expect(defaults.mode).toBe(0);
    expect(defaults.density).toBeCloseTo(0.015);
    expect(defaults.near).toBe(1);
    expect(defaults.far).toBe(1000);
    expect(defaults.colorR).toBeCloseTo(0.533);
    expect(defaults.colorG).toBeCloseTo(0.6);
    expect(defaults.colorB).toBeCloseTo(0.667);
    expect(defaults.heightFalloff).toBeCloseTo(1.0);
    expect(defaults.baseHeight).toBe(0);
    expect(defaults.volumetricStrength).toBeCloseTo(0.5);
    expect(defaults.quality).toBe(1);
    expect(defaults.noiseScale).toBeCloseTo(1.0);
  });

  it('should have config.enums for fog', () => {
    const enums = FogPlugin.config.enums.fog;
    expect(enums).toBeDefined();
    expect(enums.mode).toBeDefined();
    expect(enums.quality).toBeDefined();
  });
});
