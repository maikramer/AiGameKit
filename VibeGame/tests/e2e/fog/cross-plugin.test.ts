/**
 * E2E: Fog + Water cross-plugin coexistence.
 * Verifies both plugins register, parse, and run without conflicts.
 * Headless only, no GPU/WebGL.
 */
import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS, defineQuery } from 'vibegame';
import { createHeadlessState, parseWorldXml } from 'vibegame/cli';
import { DefaultPlugins } from 'vibegame/defaults';
import { Fog } from '../../../src/plugins/fog/components';
import { Water } from '../../../src/plugins/water/components';

globalThis.requestAnimationFrame = ((cb: any) => setTimeout(cb, 16)) as any;
globalThis.cancelAnimationFrame = clearTimeout as any;

describe('E2E: Fog + Water Cross-Plugin', () => {
  let state: State;

  beforeEach(async () => {
    state = createHeadlessState({ plugins: DefaultPlugins });
    await state.initializePlugins();
  });

  it('should register both FogPlugin and WaterPlugin', () => {
    expect(state.hasRecipe('fog')).toBe(true);
    expect(state.hasRecipe('water')).toBe(true);
    expect(state.getComponent('fog')).toBeDefined();
    expect(state.getComponent('water')).toBeDefined();
  });

  it('should parse fog and water elements together from XML', () => {
    parseWorldXml(
      state,
      `
      <static-part
        body="pos: 0 -0.5 0"
        renderer="shape: box; size: 30 1 30; color: 0x90ee90"
        collider="shape: box; size: 30 1 30" />
      <water size="128" water-level="5" />
      <fog mode="exponential" density="0.02" color="#8899aa" />
    `
    );

    const fogEntities = defineQuery([Fog])(state.world);
    const waterEntities = defineQuery([Water])(state.world);

    expect(fogEntities.length).toBeGreaterThanOrEqual(1);
    expect(waterEntities.length).toBeGreaterThanOrEqual(1);

    const fogEid = fogEntities[0];
    expect(Fog.mode[fogEid]).toBe(0);
    expect(Fog.density[fogEid]).toBeCloseTo(0.02);

    const waterEid = waterEntities[0];
    expect(Water.size[waterEid]).toBe(128);
    expect(Water.waterLevel[waterEid]).toBe(5);
  });

  it('should run simulation with both fog and water without errors', () => {
    parseWorldXml(
      state,
      `
      <static-part
        body="pos: 0 -10 0"
        collider="shape: box; size: 50 1 50" />
      <water size="64" water-level="3" />
      <fog mode="linear" near="1" far="100" />
    `
    );

    expect(() => {
      for (let i = 0; i < 60; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }
    }).not.toThrow();

    const fogEntities = defineQuery([Fog])(state.world);
    const waterEntities = defineQuery([Water])(state.world);

    expect(fogEntities.length).toBeGreaterThanOrEqual(1);
    expect(waterEntities.length).toBeGreaterThanOrEqual(1);
  });

  it('should keep fog and water component data independent', () => {
    parseWorldXml(
      state,
      `
      <water size="256" water-level="8" tint-r="0.1" tint-g="0.3" tint-b="0.5" />
      <fog mode="exponential-squared" density="0.05" />
    `
    );

    const fogEid = defineQuery([Fog])(state.world)[0];
    const waterEid = defineQuery([Water])(state.world)[0];

    expect(Fog.mode[fogEid]).toBe(1);
    expect(Fog.density[fogEid]).toBeCloseTo(0.05);

    expect(Water.size[waterEid]).toBe(256);
    expect(Water.waterLevel[waterEid]).toBe(8);
    expect(Water.tintR[waterEid]).toBeCloseTo(0.1);
    expect(Water.tintG[waterEid]).toBeCloseTo(0.3);
    expect(Water.tintB[waterEid]).toBeCloseTo(0.5);

    for (let i = 0; i < 20; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Fog.mode[fogEid]).toBe(1);
    expect(Fog.density[fogEid]).toBeCloseTo(0.05);
    expect(Water.waterLevel[waterEid]).toBe(8);
    expect(Water.tintR[waterEid]).toBeCloseTo(0.1);
  });

  it('should not contaminate fog color with water tint', () => {
    parseWorldXml(
      state,
      `
      <water size="64" water-level="5" tint-r="0.9" tint-g="0.1" tint-b="0.2" />
      <fog density="0.03" color="#aabbcc" />
    `
    );

    const fogEid = defineQuery([Fog])(state.world)[0];
    const waterEid = defineQuery([Water])(state.world)[0];

    expect(Fog.colorR[fogEid]).not.toBeCloseTo(Water.tintR[waterEid]);
    expect(Fog.colorG[fogEid]).not.toBeCloseTo(Water.tintG[waterEid]);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    expect(Fog.colorR[fogEid]).not.toBeCloseTo(Water.tintR[waterEid]);
  });

  it('should not contaminate water underwater fog with scene fog', () => {
    parseWorldXml(
      state,
      `
      <water size="64" water-level="5"
             underwater-fog-color-r="0.1" underwater-fog-color-g="0.05" underwater-fog-color-b="0.15"
             underwater-fog-density="0.2" />
      <fog mode="exponential" density="0.04" color="#ffeedd" />
    `
    );

    const fogEid = defineQuery([Fog])(state.world)[0];
    const waterEid = defineQuery([Water])(state.world)[0];

    expect(Water.underwaterFogDensity[waterEid]).toBeCloseTo(0.2);
    expect(Water.underwaterFogColorR[waterEid]).toBeCloseTo(0.1);
    expect(Water.underwaterFogColorG[waterEid]).toBeCloseTo(0.05);
    expect(Water.underwaterFogColorB[waterEid]).toBeCloseTo(0.15);

    expect(Fog.density[fogEid]).toBeCloseTo(0.04);
    expect(Fog.density[fogEid]).not.toBeCloseTo(
      Water.underwaterFogDensity[waterEid]
    );

    for (let i = 0; i < 10; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Water.underwaterFogDensity[waterEid]).toBeCloseTo(0.2);
    expect(Fog.density[fogEid]).toBeCloseTo(0.04);
  });

  it('should work with fog only (no water)', () => {
    parseWorldXml(
      state,
      `
      <static-part body="pos: 0 -0.5 0" collider="shape: box; size: 20 1 20" />
      <fog mode="linear" near="5" far="200" />
    `
    );

    const fogEntities = defineQuery([Fog])(state.world);
    const waterEntities = defineQuery([Water])(state.world);

    expect(fogEntities.length).toBeGreaterThanOrEqual(1);
    expect(waterEntities.length).toBe(0);

    for (let i = 0; i < 20; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }
  });

  it('should work with water only (no fog)', () => {
    parseWorldXml(
      state,
      `
      <static-part body="pos: 0 -10 0" collider="shape: box; size: 30 1 30" />
      <water size="128" water-level="5" />
    `
    );

    const fogEntities = defineQuery([Fog])(state.world);
    const waterEntities = defineQuery([Water])(state.world);

    expect(fogEntities.length).toBe(0);
    expect(waterEntities.length).toBeGreaterThanOrEqual(1);

    for (let i = 0; i < 20; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }
  });

  it('should handle 100 steps with both plugins active', () => {
    parseWorldXml(
      state,
      `
      <static-part body="pos: 0 -5 0" collider="shape: box; size: 40 1 40" />
      <water size="64" water-level="2" />
      <fog mode="exponential" density="0.025" />
    `
    );

    for (let i = 0; i < 100; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    const fogEntities = defineQuery([Fog])(state.world);
    const waterEntities = defineQuery([Water])(state.world);

    expect(fogEntities.length).toBeGreaterThanOrEqual(1);
    expect(waterEntities.length).toBeGreaterThanOrEqual(1);
  });
});
