/**
 * E2E: Full fog pipeline — XML → parse → entities → systems → verify state.
 * Headless only, no GPU/WebGL.
 */
import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS, defineQuery } from 'vibegame';
import { createHeadlessState, parseWorldXml } from 'vibegame/cli';
import { DefaultPlugins } from 'vibegame/defaults';
import { Fog } from '../../../src/plugins/fog/components';

globalThis.requestAnimationFrame = ((cb: any) => setTimeout(cb, 16)) as any;
globalThis.cancelAnimationFrame = clearTimeout as any;

describe('E2E: Fog Full Pipeline', () => {
  let state: State;

  beforeEach(async () => {
    state = createHeadlessState({ plugins: DefaultPlugins });
    await state.initializePlugins();
  });

  it('should parse <fog> with defaults from XML and run systems', () => {
    parseWorldXml(state, `<fog></fog>`);

    const fogQuery = defineQuery([Fog]);
    const fogEntities = fogQuery(state.world);
    expect(fogEntities.length).toBeGreaterThanOrEqual(1);

    const eid = fogEntities[0];
    expect(Fog.mode[eid]).toBe(0);
    expect(Fog.density[eid]).toBeCloseTo(0.015);
    expect(Fog.near[eid]).toBe(1);
    expect(Fog.far[eid]).toBe(1000);
    expect(Fog.colorR[eid]).toBeCloseTo(0.533);
    expect(Fog.colorG[eid]).toBeCloseTo(0.6);
    expect(Fog.colorB[eid]).toBeCloseTo(0.667);
    expect(Fog.quality[eid]).toBe(1);

    expect(() => {
      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }
    }).not.toThrow();
  });

  it('should parse exponential fog with custom density', () => {
    parseWorldXml(
      state,
      `<fog mode="exponential" density="0.05" color="#88ccee"></fog>`
    );

    const fogQuery = defineQuery([Fog]);
    const fogEntities = fogQuery(state.world);
    expect(fogEntities.length).toBeGreaterThanOrEqual(1);

    const eid = fogEntities[0];
    expect(Fog.mode[eid]).toBe(0);
    expect(Fog.density[eid]).toBeCloseTo(0.05);
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
  });

  it('should parse linear fog with near and far', () => {
    parseWorldXml(state, `<fog mode="linear" near="5" far="200"></fog>`);

    const eid = defineQuery([Fog])(state.world)[0];

    expect(Fog.mode[eid]).toBe(2);
    expect(Fog.near[eid]).toBe(5);
    expect(Fog.far[eid]).toBe(200);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
  });

  it('should parse exponential-squared fog', () => {
    parseWorldXml(
      state,
      `<fog mode="exponential-squared" density="0.1"></fog>`
    );

    const eid = defineQuery([Fog])(state.world)[0];
    expect(Fog.mode[eid]).toBe(1);
    expect(Fog.density[eid]).toBeCloseTo(0.1);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
  });

  it('should parse fog with all attributes', () => {
    parseWorldXml(
      state,
      `<fog mode="linear" density="0.08" near="2" far="150" color="#ff8800"
           height-falloff="3" base-height="-2" volumetric-strength="0.7"
           quality="high" noise-scale="2"></fog>`
    );

    const eid = defineQuery([Fog])(state.world)[0];

    expect(Fog.mode[eid]).toBe(2);
    expect(Fog.density[eid]).toBeCloseTo(0.08);
    expect(Fog.near[eid]).toBe(2);
    expect(Fog.far[eid]).toBe(150);
    expect(Fog.heightFalloff[eid]).toBeCloseTo(3);
    expect(Fog.baseHeight[eid]).toBe(-2);
    expect(Fog.volumetricStrength[eid]).toBeCloseTo(0.7);
    expect(Fog.quality[eid]).toBe(2);
    expect(Fog.noiseScale[eid]).toBeCloseTo(2);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
  });

  it('should parse fog alongside other world elements', () => {
    parseWorldXml(
      state,
      `
      <static-part
        body="pos: 0 -0.5 0"
        renderer="shape: box; size: 10 1 10; color: 0x90ee90"
        collider="shape: box; size: 10 1 10" />
      <fog mode="exponential" density="0.02"></fog>
    `
    );

    const fogEntities = defineQuery([Fog])(state.world);
    expect(fogEntities.length).toBeGreaterThanOrEqual(1);
    expect(Fog.mode[fogEntities[0]]).toBe(0);
    expect(Fog.density[fogEntities[0]]).toBeCloseTo(0.02);

    for (let i = 0; i < 30; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    const currentFog = defineQuery([Fog])(state.world);
    expect(currentFog.length).toBeGreaterThanOrEqual(1);
  });

  it('should keep fog component data stable across simulation steps', () => {
    parseWorldXml(
      state,
      `<fog mode="linear" near="10" far="300" density="0.04"></fog>`
    );

    const eid = defineQuery([Fog])(state.world)[0];
    const initialMode = Fog.mode[eid];
    const initialNear = Fog.near[eid];
    const initialFar = Fog.far[eid];
    const initialDensity = Fog.density[eid];

    for (let i = 0; i < 100; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    expect(Fog.mode[eid]).toBe(initialMode);
    expect(Fog.near[eid]).toBe(initialNear);
    expect(Fog.far[eid]).toBe(initialFar);
    expect(Fog.density[eid]).toBeCloseTo(initialDensity);
  });

  it('should parse self-closing <fog/> element', () => {
    parseWorldXml(state, `<fog/>`);

    const fogEntities = defineQuery([Fog])(state.world);
    expect(fogEntities.length).toBeGreaterThanOrEqual(1);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
  });

  it('should parse fog with quality=low', () => {
    parseWorldXml(state, `<fog quality="low" density="0.03"></fog>`);

    const eid = defineQuery([Fog])(state.world)[0];
    expect(Fog.quality[eid]).toBe(0);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
  });

  it('should parse fog with quality=medium', () => {
    parseWorldXml(state, `<fog quality="medium"></fog>`);

    const eid = defineQuery([Fog])(state.world)[0];
    expect(Fog.quality[eid]).toBe(1);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
  });

  it('should parse fog with quality=high', () => {
    parseWorldXml(state, `<fog quality="high"></fog>`);

    const eid = defineQuery([Fog])(state.world)[0];
    expect(Fog.quality[eid]).toBe(2);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
  });
});
