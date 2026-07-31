import { describe, expect, it } from 'bun:test';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import {
  BRIDGE_LANDWARD_METERS,
  bridgeApproachStubs,
  carveRoadApproaches,
  carveRoadCorridor,
  clipPathApproaches,
  designRoadProfile,
  limitProfileGrade,
  shouldCarveBridgeApproaches,
} from '../../../src/plugins/road/carve';

/** Flat sampler at a constant normalized height; high-res by default to avoid bilinear bleed. */
function flatSampler(
  heightNorm: number,
  size = 512,
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

/** Sampler with a single radial bump/depression at (x,z) field-local. */
function bumpSampler(
  base: number,
  bump: number,
  bx: number,
  bz: number,
  radius: number,
  size = 512,
  world = 200
): HeightSampler {
  const s = flatSampler(base, size, world);
  if (!s.data) return s;
  const half = world / 2;
  const step = world / (size - 1);
  const r2 = radius * radius;
  for (let zi = 0; zi < size; zi++) {
    const wz = zi * step - half;
    for (let xi = 0; xi < size; xi++) {
      const wx = xi * step - half;
      const d2 = (wx - bx) ** 2 + (wz - bz) ** 2;
      if (d2 <= r2) {
        // Cosine bump: peak at centre, 0 at radius.
        const t = 1 - d2 / r2;
        const k = (Math.cos(Math.PI * (1 - t)) + 1) / 2;
        s.data[zi * size + xi] = base + bump * k;
      }
    }
  }
  return s;
}

/**
 * Dense polyline along a straight segment from (x0,z0) to (x1,z1) at ~1 m
 * spacing. The carve samples the profile only at the given path stations, so a
 * 2-point path would miss features between them — the real RoadApplySystem
 * resamples before calling carve; tests must do the same.
 */
function densePath(x0: number, z0: number, x1: number, z1: number): number[] {
  const len = Math.hypot(x1 - x0, z1 - z0);
  const n = Math.max(2, Math.ceil(len));
  const path: number[] = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    path.push(x0 + (x1 - x0) * t, z0 + (z1 - z0) * t);
  }
  return path;
}

describe('carveRoadCorridor', () => {
  it('returns false on a sampler with no data (flat/undecoded)', () => {
    const s: HeightSampler = {
      width: 1,
      height: 1,
      data: null,
      worldSize: 200,
      maxHeight: 100,
    };
    const changed = carveRoadCorridor(s, {
      path: [-20, 0, 20, 0],
      width: 6,
      falloff: 2,
      window: 8,
    });
    expect(changed).toBe(false);
  });

  it('returns false on a path shorter than 4 numbers (< 2 points)', () => {
    const s = flatSampler(0.5);
    const changed = carveRoadCorridor(s, {
      path: [0, 0],
      width: 6,
      falloff: 2,
      window: 8,
    });
    expect(changed).toBe(false);
  });

  it('returns false when the path AABB is entirely outside the field', () => {
    const s = flatSampler(0.5, 64, 200);
    const changed = carveRoadCorridor(s, {
      path: [5000, 0, 6000, 0],
      width: 6,
      falloff: 2,
      window: 8,
    });
    expect(changed).toBe(false);
  });

  it('cuts a bump: terrace profile crushes the crest into a platform', () => {
    // Bump at origin, terrain base 50 m (0.5 norm), bump +20 m (0.2 norm).
    const s = bumpSampler(0.5, 0.2, 0, 0, 10);
    // Dense path along Z through the bump so the profile sees the bump.
    const changed = carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
      platformSink: 0.12,
    });
    expect(changed).toBe(true);
    // Centre was 70 m. Multi-pass terrace + sink must cut hard toward ~50
    // (may dip slightly below surrounding due to platformSink).
    const h = sampleHeightAt(s, 0, 0);
    expect(h).toBeLessThan(60);
    expect(h).toBeGreaterThan(45);
  });

  it('fills a valley: the corridor centre rises toward the terrace profile', () => {
    // Depression at origin: base 50 m, bump -20 m (0.2 → 0.3 norm = 30 m at centre).
    const s = bumpSampler(0.5, -0.2, 0, 0, 10);
    const changed = carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
      platformSink: 0,
    });
    expect(changed).toBe(true);
    // Centre was 30 m; terrace fills toward ~50 (no sink so stay below base).
    const h = sampleHeightAt(s, 0, 0);
    expect(h).toBeGreaterThan(35);
    expect(h).toBeLessThan(50);
  });

  it('noRaiseBelowY blocks fill into a pre-carved lake bowl', () => {
    // Depression = lake floor at ~30 m; banks at 50 m. Guard at waterY=35.
    const s = bumpSampler(0.5, -0.2, 0, 0, 10);
    const before = sampleHeightAt(s, 0, 0);
    carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
      platformSink: 0,
      noRaiseBelowY: 35,
    });
    const after = sampleHeightAt(s, 0, 0);
    // Floor must stay in the bowl — not terraced back up past the waterline.
    expect(after).toBeLessThanOrEqual(before + 0.05);
    expect(after).toBeLessThan(35);
  });

  it('preserveDiscs skip the lake footprint entirely (no shore shelf cut)', () => {
    const s = bumpSampler(0.5, 0.2, 0, 0, 12);
    const before = sampleHeightAt(s, 0, 0);
    carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
      platformSink: 0,
      preserveDiscs: [{ x: 0, z: 0, r: 14 }],
    });
    // Inside the disc the bump must be untouched (road would otherwise cut it).
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(before, 3);
  });

  it('does not touch terrain beyond the lateral reach (width/2 + falloff)', () => {
    const s = bumpSampler(0.5, 0.2, 0, 0, 10);
    const before = sampleHeightAt(s, 40, 0); // far from the path
    carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
    });
    const after = sampleHeightAt(s, 40, 0);
    expect(after).toBeCloseTo(before, 4);
  });

  it('runs once and mutates the sampler in place (single-pass design)', () => {
    // The carve is designed to run exactly once per road (RoadApplySystem
    // latches `applied=1`). It recomputes the profile from the current sampler
    // state, so it is not strictly idempotent — but a single pass must lower
    // the bump centre toward the surrounding terrain.
    const s = bumpSampler(0.5, 0.2, 0, 0, 10);
    const before = sampleHeightAt(s, 0, 0);
    carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
    });
    const after = sampleHeightAt(s, 0, 0);
    expect(after).toBeLessThan(before);
  });

  it('is a no-op on flat terrain when platformSink is 0', () => {
    const s = flatSampler(0.5);
    const changed = carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
      platformSink: 0,
    });
    // Flat → terrace == terrain; sink 0 → no texel change.
    expect(changed).toBe(false);
  });

  it('platformSink lowers a flat bed below the surrounding grade', () => {
    const s = flatSampler(0.5);
    const before = sampleHeightAt(s, 0, 0);
    const changed = carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
      platformSink: 0.12,
    });
    expect(changed).toBe(true);
    const after = sampleHeightAt(s, 0, 0);
    expect(after).toBeLessThan(before - 0.05);
    // Outside the corridor: untouched.
    expect(sampleHeightAt(s, 40, 0)).toBeCloseTo(before, 2);
  });

  it('smooths the longitudinal profile: a single-station spike is averaged down', () => {
    // Narrow spike (radius 2) at the midpoint of a long path through flat terrain.
    const s = bumpSampler(0.5, 0.3, 0, 0, 2);
    carveRoadCorridor(s, {
      path: densePath(0, -30, 0, 30),
      width: 4,
      falloff: 2,
      window: 20, // wide window → strong smoothing
    });
    const h = sampleHeightAt(s, 0, 0);
    // Without smoothing the spike (80 m) would be the target; with a 20 m window
    // the profile averages it toward the surrounding 50 m.
    expect(h).toBeLessThan(80);
  });

  it('lowers terrain inside the corridor and preserves it outside', () => {
    // Bump at origin; path along Z through it.
    const s = bumpSampler(0.5, 0.2, 0, 0, 10);
    carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
    });
    // Inside corridor (x=0): lowered (was ~70, now < 70).
    expect(sampleHeightAt(s, 0, 0)).toBeLessThan(70);
    // Outside reach (x=40): untouched (was 50, still 50).
    expect(sampleHeightAt(s, 40, 0)).toBeCloseTo(50, 2);
  });

  it('levels the roadbed laterally (prepared platform across width)', () => {
    const s = bumpSampler(0.5, 0.25, 3, 0, 8);
    carveRoadCorridor(s, {
      path: densePath(0, -30, 0, 30),
      width: 6,
      falloff: 2,
      window: 8,
      maxGrade: 0.18,
    });
    const c = sampleHeightAt(s, 0, 0);
    const l = sampleHeightAt(s, -2.5, 0);
    const r = sampleHeightAt(s, 2.5, 0);
    expect(Math.abs(l - c)).toBeLessThan(0.35);
    expect(Math.abs(r - c)).toBeLessThan(0.35);
  });

  it('uniform gentle grade keeps slope; only platformSink shifts absolute height', () => {
    // Constant ~5% world grade — under 18% max grade → terrace follows slope.
    const s = flatSampler(0.4, 512, 200);
    if (!s.data) throw new Error('expected data');
    const half = 100;
    const step = 200 / (512 - 1);
    for (let zi = 0; zi < 512; zi++) {
      const wz = zi * step - half;
      const h = 0.4 + (wz / 200) * 0.1;
      for (let xi = 0; xi < 512; xi++) {
        s.data[zi * 512 + xi] = h;
      }
    }
    const before = sampleHeightAt(s, 0, 0);
    carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 5,
      falloff: 2.5,
      window: 8,
      maxGrade: 0.18,
      platformSink: 0.12,
    });
    const after = sampleHeightAt(s, 0, 0);
    // Sink alone (~0.12 m); no dune-busting cut on a uniform grade.
    expect(Math.abs(after - (before - 0.12))).toBeLessThan(0.35);
  });
});

describe('limitProfileGrade / designRoadProfile', () => {
  it('clamps a spike so successive grades stay within maxSlope', () => {
    const arcs = [0, 10, 20, 30];
    const heights = [0, 0, 20, 0]; // 200% then -200%
    const out = limitProfileGrade(arcs, heights, 0.2);
    for (let i = 1; i < out.length; i++) {
      const ds = arcs[i]! - arcs[i - 1]!;
      expect(Math.abs(out[i]! - out[i - 1]!)).toBeLessThanOrEqual(
        0.2 * ds + 1e-6
      );
    }
  });

  it('designRoadProfile = multi-pass terrace smooth then grade limit', () => {
    const arcs = [0, 5, 10, 15, 20];
    const heights = [10, 10, 40, 10, 10];
    const designed = designRoadProfile(arcs, heights, 6, 0.15);
    // Multi-pass crush mid spike harder than a single light smooth.
    expect(designed[2]!).toBeLessThan(28);
    expect(designed[2]!).toBeGreaterThan(10);
  });
});

describe('clipPathApproaches / carveRoadApproaches', () => {
  it('keeps only tip stubs (mid-span omitted)', () => {
    const path = [0, 0, 10, 0, 20, 0, 30, 0, 40, 0];
    const stubs = clipPathApproaches(path, 8);
    expect(stubs.length).toBe(2);
    // Start stub near 0; end stub near 40.
    expect(stubs[0]![0]).toBeCloseTo(0);
    expect(stubs[1]![stubs[1]!.length - 2]).toBeCloseTo(40);
    // No stub covers mid (20).
    for (const stub of stubs) {
      for (let i = 0; i < stub.length; i += 2) {
        expect(Math.abs(stub[i]! - 20)).toBeGreaterThan(5);
      }
    }
  });

  it('bridgeApproachStubs include landward ground; into-span stays tiny', () => {
    const path = [-20, 0, 0, 0, 20, 0];
    const into = 2;
    const stubs = bridgeApproachStubs(path, into, BRIDGE_LANDWARD_METERS);
    expect(stubs.length).toBe(2);
    // Start: landward west of -20, then tip, then tiny into span.
    expect(stubs[0]![0]).toBeCloseTo(-20 - BRIDGE_LANDWARD_METERS);
    expect(stubs[0]![2]).toBeCloseTo(-20);
    expect(stubs[0]![4]).toBeCloseTo(-18);
    // End: tiny into, tip, landward east of 20.
    expect(stubs[1]![0]).toBeCloseTo(18);
    expect(stubs[1]![2]).toBeCloseTo(20);
    expect(stubs[1]![4]).toBeCloseTo(20 + BRIDGE_LANDWARD_METERS);
    // Mid-span (0) never on stub verts — into capped so channel stays clear.
    for (const stub of stubs) {
      for (let i = 0; i < stub.length; i += 2) {
        expect(Math.abs(stub[i]!)).toBeGreaterThan(5);
      }
    }
  });

  it('skips approach carve on coarse heightmaps (would plug the river)', () => {
    // world 4000 / 64 ≈ simple-rpg — texel ~63 m ≥ BRIDGE_SKIP_CARVE_TEXEL_M.
    const coarse: HeightSampler = {
      width: 64,
      height: 64,
      data: new Float32Array(64 * 64).fill(0.3),
      worldSize: 4000,
      maxHeight: 200,
    };
    expect(shouldCarveBridgeApproaches(coarse)).toBe(false);
    const yBefore = sampleHeightAt(coarse, 0, 0);
    const changed = carveRoadApproaches(coarse, {
      path: [-20, 0, 0, 0, 20, 0],
      width: 6,
      falloff: 4,
      window: 8,
      flatTargetY: 40,
    });
    expect(changed).toBe(false);
    expect(sampleHeightAt(coarse, 0, 0)).toBeCloseTo(yBefore, 5);
  });

  it('carveRoadApproaches does not raise the mid-span channel', () => {
    // Deep trench at x=0 mid; banks high at ±20.
    const s = bumpSampler(0.5, -0.4, 0, 0, 8, 256, 80);
    const path = [-20, 0, 0, 0, 20, 0];
    const yMidBefore = sampleHeightAt(s, 0, 0);
    carveRoadApproaches(s, {
      path,
      width: 4,
      falloff: 2,
      window: 8,
      maxGrade: 0.22,
      approachMeters: 6,
      landwardMeters: 8,
    });
    const yMidAfter = sampleHeightAt(s, 0, 0);
    // Mid channel must not be filled toward bank height.
    expect(yMidAfter).toBeLessThanOrEqual(yMidBefore + 0.02);
  });

  it('flatTargetY levels both bank stubs to the same lip', () => {
    // Uneven banks: left high, right low; mid trench.
    const s = flatSampler(0.3, 256, 80);
    if (!s.data) return;
    const half = 40;
    const step = 80 / 255;
    for (let zi = 0; zi < 256; zi++) {
      for (let xi = 0; xi < 256; xi++) {
        const wx = xi * step - half;
        // Left bank ~40, right ~20, mid ditch ~5.
        let h = 0.25;
        if (wx < -8) h = 0.4;
        else if (wx > 8) h = 0.2;
        else h = 0.05;
        s.data[zi * 256 + xi] = h;
      }
    }
    const path = [-20, 0, 0, 0, 20, 0];
    const lip = 30; // field metres (maxHeight=100 → norm 0.3)
    carveRoadApproaches(s, {
      path,
      width: 6,
      falloff: 3,
      window: 8,
      approachMeters: 8,
      landwardMeters: 8,
      flatTargetY: lip,
    });
    // Landward seat + tip region (not the riverward-only ±18 of old stubs).
    const yL = sampleHeightAt(s, -24, 0);
    const yR = sampleHeightAt(s, 24, 0);
    const yTipL = sampleHeightAt(s, -18, 0);
    const yTipR = sampleHeightAt(s, 18, 0);
    const yMid = sampleHeightAt(s, 0, 0);
    expect(yL).toBeCloseTo(lip, 0);
    expect(yR).toBeCloseTo(lip, 0);
    expect(yTipL).toBeCloseTo(lip, 0);
    expect(yTipR).toBeCloseTo(lip, 0);
    expect(Math.abs(yL - yR)).toBeLessThan(0.5);
    // Mid still ditch — not filled to lip.
    expect(yMid).toBeLessThan(lip - 5);
  });
});
