import { describe, expect, it } from 'bun:test';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import {
  carveBridgeDeckClearance,
  carveRoadCorridor,
} from '../../../src/plugins/road/carve';

/**
 * Cell-aware carve guard: the stamp describes the design surface at texel
 * CENTRES, but every consumer (chunk meshes, Rapier heightfields, ribbons)
 * reconstructs the terrain by bilinear interpolation between centres. A texel
 * centred in the falloff band holds `natural + (design − natural)·w` — metres
 * above the bed in a deep cut — and its cell reaches half a texel diagonal
 * toward the axis. These tests pin the contract: the reconstruction never
 * rises above the design surface inside the full-weight band.
 */

/** Coarse plateau sampler — texel step dominates the bed width, as in the racer. */
function plateauSampler(
  heightM: number,
  size = 65,
  world = 200,
  maxHeight = 40
): HeightSampler {
  return {
    width: size,
    height: size,
    data: new Float32Array(size * size).fill(heightM / maxHeight),
    worldSize: world,
    maxHeight,
  };
}

/** Dense straight polyline at ~1 m spacing (the carve samples per station). */
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

/** Per-node constant list (path is flat: one value per NODE, not per number). */
const perNode = (path: number[], v: number): number[] =>
  Array.from({ length: path.length / 2 }, () => v);

const TOL = 0.08;

describe('carveRoadCorridor — cell guard', () => {
  it('keeps the reconstructed bed at the authored plane through a deep cut', () => {
    const s = plateauSampler(20);
    const path = densePath(0, -60, 0, 60);
    const changed = carveRoadCorridor(s, {
      path,
      width: 8,
      falloff: 8,
      window: 20,
      profileY: perNode(path, 5),
      platformSink: 0,
      maxCutSlope: 0,
    });
    expect(changed).toBe(true);

    // Bed interior (and the bed edge, where the leaking cells live): the
    // bilinear reconstruction must sit on the 5 m design plane, not climb
    // toward the 20 m plateau the moment the bed ends.
    for (let z = -40; z <= 40; z += 5) {
      for (let x = -4; x <= 4; x += 1) {
        const h = sampleHeightAt(s, x, z);
        expect(h).toBeGreaterThan(5 - TOL);
        expect(h).toBeLessThan(5 + TOL);
      }
    }
    // Untouched plateau beyond the corridor reach (half 4 + falloff 8).
    expect(sampleHeightAt(s, 20, 0)).toBeCloseTo(20, 1);
  });

  it('keeps the flat run-off clear of wall texels (shoulder band)', () => {
    const s = plateauSampler(20);
    const path = densePath(0, -60, 0, 60);
    carveRoadCorridor(s, {
      path,
      width: 8,
      falloff: 8,
      window: 20,
      shoulderWidth: 4,
      profileY: perNode(path, 5),
      platformSink: 0,
      maxCutSlope: 0,
    });
    // The run-off is carved flat at bed level up to half (4) + shoulder (4);
    // wall cells may not poke over it.
    for (let z = -40; z <= 40; z += 8) {
      for (let x = -7.5; x <= 7.5; x += 1.5) {
        expect(sampleHeightAt(s, x, z)).toBeLessThan(5 + TOL);
      }
    }
  });

  it('does not dig embankment fills in valleys (lower-only)', () => {
    const s = plateauSampler(2);
    const path = densePath(0, -60, 0, 60);
    carveRoadCorridor(s, {
      path,
      width: 8,
      falloff: 8,
      window: 20,
      profileY: perNode(path, 5),
      platformSink: 0,
    });
    // Natural is BELOW the bed: the corridor fills to the design plane and
    // the guard must not cut that fill back down. (At the exact bed edge the
    // falloff blend legitimately dips — the guard is lower-only.)
    for (let z = -40; z <= 40; z += 8) {
      for (let x = -3; x <= 3; x += 1.5) {
        expect(sampleHeightAt(s, x, z)).toBeGreaterThan(5 - TOL);
      }
    }
  });

  it('is a no-op when the terrain already matches the design (no spurious digging)', () => {
    const s = plateauSampler(20);
    const path = densePath(0, -60, 0, 60);
    const before = Float32Array.from(s.data!);
    carveRoadCorridor(s, {
      path,
      width: 8,
      falloff: 8,
      window: 20,
      platformSink: 0,
    });
    expect(Array.from(s.data!)).toEqual(Array.from(before));
  });

  it('re-carves idempotently through the owner journal (guard included)', () => {
    const s = plateauSampler(20);
    const path = densePath(0, -60, 0, 60);
    const carve = (owner?: string) =>
      carveRoadCorridor(s, {
        path,
        width: 8,
        falloff: 8,
        window: 20,
        profileY: perNode(path, 5),
        platformSink: 0,
        owner,
      });
    carve('road:1');
    const afterFirst = Float32Array.from(s.data!);
    carve('road:1');
    carve('road:1');
    expect(Array.from(s.data!)).toEqual(Array.from(afterFirst));
  });

  it('preserves banked beds (the guard clamps walls, not the bed tilt)', () => {
    const s = plateauSampler(20);
    const path = densePath(0, -60, 0, 60);
    const bank = 0.3;
    carveRoadCorridor(s, {
      path,
      width: 8,
      falloff: 8,
      window: 20,
      banks: perNode(path, bank),
      profileY: perNode(path, 5),
      platformSink: 0,
    });
    // Bed plane: y = 5 + lat·sin(bank) — positive bank raises the driver's
    // right (+x for a +z heading). The reconstruction must follow the tilt,
    // proving the guard did not flatten it toward the centreline.
    //
    // Tolerance is 0.1 m, not 0.05: the sampler reconstructs with a
    // Catmull-Rom stencil two texels wide, so a probe 1.5 m inside an 8 m bed
    // still reaches the cut wall and is pulled ~8 cm toward it. The tilt is
    // what this test is about and it survives intact (5.82 against 5.0 for a
    // flat bed); the bilinear taps this replaced were exact here but faceted
    // every natural slope in the game.
    const tilt = 2.5 * Math.sin(bank);
    expect(sampleHeightAt(s, 2.5, 0)).toBeCloseTo(5 + tilt, 0.1);
    expect(sampleHeightAt(s, -2.5, 0)).toBeCloseTo(5 - tilt, 0.1);
    // ...and inside the bed the wall texels cannot poke over the tilted
    // plane (the guard may sit slightly under it — conservative direction).
    for (const x of [-3.5, 3.5]) {
      expect(sampleHeightAt(s, x, 0)).toBeLessThan(
        5 + Math.abs(x) * Math.sin(bank) + TOL
      );
    }
  });
});

describe('carveRoadCorridor — closest-elevation band priority', () => {
  /**
   * The corridor reach grows with the widest adaptive falloff on the circuit,
   * so `nearestCorridorPasses` can return distant stretches of the corridor
   * itself (or a far arm) whose design bed sits nearer the natural ground.
   * Elevation alone would let such a pass stamp its feather over a local
   * FULL-WEIGHT bed — a smooth ridge of uncarved terrain down the road.
   * A pass whose solid band contains the texel must always win.
   */
  it('a full-weight bed beats a far pass whose bed is nearer the natural ground', () => {
    const s = plateauSampler(20);
    // Near arm: straight along z at x = 0, bed 10; dense enough (>24 segments)
    // for the corridor index, which is what makes closest-elevation active.
    const near = densePath(0, -80, 0, 80);
    // Far arm: 35 m to the right, bed 18 — closer to the natural 20, with a
    // falloff long enough that its feather covers the near bed.
    const far = densePath(35, -80, 35, 80);
    carveRoadCorridor(s, {
      path: far,
      width: 8,
      falloff: 4,
      window: 20,
      profileY: perNode(far, 18),
      platformSink: 0,
      maxCutSlope: 0,
      owner: 'far',
    });
    carveRoadCorridor(s, {
      path: near,
      width: 20,
      falloff: 30,
      window: 20,
      profileY: perNode(near, 10),
      platformSink: 0,
      overlapMode: 'closest-elevation',
      owner: 'near',
    });
    // The near bed must sit on its design plane, not on the far arm's feather.
    for (const x of [-8, -4, 0, 4, 8]) {
      expect(sampleHeightAt(s, x, 0)).toBeLessThan(10 + TOL);
      expect(sampleHeightAt(s, x, 0)).toBeGreaterThan(10 - TOL);
    }
  });
});

describe('carveRoadCorridor — adaptive cut-wall falloff', () => {
  it('widens the shoulder with the cut depth and reports the effective reach', () => {
    const s = plateauSampler(20);
    const path = densePath(0, -60, 0, 60);
    let reach = 0;
    carveRoadCorridor(s, {
      path,
      width: 8,
      falloff: 4,
      window: 20,
      profileY: perNode(path, 5),
      platformSink: 0,
      onEffectiveReach: (r) => {
        reach = r;
      },
    });
    // Depth 15 m at slope 1.0 → fallEff = 1.875·15 = 28.125, reach = 4 + 28.125.
    expect(reach).toBeCloseTo(4 + (1.875 * 15) / 1.0, 0);
    // Mid-wall sits roughly halfway between bed (5) and natural (20); the
    // natural relief is back beyond the widened shoulder.
    const mid = sampleHeightAt(s, 18, 0);
    expect(mid).toBeGreaterThan(9);
    expect(mid).toBeLessThan(16);
    expect(sampleHeightAt(s, 38, 0)).toBeCloseTo(20, 0);
  });

  it('maxCutSlope=0 keeps the authored falloff fixed', () => {
    const s = plateauSampler(20);
    const path = densePath(0, -60, 0, 60);
    let reach = 0;
    carveRoadCorridor(s, {
      path,
      width: 8,
      falloff: 4,
      window: 20,
      profileY: perNode(path, 5),
      platformSink: 0,
      maxCutSlope: 0,
      onEffectiveReach: (r) => {
        reach = r;
      },
    });
    // minEffectiveFalloff clamps the authored 4 m to 1.5 texels (4.6875 m).
    expect(reach).toBeCloseTo(4 + 4.6875, 1);
    expect(sampleHeightAt(s, 12, 0)).toBeCloseTo(20, 0);
  });
});

describe('carveBridgeDeckClearance — cell guard', () => {
  it('keeps terrain under the deck lane below the deck underside', () => {
    const s = plateauSampler(12);
    const path = densePath(0, -40, 0, 40);
    const changed = carveBridgeDeckClearance(s, {
      path,
      width: 6,
      falloff: 6,
      deckYAt: () => 8,
      undercut: 0.15,
    });
    expect(changed).toBe(true);
    for (let z = -30; z <= 30; z += 5) {
      for (let x = -3; x <= 3; x += 1) {
        expect(sampleHeightAt(s, x, z)).toBeLessThan(8 - 0.15 + TOL);
      }
    }
    expect(sampleHeightAt(s, 15, 0)).toBeCloseTo(12, 1);
  });
});

describe('carveRoadCorridor — viaduct deck clearance', () => {
  /**
   * Valley (5 m) along the centerline with a lateral hill (25 m) starting
   * 8 m off the axis: the authored bed flies 10 m above the valley floor
   * (> 6 m clearance), so the bed carve must leave the ground alone — but
   * the hill still crosses the deck footprint and has to be cut.
   */
  function valleyAndHillSampler(): HeightSampler {
    const size = 65;
    const world = 200;
    const maxHeight = 40;
    const data = new Float32Array(size * size);
    const step = world / (size - 1);
    const half = world / 2;
    for (let zi = 0; zi < size; zi++) {
      for (let xi = 0; xi < size; xi++) {
        const wx = xi * step - half;
        const ax = Math.abs(wx);
        // 5 m valley inside |x| < 8, smooth rise to a 25 m hill by |x| = 14.
        const t = Math.min(1, Math.max(0, (ax - 8) / 6));
        const h = 5 + 20 * (t * t * (3 - 2 * t));
        data[zi * size + xi] = h / maxHeight;
      }
    }
    return { width: size, height: size, data, worldSize: world, maxHeight };
  }

  it('cuts a lateral hill under a flying span and leaves the valley alone', () => {
    const s = valleyAndHillSampler();
    const path = densePath(0, -50, 0, 50);
    const changed = carveRoadCorridor(s, {
      path,
      width: 30,
      falloff: 8,
      window: 20,
      profileY: path.map(() => 15),
      platformSink: 0,
      viaductClearance: 6,
    });
    expect(changed).toBe(true);
    // Hill inside the deck footprint (half = 15): below the deck underside.
    for (const x of [9, 12, 14.5]) {
      expect(sampleHeightAt(s, x, 0)).toBeLessThan(15 - 0.15 + TOL);
      expect(sampleHeightAt(s, -x, 0)).toBeLessThan(15 - 0.15 + TOL);
    }
    // Valley floor the span flies over: untouched (lower-only no-op).
    for (const x of [0, 3, 6]) {
      expect(sampleHeightAt(s, x, 0)).toBeLessThan(5 + TOL);
    }
    // Hill beyond the cut reach keeps its natural height.
    expect(sampleHeightAt(s, 40, 0)).toBeCloseTo(25, 1);
  });
});
