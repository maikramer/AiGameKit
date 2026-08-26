import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { TransformsPlugin } from 'vibegame/transforms';
import { Terrain } from '../../../src/plugins/terrain/components';
import { TerrainPlugin } from '../../../src/plugins/terrain/plugin';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import { getTerrainContext } from '../../../src/plugins/terrain/utils';

/**
 * simple-farm's ground contract.
 *
 * This file used to hardcode four abutting terraces with falloff="0.6" and
 * assert they were dead flat. That shape was the bug: a ~2 m step over 0.6 m is
 * about 75°, past the CharacterController's 45° limit, so the valley was four
 * islands joined by three staircases. The relief is continuous now and the two
 * surviving pads (town, farm plot) exist only to give the settlement a floor.
 *
 * The pads are read from the example itself rather than copied here — the old
 * copy silently drifted (it still described `at="0 16"` for a plot that had
 * moved to `at="0 10"`, and a 512 m world for a 256 m one) and kept passing
 * while asserting a world nobody shipped.
 */
const EXAMPLE = path.resolve(
  import.meta.dirname,
  '../../../examples/simple-farm/public/world/terrain.xml'
);
const TERRAIN_XML = readFileSync(EXAMPLE, 'utf8');
/**
 * Comments first: the file's own header quotes `<TerrainPad>` and the old
 * `falloff="0.6"` while explaining why they went away, and a naive match reads
 * that prose as a declaration.
 */
const TERRAIN_BODY = TERRAIN_XML.replace(/<!--[\s\S]*?-->/g, '');
const PAD_DECLS =
  TERRAIN_BODY.match(/<TerrainPad[\s\S]*?<\/TerrainPad>/g) ?? [];

const RES = 1025;
const WORLD = 512;
const MAX_H = 40;

/** The walkable interior; the rim outside this is allowed to be steep. */
const INTERIOR = 200;
/** CharacterController maxSlope. Anything above it is an invisible wall. */
const MAX_WALKABLE_DEG = 45;

/**
 * XMLParser wants a single root, and the pads are siblings in the example.
 * The wrapping <Group> sits at identity so it shifts nothing.
 */
function padsXml(): string {
  return `<Group name="pads" pos="0 0 0">${PAD_DECLS.join('\n')}</Group>`;
}

function buildState(): { state: State; sampler: HeightSampler } {
  const state = new State();
  state.registerPlugin(TransformsPlugin);
  state.registerPlugin(TerrainPlugin);

  const terrain = state.createEntity();
  state.addComponent(terrain, Terrain);
  Terrain.worldSize[terrain] = WORLD;
  Terrain.maxHeight[terrain] = MAX_H;
  Terrain.resolution[terrain] = 128;
  Terrain.levels[terrain] = 5;

  // Stand-in for farm_valley.ahgt: gentle rolling ground, no cliffs of its own,
  // so any steep sample the test finds came from a pad rather than the base.
  // `data` holds NORMALISED heights (sampleHeightAt multiplies by maxHeight),
  // not metres — filling it with metres silently scales the world by 40x.
  const data = new Float32Array(RES * RES);
  const sampler: HeightSampler = {
    width: RES,
    height: RES,
    data,
    worldSize: WORLD,
    maxHeight: MAX_H,
  };
  for (let z = 0; z < RES; z++) {
    for (let x = 0; x < RES; x++) {
      const wx = (x / (RES - 1) - 0.5) * WORLD;
      const wz = (z / (RES - 1) - 0.5) * WORLD;
      const metres = 10 + Math.sin(wx / 90) * 1.8 + Math.cos(wz / 110) * 1.6;
      data[z * RES + x] = metres / MAX_H;
    }
  }
  getTerrainContext(state).set(terrain, {
    sampler,
    chunks: new Set<number>(),
    initialized: true,
    collisionReady: false,
    worldOffset: { x: 0, y: 0, z: 0 },
    lastWireframe: 0,
    lastShowChunkBorders: 0,
    physicsBody: null,
    physicsCollider: null,
    chunkColliders: new Map(),
  } as never);

  const dom = new JSDOM(
    `<!DOCTYPE html><html><body><div id="root">${padsXml()}</div></body></html>`
  );
  global.DOMParser = dom.window.DOMParser;
  const parsed = XMLParser.parse(
    dom.window.document.getElementById('root')!.innerHTML
  );
  parseXMLToEntities(state, parsed.root);
  state.step(0.016);

  return { state, sampler };
}

function slopeDegAt(
  sampler: HeightSampler,
  x: number,
  z: number,
  step = 1
): number {
  const h = (px: number, pz: number) => sampleHeightAt(sampler, px, pz);
  const dx = (h(x + step, z) - h(x - step, z)) / (2 * step);
  const dz = (h(x, z + step) - h(x, z - step)) / (2 * step);
  return (Math.atan(Math.hypot(dx, dz)) * 180) / Math.PI;
}

describe('simple-farm ground', () => {
  let sampler: HeightSampler;

  beforeEach(() => {
    ({ sampler } = buildState());
  });

  it('declares exactly the two settlement pads, and no terraces', () => {
    expect(PAD_DECLS.length).toBe(2);
    // The terrace bug was a 0.6 m falloff over a ~2 m step (~75°). Every
    // surviving pad must ramp wide enough that its rim stays walkable.
    for (const decl of PAD_DECLS) {
      const falloff = Number(decl.match(/falloff="([\d.]+)"/)?.[1] ?? 0);
      expect(falloff).toBeGreaterThanOrEqual(8);
    }
  });

  it('the world size matches the heightmap the example ships', () => {
    // ahgt-loader ignores the XML attribute and uses the file's metadata, so a
    // mismatch silently makes the mesh read the wrong slice of the field.
    expect(TERRAIN_BODY).toContain('world-size="512"');
  });

  it('has no slope above the controller limit anywhere in the interior', () => {
    let worst = 0;
    let worstAt: [number, number] = [0, 0];
    for (let z = -INTERIOR; z <= INTERIOR; z += 4) {
      for (let x = -INTERIOR; x <= INTERIOR; x += 4) {
        const deg = slopeDegAt(sampler, x, z);
        if (deg > worst) {
          worst = deg;
          worstAt = [x, z];
        }
      }
    }
    // A mensagem inclui o ponto pior para o próximo a mexer no relevo saber
    // onde foi, em vez de só ver um número.
    expect(
      `pior declive ${worst.toFixed(1)}° em (${worstAt[0]}, ${worstAt[1]})`
    ).toBe(
      `pior declive ${worst.toFixed(1)}° em (${worstAt[0]}, ${worstAt[1]})`
    );
    expect(worst).toBeLessThan(MAX_WALKABLE_DEG);
  });

  it('the farm pad is flat where the crop tiles sit', () => {
    // <FarmPlot at="-97.5 -2.5" size="24 18"> → tiles span x ∈ [-98, -74],
    // z ∈ [-3, 15]; base-y must equal the pad height (11).
    for (let z = -3; z <= 15; z += 3) {
      for (let x = -98; x <= -74; x += 3) {
        expect(sampleHeightAt(sampler, x, z)).toBeCloseTo(11, 2);
      }
    }
  });

  it('the town pad gives the square a level floor', () => {
    for (const [x, z] of [
      [0, 0],
      [-20, 14],
      [24, -18],
      [30, 20],
    ]) {
      const here = sampleHeightAt(sampler, x, z);
      const centre = sampleHeightAt(sampler, 0, 0);
      expect(Math.abs(here - centre)).toBeLessThan(0.05);
    }
  });
});
