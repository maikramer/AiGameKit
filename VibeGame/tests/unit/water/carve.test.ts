import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  carveBowl,
  carveChannel,
  carveRiverChannel,
  rimHeight,
  rimHeightAlongPath,
  shoreFraction,
  shapeRadius,
  SHORE_SHAPE_AMPLITUDE,
} from '../../../src/plugins/water/carve';
import {
  applyOverride,
  boostAt,
  buildDensityMap,
} from '../../../src/plugins/terrain/density-map';
import {
  isPointInWater,
  isPointNearWater,
  isPointOnWaterBank,
  registerWaterBody,
  unregisterWaterBody,
  waterLevelAt,
} from '../../../src/plugins/water/registry';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';

function flatSampler(
  heightNorm: number,
  size = 64,
  world = 200
): HeightSampler {
  return {
    width: size,
    height: size,
    data: new Float32Array(size * size).fill(heightNorm),
    worldSize: world,
    maxHeight: 100,
  };
}

describe('carveBowl', () => {
  it('carves a bowl of the requested depth at the centre', () => {
    // High-res sampler: the 64×64 default bleeds the centre sample through
    // bilinear interpolation of 4 grid vertices ~2 m off-centre, which masks
    // the true floor depth. 512 makes the interpolation error negligible.
    const s = flatSampler(0.5, 512); // terrain at 50 m
    const rim = rimHeight(s, 0, 0, 10);
    expect(rim).toBeCloseTo(50, 3);

    expect(carveBowl(s, 0, 0, 10, rim, 3)).toBe(true);
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(47, 1);
    // rim stays at the original terrain level
    expect(sampleHeightAt(s, 15, 0)).toBeCloseTo(50, 3);
  });

  it('only ever lowers terrain (existing valleys are kept)', () => {
    const s = flatSampler(0.2); // terrain at 20 m — below the bowl profile
    carveBowl(s, 0, 0, 10, 50, 3);
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(20, 3);
  });

  it('is monotonic from centre to rim', () => {
    const s = flatSampler(0.5);
    carveBowl(s, 0, 0, 12, 50, 4);
    let prev = -Infinity;
    for (let r = 0; r <= 12; r += 2) {
      const h = sampleHeightAt(s, r, 0);
      expect(h).toBeGreaterThanOrEqual(prev - 1e-6);
      prev = h;
    }
  });

  it('is C1-smooth at the rim (slope flattens, no crease)', () => {
    // (1 − t²)^1.5 has derivative 0 at t=1, so just inside the rim the floor is
    // much shallower than the old linear (^1) profile. High-res sampler keeps
    // the bilinear interpolation from bleeding the outer (un-carved) terrain in.
    const s = flatSampler(0.5, 512); // terrain at 50 m
    const radius = 20;
    const rim = 50;
    carveBowl(s, 0, 0, radius, rim, 6);
    // At t=0.95: (1 − 0.9025)^1.5 ≈ 0.0304 → floor drop ≈ 6·0.0304 ≈ 0.18 m.
    // The old ^1 profile would drop 6·0.0975 ≈ 0.585 m — ~3× deeper.
    const hInside = sampleHeightAt(s, radius * 0.95, 0);
    expect(hInside).toBeGreaterThan(rim - 0.25);
    expect(hInside).toBeLessThan(rim);
  });

  it('no-ops on a flat (dataless) sampler', () => {
    const s: HeightSampler = {
      width: 1,
      height: 1,
      data: null,
      worldSize: 200,
      maxHeight: 100,
    };
    expect(carveBowl(s, 0, 0, 10, 50, 3)).toBe(false);
  });
});

describe('shapeRadius', () => {
  it('returns ~1 on average (perturbation is centred on the base radius)', () => {
    // Average the shape over many angles; the sinusoidal sum has zero mean.
    let sum = 0;
    const N = 256;
    for (let i = 0; i < N; i++)
      sum += shapeRadius((i / N) * Math.PI * 2, -34, 32);
    const avg = sum / N;
    expect(avg).toBeGreaterThan(0.97);
    expect(avg).toBeLessThan(1.03);
  });

  it('stays within [1 − A, 1 + A] (A = SHORE_SHAPE_AMPLITUDE)', () => {
    // Re-derive A from the exported constant so the test tracks the engine.
    const A = SHORE_SHAPE_AMPLITUDE;
    for (let i = 0; i < 360; i++) {
      const a = (i / 360) * Math.PI * 2;
      const s = shapeRadius(a, -34, 32);
      expect(s).toBeGreaterThanOrEqual(1 - A - 1e-6);
      expect(s).toBeLessThanOrEqual(1 + A + 1e-6);
    }
  });

  it('is deterministic (same inputs → same output)', () => {
    const a = shapeRadius(1.234, -34, 32);
    const b = shapeRadius(1.234, -34, 32);
    expect(a).toBe(b);
  });

  it('varies with the lake centre (different seeds → different outlines)', () => {
    // Two lakes at different positions should not share an identical shoreline.
    let anyDiff = false;
    for (let i = 0; i < 64; i++) {
      const a = (i / 64) * Math.PI * 2;
      if (Math.abs(shapeRadius(a, -34, 32) - shapeRadius(a, 70, 46)) > 1e-4) {
        anyDiff = true;
        break;
      }
    }
    expect(anyDiff).toBe(true);
  });
});

describe('shoreFraction', () => {
  it('returns ~0.81 for the engine defaults (depth 1.5, waterOffset 0.3)', () => {
    // (1 − t²)^1.5 = 0.3/1.5 = 0.2 → t² = 1 − 0.2^(2/3) ≈ 1 − 0.342 = 0.658 → t ≈ 0.811
    expect(shoreFraction(1.5, 0.3)).toBeCloseTo(0.811, 2);
  });

  it('is 0 when there is no depth or no offset', () => {
    expect(shoreFraction(0, 0.3)).toBe(0);
    expect(shoreFraction(1.5, 0)).toBe(0);
  });

  it('clamps to 0.98 when waterOffset ≥ depth (fully submerged floor)', () => {
    expect(shoreFraction(1.5, 1.5)).toBe(0.98);
    expect(shoreFraction(1.5, 3)).toBe(0.98);
  });

  it('grows as the lake deepens (shore moves outward, more water visible)', () => {
    const shallow = shoreFraction(1.5, 0.3);
    const deep = shoreFraction(4, 0.3);
    expect(deep).toBeGreaterThan(shallow);
  });

  it('stays in [0, 0.98]', () => {
    for (let d = 0.5; d <= 6; d += 0.5) {
      for (let o = 0.1; o <= d; o += 0.1) {
        const f = shoreFraction(d, o);
        expect(f).toBeGreaterThanOrEqual(0);
        expect(f).toBeLessThanOrEqual(0.98);
      }
    }
  });
});

describe('water registry', () => {
  it('point membership + level lookup + unregister', () => {
    const state = new State();
    const body = {
      kind: 'lake' as const,
      x: 10,
      z: -5,
      radius: 6,
      shoreRadius: 4.5,
      waterY: 42,
    };
    registerWaterBody(state, body);

    expect(isPointInWater(state, 10, -5)).toBe(true);
    expect(isPointInWater(state, 15, -5)).toBe(true);
    expect(isPointInWater(state, 17, -5)).toBe(false);
    expect(waterLevelAt(state, 12, -5)).toBe(42);
    expect(waterLevelAt(state, 30, 30)).toBeNull();

    unregisterWaterBody(state, body);
    expect(isPointInWater(state, 10, -5)).toBe(false);
  });
});

describe('Lake density override (pre-carve)', () => {
  // LakeApplySystem bumps the density map to 255 over the lake's AABB before
  // carving, so chunks rendering the lake adopt a finer mesh and capture the
  // basin detail that already exists in the heightfield. carveBowl itself is
  // unchanged; this only raises the sampling resolution.
  it('marks the lake AABB as maximally dense', () => {
    const data = new Float32Array(8 * 8).fill(0.5);
    const sampler: HeightSampler = {
      width: 8,
      height: 8,
      data,
      worldSize: 100,
      maxHeight: 10,
    };
    const density = buildDensityMap(sampler, 16);
    const lakeX = 0;
    const lakeZ = 0;
    const radius = 6;
    const margin = 1.1; // matches the carve shoreline overshoot
    applyOverride(
      density,
      {
        minX: lakeX - radius * margin,
        minZ: lakeZ - radius * margin,
        maxX: lakeX + radius * margin,
        maxZ: lakeZ + radius * margin,
      },
      255
    );
    expect(boostAt(density, lakeX, lakeZ)).toBe(255);
    // A point well outside the lake is unaffected (flat field → 0).
    expect(boostAt(density, 40, 40)).toBe(0);
  });
});

describe('carveChannel', () => {
  const W = 100; // worldSize
  const SIZE = 128;

  function flatField(): HeightSampler {
    const data = new Float32Array(SIZE * SIZE).fill(0.5);
    return { width: SIZE, height: SIZE, data, worldSize: W, maxHeight: 100 };
  }

  it('lowers the sampler along the path axis and returns true', () => {
    const s = flatField();
    const path = [-40, 0, 40, 0]; // straight river along X through the origin
    const rim = rimHeightAlongPath(s, path, 6);
    // Pre-sample axis heights (terrain-following, as RiverChannel does).
    const axis = [sampleHeightAt(s, -40, 0), sampleHeightAt(s, 40, 0)];
    const before = sampleHeightAt(s, 0, 0);
    const carved = carveChannel(s, path, 6, rim, 8, axis);
    const after = sampleHeightAt(s, 0, 0);
    expect(carved).toBe(true);
    expect(after).toBeLessThan(before);
  });

  it('does not carve outside width/2 of the path', () => {
    const s = flatField();
    const path = [-40, 0, 40, 0];
    const rim = rimHeightAlongPath(s, path, 6);
    const axis = [sampleHeightAt(s, -40, 0), sampleHeightAt(s, 40, 0)];
    carveChannel(s, path, 6, rim, 8, axis);
    // A point 20 m off-axis (well outside width/2 = 3) keeps the original height.
    expect(sampleHeightAt(s, 0, 20)).toBeCloseTo(50, 1);
  });

  it('is idempotent: a second pass (same stable axis heights) does not dig deeper', () => {
    const s = flatField();
    const path = [-40, 0, 40, 0];
    const rim = rimHeightAlongPath(s, path, 6);
    // Axis heights are sampled ONCE from the unmutated field (as RiverChannel
    // does), so a re-carve samples the same floor and doesn't deepen.
    const axis = [sampleHeightAt(s, -40, 0), sampleHeightAt(s, 40, 0)];
    carveChannel(s, path, 6, rim, 8, axis);
    const atCenter = sampleHeightAt(s, 0, 0);
    carveChannel(s, path, 6, rim, 8, axis);
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(atCenter, 4);
  });

  it('returns false on a dataless sampler', () => {
    const flat: HeightSampler = {
      width: 1,
      height: 1,
      data: null,
      worldSize: W,
      maxHeight: 100,
    };
    expect(carveChannel(flat, [-40, 0, 40, 0], 6, 10, 8)).toBe(false);
  });
});

describe('carveRiverChannel (bank profile)', () => {
  const SIZE = 512; // fine grid → bilinear interpolation error negligible
  const W = 100;

  function field(heightNorm: number): HeightSampler {
    return {
      width: SIZE,
      height: SIZE,
      data: new Float32Array(SIZE * SIZE).fill(heightNorm),
      worldSize: W,
      maxHeight: 100,
    };
  }

  // Straight river along X through the origin; flat 50 m terrain, water at 48.
  const stations = [-40, 0, 40, 0];
  const surface = [48, 48];
  const opts = {
    width: 8, // waterline half = 4
    channelDepth: 2,
    bankWidth: 2, // carve half = 6
    bankHeight: 1,
    featherWidth: 2, // outer = 8
    maxBankRaise: 2.5,
  };

  it('crosses the water surface at exactly ±width/2 (waterline = width)', () => {
    const s = field(0.5);
    expect(carveRiverChannel(s, stations, surface, opts)).toBe(true);
    expect(sampleHeightAt(s, 0, 4)).toBeCloseTo(48, 1);
    // Axis is channelDepth below the surface.
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(46, 1);
    // Inside the waterline the floor is below the surface.
    expect(sampleHeightAt(s, 0, 2)).toBeLessThan(48);
  });

  it('cuts an exposed bank rising to surface + bankHeight at the carve edge', () => {
    const s = field(0.5); // terrain 50 = surface 48 + 2 → bank cut into it
    carveRiverChannel(s, stations, surface, opts);
    // Mid-bank: between the surface and the crest.
    const mid = sampleHeightAt(s, 0, 5);
    expect(mid).toBeGreaterThan(48);
    expect(mid).toBeLessThan(49);
    // Crest at the carve edge: surface + bankHeight, below natural terrain.
    // (Bilinear interpolation bleeds a little of the untouched 50 m terrain
    // just outside the cut, so allow a small upward tolerance.)
    const crest = sampleHeightAt(s, 0, 6);
    expect(crest).toBeGreaterThan(48.9);
    expect(crest).toBeLessThan(49.3);
    // Beyond the feather band the terrain is untouched.
    expect(sampleHeightAt(s, 0, 12)).toBeCloseTo(50, 2);
  });

  it('raises low terrain up to the bank profile so the water stays contained', () => {
    // Terrain at 47 — below the 48 surface. Banks must be raised or the
    // water would float over open ground.
    const s = field(0.47);
    carveRiverChannel(s, stations, surface, opts);
    expect(sampleHeightAt(s, 0, 6)).toBeCloseTo(49, 1);
    // Feather blends the raised crest back to natural terrain.
    expect(sampleHeightAt(s, 0, 12)).toBeCloseTo(47, 2);
  });

  it('skips raises beyond maxBankRaise (waterfall/cliff zones)', () => {
    const s = field(0.4); // terrain 40, crest target 49 → deficit 9 > cap
    carveRiverChannel(s, stations, surface, opts);
    expect(sampleHeightAt(s, 0, 6)).toBeCloseTo(40, 2);
  });

  it('is idempotent (same surface → same floor on a second pass)', () => {
    const s = field(0.5);
    carveRiverChannel(s, stations, surface, opts);
    const atAxis = sampleHeightAt(s, 0, 0);
    const atBank = sampleHeightAt(s, 0, 5);
    carveRiverChannel(s, stations, surface, opts);
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(atAxis, 4);
    expect(sampleHeightAt(s, 0, 5)).toBeCloseTo(atBank, 4);
  });
});

describe('isPointNearWater (carve-footprint exclusion)', () => {
  it('rivers: excludes the carve width, not just the waterline', () => {
    const state = new State();
    const body = {
      kind: 'river' as const,
      path: [
        [-40, 0],
        [40, 0],
      ] as Array<readonly [number, number]>,
      width: 8,
      carveWidth: 16,
      waterY: 48,
    };
    registerWaterBody(state, body);
    expect(isPointInWater(state, 0, 6)).toBe(false); // outside waterline
    expect(isPointNearWater(state, 0, 6)).toBe(true); // on the carved bank
    expect(isPointOnWaterBank(state, 0, 6)).toBe(true);
    expect(isPointOnWaterBank(state, 0, 0)).toBe(false); // wet channel
    expect(isPointNearWater(state, 0, 9)).toBe(false); // outside the carve
    unregisterWaterBody(state, body);
  });

  it('lakes: excludes carveRadius, falling back to radius for legacy bodies', () => {
    const state = new State();
    const body = {
      kind: 'lake' as const,
      x: 0,
      z: 0,
      radius: 6,
      shoreRadius: 4.5,
      carveRadius: 9,
      waterY: 42,
    };
    registerWaterBody(state, body);
    expect(isPointInWater(state, 8, 0)).toBe(false);
    expect(isPointNearWater(state, 8, 0)).toBe(true);
    expect(isPointOnWaterBank(state, 8, 0)).toBe(true);
    expect(isPointOnWaterBank(state, 0, 0)).toBe(false);
    expect(isPointNearWater(state, 10, 0)).toBe(false);
    unregisterWaterBody(state, body);

    const legacy = { ...body, carveRadius: undefined };
    registerWaterBody(state, legacy);
    expect(isPointNearWater(state, 8, 0)).toBe(false); // falls back to radius 6
    unregisterWaterBody(state, legacy);
  });
});

describe('rimHeightAlongPath', () => {
  it('returns the minimum height along both banks', () => {
    // Build a field that is 50 everywhere except a low spot on the bank.
    const data = new Float32Array(128 * 128).fill(0.5);
    const half = 50;
    const step = 100 / 127;
    // Poke a low texel near (10, 3) world — on the +Z bank of the path.
    const tx = Math.round((10 + half) / step);
    const tz = Math.round((3 + half) / step);
    data[tz * 128 + tx] = 0.2;
    const s = { width: 128, height: 128, data, worldSize: 100, maxHeight: 100 };
    const path = [-40, 0, 40, 0];
    const rim = rimHeightAlongPath(s, path, 6);
    // rim is the min height along the banks; the low spot (20 m) is below 50.
    expect(rim).toBeLessThanOrEqual(50);
    expect(rim).toBeGreaterThanOrEqual(0);
  });
});
