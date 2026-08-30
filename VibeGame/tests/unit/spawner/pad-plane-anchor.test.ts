import { describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import { sampleTerrainSurfaceMatrix } from '../../../src/plugins/spawner/surface';
import { registerGroundBrush } from '../../../src/plugins/terrain/brush-registry';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { getTerrainContext } from '../../../src/plugins/terrain/utils';
import { Terrain } from '../../../src/plugins/terrain/components';

/**
 * Placement inside a TerrainPad's flat core must anchor to the analytic pad
 * plane (`worldY` = plane, `padPlane` = true). Sampling the mesh lattice
 * there blends the pad edge with untouched terrain — at a coarse LOD step
 * (small pads viewed from far away) that floats/sinks props by up to ~1 m.
 *
 * Regression for the interior rooms (simple-rpg interiors.xml): furniture
 * on 8–16 m pads landed up to ±0.9 m off the floor before the anchor.
 */

function buildState(): State {
  const state = new State();
  state.registerComponent('terrain', Terrain);
  const terrain = state.createEntity();
  state.addComponent(terrain, Terrain);
  Terrain.resolution[terrain] = 64;
  Terrain.levels[terrain] = 4;

  const sampler: HeightSampler = {
    width: 257,
    height: 257,
    // Sloped heightmap: h = 100 + 2 * lx (rises 2 m per metre east) — the
    // pad plane cuts through it, so a lattice sample at the pad centre would
    // read ~110 while the plane is 100.
    data: new Float32Array(257 * 257) as Float32Array,
    worldSize: 2000,
    maxHeight: 200,
  };
  for (let z = 0; z < 257; z++) {
    for (let x = 0; x < 257; x++) {
      const lx = (x / 256 - 0.5) * 2000;
      sampler.data![z * 257 + x] = (100 + 2 * lx) / 200;
    }
  }

  const ctx = getTerrainContext(state);
  ctx.set(terrain, {
    initialized: true,
    worldOffset: { x: 0, z: 0 },
    sampler,
    density: undefined,
  } as never);

  // Pad centred at local (0, 0), flat core 12×10 m, plane at y = 100.
  registerGroundBrush(state, {
    kind: 'pad',
    minX: -12,
    maxX: 12,
    minZ: -10,
    maxZ: 10,
    targetY: 100,
    halfX: 8,
    halfZ: 6,
    cornerRadius: 2,
  });
  return state;
}

describe('sampleTerrainSurfaceMatrix pad-plane anchor', () => {
  it('inside the flat core returns the pad plane with padPlane=true', () => {
    const state = buildState();
    const s = sampleTerrainSurfaceMatrix(state, 3, -2, 0.75);
    expect(s).not.toBeNull();
    expect(s!.worldY).toBeCloseTo(100, 5);
    expect(s!.padPlane).toBe(true);
    expect(s!.slopeAngleRad).toBe(0);
  });

  it('outside every pad core falls back to the mesh lattice (padPlane unset)', () => {
    const state = buildState();
    // 500 m east: far outside the 12×10 pad → no plane, lattice surface.
    const s = sampleTerrainSurfaceMatrix(state, 500, 0, 0.75);
    expect(s).not.toBeNull();
    expect(s!.padPlane).toBeUndefined();
    expect(s!.worldY).toBeGreaterThan(100);
  });

  it('no brushes registered → no pad plane (lattice path)', () => {
    const plain = new State();
    plain.registerComponent('terrain', Terrain);
    const t = plain.createEntity();
    plain.addComponent(t, Terrain);
    Terrain.resolution[t] = 64;
    Terrain.levels[t] = 4;
    const sampler: HeightSampler = {
      width: 2,
      height: 2,
      data: new Float32Array([0.5, 0.5, 0.5, 0.5]),
      worldSize: 2000,
      maxHeight: 200,
    };
    getTerrainContext(plain).set(t, {
      initialized: true,
      worldOffset: { x: 0, z: 0 },
      sampler,
      density: undefined,
    } as never);
    const s = sampleTerrainSurfaceMatrix(plain, 0, 0, 0.75);
    expect(s).not.toBeNull();
    expect(s!.padPlane).toBeUndefined();
  });
});
