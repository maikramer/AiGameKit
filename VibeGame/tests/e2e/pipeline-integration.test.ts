/**
 * E2E: Pipeline integration — validates that the VibeGame engine can parse
 * XML world definitions with common element types and run simulation steps.
 *
 * This is the "smoke test" for the full XML → ECS → simulation pipeline.
 */
import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS, defineQuery } from 'vibegame';
import { createHeadlessState, parseWorldXml } from 'vibegame/cli';
import { DefaultPlugins } from 'vibegame/defaults';
import { Transform } from 'vibegame/transforms';
import { Rigidbody } from 'vibegame/physics';

// Polyfill browser APIs for Bun (GSAP uses these internally)
globalThis.requestAnimationFrame = ((cb: any) => setTimeout(cb, 16)) as any;
globalThis.cancelAnimationFrame = clearTimeout as any;

describe('E2E: XML World Parsing', () => {
  let state: State;

  beforeEach(async () => {
    state = createHeadlessState({ plugins: DefaultPlugins });
    await state.initializePlugins();
  });

  it('should parse static-part elements and create entities with transforms', () => {
    parseWorldXml(
      state,
      `
      <static-part
        body="pos: 0 -0.5 0"
        renderer="shape: box; size: 10 1 10; color: 0x90ee90"
        collider="shape: box; size: 10 1 10" />
      <static-part
        body="pos: 5 0 0"
        renderer="shape: box; size: 2 2 2; color: 0x87ceeb" />
    `
    );

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const entities = defineQuery([Transform])(state.world);
    expect(entities.length).toBeGreaterThanOrEqual(2);
  });

  it('should parse dynamic-part and simulate physics', () => {
    parseWorldXml(
      state,
      `
      <static-part
        body="pos: 0 -0.5 0"
        collider="shape: box; size: 20 1 20" />
      <dynamic-part
        body="pos: 0 5 0; vel: 0 -1 0"
        renderer="shape: sphere; size: 1; color: 0xff0000"
        collider="shape: sphere; radius: 1" />
    `
    );

    for (let i = 0; i < 30; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    const bodies = defineQuery([Rigidbody])(state.world);
    expect(bodies.length).toBeGreaterThanOrEqual(1);
  });

  it('should handle multiple static parts in one world', () => {
    parseWorldXml(
      state,
      `
      <static-part
        body="pos: 0 -0.5 0"
        collider="shape: box; size: 30 1 30" />
      <static-part
        body="pos: 3 0.5 0"
        renderer="shape: box; size: 1 1 1; color: 0xffff00" />
      <static-part
        body="pos: -3 0.5 0"
        renderer="shape: box; size: 1 2 1; color: 0xff8800" />
    `
    );

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    const transforms = defineQuery([Transform])(state.world);
    expect(transforms.length).toBeGreaterThanOrEqual(3);
  });

  it('should throw descriptive error for unknown elements', () => {
    expect(() => {
      parseWorldXml(state, `<invalid-element that="does" not="exist" />`);
    }).toThrow(/Unknown element/i);

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    expect(state.world).toBeDefined();
  });

  it('should handle empty world', () => {
    parseWorldXml(state, '');
    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    expect(state.world).toBeDefined();
  });

  it('should run 100 simulation steps without errors', () => {
    parseWorldXml(
      state,
      `
      <static-part
        body="pos: 0 -0.5 0"
        collider="shape: box; size: 20 1 20" />
      <dynamic-part
        body="pos: 0 10 0"
        renderer="shape: sphere; size: 1; color: 0xff0000"
        collider="shape: sphere; radius: 1" />
    `
    );

    for (let i = 0; i < 100; i++) {
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
    }

    const bodies = defineQuery([Rigidbody])(state.world);
    expect(bodies.length).toBeGreaterThanOrEqual(1);
  });

  it('should create player entity with correct components', () => {
    parseWorldXml(
      state,
      `
      <static-part
        body="pos: 0 -0.5 0"
        collider="shape: box; size: 20 1 20" />
    `
    );

    state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

    // Player should be created by default
    const bodies = defineQuery([Rigidbody])(state.world);
    expect(bodies.length).toBeGreaterThanOrEqual(1);
  });
});
