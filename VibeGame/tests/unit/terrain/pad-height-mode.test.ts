import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { Terrain, TerrainPad } from '../../../src/plugins/terrain/components';
import { TerrainPadApplySystem } from '../../../src/plugins/terrain/pad-systems';
import { TerrainPlugin } from '../../../src/plugins/terrain/plugin';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import { getTerrainContext } from '../../../src/plugins/terrain/utils';
import { Transform, TransformsPlugin } from 'vibegame/transforms';

const RES = 257;
const WORLD = 512;
const MAX_H = 40;

/**
 * `TerrainPad.height` alone cannot express "pin this pad to exactly zero" —
 * the old `height !== 0` sentinel read that as "unset, go sample the terrain".
 * `heightMode` splits the two, which is what lets terraces stack from a zero
 * baseline. Default stays `auto`, so every existing pad behaves identically.
 */
function buildState(): {
  state: State;
  terrain: number;
  sampler: HeightSampler;
} {
  const state = new State();
  state.registerPlugin(TransformsPlugin);
  state.registerPlugin(TerrainPlugin);

  const terrain = state.createEntity();
  state.addComponent(terrain, Terrain);
  Terrain.worldSize[terrain] = WORLD;
  Terrain.maxHeight[terrain] = MAX_H;
  Terrain.resolution[terrain] = 128;
  Terrain.levels[terrain] = 4;

  // Gentle east-rising slope so an auto-sampled pad and an absolute one differ.
  const sampler: HeightSampler = {
    width: RES,
    height: RES,
    data: new Float32Array(RES * RES),
    worldSize: WORLD,
    maxHeight: MAX_H,
  };
  for (let z = 0; z < RES; z++) {
    for (let x = 0; x < RES; x++) {
      const lx = (x / (RES - 1) - 0.5) * WORLD;
      sampler.data![z * RES + x] = (20 + lx * 0.02) / MAX_H;
    }
  }

  // Minimal field data: enough for the brush + rebuild path, no chunks/physics
  // (this suite is about the height data, not the mesh or the colliders).
  getTerrainContext(state).set(terrain, {
    initialized: true,
    collisionReady: false,
    worldOffset: { x: 0, y: 0, z: 0 },
    sampler,
    chunks: new Set<number>(),
    chunkColliders: new Map(),
    lastWireframe: 0,
    lastShowChunkBorders: 0,
    physicsBody: null,
    physicsCollider: null,
    density: undefined,
  } as never);

  return { state, terrain, sampler };
}

function makePad(
  state: State,
  opts: {
    x: number;
    z: number;
    halfX: number;
    halfZ: number;
    height?: number;
    heightMode?: number;
    falloff?: number;
    cornerRadius?: number;
  }
): number {
  const pad = state.createEntity();
  state.addComponent(pad, Transform);
  state.addComponent(pad, TerrainPad);
  Transform.posX[pad] = opts.x;
  Transform.posZ[pad] = opts.z;
  TerrainPad.halfX[pad] = opts.halfX;
  TerrainPad.halfZ[pad] = opts.halfZ;
  TerrainPad.height[pad] = opts.height ?? 0;
  TerrainPad.heightMode[pad] = opts.heightMode ?? 0;
  TerrainPad.falloff[pad] = opts.falloff ?? 4;
  TerrainPad.cornerRadius[pad] = opts.cornerRadius ?? 0;
  return pad;
}

describe('TerrainPad heightMode', () => {
  let ctx: ReturnType<typeof buildState>;

  beforeEach(() => {
    ctx = buildState();
  });

  describe('defaults (unchanged behaviour)', () => {
    it('defaults to auto', () => {
      const pad = makePad(ctx.state, { x: 0, z: 0, halfX: 10, halfZ: 10 });
      expect(TerrainPad.heightMode[pad]).toBe(0);
    });

    it('auto flattens to the terrain height at the pad centre', () => {
      const before = sampleHeightAt(ctx.sampler, 40, 0);
      makePad(ctx.state, { x: 40, z: 0, halfX: 10, halfZ: 10 });

      TerrainPadApplySystem.update!(ctx.state);

      expect(sampleHeightAt(ctx.sampler, 40, 0)).toBeCloseTo(before, 3);
      // The slope is gone: a point 8 m east inside the core now matches centre.
      expect(sampleHeightAt(ctx.sampler, 48, 0)).toBeCloseTo(before, 2);
    });

    it('writes the resolved plane back into height', () => {
      const expected = sampleHeightAt(ctx.sampler, 40, 0);
      const pad = makePad(ctx.state, { x: 40, z: 0, halfX: 10, halfZ: 10 });

      TerrainPadApplySystem.update!(ctx.state);

      expect(TerrainPad.height[pad]).toBeCloseTo(expected, 3);
    });

    it('promotes the mode to absolute once stamped, so a re-apply reuses the plane', () => {
      const pad = makePad(ctx.state, { x: 40, z: 0, halfX: 10, halfZ: 10 });

      TerrainPadApplySystem.update!(ctx.state);
      const firstPlane = TerrainPad.height[pad];

      TerrainPad.applied[pad] = 0;
      TerrainPadApplySystem.update!(ctx.state);

      expect(TerrainPad.heightMode[pad]).toBe(1);
      expect(TerrainPad.height[pad]).toBeCloseTo(firstPlane, 5);
    });

    it('marks the pad applied', () => {
      const pad = makePad(ctx.state, { x: 0, z: 0, halfX: 10, halfZ: 10 });
      TerrainPadApplySystem.update!(ctx.state);
      expect(TerrainPad.applied[pad]).toBe(1);
    });
  });

  describe('absolute mode', () => {
    it('flattens to the requested height', () => {
      makePad(ctx.state, {
        x: 0,
        z: 0,
        halfX: 12,
        halfZ: 12,
        height: 31.5,
        heightMode: 1,
      });

      TerrainPadApplySystem.update!(ctx.state);

      expect(sampleHeightAt(ctx.sampler, 0, 0)).toBeCloseTo(31.5, 2);
    });

    it('honours height="0" instead of auto-sampling — the whole point of the flag', () => {
      makePad(ctx.state, {
        x: 0,
        z: 0,
        halfX: 12,
        halfZ: 12,
        height: 0,
        heightMode: 1,
      });

      TerrainPadApplySystem.update!(ctx.state);

      expect(sampleHeightAt(ctx.sampler, 0, 0)).toBeCloseTo(0, 2);
    });

    it('the same pad in auto mode would NOT flatten to zero', () => {
      makePad(ctx.state, { x: 0, z: 0, halfX: 12, halfZ: 12, height: 0 });

      TerrainPadApplySystem.update!(ctx.state);

      expect(sampleHeightAt(ctx.sampler, 0, 0)).toBeCloseTo(20, 1);
    });

    it('raises hollows and shaves bumps alike', () => {
      makePad(ctx.state, {
        x: -60,
        z: 0,
        halfX: 10,
        halfZ: 10,
        height: 25,
        heightMode: 1,
      });

      TerrainPadApplySystem.update!(ctx.state);

      // Terrain there was ~18.8 m (below the plane) — it must have come up.
      expect(sampleHeightAt(ctx.sampler, -60, 0)).toBeCloseTo(25, 2);
    });
  });

  describe('terrace stack', () => {
    it('keeps each terrace at its own plane', () => {
      makePad(ctx.state, {
        x: 0,
        z: -80,
        halfX: 30,
        halfZ: 20,
        height: 8,
        heightMode: 1,
        falloff: 0.6,
      });
      makePad(ctx.state, {
        x: 0,
        z: 0,
        halfX: 30,
        halfZ: 20,
        height: 12,
        heightMode: 1,
        falloff: 0.6,
      });

      TerrainPadApplySystem.update!(ctx.state);

      expect(sampleHeightAt(ctx.sampler, 0, -80)).toBeCloseTo(8, 2);
      expect(sampleHeightAt(ctx.sampler, 0, 0)).toBeCloseTo(12, 2);
    });

    it('a tight falloff makes the step too steep to walk (>45°)', () => {
      makePad(ctx.state, {
        x: 0,
        z: 0,
        halfX: 30,
        halfZ: 20,
        height: 12,
        heightMode: 1,
        falloff: 0.6,
      });
      makePad(ctx.state, {
        x: 0,
        z: -60,
        halfX: 30,
        halfZ: 20,
        height: 10,
        heightMode: 1,
        falloff: 0.6,
      });

      TerrainPadApplySystem.update!(ctx.state);

      // Sample across the bank between the two cores (z = -20 .. -40).
      let maxSlopeDeg = 0;
      const step = WORLD / (RES - 1);
      for (let z = -40; z <= -20; z += step) {
        const h0 = sampleHeightAt(ctx.sampler, 0, z);
        const h1 = sampleHeightAt(ctx.sampler, 0, z + step);
        const deg = (Math.atan2(Math.abs(h1 - h0), step) * 180) / Math.PI;
        if (deg > maxSlopeDeg) maxSlopeDeg = deg;
      }

      expect(maxSlopeDeg).toBeGreaterThan(45);
    });
  });

  describe('dead-flat farm plateau', () => {
    it('is constant across the whole core, so a tile grid can assume one Y', () => {
      makePad(ctx.state, {
        x: 0,
        z: 16,
        halfX: 60,
        halfZ: 44,
        height: 12,
        heightMode: 1,
        falloff: 0.6,
        cornerRadius: 0,
      });

      TerrainPadApplySystem.update!(ctx.state);

      // 100 points spread over the core, well inside the falloff ring.
      for (let i = 0; i < 10; i++) {
        for (let j = 0; j < 10; j++) {
          const x = -50 + (i / 9) * 100;
          const z = 16 - 34 + (j / 9) * 68;
          expect(sampleHeightAt(ctx.sampler, x, z)).toBeCloseTo(12, 3);
        }
      }
    });

    it('corner-radius 0 keeps the core an exact rectangle (corners are flat too)', () => {
      makePad(ctx.state, {
        x: 0,
        z: 0,
        halfX: 40,
        halfZ: 30,
        height: 12,
        heightMode: 1,
        falloff: 0.6,
        cornerRadius: 0,
      });

      TerrainPadApplySystem.update!(ctx.state);

      for (const [x, z] of [
        [-39, -29],
        [39, -29],
        [-39, 29],
        [39, 29],
      ]) {
        expect(sampleHeightAt(ctx.sampler, x, z)).toBeCloseTo(12, 3);
      }
    });
  });
});
