/**
 * E2E: Fog plugin lifecycle — register, create entity, run systems, remove, cleanup.
 * All tests run headless (no GPU/WebGL required).
 */
import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS, defineQuery, addComponent, addEntity, removeEntity } from 'vibegame';
import { createHeadlessState } from 'vibegame/cli';
import { DefaultPlugins } from 'vibegame/defaults';
import { FogPlugin } from '../../../src/plugins/fog/plugin';
import { Fog } from '../../../src/plugins/fog/components';

// Polyfill browser APIs for Bun (GSAP uses these internally)
globalThis.requestAnimationFrame = ((cb: any) => setTimeout(cb, 16)) as any;
globalThis.cancelAnimationFrame = clearTimeout as any;

describe('E2E: Fog Plugin Lifecycle', () => {
  let state: State;

  beforeEach(async () => {
    state = createHeadlessState({ plugins: DefaultPlugins });
    await state.initializePlugins();
  });

  it('should register FogPlugin with recipes, components, and systems', () => {
    expect(state.hasRecipe('fog')).toBe(true);

    const fogComponent = state.getComponent('fog');
    expect(fogComponent).toBeDefined();

    const systemNames = Array.from(state.systems).map((s) => s.name || String(s));
    expect(state.systems.size).toBeGreaterThan(0);
  });

  it('should create a fog entity and apply plugin defaults', () => {
    const entity = addEntity(state.world);
    addComponent(state.world, Fog, entity);

    const defaults = FogPlugin.config!.defaults!.fog;
    Fog.mode[entity] = defaults.mode;
    Fog.density[entity] = defaults.density;
    Fog.near[entity] = defaults.near;
    Fog.far[entity] = defaults.far;
    Fog.colorR[entity] = defaults.colorR;
    Fog.colorG[entity] = defaults.colorG;
    Fog.colorB[entity] = defaults.colorB;
    Fog.heightFalloff[entity] = defaults.heightFalloff;
    Fog.baseHeight[entity] = defaults.baseHeight;
    Fog.volumetricStrength[entity] = defaults.volumetricStrength;
    Fog.quality[entity] = defaults.quality;
    Fog.noiseScale[entity] = defaults.noiseScale;

    expect(Fog.mode[entity]).toBe(0);
    expect(Fog.density[entity]).toBeCloseTo(0.015);
    expect(Fog.near[entity]).toBe(1);
    expect(Fog.far[entity]).toBe(1000);
    expect(Fog.colorR[entity]).toBeCloseTo(0.533);
    expect(Fog.quality[entity]).toBe(1);
  });

  it('should run simulation steps with fog entity without errors', () => {
    const entity = addEntity(state.world);
    addComponent(state.world, Fog, entity);
    Fog.mode[entity] = 0;
    Fog.density[entity] = 0.02;
    Fog.colorR[entity] = 0.5;
    Fog.colorG[entity] = 0.5;
    Fog.colorB[entity] = 0.5;

    expect(() => {
      for (let i = 0; i < 50; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }
    }).not.toThrow();
  });

  it('should query fog entities via defineQuery', () => {
    const fogQuery = defineQuery([Fog]);

    const e1 = addEntity(state.world);
    addComponent(state.world, Fog, e1);
    Fog.mode[e1] = 2;
    Fog.density[e1] = 0.05;
    Fog.near[e1] = 5;
    Fog.far[e1] = 200;

    const e2 = addEntity(state.world);
    addComponent(state.world, Fog, e2);
    Fog.mode[e2] = 0;
    Fog.density[e2] = 0.01;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const fogEntities = fogQuery(state.world);
    expect(fogEntities.length).toBeGreaterThanOrEqual(2);

    const modes = fogEntities.map((e: number) => Fog.mode[e]);
    expect(modes).toContain(0);
    expect(modes).toContain(2);
  });

  it('should remove fog entity and have zero fog entities in query', () => {
    const fogQuery = defineQuery([Fog]);

    const entity = addEntity(state.world);
    addComponent(state.world, Fog, entity);
    Fog.mode[entity] = 0;
    Fog.density[entity] = 0.02;

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    let fogEntities = fogQuery(state.world);
    expect(fogEntities.length).toBeGreaterThanOrEqual(1);

    removeEntity(state.world, entity);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    fogEntities = fogQuery(state.world);
    expect(fogEntities).not.toContain(entity);
  });

  it('should handle multiple fog entities with different modes', () => {
    const fogQuery = defineQuery([Fog]);

    const e1 = addEntity(state.world);
    addComponent(state.world, Fog, e1);
    Fog.mode[e1] = 0;
    Fog.density[e1] = 0.03;

    const e2 = addEntity(state.world);
    addComponent(state.world, Fog, e2);
    Fog.mode[e2] = 2;
    Fog.near[e2] = 10;
    Fog.far[e2] = 500;

    for (let i = 0; i < 10; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    const fogEntities = fogQuery(state.world);
    expect(fogEntities.length).toBeGreaterThanOrEqual(2);
  });

  it('should run 100 steps with fog entity without degradation', () => {
    const entity = addEntity(state.world);
    addComponent(state.world, Fog, entity);
    Fog.mode[entity] = 0;
    Fog.density[entity] = 0.02;
    Fog.colorR[entity] = 0.5;
    Fog.colorG[entity] = 0.6;
    Fog.colorB[entity] = 0.7;

    const initialDensity = Fog.density[entity];

    for (let i = 0; i < 100; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Fog.density[entity]).toBeCloseTo(initialDensity);
    expect(Fog.mode[entity]).toBe(0);
    expect(Fog.colorR[entity]).toBeCloseTo(0.5);
  });

  it('should not crash with no fog entities present', () => {
    expect(() => {
      for (let i = 0; i < 20; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }
    }).not.toThrow();
  });
});
