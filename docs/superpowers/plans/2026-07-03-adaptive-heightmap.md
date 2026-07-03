# Adaptive Heightmap — Variable Mesh Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make terrain mesh and collision resolution adapt per-region so `<Lake>` basins (and other featured areas) render at full heightfield detail, while plateaus spend fewer vertices — plus a higher-precision `.ahgt` disk format.

**Architecture:** A coarse density map (64×64 tiles) scores each world region by gradient/curvature and is bumped to max by `<Lake>` overrides. `TerrainLodSelectSystem` reads it via a new `effectiveResolution(base, level, boost)` layered on top of the existing camera-LOD `resolutionForLevel`. The height sampler and carve are unchanged; skirts (already in `chunk-geometry.ts`) cover the new resolution variance. A `.ahgt` format (uint16 + deflate) is added for disk precision, with PNG fallback for retrocompat.

**Tech Stack:** TypeScript, bitecs (ECS), Three.js (terrain mesh), Rapier (collision), Bun (`bun:test`), fflate (deflate, transitive via three).

## Global Constraints

- **Test runner:** `bun test` (`bun:test`'s `describe/it/expect`). Unit tests live in `VibeGame/tests/unit/terrain/` and `VibeGame/tests/unit/water/` (matching existing layout). Run via `make test-vibegame` from repo root (runs `bun install --frozen-lockfile` then `bun test tests/unit tests/integration tests/e2e`).
- **All commands run from `VibeGame/`** unless prefixed with `cd` to repo root for `make`.
- **Formatting:** Prettier. Run `make fmt-vibegame` before committing (or `bun run format` inside `VibeGame/`).
- **Typecheck:** `make check-vibegame` (tsc --noEmit) must pass before commit.
- **No behavior change when density boost = 0** — `effectiveResolution(base, level, 0)` MUST equal `resolutionForLevel(base, level)`. This is the retrocompat contract.
- **`height-sampler.ts`, `carve.ts`, `chunk-geometry.ts` must NOT be modified** in this plan — they're consumed unchanged.
- **Conventional Commits** (`feat:`, `test:`, etc.) — one commit per task.
- **Skirts already exist** at `VibeGame/src/plugins/terrain/chunk-geometry.ts:93-127` (`addSkirtStrip`); they cover T-junctions between chunks of differing resolution. Do not reimplement.
- **Density tiles default 64×64** over the world; boost is `Uint8Array` 0..255; max boost doubles LOD resolution, capped at `baseResolution`.

---

### Task 1: `effectiveResolution` in lod-select.ts

Adds the pure function that layers density boost onto the existing `resolutionForLevel`. No behavior change when boost = 0. This is the core primitive every later task consumes.

**Files:**
- Modify: `VibeGame/src/plugins/terrain/lod-select.ts` (add export after `resolutionForLevel` at line 149-154)
- Test: `VibeGame/tests/unit/terrain/effective-resolution.test.ts` (new)

**Interfaces:**
- Consumes: `resolutionForLevel(baseResolution, level)` already exported from `lod-select.ts:149`.
- Produces: `effectiveResolution(baseResolution: number, level: number, densityBoost: number): number` — when `densityBoost <= 0` returns exactly `resolutionForLevel(baseResolution, level)`; when `densityBoost === 255` returns `min(baseResolution, round(lodRes * 2))`; otherwise scales linearly in between.

- [ ] **Step 1: Write the failing test**

Create `VibeGame/tests/unit/terrain/effective-resolution.test.ts`:

```ts
import { describe, expect, it } from 'bun:test';
import {
  effectiveResolution,
  resolutionForLevel,
} from '../../../src/plugins/terrain/lod-select';

describe('effectiveResolution', () => {
  const base = 64;

  it('matches resolutionForLevel when boost is 0', () => {
    for (const level of [0, 1, 2, 3, 4, 5]) {
      expect(effectiveResolution(base, level, 0)).toBe(
        resolutionForLevel(base, level)
      );
    }
  });

  it('matches resolutionForLevel when boost is negative', () => {
    expect(effectiveResolution(base, 5, -1)).toBe(resolutionForLevel(base, 5));
  });

  it('doubles LOD resolution (capped at baseResolution) at max boost', () => {
    // LOD level 5: lodRes = max(4, 64>>5) = 4 → boosted = 8.
    expect(effectiveResolution(base, 5, 255)).toBe(8);
    // LOD level 0: lodRes = 64 → boosted would be 128, capped at base 64.
    expect(effectiveResolution(base, 0, 255)).toBe(64);
  });

  it('scales linearly for intermediate boost', () => {
    // lodRes at level 5 = 4. boost 128 ≈ factor 1.5 → round(4*1.5)=6.
    expect(effectiveResolution(base, 5, 128)).toBe(6);
  });

  it('never exceeds baseResolution', () => {
    for (const level of [0, 1, 2, 3]) {
      for (const boost of [0, 64, 128, 200, 255]) {
        expect(effectiveResolution(base, level, boost)).toBeLessThanOrEqual(base);
      }
    }
  });

  it('never goes below resolutionForLevel', () => {
    for (const level of [0, 1, 2, 3, 4, 5]) {
      for (const boost of [0, 1, 50, 200, 255]) {
        expect(effectiveResolution(base, level, boost)).toBeGreaterThanOrEqual(
          resolutionForLevel(base, level)
        );
      }
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/terrain/effective-resolution.test.ts`
Expected: FAIL — `effectiveResolution is not exported` (or `undefined is not a function`).

- [ ] **Step 3: Write minimal implementation**

Add to `VibeGame/src/plugins/terrain/lod-select.ts` after the existing `resolutionForLevel` (after line 154):

```ts
/**
 * Effective mesh resolution for a chunk, layering a spatial density boost on
 * top of the camera-LOD resolution. boost=0 (no featured region) reproduces
 * {@link resolutionForLevel} exactly — the retrocompat contract. boost=255
 * doubles the LOD resolution, capped at `baseResolution` so a boosted chunk
 * never exceeds the finest mesh the LOD system would ever produce.
 *
 * Intermediate boost scales linearly: factor = 1 + boost/255.
 *
 * @param baseResolution Terrain.resolution (e.g. 64)
 * @param level          LOD quadtree depth (0 = root)
 * @param densityBoost   0..255 from the DensityMap tiles the chunk overlaps
 */
export function effectiveResolution(
  baseResolution: number,
  level: number,
  densityBoost: number
): number {
  const lodRes = resolutionForLevel(baseResolution, level);
  if (densityBoost <= 0) return lodRes;
  const factor = 1 + densityBoost / 255;
  const boosted = Math.round(lodRes * factor);
  return Math.min(baseResolution, Math.max(lodRes, boosted));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd VibeGame && bun test tests/unit/terrain/effective-resolution.test.ts`
Expected: PASS — all 6 cases.

- [ ] **Step 5: Typecheck, format, commit**

```bash
cd VibeGame && bun run typecheck && bun run format
cd .. && git add VibeGame/src/plugins/terrain/lod-select.ts VibeGame/tests/unit/terrain/effective-resolution.test.ts
git commit -m "feat(vibegame): effectiveResolution layers density boost on LOD resolution"
```

---

### Task 2: DensityMap — build, override, query

Pure data structure: a coarse 64×64 grid of boost values over the world, built from the height sampler's gradient/curvature, with an override API for `<Lake>` and a max-over-AABB query for chunks. No ECS, no Three.js — pure functions, easy to test.

**Files:**
- Create: `VibeGame/src/plugins/terrain/density-map.ts` (new)
- Test: `VibeGame/tests/unit/terrain/density-map.test.ts` (new)

**Interfaces:**
- Consumes: `HeightSampler` from `height-sampler.ts:13` (read-only: `.data`, `.width`, `.height`, `.worldSize`, `.maxHeight`).
- Produces:
  - `interface DensityMap { tilesX: number; tilesZ: number; boost: Uint8Array; worldSize: number }`
  - `buildDensityMap(sampler, tilesPerAxis?, opts?): DensityMap`
  - `applyOverride(density, aabb, boost): void`
  - `boostAt(density, worldX, worldZ): number`
  - `maxBoostOverAabb(density, aabb): number`
  - `interface WorldAabb { minX: number; minZ: number; maxX: number; maxZ: number }`

- [ ] **Step 1: Write the failing test**

Create `VibeGame/tests/unit/terrain/density-map.test.ts`:

```ts
import { describe, expect, it } from 'bun:test';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import {
  applyOverride,
  boostAt,
  buildDensityMap,
  maxBoostOverAabb,
  type DensityMap,
  type WorldAabb,
} from '../../../src/plugins/terrain/density-map';

/** Build a sampler with a flat plane (height 0 everywhere) except a step. */
function syntheticSampler(
  size: number,
  worldSize: number,
  paint: (x: number, z: number) => number
): HeightSampler {
  const data = new Float32Array(size * size);
  const half = worldSize / 2;
  const step = worldSize / (size - 1);
  for (let z = 0; z < size; z++) {
    for (let x = 0; x < size; x++) {
      const wx = x * step - half;
      const wz = z * step - half;
      data[z * size + x] = paint(wx, wz);
    }
  }
  return { width: size, height: size, data, worldSize, maxHeight: 100 };
}

describe('buildDensityMap', () => {
  it('returns zero boost on a perfectly flat field', () => {
    const sampler = syntheticSampler(16, 100, () => 0.5);
    const dm = buildDensityMap(sampler, 8);
    expect(dm.tilesX).toBe(8);
    expect(dm.tilesZ).toBe(8);
    expect(dm.boost.every((b) => b === 0)).toBe(true);
  });

  it('assigns higher boost to a region with a height step than to a flat region', () => {
    // Left half flat at 0, right half flat at 1 → a step at x=0.
    const sampler = syntheticSampler(32, 100, (x) => (x < 0 ? 0 : 1));
    const dm = buildDensityMap(sampler, 8);
    // Tiles straddling x=0 (columns 3 and 4 of 8 over [-50,50]) should have
    // higher boost than far-flat tiles (column 0 or 7).
    const stepBoost = dm.boost[Math.floor(dm.tilesZ / 2) * dm.tilesX + 3];
    const flatBoost = dm.boost[Math.floor(dm.tilesZ / 2) * dm.tilesX + 0];
    expect(stepBoost).toBeGreaterThan(flatBoost);
  });
});

describe('applyOverride', () => {
  it('sets boost to the given value within the AABB and clamps to 255', () => {
    const sampler = syntheticSampler(16, 100, () => 0.5);
    const dm = buildDensityMap(sampler, 8);
    const aabb: WorldAabb = { minX: -10, minZ: -10, maxX: 10, maxZ: 10 };
    applyOverride(dm, aabb, 255);
    // Tile containing world (0,0): center of world is tile (4,4) for 8 tiles over 100m.
    expect(boostAt(dm, 0, 0)).toBe(255);
    // Far outside the AABB stays 0.
    expect(boostAt(dm, 40, 40)).toBe(0);
  });

  it('does not lower an existing higher boost (takes the max)', () => {
    const sampler = syntheticSampler(16, 100, () => 0.5);
    const dm = buildDensityMap(sampler, 8);
    applyOverride(dm, { minX: -10, minZ: -10, maxX: 10, maxZ: 10 }, 200);
    applyOverride(dm, { minX: -10, minZ: -10, maxX: 10, maxZ: 10 }, 100);
    expect(boostAt(dm, 0, 0)).toBe(200);
  });
});

describe('maxBoostOverAabb', () => {
  it('returns the maximum boost among tiles intersecting the chunk AABB', () => {
    const dm: DensityMap = {
      tilesX: 8,
      tilesZ: 8,
      boost: new Uint8Array(64),
      worldSize: 100,
    };
    dm.boost[0] = 10;
    dm.boost[63] = 250;
    // AABB covering only the bottom-left tile.
    expect(
      maxBoostOverAabb(dm, { minX: -50, minZ: -50, maxX: -40, maxZ: -40 })
    ).toBe(10);
    // AABB covering the whole world.
    expect(
      maxBoostOverAabb(dm, { minX: -50, minZ: -50, maxX: 50, maxZ: 50 })
    ).toBe(250);
  });
});

describe('boostAt', () => {
  it('clamps out-of-world coordinates to the nearest edge tile', () => {
    const dm: DensityMap = {
      tilesX: 4,
      tilesZ: 4,
      boost: new Uint8Array(16).fill(7),
      worldSize: 100,
    };
    expect(boostAt(dm, 999, 999)).toBe(7);
    expect(boostAt(dm, -999, -999)).toBe(7);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/terrain/density-map.test.ts`
Expected: FAIL — module not found (`Cannot find module .../density-map`).

- [ ] **Step 3: Write minimal implementation**

Create `VibeGame/src/plugins/terrain/density-map.ts`:

```ts
import type { HeightSampler } from './height-sampler';

/** Axis-aligned bounding box in field-local world space (X/Z, metres). */
export interface WorldAabb {
  minX: number;
  minZ: number;
  maxX: number;
  maxZ: number;
}

/**
 * Coarse grid of per-region density boosts layered over a terrain field.
 * One entry per tile; value 0..255 indicates how much extra mesh resolution
 * the region deserves (consumed by {@link effectiveResolution}).
 */
export interface DensityMap {
  tilesX: number;
  tilesZ: number;
  /** Row-major `tilesX*tilesZ` boost values, 0..255. */
  boost: Uint8Array;
  /** World extent the tiles span (matches sampler.worldSize). */
  worldSize: number;
}

export interface BuildDensityOptions {
  /** Weight of local height variance in the score (default 1.0). */
  varianceWeight?: number;
  /**
   * Normalized variance threshold above which a tile gets a non-zero boost
   * (default 0.02). Tiles below the threshold stay at 0.
   */
  threshold?: number;
}

/** Tile index (clamped) for a world coordinate. */
function tileIndex(density: DensityMap, worldX: number, worldZ: number): {
  tx: number;
  tz: number;
} {
  const half = density.worldSize / 2;
  const nx = (worldX + half) / density.worldSize; // [0,1]
  const nz = (worldZ + half) / density.worldSize;
  const tx = Math.min(
    density.tilesX - 1,
    Math.max(0, Math.floor(nx * density.tilesX))
  );
  const tz = Math.min(
    density.tilesZ - 1,
    Math.max(0, Math.floor(nz * density.tilesZ))
  );
  return { tx, tz };
}

/**
 * Build a density map from a height sampler by scoring each tile's local
 * height variance. Flat regions score 0; featured regions (canyons, ridges,
 * lake beds) score high. The sampler is read-only and may be flat (then every
 * tile is 0).
 */
export function buildDensityMap(
  sampler: HeightSampler,
  tilesPerAxis = 64,
  opts: BuildDensityOptions = {}
): DensityMap {
  const varianceWeight = opts.varianceWeight ?? 1.0;
  const threshold = opts.threshold ?? 0.02;

  const tilesX = Math.max(1, Math.floor(tilesPerAxis));
  const tilesZ = tilesX;
  const boost = new Uint8Array(tilesX * tilesZ);

  const { data, width, height, worldSize } = sampler;
  if (!data || width < 2 || height < 2) {
    return { tilesX, tilesZ, boost, worldSize };
  }

  // Sample grid: for each tile, probe a few heights and compute variance.
  const probes = 4; // 4x4 probes per tile
  for (let tz = 0; tz < tilesZ; tz++) {
    for (let tx = 0; tx < tilesX; tx++) {
      const samples: number[] = [];
      for (let sz = 0; sz < probes; sz++) {
        for (let sx = 0; sx < probes; sx++) {
          const u = (tx + (sx + 0.5) / probes) / tilesX;
          const v = (tz + (sz + 0.5) / probes) / tilesZ;
          const gx = Math.min(width - 1, Math.floor(u * (width - 1)));
          const gz = Math.min(height - 1, Math.floor(v * (height - 1)));
          samples.push(data[gz * width + gx] ?? 0);
        }
      }
      const mean =
        samples.reduce((a, b) => a + b, 0) / Math.max(1, samples.length);
      const variance =
        samples.reduce((a, b) => a + (b - mean) * (b - mean), 0) /
        Math.max(1, samples.length);
      const score = variance * varianceWeight;
      if (score <= threshold) {
        boost[tz * tilesX + tx] = 0;
      } else {
        // Map (threshold, +∞) → (0, 255] with a soft curve; cap at 255.
        const over = score - threshold;
        const mapped = Math.min(255, Math.round(over * 4000));
        boost[tz * tilesX + tx] = Math.max(0, mapped);
      }
    }
  }
  return { tilesX, tilesZ, boost, worldSize };
}

/** Boost at a world point, clamped to the nearest edge tile. */
export function boostAt(
  density: DensityMap,
  worldX: number,
  worldZ: number
): number {
  const { tx, tz } = tileIndex(density, worldX, worldZ);
  return density.boost[tz * density.tilesX + tx] ?? 0;
}

/**
 * Force a minimum boost on every tile intersecting the AABB. Takes the max
 * with the existing value so multiple overrides compose safely (e.g. several
 * lakes, or a lake over a naturally featured region).
 */
export function applyOverride(
  density: DensityMap,
  aabb: WorldAabb,
  boost: number
): void {
  const clamped = Math.max(0, Math.min(255, Math.round(boost)));
  const min = tileIndex(density, aabb.minX, aabb.minZ);
  const max = tileIndex(density, aabb.maxX, aabb.maxZ);
  for (let tz = min.tz; tz <= max.tz; tz++) {
    for (let tx = min.tx; tx <= max.tx; tx++) {
      const i = tz * density.tilesX + tx;
      if (clamped > (density.boost[i] ?? 0)) density.boost[i] = clamped;
    }
  }
}

/**
 * Maximum boost over all tiles intersecting the AABB. Used by
 * {@link TerrainLodSelectSystem} to pick a chunk's effective resolution: a
 * chunk that merely touches a featured region adopts that region's boost,
 * avoiding intra-chunk resolution cracks.
 */
export function maxBoostOverAabb(
  density: DensityMap,
  aabb: WorldAabb
): number {
  const min = tileIndex(density, aabb.minX, aabb.minZ);
  const max = tileIndex(density, aabb.maxX, aabb.maxZ);
  let best = 0;
  for (let tz = min.tz; tz <= max.tz; tz++) {
    for (let tx = min.tx; tx <= max.tx; tx++) {
      const v = density.boost[tz * density.tilesX + tx] ?? 0;
      if (v > best) best = v;
    }
  }
  return best;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd VibeGame && bun test tests/unit/terrain/density-map.test.ts`
Expected: PASS — all cases (flat → 0, step → high, override clamp/max, AABB max).

- [ ] **Step 5: Typecheck, format, commit**

```bash
cd VibeGame && bun run typecheck && bun run format
cd .. && git add VibeGame/src/plugins/terrain/density-map.ts VibeGame/tests/unit/terrain/density-map.test.ts
git commit -m "feat(vibegame): DensityMap — per-region mesh density boost"
```

---

### Task 3: Wire DensityMap into TerrainLodSelectSystem

Thread the density map from bootstrap to LOD selection. Bootstrap builds it after the heightmap loads; LOD select reads `maxBoostOverAabb` per chunk and calls `effectiveResolution`. The flat-sampler (pre-heightmap) path skips density (boost stays 0 → retrocompat).

**Files:**
- Modify: `VibeGame/src/plugins/terrain/utils.ts` (add `density?: DensityMap` to `TerrainEntityData`)
- Modify: `VibeGame/src/plugins/terrain/systems.ts` (bootstrap builds density after heightmap load at ~line 643-651; `TerrainLodSelectSystem` uses `effectiveResolution` at ~line 816)
- Test: extend `VibeGame/tests/unit/terrain/terrain-height-sampler.test.ts` is NOT the right place; add a focused test in `VibeGame/tests/unit/terrain/lod-density.test.ts` (new) that verifies resolution bumps when a density map is present.

**Interfaces:**
- Consumes: `DensityMap`, `buildDensityMap`, `maxBoostOverAabb` from Task 2; `effectiveResolution` from Task 1.
- Produces: `TerrainEntityData.density?: DensityMap` (new optional field); `TerrainChunk.resolution[chunk]` now reflects density.

- [ ] **Step 1: Read current utils.ts TerrainEntityData shape**

Run: `cd VibeGame && sed -n '1,40p' src/plugins/terrain/utils.ts`
Confirm the `TerrainEntityData` interface fields so the new `density?` field is added in the right place. (The interface is referenced at `systems.ts:624-637` and `terrain-height-sampler.test.ts:16`.)

- [ ] **Step 2: Write the failing test**

Create `VibeGame/tests/unit/terrain/lod-density.test.ts`:

```ts
import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import { Terrain } from '../../../src/plugins/terrain/components';
import {
  effectiveResolution,
  resolutionForLevel,
  selectChunks,
} from '../../../src/plugins/terrain/lod-select';
import {
  buildDensityMap,
  applyOverride,
  maxBoostOverAabb,
} from '../../../src/plugins/terrain/density-map';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { getTerrainContext } from '../../../src/plugins/terrain/utils';

function flatSampler(): HeightSampler {
  return {
    width: 2,
    height: 2,
    data: new Float32Array([0.5, 0.5, 0.5, 0.5]),
    worldSize: 100,
    maxHeight: 10,
  };
}

describe('LOD + density integration', () => {
  it('a chunk overlapping a max-boost region resolves to higher resolution than camera-LOD alone', () => {
    const base = 64;
    const sampler = flatSampler();
    const density = buildDensityMap(sampler, 8);
    // Mark a region around the world origin as maximally important.
    applyOverride(density, { minX: -10, minZ: -10, maxX: 10, maxZ: 10 }, 255);

    // Simulate a chunk at LOD level 5 centred on the origin (size = worldSize/32).
    const worldSize = sampler.worldSize;
    const level = 5;
    const chunkSize = worldSize / Math.pow(2, level);
    const desc = selectChunks(worldSize, 6, 2.0, 1.2, 0, 0).find(
      (d) => d.level === level
    );
    expect(desc).toBeDefined();
    const d = desc!;
    const aabb = {
      minX: d.originX - chunkSize / 2,
      minZ: d.originZ - chunkSize / 2,
      maxX: d.originX + chunkSize / 2,
      maxZ: d.originZ + chunkSize / 2,
    };
    const boost = maxBoostOverAabb(density, aabb);
    const lodRes = resolutionForLevel(base, level);
    const effRes = effectiveResolution(base, level, boost);
    // The chunk at the origin overlaps the boosted region → higher resolution.
    if (boost > 0) {
      expect(effRes).toBeGreaterThan(lodRes);
    }
    // Sanity: max-boost doubles, capped at base.
    expect(effRes).toBeLessThanOrEqual(base);
  });

  it('without a density boost, effective resolution equals camera-LOD resolution', () => {
    const base = 64;
    const sampler = flatSampler();
    const density = buildDensityMap(sampler, 8); // flat → all zeros
    const aabb = { minX: -5, minZ: -5, maxX: 5, maxZ: 5 };
    const boost = maxBoostOverAabb(density, aabb);
    expect(boost).toBe(0);
    expect(effectiveResolution(base, 5, boost)).toBe(resolutionForLevel(base, 5));
  });
});
```

- [ ] **Step 3: Run test to verify it fails (or passes trivially before wiring)**

Run: `cd VibeGame && bun test tests/unit/terrain/lod-density.test.ts`
Expected: This test exercises already-implemented pure functions (Tasks 1 & 2). It should PASS immediately — it documents the contract the wiring must preserve. If it FAILS, fix the pure functions first. Once green, proceed to wire ECS.

- [ ] **Step 4: Add `density?` to TerrainEntityData**

Edit `VibeGame/src/plugins/terrain/utils.ts`. Read it first, then add the import and field. The edit:

```ts
// At top, add to imports:
import type { DensityMap } from './density-map';

// In the TerrainEntityData interface, add:
export interface TerrainEntityData {
  // ... existing fields ...
  /** Per-region mesh density boost, built after the heightmap loads. */
  density?: DensityMap;
}
```

- [ ] **Step 5: Build density in TerrainFieldBootstrapSystem after heightmap load**

In `VibeGame/src/plugins/terrain/systems.ts`, find the `.then((imgData) => {...})` block inside `TerrainFieldBootstrapSystem` (around line 643-664). After `data.sampler = createHeightmapSampler(...)`, add density construction. Add the import at the top of the file:

```ts
import { buildDensityMap } from './density-map';
```

Then inside the `.then(...)` callback, right after `data.sampler = createHeightmapSampler(worldSize, maxHeight, imgData);`:

```ts
data.sampler = createHeightmapSampler(worldSize, maxHeight, imgData);
// Build the per-region density map from the freshly loaded heightfield.
// Flat regions score 0 (retrocompat); featured regions get a boost that
// TerrainLodSelectSystem layers onto camera-LOD resolution.
data.density = buildDensityMap(data.sampler, 64);
invalidateTerrainBvh(state, field);
```

Also update the retry path (~line 977) the same way:

```ts
data.sampler = createHeightmapSampler(
  Terrain.worldSize[entity],
  Terrain.maxHeight[entity],
  imgData
);
data.density = buildDensityMap(data.sampler, 64);
for (const chunk of data.chunks) {
  TerrainChunk.meshDirty[chunk] = 1;
}
```

- [ ] **Step 6: Use effectiveResolution in TerrainLodSelectSystem**

In `VibeGame/src/plugins/terrain/systems.ts`, find the chunk-creation block in `TerrainLodSelectSystem` (around line 814-816, where `const res = resolutionForLevel(baseResolution, desc.level);`). Replace with density-aware logic:

Add imports at top of `systems.ts`:

```ts
import { effectiveResolution } from './lod-select';
import { maxBoostOverAabb } from './density-map';
```

Replace the resolution assignment (around line 816):

```ts
const chunk = state.createEntity();
const lodRes = resolutionForLevel(baseResolution, desc.level);
let res = lodRes;
if (data.density) {
  const half = desc.size / 2;
  const boost = maxBoostOverAabb(data.density, {
    minX: desc.originX - half,
    minZ: desc.originZ - half,
    maxX: desc.originX + half,
    maxZ: desc.originZ + half,
  });
  res = effectiveResolution(baseResolution, desc.level, boost);
}
TerrainChunk.resolution[chunk] = res;
```

(Read the exact current lines around 814-816 first to match the existing variable names — the `desc.size` may be `desc.size` or computed; match what's there.)

- [ ] **Step 7: Run all terrain + water tests**

Run: `cd VibeGame && bun test tests/unit/terrain tests/unit/water`
Expected: PASS — including existing `terrain-height-sampler.test.ts`, `carve.test.ts`, and the new `lod-density.test.ts`.

- [ ] **Step 8: Typecheck, format, commit**

```bash
cd VibeGame && bun run typecheck && bun run format
cd .. && git add VibeGame/src/plugins/terrain/utils.ts VibeGame/src/plugins/terrain/systems.ts VibeGame/tests/unit/terrain/lod-density.test.ts
git commit -m "feat(vibegame): wire DensityMap into TerrainLodSelectSystem"
```

---

### Task 4: `<Lake>` overrides density before carve

The payoff task: when `LakeApplySystem` runs, it first bumps the density map to max on the lake's AABB, so the chunks rendering the lake get high resolution and capture the basin detail that already exists in the heightfield. `carveBowl` itself is unchanged.

**Files:**
- Modify: `VibeGame/src/plugins/water/systems.ts` (`LakeApplySystem.update`, around line 254-266, before the `carveBowl` call)
- Test: extend `VibeGame/tests/unit/water/carve.test.ts` with a case that asserts the density override happens. (Read existing file first.)

**Interfaces:**
- Consumes: `applyOverride` from `terrain/density-map` (Task 2); `Lake.radius` from `water/components`; `data.density` from `TerrainEntityData` (Task 3).
- Produces: `data.density` boost set to 255 on the lake AABB before carve.

- [ ] **Step 1: Read the existing carve test and LakeApplySystem block**

Run: `cd VibeGame && sed -n '1,60p' tests/unit/water/carve.test.ts` and `sed -n '232,290p' src/plugins/water/systems.ts`
Confirm the existing test shape and the exact carve call site (`carveBowl(data.sampler, lx, lz, radius, rimY, depth)` at ~line 266).

- [ ] **Step 2: Write the failing test**

Add to `VibeGame/tests/unit/water/carve.test.ts` a new describe block (append; do not modify existing tests). The test verifies the override helper in isolation first (the ECS wiring is exercised in integration later):

```ts
// At top of carve.test.ts, add imports if not present:
import {
  buildDensityMap,
  applyOverride,
  boostAt,
} from '../../src/plugins/terrain/density-map';
import type { HeightSampler } from '../../src/plugins/terrain/height-sampler';

// Append:
describe('Lake density override', () => {
  it('marks the lake AABB as maximally dense before carving', () => {
    // Flat sampler with a tiny bump so buildDensityMap is non-trivial.
    const data = new Float32Array(8 * 8);
    for (let i = 0; i < data.length; i++) data[i] = 0.5;
    const sampler: HeightSampler = {
      width: 8,
      height: 8,
      data,
      worldSize: 100,
      maxHeight: 10,
    };
    const density = buildDensityMap(sampler, 16);
    // Simulate LakeApplySystem: a lake of radius 6 at the origin.
    const lakeX = 0;
    const lakeZ = 0;
    const radius = 6;
    const margin = 1.1; // small overshoot, matching the carve shoreline pad
    applyOverride(density, {
      minX: lakeX - radius * margin,
      minZ: lakeZ - radius * margin,
      maxX: lakeX + radius * margin,
      maxZ: lakeZ + radius * margin,
    }, 255);
    // Centre of the lake is now maximally dense.
    expect(boostAt(density, lakeX, lakeZ)).toBe(255);
    // A point well outside the lake is unaffected (flat → 0).
    expect(boostAt(density, 40, 40)).toBe(0);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/water/carve.test.ts`
Expected: FAIL on the new block — imports missing (`buildDensityMap` etc. not imported). Fix imports so the test compiles; it should then PASS (it tests already-implemented Task 2 functions). Once green, proceed to wire ECS.

- [ ] **Step 4: Wire the override into LakeApplySystem**

In `VibeGame/src/plugins/water/systems.ts`, add import at top:

```ts
import { applyOverride } from '../terrain/density-map';
```

In `LakeApplySystem.update`, find the block just before `const rimY = rimHeight(...)` (around line 264). Insert the density override. The lake center in field-local space is already `(lx, lz)` (computed at line 261-262). Add after the `lx`/`lz` computation and before `rimY`:

```ts
const lx = Transform.posX[eid] - data.worldOffset.x;
const lz = Transform.posZ[eid] - data.worldOffset.z;

// Bump the density map around the lake so chunks rendering it use a finer
// mesh — capturing the basin detail that already exists in the heightfield.
// carveBowl itself is unchanged; this only raises the sampling resolution.
if (data.density) {
  const margin = 1.1;
  applyOverride(
    data.density,
    {
      minX: lx - radius * margin,
      minZ: lz - radius * margin,
      maxX: lx + radius * margin,
      maxZ: lz + radius * margin,
    },
    255
  );
}

const rimY = rimHeight(data.sampler, lx, lz, radius);
```

Note: this only affects **newly selected** chunks. Existing chunks already spawned won't re-resolve until the camera moves and LOD reselects. That's acceptable for lakes carved at startup (the heightmap load triggers a chunk rebuild via `meshDirty`). For runtime-added lakes, the next LOD reselect (camera moves >6m, `LOD_RESELECT_DISTANCE`) picks up the boost. This matches the existing carve propagation model.

- [ ] **Step 5: Run all terrain + water tests**

Run: `cd VibeGame && bun test tests/unit/terrain tests/unit/water`
Expected: PASS — all existing tests still green, new test green.

- [ ] **Step 6: Typecheck, format, commit**

```bash
cd VibeGame && bun run typecheck && bun run format
cd .. && git add VibeGame/src/plugins/water/systems.ts VibeGame/tests/unit/water/carve.test.ts
git commit -m "feat(vibegame): <Lake> bumps density map before carve for fine mesh"
```

---

### Task 5: `.ahgt` format — uint16 + deflate (writer + reader)

The disk-precision piece. Pure functions: serialize a `HeightSampler` to an `.ahgt` blob and parse it back. uint16 quantization (3mm over 200m vs 0.78m PNG), deflate via fflate (transitive through three). No DOM needed, no Three.js — pure TS, fully unit-testable.

**Files:**
- Create: `VibeGame/src/plugins/terrain/ahgt-format.ts` (new)
- Test: `VibeGame/tests/unit/terrain/ahgt-format.test.ts` (new)

**Interfaces:**
- Consumes: `HeightSampler` from `height-sampler.ts`; fflate from `three/examples/jsm/libs/fflate.module.js` (or `import { deflateSync, inflateSync } from 'fflate'` if resolvable — confirm via the test).
- Produces:
  - `serializeAhgt(sampler, meta?): Uint8Array`
  - `parseAhgt(bytes: Uint8Array): { sampler: HeightSampler; meta: AhgtMeta }`
  - `interface AhgtMeta { worldSize: number; maxHeight: number; originX: number; originZ: number }`
  - `AHGT_MAGIC = 0x54474841` ("AHGT" little-endian), `AHGT_VERSION = 1`

- [ ] **Step 1: Confirm fflate import path works under Bun**

Run: `cd VibeGame && bun -e "import('fflate').then(m => console.log(typeof m.deflateSync, typeof m.inflateSync)).catch(e => console.log('fflate direct fail:', e.message))"`
If that prints `function function`, use `import { deflateSync, inflateSync } from 'fflate'`. If it fails, use `import { deflateSync, inflateSync } from 'three/examples/jsm/libs/fflate.module.js'`. Record which works for Step 3.

- [ ] **Step 2: Write the failing test**

Create `VibeGame/tests/unit/terrain/ahgt-format.test.ts`:

```ts
import { describe, expect, it } from 'bun:test';
import {
  AHGT_MAGIC,
  AHGT_VERSION,
  parseAhgt,
  serializeAhgt,
} from '../../../src/plugins/terrain/ahgt-format';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';

function makeSampler(size: number, worldSize: number, maxHeight: number): HeightSampler {
  const data = new Float32Array(size * size);
  const half = worldSize / 2;
  const step = worldSize / (size - 1);
  for (let z = 0; z < size; z++) {
    for (let x = 0; x < size; x++) {
      const wx = x * step - half;
      const wz = z * step - half;
      // A gentle radial bump so quantization round-trip is non-trivial.
      const r = Math.hypot(wx, wz) / half;
      data[z * size + x] = Math.max(0, 1 - r) * (0.7 / maxHeight + 0.0);
    }
  }
  return { width: size, height: size, data, worldSize, maxHeight };
}

describe('ahgt round-trip', () => {
  it('serializes and parses back to an equivalent sampler (uint16 precision)', () => {
    const original = makeSampler(64, 200, 50);
    const bytes = serializeAhgt(original);
    // Magic header.
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    expect(view.getUint32(0, true)).toBe(AHGT_MAGIC);
    expect(view.getUint16(4, true)).toBe(AHGT_VERSION);

    const { sampler, meta } = parseAhgt(bytes);
    expect(sampler.width).toBe(original.width);
    expect(sampler.height).toBe(original.height);
    expect(sampler.worldSize).toBe(original.worldSize);
    expect(sampler.maxHeight).toBe(original.maxHeight);
    expect(meta.worldSize).toBe(original.worldSize);

    // Heights match within uint16 quantization step (maxHeight/65535).
    const eps = original.maxHeight / 65535 + 1e-6;
    for (const [x, z] of [
      [0, 0],
      [50, 0],
      [0, 50],
      [50, 50],
      [-30, 20],
    ]) {
      const before = sampleHeightAt(original, x, z);
      const after = sampleHeightAt(sampler, x, z);
      expect(Math.abs(before - after)).toBeLessThan(eps);
    }
  });

  it('deflate actually compresses (output smaller than raw uint16 for smooth terrain)', () => {
    const sampler = makeSampler(64, 200, 50);
    const bytes = serializeAhgt(sampler);
    const rawUint16 = 64 * 64 * 2;
    // Header + meta + compressed height should be < raw uint16 for smooth terrain.
    expect(bytes.byteLength).toBeLessThan(rawUint16);
  });

  it('throws on bad magic', () => {
    const bad = new Uint8Array(16);
    new DataView(bad.buffer).setUint32(0, 0xdeadbeef, true);
    expect(() => parseAhgt(bad)).toThrow(/magic|AHGT/i);
  });

  it('handles a flat (dataless) sampler by serializing a zero grid', () => {
    const flat: HeightSampler = {
      width: 1,
      height: 1,
      data: null,
      worldSize: 100,
      maxHeight: 10,
    };
    const bytes = serializeAhgt(flat);
    const { sampler } = parseAhgt(bytes);
    expect(sampleHeightAt(sampler, 0, 0)).toBe(0);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/terrain/ahgt-format.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 4: Write minimal implementation**

Create `VibeGame/src/plugins/terrain/ahgt-format.ts`. Use the fflate import path confirmed in Step 1.

```ts
// Use the import confirmed in Step 1. Prefer the direct 'fflate' if it resolved.
import { deflateSync, inflateSync } from 'fflate';
import type { HeightSampler } from './height-sampler';

/** AHGT magic, "AHGT" little-endian (0x41 0x48 0x47 0x54 → 0x54474841). */
export const AHGT_MAGIC = 0x54474841;
export const AHGT_VERSION = 1;

export interface AhgtMeta {
  worldSize: number;
  maxHeight: number;
  originX: number;
  originZ: number;
}

interface AhgtHeader {
  magic: number;
  version: number;
  size: number;
  reserved: number;
  flags: number;
}

const HEADER_BYTES = 16;

function encodeUtf8(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

/**
 * Serialize a HeightSampler to a compact `.ahgt` blob: 16-byte binary header,
 * a length-prefixed JSON metadata block, then a deflate-compressed uint16
 * height grid quantized to [0, maxHeight]. Precision is ~maxHeight/65535
 * (e.g. 3mm over 200m), far better than PNG uint8 grayscale (~0.78m over 200m).
 */
export function serializeAhgt(
  sampler: HeightSampler,
  metaOverrides: Partial<AhgtMeta> = {}
): Uint8Array {
  const size = Math.max(1, sampler.width);
  const quantized = new Uint16Array(size * size);
  const { data, maxHeight } = sampler;
  if (data && maxHeight > 0) {
    for (let i = 0; i < quantized.length && i < data.length; i++) {
      const normalized = Math.max(0, Math.min(1, data[i]!));
      quantized[i] = Math.round(normalized * 65535);
    }
  }
  const rawHeightBytes = new Uint8Array(
    quantized.buffer,
    quantized.byteOffset,
    quantized.byteLength
  );
  const compressed = deflateSync(rawHeightBytes, { level: 6 });

  const meta: AhgtMeta = {
    worldSize: sampler.worldSize,
    maxHeight: sampler.maxHeight,
    originX: metaOverrides.originX ?? 0,
    originZ: metaOverrides.originZ ?? 0,
  };
  const metaJson = encodeUtf8(JSON.stringify(meta));

  // Layout: header(16) + u32 metaLen + metaJson + compressed heights.
  const total =
    HEADER_BYTES + 4 + metaJson.byteLength + compressed.byteLength;
  const out = new Uint8Array(total);
  const view = new DataView(out.buffer);
  view.setUint32(0, AHGT_MAGIC, true);
  view.setUint16(4, AHGT_VERSION, true);
  view.setUint16(6, size, true);
  view.setUint16(8, 0, true); // reserved
  view.setUint16(10, 0, true); // flags
  // bytes 12..15 reserved padding
  view.setUint32(HEADER_BYTES, metaJson.byteLength, true);
  out.set(metaJson, HEADER_BYTES + 4);
  out.set(compressed, HEADER_BYTES + 4 + metaJson.byteLength);
  return out;
}

/** Parse an `.ahgt` blob back into a HeightSampler + metadata. */
export function parseAhgt(bytes: Uint8Array): {
  sampler: HeightSampler;
  meta: AhgtMeta;
} {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.byteLength < HEADER_BYTES) {
    throw new Error('AHGT: truncated header');
  }
  const magic = view.getUint32(0, true);
  if (magic !== AHGT_MAGIC) {
    throw new Error(`AHGT: bad magic 0x${magic.toString(16)} (expected AHGT)`);
  }
  const version = view.getUint16(4, true);
  if (version !== AHGT_VERSION) {
    throw new Error(`AHGT: unsupported version ${version}`);
  }
  const size = view.getUint16(6, true);
  const metaLen = view.getUint32(HEADER_BYTES, true);
  const metaStart = HEADER_BYTES + 4;
  const metaJson = new TextDecoder().decode(
    bytes.subarray(metaStart, metaStart + metaLen)
  );
  const meta = JSON.parse(metaJson) as AhgtMeta;

  const heightStart = metaStart + metaLen;
  const compressed = bytes.subarray(heightStart);
  const raw = inflateSync(compressed);
  const quantized = new Uint16Array(
    raw.buffer,
    raw.byteOffset,
    raw.byteLength / 2
  );

  const data = new Float32Array(size * size);
  for (let i = 0; i < data.length && i < quantized.length; i++) {
    data[i] = quantized[i]! / 65535;
  }

  return {
    sampler: {
      width: size,
      height: size,
      data,
      worldSize: meta.worldSize,
      maxHeight: meta.maxHeight,
    },
    meta,
  };
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd VibeGame && bun test tests/unit/terrain/ahgt-format.test.ts`
Expected: PASS — round-trip within uint16 epsilon, compression ratio, bad-magic throw, flat sampler.

- [ ] **Step 6: Typecheck, format, commit**

```bash
cd VibeGame && bun run typecheck && bun run format
cd .. && git add VibeGame/src/plugins/terrain/ahgt-format.ts VibeGame/tests/unit/terrain/ahgt-format.test.ts
git commit -m "feat(vibegame): .ahgt format — uint16 + deflate heightmap"
```

---

### Task 6: Loader integration — `.ahgt` vs `.png` dispatch

Wire `.ahgt` into the terrain bootstrap so `<Terrain url=".../foo.ahgt">` loads via the new parser and `.png` keeps the legacy path. Retrocompat: PNG behaves exactly as today.

**Files:**
- Modify: `VibeGame/src/plugins/terrain/systems.ts` (`TerrainFieldBootstrapSystem`, the `loadHeightmapFromUrl` call at ~line 643 and the retry at ~line 975)
- Test: `VibeGame/tests/unit/terrain/ahgt-loader.test.ts` (new) — dispatch logic in isolation.

**Interfaces:**
- Consumes: `parseAhgt` from Task 5; `loadHeightmapFromUrl`, `createHeightmapSampler` from `height-sampler.ts`.
- Produces: a helper `loadHeightfield(url): Promise<HeightSampler>` that dispatches on extension. Bootstrap calls it instead of `loadHeightmapFromUrl` directly.

- [ ] **Step 1: Write the failing test**

Create `VibeGame/tests/unit/terrain/ahgt-loader.test.ts`:

```ts
import { describe, expect, it } from 'bun:test';
import { loadHeightfield } from '../../../src/plugins/terrain/ahgt-loader';
import { serializeAhgt } from '../../../src/plugins/terrain/ahgt-format';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';

function makeSampler(): HeightSampler {
  const size = 16;
  const data = new Float32Array(size * size);
  for (let i = 0; i < data.length; i++) data[i] = 0.3 + (i % 5) * 0.02;
  return { width: size, height: size, data, worldSize: 50, maxHeight: 20 };
}

describe('loadHeightfield dispatch', () => {
  it('parses a data: URL ending in .ahgt via the AHGT parser', async () => {
    const original = makeSampler();
    const bytes = serializeAhgt(original);
    // Build a data: URL with a fake .ahgt filename so the dispatcher sees the extension.
    const b64 = Buffer.from(bytes).toString('base64');
    const url = `data:application/octet-stream;base64,${b64}#fake.ahgt`;
    const sampler = await loadHeightfield(url);
    expect(sampler.width).toBe(original.width);
    expect(Math.abs(sampleHeightAt(sampler, 0, 0) - sampleHeightAt(original, 0, 0))).toBeLessThan(
      original.maxHeight / 65535 + 1e-6
    );
  });

  it('rejects unknown extensions with a clear error', async () => {
    await expect(loadHeightfield('data:,#foo.unknown')).rejects.toThrow(
      /heightmap format|extension/i
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/terrain/ahgt-loader.test.ts`
Expected: FAIL — module `ahgt-loader` not found.

- [ ] **Step 3: Write the loader**

Create `VibeGame/src/plugins/terrain/ahgt-loader.ts`:

```ts
import type { HeightSampler } from './height-sampler';
import { createHeightmapSampler, loadHeightmapFromUrl } from './height-sampler';
import { parseAhgt } from './ahgt-format';

/**
 * Load a heightfield from a URL, dispatching on the file extension:
 *  - `.ahgt` → binary uint16+deflate format (parseAhgt)
 *  - `.png` (or any other image) → legacy luminance decode (loadHeightmapFromUrl)
 *
 * PNG is the default for retrocompat: existing `<Terrain url="...png">` markup
 * keeps working unchanged. `.ahgt` is opt-in for higher precision (3mm vs 0.78m).
 *
 * For `.ahgt`, the URL is fetched as a binary blob and parsed; worldSize /
 * maxHeight come from the file's metadata block. For images, the caller still
 * supplies worldSize/maxHeight (matching the existing API).
 */
export async function loadHeightfield(
  url: string,
  fallbackWorldSize?: number,
  fallbackMaxHeight?: number
): Promise<HeightSampler> {
  // Strip query/hash to inspect the extension reliably.
  const cleanUrl = url.split('#')[0]!.split('?')[0]!;
  const ext = cleanUrl.slice(cleanUrl.lastIndexOf('.') + 1).toLowerCase();

  if (ext === 'ahgt') {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`AHGT fetch ${response.status}: ${url}`);
    }
    const buf = new Uint8Array(await response.arrayBuffer());
    const { sampler } = parseAhgt(buf);
    return sampler;
  }

  if (ext === 'png' || ext === 'jpg' || ext === 'jpeg' || ext === 'webp') {
    if (fallbackWorldSize === undefined || fallbackMaxHeight === undefined) {
      throw new Error(
        `Image heightmap requires worldSize and maxHeight, got: ${fallbackWorldSize}, ${fallbackMaxHeight}`
      );
    }
    const imgData = await loadHeightmapFromUrl(url);
    return createHeightmapSampler(fallbackWorldSize, fallbackMaxHeight, imgData);
  }

  throw new Error(
    `Unknown heightmap format (extension "${ext || '(none)'}") for: ${url}`
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd VibeGame && bun test tests/unit/terrain/ahgt-loader.test.ts`
Expected: PASS — `.ahgt` dispatch round-trips, unknown extension throws.

- [ ] **Step 5: Wire loader into TerrainFieldBootstrapSystem**

In `VibeGame/src/plugins/terrain/systems.ts`, replace the two `loadHeightmapFromUrl(heightmapUrl).then((imgData) => { data.sampler = createHeightmapSampler(...) })` calls (bootstrap at ~643 and retry at ~975) with `loadHeightfield`. Add import at top:

```ts
import { loadHeightfield } from './ahgt-loader';
```

Bootstrap path (~line 643):

```ts
if (heightmapUrl) {
  const field = entity;
  const worldSize = Terrain.worldSize[entity];
  const maxHeight = Terrain.maxHeight[entity];
  loadHeightfield(heightmapUrl, worldSize, maxHeight)
    .then((sampler) => {
      const data = context.get(field);
      if (!data) return;
      data.sampler = sampler;
      data.density = buildDensityMap(sampler, 64);
      invalidateTerrainBvh(state, field);
      for (const chunk of data.chunks) {
        TerrainChunk.meshDirty[chunk] = 1;
      }
      const rapierWorld = getRapierWorld(state);
      if (data.physicsBody && rapierWorld) {
        rapierWorld.removeRigidBody(data.physicsBody);
        data.physicsBody = null;
        data.physicsCollider = null;
      }
      removeChunkColliders(rapierWorld, data);
      data.collisionReady = false;
      fireHeightmapReloadCallbacks(state);
    })
    .catch((err) => {
      logger.error(
        `Heightmap load failed: ${heightmapUrl} — ${err instanceof Error ? err.message : err}`
      );
    });
}
```

Retry path (~line 975): same swap, replace `loadHeightmapFromUrl(...).then(imgData => { data.sampler = createHeightmapSampler(...); data.density = buildDensityMap(...) })`.

Note: if `loadHeightmapFromUrl` / `createHeightmapSampler` imports become unused after this swap, leave them — `loadHeightfield` re-exports them transitively and other code may import them. Run `bun run typecheck` to confirm no unused-import lint errors (if lint flags them, keep only what's still referenced).

- [ ] **Step 6: Run all terrain + water tests**

Run: `cd VibeGame && bun test tests/unit/terrain tests/unit/water`
Expected: PASS.

- [ ] **Step 7: Typecheck, format, commit**

```bash
cd VibeGame && bun run typecheck && bun run format
cd .. && git add VibeGame/src/plugins/terrain/ahgt-loader.ts VibeGame/src/plugins/terrain/systems.ts VibeGame/tests/unit/terrain/ahgt-loader.test.ts
git commit -m "feat(vibegame): dispatch .ahgt vs .png heightmap loading"
```

---

### Task 7: terrain.json `heightmap_format` field

Allow `terrain.json` to declare the format so the loader knows what to expect even if the URL lacks a clear extension (and for documentation). Default `png` for retrocompat.

**Files:**
- Modify: `VibeGame/src/plugins/terrain/terrain-data-loader.ts` (parse `heightmap_format`, default `"png"`)
- Test: `VibeGame/tests/unit/terrain/terrain-data-loader.test.ts` (extend — read existing first)

**Interfaces:**
- Consumes: existing `parseTerrainData`.
- Produces: `TerrainData.heightmap_format: 'png' | 'ahgt'` (default `'png'`).

- [ ] **Step 1: Read the existing terrain-data-loader test**

Run: `cd VibeGame && sed -n '1,80p' tests/unit/terrain/terrain-data-loader.test.ts`
Note the existing test shape and the field defaults being asserted.

- [ ] **Step 2: Write the failing test (append to existing file)**

```ts
describe('heightmap_format', () => {
  it('defaults to "png" when absent', () => {
    // Use the same parseTerrainData call pattern as existing tests; build a
    // minimal terrain.json object missing the heightmap_format field.
    const parsed = parseTerrainData({
      version: '2.0',
      terrain: { size: 2048, world_size: 2000, max_height: 200 },
    });
    expect(parsed.heightmapFormat).toBe('png');
  });

  it('reads "ahgt" when present', () => {
    const parsed = parseTerrainData({
      version: '2.0',
      terrain: { size: 2048, world_size: 2000, max_height: 200 },
      heightmap_format: 'ahgt',
    });
    expect(parsed.heightmapFormat).toBe('ahgt');
  });
});
```

(Adjust the field name `heightmapFormat` vs `heightmap_format` to match the existing camelCase convention in `parseTerrainData`'s return type — read the file first.)

- [ ] **Step 3: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/terrain/terrain-data-loader.test.ts`
Expected: FAIL — `heightmapFormat` not present on the parsed result.

- [ ] **Step 4: Implement**

In `VibeGame/src/plugins/terrain/terrain-data-loader.ts`, add to the parsed result type and defaulting logic:

```ts
heightmapFormat: (raw.heightmap_format === 'ahgt' ? 'ahgt' : 'png'),
```

(Place it alongside the other defaults; read the file for the exact return-object construction site.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd VibeGame && bun test tests/unit/terrain/terrain-data-loader.test.ts`
Expected: PASS.

- [ ] **Step 6: Typecheck, format, commit**

```bash
cd VibeGame && bun run typecheck && bun run format
cd .. && git add VibeGame/src/plugins/terrain/terrain-data-loader.ts VibeGame/tests/unit/terrain/terrain-data-loader.test.ts
git commit -m "feat(vibegame): terrain.json heightmap_format field (png|ahgt)"
```

---

### Task 8: Full regression + visual validation

Final gate: run the entire VibeGame suite, typecheck, lint, format check. Then launch `simple-rpg` in the browser via Chrome MCP and visually confirm the `<Lake>` basin edge is sharper than before (the whole point of the reframe).

**Files:** None modified (validation only).

- [ ] **Step 1: Run full test suite**

Run: `cd VibeGame && bun test`
Expected: PASS — all unit, integration, e2e tests green.

- [ ] **Step 2: Typecheck + lint + format check**

Run: `make check-vibegame lint-vibegame fmt-check-vibegame` (from repo root)
Expected: All pass. If lint/typecheck flags unused imports from the loader swap in Task 6, clean them up and re-run.

- [ ] **Step 3: Launch simple-rpg and locate a lake**

Use the Chrome DevTools MCP to navigate to the `simple-rpg` dev server. (Run `cd VibeGame/examples/simple-rpg && bun run dev` in the background, then `mcp__chrome-devtools__navigate_page` to the printed URL.) Take a snapshot/screenshot to confirm the scene loads, then navigate the camera near a `<Lake>` element.

- [ ] **Step 4: Visual comparison**

Take a screenshot of the lake basin edge before (git stash the terrain plugin changes, reload, screenshot) and after (restore changes, reload, screenshot). Confirm: the basin edge is sharper / less polygonal after. If there's no visible difference, the density map likely isn't being applied — debug by checking `data.density` is non-null in the bootstrap path and `maxBoostOverAabb` returns >0 for the lake chunk.

- [ ] **Step 5: Commit any fixups**

If Step 4 surfaced a bug, fix it with its own commit (`fix(vibegame): ...`). If all clean, no commit needed.

- [ ] **Step 6: Final summary commit (optional, only if docs need updating)**

If the implementation diverged from the spec in any way (e.g. density map tile count, boost curve), update `docs/superpowers/specs/2026-07-03-adaptive-heightmap-design.md` §4.2 and commit:

```bash
git add docs/superpowers/specs/2026-07-03-adaptive-heightmap-design.md
git commit -m "docs: align adaptive heightmap spec with implementation"
```

---

## Summary of behavior change

| Before | After |
|---|---|
| All chunks use `resolutionForLevel(base, level)` regardless of terrain features | Chunks overlapping featured regions (high gradient/curvature, or `<Lake>` AABBs) use up to 2× the resolution, capped at `base` |
| `<Lake>` basin edge rounded by coarse mesh (15.6m/segment at LOD 5) | `<Lake>` AABB bumped to max density → mesh captures the 0.98m/texel heightfield detail |
| Heightmap loaded as PNG uint8 (0.78m steps over 200m) | `.ahgt` uint16 opt-in (3mm steps); PNG still default |
| `carve.ts`, `chunk-geometry.ts`, `height-sampler.ts` | **Unchanged** |
