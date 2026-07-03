# River System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a declarative river system to VibeGame that shares code with the existing lake system via a `WaterShape` abstraction — rivers carve a channel along a polyline, render as an animated water ribbon, and integrate with the water registry (drag/splash) and `terrain.json`.

**Architecture:** A `WaterShape` interface (`computeAabb`/`carve`/`buildGeometry`/`densityBoost`/`toWaterBody`) + an `applyWaterShape` helper that holds the common apply flow (density boost, carve, mark dirty, spawn mesh, register body, cleanup). `LakeBowl` and `RiverChannel` implement it. The water material becomes shape-agnostic via a `t` varying (0=axis/center, 1=margin) computed in the geometry. The `WaterBody` registry becomes a union `lake | river` with `containsPoint` dispatch. Lake refactor is behavior-preserving.

**Tech Stack:** TypeScript, bitecs (ECS), Three.js (mesh + onBeforeCompile shaders), Rapier (colliders — touched only indirectly via `refreshChunkResolutions`), Bun (`bun:test`).

## Global Constraints

- **Test runner:** `bun test` (`bun:test`'s `describe/it/expect`). Unit tests live in `VibeGame/tests/unit/water/` and `VibeGame/tests/unit/terrain/`. Run via `bun test tests/unit` from `VibeGame/`.
- **All commands run from `VibeGame/`** unless prefixed with `cd ..` for repo-root `make`.
- **Formatting/typecheck:** `bun run format && bun run check && bun run lint` before each commit (`make fmt-vibegame`, `make check-vibegame`, `make lint-vibegame` from repo root also work).
- **No behavior change to lakes** — `LakeApplySystem` refactor must be visually and functionally identical. The existing `tests/unit/water/carve.test.ts` and `tests/unit/terrain/terrain-height-sampler.test.ts` are regression guards.
- **Path coordinates are world coords** (metres, X/Z), NOT pixel coords. The `terrain.json` emitter converts pixel→world (`worldX = (pixelX / size) * worldSize - worldSize/2`).
- **bitecs can't store arrays** — `River.path` lives in a `WeakMap<State, Map<entityId, number[]>>` side-channel (flat `[x0,z0,x1,z1,...]`). `Transform.posX/posZ` of a river = first path point (source).
- **Profile exponent `(1−t²)^1.5`** is reused from `carveBowl` (carve.ts) for the river's transverse profile — do NOT change the lake's; import the shared constant `BOWL_PROFILE_EXPONENT`.
- **Conventional Commits** (`feat:`, `refactor:`, `test:`, `chore:`) — one commit per task.
- **Water material uniforms `uCenter`/`uShoreRadius`/`uSeedX`/`uSeedZ` are REMOVED** in Task 6; the `shapeRadius` GLSL function and `lakeShapeT()` are removed too. `t` comes from the geometry attribute `aWaterT` (Task 6).
- **Dead code to remove** in Task 10: `createRiverWaterEntities`, `createLakeWaterEntities` (lake-renderer.ts), and the recipe `'Water'` reference in `spawnWaterEntitiesFromTerrainData`.

---

### Task 1: Path geometry utilities — polyline AABB, length, point-segment distance

Pure functions on a flat polyline (no THREE, no ECS). Foundation for the registry `containsPoint`, `carveChannel`'s AABB, and `makeRiverGeometry`. No dependencies on any other task.

**Files:**
- Create: `VibeGame/src/plugins/water/path-utils.ts` (new)
- Test: `VibeGame/tests/unit/water/path-utils.test.ts` (new)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type FlatPath = number[]` (flat `[x0,z0,x1,z1,...]`, length ≥ 4 → ≥ 2 points)
  - `pathAabb(path: FlatPath, pad: number): { minX: number; minZ: number; maxX: number; maxZ: number }`
  - `pathLength(path: FlatPath): number`
  - `distanceToSegment(px: number, pz: number, ax: number, az: number, bx: number, bz: number): number`
  - `distanceToPath(path: FlatPath, px: number, pz: number): number`

- [ ] **Step 1: Write the failing test**

Create `VibeGame/tests/unit/water/path-utils.test.ts`:

```ts
import { describe, expect, it } from 'bun:test';
import {
  distanceToPath,
  distanceToSegment,
  pathAabb,
  pathLength,
} from '../../../src/plugins/water/path-utils';

const SEG = [0, 0, 10, 0]; // one segment along +X from (0,0) to (10,0)
const ZIG = [0, 0, 10, 0, 10, 10]; // (0,0)→(10,0)→(10,10)

describe('pathAabb', () => {
  it('returns the bounding box of all points, expanded by pad', () => {
    expect(pathAabb(ZIG, 0)).toEqual({ minX: 0, minZ: 0, maxX: 10, maxZ: 10 });
    expect(pathAabb(ZIG, 2)).toEqual({ minX: -2, minZ: -2, maxX: 12, maxZ: 12 });
  });
  it('handles a single segment', () => {
    expect(pathAabb(SEG, 1)).toEqual({ minX: -1, minZ: -1, maxX: 11, maxZ: 1 });
  });
});

describe('pathLength', () => {
  it('sums segment lengths', () => {
    expect(pathLength(SEG)).toBeCloseTo(10, 6);
    expect(pathLength(ZIG)).toBeCloseTo(20, 6);
  });
});

describe('distanceToSegment', () => {
  it('is 0 on the segment', () => {
    expect(distanceToSegment(5, 0, 0, 0, 10, 0)).toBeCloseTo(0, 6);
  });
  it('measures perpendicular distance to the infinite line when projecting inside', () => {
    expect(distanceToSegment(5, 3, 0, 0, 10, 0)).toBeCloseTo(3, 6);
  });
  it('falls back to endpoint distance past the ends', () => {
    expect(distanceToSegment(-4, 0, 0, 0, 10, 0)).toBeCloseTo(4, 6);
    expect(distanceToSegment(15, 0, 0, 0, 10, 0)).toBeCloseTo(5, 6);
  });
});

describe('distanceToPath', () => {
  it('returns the minimum distance to any segment', () => {
    // Point near the bend (10,0): closest to the first segment at distance 0.
    expect(distanceToPath(ZIG, 10, 0)).toBeCloseTo(0, 6);
    // Point at (5,5): 5 from each of the two segments.
    expect(distanceToPath(ZIG, 5, 5)).toBeCloseTo(5, 6);
    // Point at (13,5): closest to the vertical segment (10,0)-(10,10) → distance 3.
    expect(distanceToPath(ZIG, 13, 5)).toBeCloseTo(3, 6);
  });
  it('throws on a path with fewer than 2 points', () => {
    expect(() => distanceToPath([0, 0], 0, 0)).toThrow(/at least 2 points/i);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/water/path-utils.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

Create `VibeGame/src/plugins/water/path-utils.ts`:

```ts
/** Flat polyline in world XZ: `[x0, z0, x1, z1, ...]`. Must have ≥ 2 points. */
export type FlatPath = number[];

export interface PathAabb {
  minX: number;
  minZ: number;
  maxX: number;
  maxZ: number;
}

/** Bounding box of all path points, expanded by `pad` on every side. */
export function pathAabb(path: FlatPath, pad: number): PathAabb {
  if (path.length < 4) {
    throw new Error('pathAabb: path must have at least 2 points (4 numbers)');
  }
  let minX = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i < path.length; i += 2) {
    const x = path[i]!;
    const z = path[i + 1]!;
    if (x < minX) minX = x;
    if (z < minZ) minZ = z;
    if (x > maxX) maxX = x;
    if (z > maxZ) maxZ = z;
  }
  return { minX: minX - pad, minZ: minZ - pad, maxX: maxX + pad, maxZ: maxZ + pad };
}

/** Total length of the polyline (sum of segment lengths). */
export function pathLength(path: FlatPath): number {
  let total = 0;
  for (let i = 0; i + 3 < path.length; i += 2) {
    const dx = path[i + 2]! - path[i]!;
    const dz = path[i + 3]! - path[i + 1]!;
    total += Math.hypot(dx, dz);
  }
  return total;
}

/**
 * Shortest distance from point P to segment AB. Falls back to the nearer
 * endpoint distance when P projects outside the segment.
 */
export function distanceToSegment(
  px: number,
  pz: number,
  ax: number,
  az: number,
  bx: number,
  bz: number
): number {
  const dx = bx - ax;
  const dz = bz - az;
  const lenSq = dx * dx + dz * dz;
  if (lenSq === 0) return Math.hypot(px - ax, pz - az);
  let t = ((px - ax) * dx + (pz - az) * dz) / lenSq;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const cx = ax + t * dx;
  const cz = az + t * dz;
  return Math.hypot(px - cx, pz - cz);
}

/** Shortest distance from point P to the polyline (min over all segments). */
export function distanceToPath(path: FlatPath, px: number, pz: number): number {
  if (path.length < 4) {
    throw new Error('distanceToPath: path must have at least 2 points (4 numbers)');
  }
  let best = Infinity;
  for (let i = 0; i + 3 < path.length; i += 2) {
    const d = distanceToSegment(
      px,
      pz,
      path[i]!,
      path[i + 1]!,
      path[i + 2]!,
      path[i + 3]!
    );
    if (d < best) best = d;
  }
  return best;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd VibeGame && bun test tests/unit/water/path-utils.test.ts`
Expected: PASS — all cases.

- [ ] **Step 5: Format, typecheck, commit**

```bash
cd VibeGame && bun run format && bun run check
git add VibeGame/src/plugins/water/path-utils.ts VibeGame/tests/unit/water/path-utils.test.ts
git commit -m "feat(vibegame): path geometry utils (aabb, length, point-segment distance)"
```

---

### Task 2: Generalize `WaterBody` registry to a `lake | river` union

Turn the disc-only `WaterBody` into a discriminated union and make the three queries dispatch on `kind`. This unblocks river drag/splash (`waterLevelAt` in effects.ts:136) once a river body is registered. No ECS changes yet — pure registry refactor. Existing lake callers must keep working (they construct the `lake` branch).

**Files:**
- Modify: `VibeGame/src/plugins/water/registry.ts` (entire file restructured around the union + dispatch)
- Test: `VibeGame/tests/unit/water/registry.test.ts` (new — there is no existing registry test; check first)

**Interfaces:**
- Consumes: `distanceToPath` from Task 1's `path-utils.ts`.
- Produces:
  - `type WaterBody = LakeWaterBody | RiverWaterBody` where `LakeWaterBody = { kind: 'lake'; x; z; radius; shoreRadius; waterY }` and `RiverWaterBody = { kind: 'river'; path: ReadonlyArray<readonly [number, number]>; width; waterY }`.
  - Same exported query signatures: `isPointInWater`, `waterBodyAt`, `waterLevelAt`, `registerWaterBody`, `unregisterWaterBody`, `getWaterBodies`.
  - New internal `containsPoint(body, x, z): boolean`.

- [ ] **Step 1: Check for an existing registry test, then write the failing test**

Run: `cd VibeGame && ls tests/unit/water/` and `grep -rl "registry" tests/unit/water/` to confirm there's no existing registry test. Then create `VibeGame/tests/unit/water/registry.test.ts`:

```ts
import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  getWaterBodies,
  isPointInWater,
  registerWaterBody,
  unregisterWaterBody,
  waterBodyAt,
  waterLevelAt,
  type WaterBody,
} from '../../../src/plugins/water/registry';

describe('WaterBody registry', () => {
  let state: State;
  beforeEach(() => {
    state = new State();
  });

  describe('lake body', () => {
    const lake: WaterBody = {
      kind: 'lake',
      x: 10,
      z: 20,
      radius: 5,
      shoreRadius: 4,
      waterY: 8,
    };

    it('isPointInWater is true inside the disc', () => {
      registerWaterBody(state, lake);
      expect(isPointInWater(state, 10, 20)).toBe(true);
      expect(isPointInWater(state, 13, 20)).toBe(true); // within radius
      expect(isPointInWater(state, 16, 20)).toBe(false); // outside radius
    });

    it('waterLevelAt returns the surface height inside the disc', () => {
      registerWaterBody(state, lake);
      expect(waterLevelAt(state, 10, 20)).toBe(8);
      expect(waterLevelAt(state, 100, 100)).toBeNull();
    });

    it('waterBodyAt returns the body inside, null outside', () => {
      registerWaterBody(state, lake);
      expect(waterBodyAt(state, 10, 20)).toBe(lake);
      expect(waterBodyAt(state, 100, 100)).toBeNull();
    });
  });

  describe('river body', () => {
    // A river along +X from (0,0) to (100,0), width 6.
    const river: WaterBody = {
      kind: 'river',
      path: [
        [0, 0],
        [100, 0],
      ],
      width: 6,
      waterY: 3,
    };

    it('isPointInWater is true within width/2 of the path', () => {
      registerWaterBody(state, river);
      expect(isPointInWater(state, 50, 0)).toBe(true); // on the axis
      expect(isPointInWater(state, 50, 2)).toBe(true); // within width/2 = 3
      expect(isPointInWater(state, 50, 4)).toBe(false); // outside width/2
      expect(isPointInWater(state, -5, 0)).toBe(false); // past the source end
    });

    it('waterLevelAt returns the surface height inside the channel', () => {
      registerWaterBody(state, river);
      expect(waterLevelAt(state, 50, 1)).toBe(3);
      expect(waterLevelAt(state, 50, 10)).toBeNull();
    });
  });

  it('unregister removes the body so queries no longer see it', () => {
    const lake: WaterBody = {
      kind: 'lake',
      x: 0,
      z: 0,
      radius: 5,
      shoreRadius: 4,
      waterY: 1,
    };
    registerWaterBody(state, lake);
    expect(isPointInWater(state, 0, 0)).toBe(true);
    unregisterWaterBody(state, lake);
    expect(isPointInWater(state, 0, 0)).toBe(false);
    expect(getWaterBodies(state)).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/water/registry.test.ts`
Expected: FAIL — `kind` not a property on `WaterBody`, `path` branch not handled.

- [ ] **Step 3: Restructure registry.ts**

Replace `VibeGame/src/plugins/water/registry.ts` with:

```ts
import type { State } from '../../core';
import { distanceToPath } from './path-utils';

/** Lake water body: a disc centred at (x,z). */
export interface LakeWaterBody {
  kind: 'lake';
  x: number;
  z: number;
  /** Full bowl radius (m). Spawner/navmesh membership + the disc geometry. */
  radius: number;
  /** Radius of the waterline (m). */
  shoreRadius: number;
  waterY: number;
}

/** River water body: a channel along a polyline of given width. */
export interface RiverWaterBody {
  kind: 'river';
  /** Polyline points `[x,z]` in world coords. */
  path: ReadonlyArray<readonly [number, number]>;
  /** Channel width (m). Points within width/2 of the path are "in water". */
  width: number;
  waterY: number;
}

/** A registered water surface (spawn avoidance / gameplay queries). */
export type WaterBody = LakeWaterBody | RiverWaterBody;

const WATER_BODIES = new WeakMap<State, WaterBody[]>();

export function getWaterBodies(state: State): WaterBody[] {
  let list = WATER_BODIES.get(state);
  if (!list) {
    list = [];
    WATER_BODIES.set(state, list);
  }
  return list;
}

export function registerWaterBody(state: State, body: WaterBody): void {
  getWaterBodies(state).push(body);
}

export function unregisterWaterBody(state: State, body: WaterBody): void {
  const list = getWaterBodies(state);
  const i = list.indexOf(body);
  if (i >= 0) list.splice(i, 1);
}

/** True when the world XZ point lies inside a water surface (disc or channel). */
function containsPoint(body: WaterBody, x: number, z: number): boolean {
  if (body.kind === 'lake') {
    const dx = x - body.x;
    const dz = z - body.z;
    return dx * dx + dz * dz <= body.radius * body.radius;
  }
  // river: distance to the polyline ≤ width/2. Flatten the [x,z] pairs.
  const flat: number[] = [];
  for (const p of body.path) {
    flat.push(p[0], p[1]);
  }
  return distanceToPath(flat, x, z) <= body.width / 2;
}

/**
 * True when the world XZ point lies inside a water surface. Backs the spawner's
 * `avoid-water` flag and any gameplay splash/swim checks (lakes AND rivers).
 */
export function isPointInWater(state: State, x: number, z: number): boolean {
  for (const b of getWaterBodies(state)) {
    if (containsPoint(b, x, z)) return true;
  }
  return false;
}

/** The water body whose surface contains the world XZ point, or null. */
export function waterBodyAt(
  state: State,
  x: number,
  z: number
): WaterBody | null {
  for (const b of getWaterBodies(state)) {
    if (containsPoint(b, x, z)) return b;
  }
  return null;
}

/** Water surface height at the point, or null when not over water. */
export function waterLevelAt(
  state: State,
  x: number,
  z: number
): number | null {
  for (const b of getWaterBodies(state)) {
    if (containsPoint(b, x, z)) return b.waterY;
  }
  return null;
}
```

- [ ] **Step 4: Update the lake caller in systems.ts to construct the union**

In `VibeGame/src/plugins/water/systems.ts`, find the `const body: WaterBody = { x: ..., z: ..., radius, shoreRadius: shoreR, waterY: ... }` block (around line 424-430). Add `kind: 'lake'` as the first field:

```ts
const body: WaterBody = {
  kind: 'lake',
  x: Transform.posX[eid],
  z: Transform.posZ[eid],
  radius,
  shoreRadius: shoreR,
  waterY: data.worldOffset.y + waterY,
};
```

- [ ] **Step 5: Run registry test + regression**

Run: `cd VibeGame && bun test tests/unit/water tests/unit/terrain`
Expected: PASS — new registry tests green; existing carve/terrain tests unchanged.

- [ ] **Step 6: Format, typecheck, commit**

```bash
cd VibeGame && bun run format && bun run check && bun run lint
git add VibeGame/src/plugins/water/registry.ts VibeGame/src/plugins/water/systems.ts VibeGame/tests/unit/water/registry.test.ts
git commit -m "refactor(vibegame): generalize WaterBody registry to lake|river union"
```

---

### Task 3: `WaterShape` interface + `LakeBowl` adapter (behavior-preserving)

Define the interface and wrap the existing lake functions behind it. Do NOT yet change `LakeApplySystem` — `LakeBowl` is built and tested in isolation here. The system rewiring happens in Task 7 (after the material is shape-agnostic in Task 6, so the lake keeps rendering correctly throughout).

**Files:**
- Create: `VibeGame/src/plugins/water/water-shape.ts` (new — interface + types only)
- Create: `VibeGame/src/plugins/water/lake-bowl.ts` (new — `LakeBowl implements WaterShape`)
- Test: `VibeGame/tests/unit/water/lake-bowl.test.ts` (new)

**Interfaces:**
- Consumes: `carveBowl`, `rimHeight`, `shoreFraction`, `shapeRadius` from `carve.ts`; `makeLakeGeometry` (currently in `systems.ts` — will be exported; see Step 3); `HeightSampler` from `terrain/height-sampler.ts`; `WorldAabb` from `terrain/density-map.ts`; `WaterBody` from `registry.ts`.
- Produces:
  - `interface WaterShape { computeAabb(): WorldAabb; carve(sampler): WaterShapeResult; buildGeometry(): THREE.BufferGeometry; densityBoost(): number; toWaterBody(worldWaterY: number): WaterBody; }`
  - `interface WaterShapeResult { carved: boolean; rimY: number; waterY: number; }`
  - `class LakeBowl implements WaterShape` — constructor `(opts: { localX; localZ; radius; depth; waterOffset })`. (`worldWaterY` is passed to `toWaterBody` at apply time, since only `applyWaterShape` knows `data.worldOffset.y`.)

- [ ] **Step 1: Write the failing test**

Create `VibeGame/tests/unit/water/lake-bowl.test.ts`:

```ts
import { describe, expect, it } from 'bun:test';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import { LakeBowl } from '../../../src/plugins/water/lake-bowl';

function flatSampler(heightNorm = 0.5, size = 64): HeightSampler {
  const data = new Float32Array(size * size).fill(heightNorm);
  return { width: size, height: size, data, worldSize: 100, maxHeight: 100 };
}

describe('LakeBowl', () => {
  it('computeAabb covers the disc (× margin)', () => {
    const bowl = new LakeBowl({
      localX: 20,
      localZ: 0,
      radius: 6,
      depth: 2,
      waterOffset: 0.3,
    });
    const aabb = bowl.computeAabb();
    // The AABB must contain the disc centred at (20,0) radius 6 × margin.
    expect(aabb.minX).toBeLessThanOrEqual(20 - 6);
    expect(aabb.maxX).toBeGreaterThanOrEqual(20 + 6);
    expect(aabb.minZ).toBeLessThanOrEqual(0 - 6);
    expect(aabb.maxZ).toBeGreaterThanOrEqual(0 + 6);
  });

  it('carve lowers the sampler at the lake centre and returns carved=true', () => {
    const sampler = flatSampler(0.5);
    const bowl = new LakeBowl({
      localX: 0,
      localZ: 0,
      radius: 10,
      depth: 8,
      waterOffset: 0.3,
    });
    const before = sampleHeightAt(sampler, 0, 0);
    const result = bowl.carve(sampler);
    const after = sampleHeightAt(sampler, 0, 0);
    expect(result.carved).toBe(true);
    expect(after).toBeLessThan(before);
  });

  it('carve returns carved=false on a dataless sampler', () => {
    const flat: HeightSampler = {
      width: 1,
      height: 1,
      data: null,
      worldSize: 100,
      maxHeight: 100,
    };
    const bowl = new LakeBowl({
      localX: 0,
      localZ: 0,
      radius: 10,
      depth: 8,
      waterOffset: 0.3,
    });
    expect(bowl.carve(flat).carved).toBe(false);
  });

  it('densityBoost returns 255', () => {
    const bowl = new LakeBowl({
      localX: 0,
      localZ: 0,
      radius: 6,
      depth: 2,
      waterOffset: 0.3,
    });
    expect(bowl.densityBoost()).toBe(255);
  });

  it('toWaterBody(worldWaterY) returns a lake body with kind="lake" and the given waterY', () => {
    const bowl = new LakeBowl({
      localX: 5,
      localZ: 7,
      radius: 6,
      depth: 2,
      waterOffset: 0.3,
    });
    const body = bowl.toWaterBody(42);
    expect(body.kind).toBe('lake');
    if (body.kind === 'lake') {
      expect(body.x).toBe(5);
      expect(body.z).toBe(7);
      expect(body.radius).toBe(6);
      expect(body.waterY).toBe(42);
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/water/lake-bowl.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Export `makeLakeGeometry` from systems.ts**

`makeLakeGeometry` is currently a private function in `systems.ts`. `LakeBowl.buildGeometry()` needs it. Add the `export` keyword:

In `VibeGame/src/plugins/water/systems.ts`, change `function makeLakeGeometry(` to `export function makeLakeGeometry(`.

- [ ] **Step 4: Write the interface**

Create `VibeGame/src/plugins/water/water-shape.ts`:

```ts
import type * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import type { WaterBody } from './registry';

/** Result of carving a water shape into the terrain sampler. */
export interface WaterShapeResult {
  /** false when the sampler was flat/dataless — caller should retry next frame. */
  carved: boolean;
  /** Crest height of the rim/margins (metres). */
  rimY: number;
  /** Water surface height in field-local space (metres). */
  waterY: number;
}

/**
 * A water shape (lake bowl, river channel, future swamp/coastline) presented
 * through a uniform interface so the common apply flow (density boost, carve,
 * mark dirty, spawn mesh, register body, cleanup) can live in one helper.
 *
 * The only shape-specific behaviour is the "distance from the margin" metric:
 * lakes compute it radially, rivers laterally along the path. The water
 * material is shape-agnostic because that metric (`aWaterT`) is baked into
 * the geometry by `buildGeometry()`.
 */
export interface WaterShape {
  /** AABB in field-local coords (X/Z), for density boost + chunk invalidation. */
  computeAabb(): WorldAabb;
  /** Carve the shape into the sampler in place (heights only go down). */
  carve(sampler: HeightSampler): WaterShapeResult;
  /** Surface mesh geometry. Must set the `aWaterT` attribute (0=center, 1=margin). */
  buildGeometry(): THREE.BufferGeometry;
  /** Density boost for terrain chunks overlapping this shape (255 = max detail). */
  densityBoost(): number;
  /**
   * Water body for the registry. `worldWaterY` is the resolved world-space
   * surface height (= field.worldOffset.y + carve waterY), supplied by
   * `applyWaterShape` because only it knows the field's world offset.
   */
  toWaterBody(worldWaterY: number): WaterBody;
}

/** Visual config shared by all water shapes for the material. */
export interface WaterMaterialConfig {
  color: number;
  opacity: number;
  ripple: number;
  waveHeight: number;
  waveSpeed: number;
}
```

- [ ] **Step 5: Write LakeBowl**

Create `VibeGame/src/plugins/water/lake-bowl.ts`:

```ts
import type * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import { carveBowl, rimHeight, shoreFraction } from './carve';
import { makeLakeGeometry } from './systems';
import type { WaterBody } from './registry';
import type { WaterShape, WaterShapeResult } from './water-shape';

export interface LakeBowlOpts {
  localX: number;
  localZ: number;
  radius: number;
  depth: number;
  waterOffset: number;
}

/**
 * Lake water shape: a sculpted bowl. Wraps the existing carveBowl + rimHeight +
 * makeLakeGeometry so lakes flow through the same WaterShape pipeline as rivers
 * without changing their behaviour.
 *
 * The `aWaterT` attribute (radial distance / shaped shore radius, 0..1) is set
 * inside makeLakeGeometry once Task 6 bakes it there; until then buildGeometry
 * returns the fan as-is.
 */
export class LakeBowl implements WaterShape {
  private readonly rimMargin = 1.3; // covers shapeRadius overshoot (amplitude 0.28)

  constructor(private readonly opts: LakeBowlOpts) {}

  computeAabb(): WorldAabb {
    const { localX, localZ, radius } = this.opts;
    const m = radius * this.rimMargin;
    return {
      minX: localX - m,
      minZ: localZ - m,
      maxX: localX + m,
      maxZ: localZ + m,
    };
  }

  carve(sampler: HeightSampler): WaterShapeResult {
    const { localX, localZ, radius, depth, waterOffset } = this.opts;
    const rimY = rimHeight(sampler, localX, localZ, radius);
    const waterY = rimY - waterOffset;
    const carved = carveBowl(sampler, localX, localZ, radius, rimY, depth);
    return { carved, rimY, waterY };
  }

  buildGeometry(): THREE.BufferGeometry {
    const { localX, localZ, radius } = this.opts;
    // seedX/seedZ = lake centre in local carve space (drives shapeRadius).
    return makeLakeGeometry(radius, localX, localZ);
  }

  densityBoost(): number {
    return 255;
  }

  toWaterBody(worldWaterY: number): WaterBody {
    const { localX, localZ, radius, depth, waterOffset } = this.opts;
    const shoreR = shoreFraction(depth, waterOffset) * radius;
    return {
      kind: 'lake',
      x: localX,
      z: localZ,
      radius,
      shoreRadius: shoreR,
      waterY: worldWaterY,
    };
  }
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd VibeGame && bun test tests/unit/water/lake-bowl.test.ts`
Expected: PASS — all 5 cases.

- [ ] **Step 7: Format, typecheck, commit**

```bash
cd VibeGame && bun run format && bun run check && bun run lint
git add VibeGame/src/plugins/water/water-shape.ts VibeGame/src/plugins/water/lake-bowl.ts VibeGame/src/plugins/water/systems.ts VibeGame/tests/unit/water/lake-bowl.test.ts
git commit -m "feat(vibegame): WaterShape interface + LakeBowl adapter"
```

---

### Task 4: `carveChannel` + `rimHeightAlongPath` (river carve logic, no geometry yet)

The river's carve: for each texel in the path AABB, find the nearest segment, compute lateral `t`, apply the `(1−t²)^1.5` profile. Plus the rim probe along both banks. Pure functions on the sampler + flat path; no THREE, no ECS.

**Files:**
- Modify: `VibeGame/src/plugins/water/carve.ts` (add `carveChannel` + `rimHeightAlongPath` + export `BOWL_PROFILE_EXPONENT` — it's already exported at line 75; confirm)
- Test: `VibeGame/tests/unit/water/carve.test.ts` (extend — append `carveChannel` describe block)

**Interfaces:**
- Consumes: `BOWL_PROFILE_EXPONENT` (already in carve.ts), `sampleHeightAt` (height-sampler.ts), `pathAabb`/`distanceToPath` (Task 1's path-utils).
- Produces:
  - `carveChannel(sampler: HeightSampler, path: FlatPath, width: number, rimY: number, depth: number): boolean`
  - `rimHeightAlongPath(sampler: HeightSampler, path: FlatPath, width: number): number`

- [ ] **Step 1: Confirm BOWL_PROFILE_EXPONENT is exported**

Run: `cd VibeGame && grep -n "export const BOWL_PROFILE_EXPONENT" src/plugins/water/carve.ts`
Expected: a match (carve.ts:75). If missing, add `export`.

- [ ] **Step 2: Write the failing test (append to carve.test.ts)**

Add to `VibeGame/tests/unit/water/carve.test.ts` (after the existing blocks; add the import at the top):

```ts
// At the top imports, add:
import {
  carveChannel,
  rimHeightAlongPath,
} from '../../../src/plugins/water/carve';
```

Append the new describe block at the end of the file:

```ts
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
    const before = sampleHeightAt(s, 0, 0);
    const carved = carveChannel(s, path, 6, rim, 8);
    const after = sampleHeightAt(s, 0, 0);
    expect(carved).toBe(true);
    expect(after).toBeLessThan(before);
  });

  it('does not carve outside width/2 of the path', () => {
    const s = flatField();
    const path = [-40, 0, 40, 0];
    const rim = rimHeightAlongPath(s, path, 6);
    carveChannel(s, path, 6, rim, 8);
    // A point 20 m off-axis (well outside width/2 = 3) keeps the original height.
    expect(sampleHeightAt(s, 0, 20)).toBeCloseTo(50, 1);
  });

  it('only ever lowers heights (min)', () => {
    const s = flatField();
    const path = [-40, 0, 40, 0];
    const rim = rimHeightAlongPath(s, path, 6);
    carveChannel(s, path, 6, rim, 8);
    carveChannel(s, path, 6, rim, 8); // second pass
    const atCenter = sampleHeightAt(s, 0, 0);
    // A third pass must not lower it further.
    carveChannel(s, path, 6, rim, 8);
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

describe('rimHeightAlongPath', () => {
  it('returns the minimum height along both banks', () => {
    // Build a field that is 50 everywhere except a low spot on the bank.
    const data = new Float32Array(128 * 128).fill(0.5);
    // Field: worldSize 100, so origin (0,0) maps to texel ~64.
    // Poke a low texel at (10, 3) world → that's on the +Z bank of the path.
    const half = 50;
    const step = 100 / 127;
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/water/carve.test.ts`
Expected: FAIL — `carveChannel`/`rimHeightAlongPath` not exported.

- [ ] **Step 4: Implement carveChannel + rimHeightAlongPath**

Add to `VibeGame/src/plugins/water/carve.ts` (after the existing `carveBowl`), and add the import at the top:

```ts
// At top of carve.ts, add:
import { pathAabb } from './path-utils';
```

Append the two functions at the end of `carve.ts`:

```ts
/**
 * Carve a river channel along a polyline. For each texel within the path AABB,
 * find the nearest path segment, compute the lateral coordinate `t = d/(width/2)`
 * (0 on the axis, 1 at the bank), and apply the same C1-smooth profile as
 * `carveBowl`: `rimY − depth·(1 − t²)^1.5`. Heights only go down (min), so
 * overlapping channels and pre-existing valleys are safe.
 *
 * @returns true when at least one texel changed (false on a flat sampler).
 */
export function carveChannel(
  sampler: HeightSampler,
  path: number[],
  width: number,
  rimY: number,
  depth: number
): boolean {
  const { data, width: gw, height: gh, worldSize, maxHeight } = sampler;
  if (!data || gw < 2 || gh < 2 || maxHeight <= 0) return false;

  const half = worldSize / 2;
  const stepX = worldSize / (gw - 1);
  const stepZ = worldSize / (gh - 1);
  const halfWidth = width / 2;

  const aabb = pathAabb(path, halfWidth);
  const x0 = Math.max(0, Math.floor((aabb.minX + half) / stepX));
  const x1 = Math.min(gw - 1, Math.ceil((aabb.maxX + half) / stepX));
  const z0 = Math.max(0, Math.floor((aabb.minZ + half) / stepZ));
  const z1 = Math.min(gh - 1, Math.ceil((aabb.maxZ + half) / stepZ));

  // Inline nearest-segment distance to keep the inner loop allocation-free.
  let changed = false;
  for (let zi = z0; zi <= z1; zi++) {
    const wz = zi * stepZ - half;
    for (let xi = x0; xi <= x1; xi++) {
      const wx = xi * stepX - half;
      // nearest distance to the polyline
      let best = Infinity;
      for (let i = 0; i + 3 < path.length; i += 2) {
        const ax = path[i]!;
        const az = path[i + 1]!;
        const bx = path[i + 2]!;
        const bz = path[i + 3]!;
        const dx = bx - ax;
        const dz = bz - az;
        const lenSq = dx * dx + dz * dz;
        let t = lenSq === 0 ? 0 : ((wx - ax) * dx + (wz - az) * dz) / lenSq;
        t = t < 0 ? 0 : t > 1 ? 1 : t;
        const cx = ax + t * dx;
        const cz = az + t * dz;
        const d = Math.hypot(wx - cx, wz - cz);
        if (d < best) best = d;
      }
      const tLat = best / halfWidth;
      if (tLat >= 1) continue;
      const bowlY = rimY - depth * Math.pow(1 - tLat * tLat, BOWL_PROFILE_EXPONENT);
      const target = Math.min(1, Math.max(0, bowlY / maxHeight));
      const idx = zi * gw + xi;
      if (data[idx]! > target) {
        data[idx] = target;
        changed = true;
      }
    }
  }
  return changed;
}

/**
 * Lowest terrain height along both banks of the river path. Probes ~16 points
 * per bank (offset ±width/2 along the segment normal) and returns the minimum
 * so the water surface never leaks over a low bank. Analogous to `rimHeight`
 * for lakes but sampled along the polyline instead of a ring.
 */
export function rimHeightAlongPath(
  sampler: HeightSampler,
  path: number[],
  width: number
): number {
  let min = Infinity;
  const halfWidth = width / 2;
  for (let i = 0; i + 3 < path.length; i += 2) {
    const ax = path[i]!;
    const az = path[i + 1]!;
    const bx = path[i + 2]!;
    const bz = path[i + 3]!;
    const dx = bx - ax;
    const dz = bz - az;
    const len = Math.hypot(dx, dz);
    if (len === 0) continue;
    const nx = -dz / len; // segment normal (perpendicular)
    const nz = dx / len;
    for (let s = 0; s <= 16; s++) {
      const f = s / 16;
      const px = ax + dx * f;
      const pz = az + dz * f;
      const hPlus = sampleHeightAt(sampler, px + nx * halfWidth, pz + nz * halfWidth);
      const hMinus = sampleHeightAt(sampler, px - nx * halfWidth, pz - nz * halfWidth);
      if (hPlus < min) min = hPlus;
      if (hMinus < min) min = hMinus;
    }
  }
  return Number.isFinite(min) ? min : 0;
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd VibeGame && bun test tests/unit/water/carve.test.ts`
Expected: PASS — existing carve tests + new carveChannel/rimHeightAlongPath tests.

- [ ] **Step 6: Format, typecheck, commit**

```bash
cd VibeGame && bun run format && bun run check && bun run lint
git add VibeGame/src/plugins/water/carve.ts VibeGame/tests/unit/water/carve.test.ts
git commit -m "feat(vibegame): carveChannel + rimHeightAlongPath for rivers"
```

---

### Task 5: `makeRiverGeometry` — ribbon mesh with `aWaterT`

Build the river surface as a ribbon: two rows of vertices offset ±width/2 along the path normals, miter joints at internal nodes, UV.u = accumulated length, UV.v = lateral [0,1], and `aWaterT` = lateral/[width/2]. Pure Three.js geometry builder; no shader yet.

**Files:**
- Create: `VibeGame/src/plugins/water/river-geometry.ts` (new)
- Test: `VibeGame/tests/unit/water/river-geometry.test.ts` (new)

**Interfaces:**
- Consumes: `pathLength` from `path-utils.ts`.
- Produces: `makeRiverGeometry(path: number[], width: number): THREE.BufferGeometry` with attributes `position`, `uv`, `aWaterT`, and an index.

- [ ] **Step 1: Write the failing test**

Create `VibeGame/tests/unit/water/river-geometry.test.ts`:

```ts
import { describe, expect, it } from 'bun:test';
import { makeRiverGeometry } from '../../../src/plugins/water/river-geometry';

describe('makeRiverGeometry', () => {
  it('produces 2 vertices per path node (one ribbon row per side)', () => {
    const path = [0, 0, 10, 0, 10, 10]; // 3 nodes
    const geo = makeRiverGeometry(path, 4);
    const pos = geo.getAttribute('position');
    expect(pos.count).toBe(3 * 2); // 3 nodes × 2 sides
  });

  it('aWaterT is 0 on the axis-adjacent... no: 0 at one bank, 1 at the other', () => {
    // Actually aWaterT = lateral / halfWidth, mapped to [0,1] across the channel.
    // Left bank → 1, right bank → 1 (both are at |lateral| = halfWidth).
    // The axis is interpolated between the two bank vertices.
    const path = [0, 0, 10, 0];
    const geo = makeRiverGeometry(path, 4);
    const t = geo.getAttribute('aWaterT');
    // Node 0 has 2 verts: left (t should be 1, at -halfWidth) and right (t=1).
    expect(t.array[0]).toBeCloseTo(1, 5); // left bank
    expect(t.array[1]).toBeCloseTo(1, 5); // right bank
  });

  it('UV.v spans 0..1 across the channel width', () => {
    const path = [0, 0, 10, 0];
    const geo = makeRiverGeometry(path, 4);
    const uv = geo.getAttribute('uv');
    // First node: left v=0, right v=1 (or vice versa).
    expect(uv.array[1]).toBe(0);
    expect(uv.array[3]).toBe(1);
  });

  it('UV.u is 0 at the source and grows with accumulated length', () => {
    const path = [0, 0, 10, 0, 10, 10];
    const geo = makeRiverGeometry(path, 4);
    const uv = geo.getAttribute('uv');
    // Source node u = 0.
    expect(uv.array[0]).toBeCloseTo(0, 5);
    // Last node u ≈ 20 (10 + 10).
    const lastU = uv.array[(uv.count - 1) * 2];
    expect(lastU).toBeCloseTo(20, 0);
  });

  it('has a non-empty index for the ribbon triangles', () => {
    const path = [0, 0, 10, 0, 10, 10];
    const geo = makeRiverGeometry(path, 4);
    expect(geo.index).not.toBeNull();
    expect(geo.index!.count).toBeGreaterThan(0);
  });

  it('oversizes the ribbon slightly past width/2 (margin for alpha fade)', () => {
    // The left/right vertices must be at |offset| >= halfWidth (× ~1.05).
    const path = [0, 0, 10, 0];
    const geo = makeRiverGeometry(path, 4);
    const pos = geo.getAttribute('position');
    // Left bank of node 0: position.x ~ 0, position.z = -(>=2) → check |z| ≥ 2.
    expect(Math.abs(pos.array[2])).toBeGreaterThanOrEqual(2);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/water/river-geometry.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement makeRiverGeometry**

Create `VibeGame/src/plugins/water/river-geometry.ts`:

```ts
import * as THREE from 'three';

/**
 * Build a river surface as a ribbon along the polyline. At each path node we
 * emit two vertices offset ±halfWidth·pad along the miter normal (average of
 * the incoming/outgoing segment normals), so curves don't gap. UV.u is the
 * accumulated length along the path (for flow), UV.v is 0..1 across the
 * channel. `aWaterT` (0 at the axis, 1 at the banks) is baked here so the
 * water material can be shape-agnostic.
 *
 * Vertices lie in the XZ plane (+Y up); the caller places the mesh at the
 * water surface height with no rotation, matching `makeLakeGeometry`.
 *
 * @param path  Flat polyline `[x0,z0,x1,z1,...]` in field-local world coords.
 * @param width Channel width (m). The ribbon is oversized by `pad` so the
 *              alpha fade at the bank falls over the carved channel, not a
 *              hard polygon edge (analogous to makeLakeGeometry's pad).
 */
export function makeRiverGeometry(path: number[], width: number): THREE.BufferGeometry {
  if (path.length < 4) {
    throw new Error('makeRiverGeometry: path must have at least 2 points (4 numbers)');
  }
  const nodeCount = path.length / 2;
  const pad = 1.05;
  const halfWidth = (width / 2) * pad;

  const positions: number[] = [];
  const uvs: number[] = [];
  const waterT: number[] = [];
  const indices: number[] = [];

  let accLen = 0;

  for (let i = 0; i < nodeCount; i++) {
    const x = path[i * 2]!;
    const z = path[i * 2 + 1]!;

    // Miter normal: average of incoming and outgoing segment normals.
    let nx = 0;
    let nz = 0;
    if (i > 0) {
      const px = path[(i - 1) * 2]!;
      const pz = path[(i - 1) * 2 + 1]!;
      const dx = x - px;
      const dz = z - pz;
      const len = Math.hypot(dx, dz) || 1;
      nx += -dz / len;
      nz += dx / len;
    }
    if (i < nodeCount - 1) {
      const qx = path[(i + 1) * 2]!;
      const qz = path[(i + 1) * 2 + 1]!;
      const dx = qx - x;
      const dz = qz - z;
      const len = Math.hypot(dx, dz) || 1;
      nx += -dz / len;
      nz += dx / len;
    }
    const nlen = Math.hypot(nx, nz) || 1;
    nx /= nlen;
    nz /= nlen;
    // Clamp miter length to avoid spikes on near-180° turns (bevel fallback).
    const miterScale = Math.min(1.5, 1 / Math.max(0.001, nlen > 0 ? Math.abs(nx) + Math.abs(nz) : 1));
    const ox = nx * halfWidth * Math.min(miterScale, 1);
    const oz = nz * halfWidth * Math.min(miterScale, 1);

    // Two bank vertices for this node.
    positions.push(x - ox, 0, z - oz); // left bank
    positions.push(x + ox, 0, z + oz); // right bank
    uvs.push(accLen, 0, accLen, 1);
    waterT.push(1, 1); // both banks are at full |t|; the axis is interpolated

    // Accumulate length at the node boundary (after emitting, for the next node).
    if (i < nodeCount - 1) {
      const qx = path[(i + 1) * 2]!;
      const qz = path[(i + 1) * 2 + 1]!;
      accLen += Math.hypot(qx - x, qz - z);
    }
  }

  // Ribbon quads between consecutive nodes.
  for (let i = 0; i < nodeCount - 1; i++) {
    const a = i * 2; // node i, left
    const b = i * 2 + 1; // node i, right
    const c = (i + 1) * 2; // node i+1, left
    const d = (i + 1) * 2 + 1; // node i+1, right
    // Two triangles per quad; winding keeps the face normal at +Y.
    indices.push(a, c, b, b, c, d);
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setAttribute('aWaterT', new THREE.Float32BufferAttribute(waterT, 1));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd VibeGame && bun test tests/unit/water/river-geometry.test.ts`
Expected: PASS — all cases. (If the `aWaterT`/`uv` assertions need tweaking based on the exact left/right ordering, adjust the expected values in the test to match the implementation — the contract is "banks at t=1, UV.v spans 0..1".)

- [ ] **Step 5: Format, typecheck, commit**

```bash
cd VibeGame && bun run format && bun run check && bun run lint
git add VibeGame/src/plugins/water/river-geometry.ts VibeGame/tests/unit/water/river-geometry.test.ts
git commit -m "feat(vibegame): makeRiverGeometry ribbon with aWaterT attribute"
```

---

### Task 6: Shape-agnostic water material (`t` as varying)

Replace the radial `lakeShapeT()`/`uCenter`/`uShoreRadius`/`uSeedX`/`uSeedZ` shader code with a `t` varying read from the geometry's `aWaterT` attribute. Bake `aWaterT` into `makeLakeGeometry` so lakes render identically. This is the highest-regression-risk task; the lake carve tests + visual validation guard it.

**Files:**
- Modify: `VibeGame/src/plugins/water/systems.ts` — `makeWaterMaterial` shader rewrite; `makeLakeGeometry` gains `aWaterT`.
- Test: `VibeGame/tests/unit/water/systems-material.test.ts` (new — verify the material compiles with `aWaterT` and the uniforms dropped).

**Interfaces:**
- Consumes: `WaterMaterialConfig` (Task 3) — but with `center`/`radius`/`shoreRadius`/`seedX`/`seedZ` REMOVED from the material's needs (they now live in geometry).
- Produces: `makeWaterMaterial(cfg, onShader)` where `cfg` is `{ color, opacity, ripple, waveHeight, waveSpeed }` only. `makeLakeGeometry` now sets `aWaterT`.

- [ ] **Step 1: Write the failing test (material uniform contract)**

Create `VibeGame/tests/unit/water/systems-material.test.ts`:

```ts
import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { makeLakeGeometry } from '../../../src/plugins/water/systems';
import { makeRiverGeometry } from '../../../src/plugins/water/river-geometry';

describe('Water shape geometry carries aWaterT', () => {
  it('lake fan has an aWaterT attribute', () => {
    const geo = makeLakeGeometry(6, 0, 0);
    expect(geo.getAttribute('aWaterT')).toBeDefined();
    const t = geo.getAttribute('aWaterT');
    // Centre vertex t=0, rim vertices t≈1.
    expect(t.array[0]).toBeCloseTo(0, 5);
  });

  it('river ribbon has an aWaterT attribute', () => {
    const geo = makeRiverGeometry([0, 0, 10, 0], 4);
    expect(geo.getAttribute('aWaterT')).toBeDefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/water/systems-material.test.ts`
Expected: FAIL — `makeLakeGeometry` has no `aWaterT` attribute yet.

- [ ] **Step 3: Add `aWaterT` to makeLakeGeometry**

In `VibeGame/src/plugins/water/systems.ts`, modify `makeLakeGeometry` to compute and set `aWaterT` per vertex. The centre vertex is `t=0`; each rim vertex is `t = (shaped radius) / shoreRadius` clamped to [0,1]. Replace the function body:

```ts
export function makeLakeGeometry(
  radius: number,
  seedX: number,
  seedZ: number,
  segments = 72
): THREE.BufferGeometry {
  const positions: number[] = [0, 0, 0];
  const uvs: number[] = [0.5, 0.5];
  const waterT: number[] = [0]; // centre vertex: t=0
  const indices: number[] = [];
  const pad = 1.04;
  for (let i = 0; i < segments; i++) {
    const a = (i / segments) * Math.PI * 2;
    const shaped = shapeRadius(a, seedX, seedZ);
    const r = radius * shaped * pad;
    positions.push(Math.cos(a) * r, 0, Math.sin(a) * r);
    uvs.push(0.5 + Math.cos(a) * 0.5, 0.5 + Math.sin(a) * 0.5);
    // t = shaped radius / base shaped radius ≈ pad (oversized) → clamp to 1 at the rim.
    waterT.push(Math.min(1, shaped * pad));
    indices.push(0, ((i + 1) % segments) + 1, i + 1);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setAttribute('aWaterT', new THREE.Float32BufferAttribute(waterT, 1));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}
```

- [ ] **Step 4: Rewrite makeWaterMaterial to use the `t` varying**

In `VibeGame/src/plugins/water/systems.ts`:
1. Change `WaterMaterialConfig` to drop `center`, `radius`, `shoreRadius`:

```ts
interface WaterMaterialConfig {
  color: number;
  opacity: number;
  ripple: number;
  waveHeight: number;
  waveSpeed: number;
}
```

2. In `makeWaterMaterial`, remove the uniforms `uCenter`, `uShoreRadius`, `uSeedX`, `uSeedZ` (lines ~88-89, 98-99). Add the `aWaterT` attribute + `vWaterT` varying. Replace the vertex shader `<common>` block additions to declare `attribute float aWaterT; varying float vWaterT;` (in addition to the existing `vWaveXZ`/`vViewDir`), and in the `<begin_vertex>` replacement add `vWaterT = aWaterT;`.

3. In the fragment shader, remove the `shapeRadius()` GLSL function and `lakeShapeT()`. Replace `lakeDepthNorm`, `shoreAlpha`, `lakeFoam` to use `vWaterT` directly:

```glsl
// In #include <common> replacement, REMOVE shapeRadius + lakeShapeT, and:
float lakeDepthNorm() {
  return 1.0 - smoothstep(0.0, 1.0, vWaterT);
}
float shoreAlpha() {
  return 1.0 - smoothstep(0.9, 1.0, vWaterT);
}
float lakeFoam() {
  float t = vWaterT;
  float ft = uTime * uWaveSpeed;
  float wob = (vnoise(vWaveXZ * 0.8 + vec2(ft * 0.15, -ft * 0.12)) - 0.5) * 0.1;
  float band = smoothstep(0.86 + wob, 0.90 + wob, t) *
               (1.0 - smoothstep(0.97 + wob, 1.01 + wob, t));
  float dashT = t + wob;
  float dashBand = smoothstep(0.76, 0.78, dashT) *
                   (1.0 - smoothstep(0.82, 0.84, dashT));
  float dashes = step(0.5, vnoise(vWaveXZ * 2.0 + vec2(ft * 0.1, 0.0)));
  float foam = clamp(band + dashBand * dashes * 0.8, 0.0, 1.0);
  return smoothstep(0.25, 0.55, foam);
}
```

Also add `varying float vWaterT;` to the fragment `<common>` block.

4. Update the `makeWaterMaterial(...)` call site in `LakeApplySystem` (around line 394-404) to drop `center`, `radius`, `shoreRadius`, `uSeedX`, `uSeedZ`:

```ts
const material = makeWaterMaterial(
  {
    color: hexToInt(Lake.color[eid]),
    opacity: Lake.opacity[eid],
    ripple: Lake.ripple[eid],
    waveHeight,
    waveSpeed: Lake.waveSpeed[eid] || 1,
  },
  (shader) => {
    const c = cars.get(eid);
    if (c) c.shader = shader;
  }
);
```

- [ ] **Step 5: Run material test + full regression**

Run: `cd VibeGame && bun test tests/unit/water tests/unit/terrain`
Expected: PASS — material test green; carve/registry/lake-bowl tests unchanged (they don't touch the shader).

- [ ] **Step 6: Visual sanity (manual, optional but recommended)**

Run the simple-rpg dev server and confirm lakes still render. If the water looks wrong, the most likely cause is the `vWaterT` declaration mismatch between vertex/fragment — ensure `varying float vWaterT;` is declared in BOTH shader stages.

- [ ] **Step 7: Format, typecheck, commit**

```bash
cd VibeGame && bun run format && bun run check && bun run lint
git add VibeGame/src/plugins/water/systems.ts VibeGame/tests/unit/water/systems-material.test.ts
git commit -m "refactor(vibegame): shape-agnostic water material (t as varying)"
```

---

### Task 7: `RiverChannel` + `applyWaterShape` helper + rewire `LakeApplySystem`

Assemble the river shape, extract the common apply flow into `applyWaterShape`, and make `LakeApplySystem` a thin wrapper. This is where the lake gets rewired through `LakeBowl` + `applyWaterShape`; the existing lake behaviour must be preserved exactly (the lake tests + visual are the guard).

**Files:**
- Create: `VibeGame/src/plugins/water/river-channel.ts` (new — `RiverChannel implements WaterShape`)
- Modify: `VibeGame/src/plugins/water/water-shape.ts` (add `applyWaterShape` helper)
- Modify: `VibeGame/src/plugins/water/systems.ts` (rewrite `LakeApplySystem` to build a `LakeBowl` + call `applyWaterShape`)
- Test: `VibeGame/tests/unit/water/river-channel.test.ts` (new)

**Interfaces:**
- Consumes: `WaterShape`, `WaterMaterialConfig` (Task 3); `carveChannel`, `rimHeightAlongPath` (Task 4); `makeRiverGeometry` (Task 5); `applyOverride`, `refreshChunkResolutions`, registry, `makeWaterMaterial` (existing).
- Produces:
  - `class RiverChannel implements WaterShape`
  - `applyWaterShape(state, entity, shape, config): void`

- [ ] **Step 1: Write the failing test for RiverChannel**

Create `VibeGame/tests/unit/water/river-channel.test.ts`:

```ts
import { describe, expect, it } from 'bun:test';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import { RiverChannel } from '../../../src/plugins/water/river-channel';

function flatSampler(): HeightSampler {
  const data = new Float32Array(128 * 128).fill(0.5);
  return { width: 128, height: 128, data, worldSize: 100, maxHeight: 100 };
}

describe('RiverChannel', () => {
  it('computeAabb covers the path expanded by width/2', () => {
    const rc = new RiverChannel({
      path: [-40, 0, 40, 0],
      width: 6,
      depth: 4,
      waterOffset: 0.3,
    });
    const a = rc.computeAabb();
    expect(a.minX).toBeLessThanOrEqual(-40);
    expect(a.maxX).toBeGreaterThanOrEqual(40);
    expect(a.minZ).toBeLessThanOrEqual(-3);
    expect(a.maxZ).toBeGreaterThanOrEqual(3);
  });

  it('carve lowers the sampler along the path and returns carved=true', () => {
    const s = flatSampler();
    const rc = new RiverChannel({
      path: [-40, 0, 40, 0],
      width: 6,
      depth: 4,
      waterOffset: 0.3,
    });
    const before = sampleHeightAt(s, 0, 0);
    const result = rc.carve(s);
    const after = sampleHeightAt(s, 0, 0);
    expect(result.carved).toBe(true);
    expect(after).toBeLessThan(before);
  });

  it('densityBoost returns 255', () => {
    const rc = new RiverChannel({
      path: [0, 0, 10, 0],
      width: 6,
      depth: 4,
      waterOffset: 0.3,
    });
    expect(rc.densityBoost()).toBe(255);
  });

  it('toWaterBody(worldWaterY) returns a river body with kind="river" and the given waterY', () => {
    const rc = new RiverChannel({
      path: [0, 0, 100, 0],
      width: 6,
      depth: 4,
      waterOffset: 0.3,
    });
    const body = rc.toWaterBody(5);
    expect(body.kind).toBe('river');
    if (body.kind === 'river') {
      expect(body.width).toBe(6);
      expect(body.waterY).toBe(5);
      expect(body.path.length).toBe(2);
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/water/river-channel.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement RiverChannel**

Create `VibeGame/src/plugins/water/river-channel.ts`:

```ts
import type * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import { carveChannel, rimHeightAlongPath } from './carve';
import { makeRiverGeometry } from './river-geometry';
import { pathAabb } from './path-utils';
import type { WaterBody } from './registry';
import type { WaterShape, WaterShapeResult } from './water-shape';

export interface RiverChannelOpts {
  /** Flat polyline `[x0,z0,...]` in field-local world coords. */
  path: number[];
  width: number;
  depth: number;
  waterOffset: number;
}

/** River water shape: a sculpted channel along a polyline. */
export class RiverChannel implements WaterShape {
  constructor(private readonly opts: RiverChannelOpts) {}

  computeAabb(): WorldAabb {
    return pathAabb(this.opts.path, this.opts.width / 2);
  }

  carve(sampler: HeightSampler): WaterShapeResult {
    const { path, width, depth, waterOffset } = this.opts;
    const rimY = rimHeightAlongPath(sampler, path, width);
    const waterY = rimY - waterOffset;
    const carved = carveChannel(sampler, path, width, rimY, depth);
    return { carved, rimY, waterY };
  }

  buildGeometry(): THREE.BufferGeometry {
    return makeRiverGeometry(this.opts.path, this.opts.width);
  }

  densityBoost(): number {
    return 255;
  }

  toWaterBody(worldWaterY: number): WaterBody {
    // Convert flat path to [x,z] pairs for the registry.
    const pairs: Array<readonly [number, number]> = [];
    for (let i = 0; i < this.opts.path.length; i += 2) {
      pairs.push([this.opts.path[i]!, this.opts.path[i + 1]!]);
    }
    return {
      kind: 'river',
      path: pairs,
      width: this.opts.width,
      waterY: worldWaterY,
    };
  }
}
```

- [ ] **Step 4: Add applyWaterShape helper to water-shape.ts**

Append to `VibeGame/src/plugins/water/water-shape.ts`:

```ts
import * as THREE from 'three';
import type { State } from '../../core';
import { Transform } from '../transforms/components';
import { TerrainChunk } from '../terrain/components';
import { getTerrainContext } from '../terrain/utils';
import { invalidateTerrainBvh } from '../bvh';
import { getRapierWorld } from '../physics';
import { getRenderingContext } from '../rendering';
import { applyOverride } from '../terrain/density-map';
import { refreshChunkResolutions } from '../terrain/systems';
import { registerWaterBody, unregisterWaterBody } from './registry';
import { logger } from '../../core/utils/logger';
import { makeWaterMaterial } from './systems';

export interface WaterSideCar {
  mesh: THREE.Mesh;
  material: THREE.MeshStandardMaterial;
  shader: { uniforms: Record<string, { value: unknown }> } | null;
  body: import('./registry').WaterBody;
}

const SIDE_CARS = new WeakMap<State, Map<number, WaterSideCar>>();
export function waterSideCars(state: State): Map<number, WaterSideCar> {
  let m = SIDE_CARS.get(state);
  if (!m) {
    m = new Map();
    SIDE_CARS.set(state, m);
  }
  return m;
}

/**
 * Apply a water shape (lake or river) to the terrain: density boost, carve,
 * mark terrain derivatives dirty, spawn the surface mesh, register the body,
 * and wire cleanup. Shared by LakeApplySystem and RiverApplySystem so the
 * common flow lives in one place.
 *
 * @param state   ECS state.
 * @param entity  The water entity id (for sidecar + onDestroy).
 * @param shape   The WaterShape (LakeBowl / RiverChannel / future).
 * @param config  Material config (color/opacity/ripple/wave).
 * @returns true if applied this frame, false if deferred (sampler not ready).
 */
export function applyWaterShape(
  state: State,
  entity: number,
  shape: WaterShape,
  config: WaterMaterialConfig
): boolean {
  // Find the terrain field.
  const context = getTerrainContext(state);
  let field: { entity: number; data: import('../terrain/utils').TerrainEntityData } | null = null;
  for (const [fe, fd] of context) {
    if (fd.initialized && fd.sampler.data) {
      field = { entity: fe, data: fd };
      break;
    }
  }
  if (!field) return false;
  const { data } = field;

  // 1. Density boost + refresh chunk resolutions.
  if (data.density) {
    applyOverride(data.density, shape.computeAabb(), shape.densityBoost());
    refreshChunkResolutions(state, field.entity, data);
  }

  // 2. Carve.
  const result = shape.carve(data.sampler);
  if (!result.carved) {
    logger.warn(
      `[water] ${entity} carve skipped (sampler not ready); will retry next frame`
    );
    return false;
  }
  const worldWaterY = data.worldOffset.y + result.waterY;

  // 3. Mark terrain derivatives dirty.
  for (const chunk of data.chunks) TerrainChunk.meshDirty[chunk] = 1;
  const world = getRapierWorld(state);
  if (world) {
    for (const body of data.chunkColliders.values()) world.removeRigidBody(body);
    data.chunkColliders.clear();
  }
  invalidateTerrainBvh(state, field.entity);

  // 4. Spawn surface mesh with the shape-agnostic material.
  const scene = getRenderingContext(state).scene;
  const cars = waterSideCars(state);
  const material = makeWaterMaterial(config, (shader) => {
    const c = cars.get(entity);
    if (c) c.shader = shader;
  });
  const mesh = new THREE.Mesh(shape.buildGeometry(), material);
  // Place the mesh so its local +Y is world up; XZ from the geometry (already world).
  mesh.position.set(0, worldWaterY, 0);
  mesh.renderOrder = 2;
  mesh.receiveShadow = true;
  scene.add(mesh);

  // 5. Register the water body (resolved worldY = field offset + local waterY).
  const body = shape.toWaterBody(worldWaterY);
  registerWaterBody(state, body);
  cars.set(entity, { mesh, material, shader: null, body });

  // 6. Cleanup on destroy.
  state.onDestroy(entity, () => {
    const c = cars.get(entity);
    if (!c) return;
    cars.delete(entity);
    c.mesh.removeFromParent();
    c.mesh.geometry.dispose();
    c.material.dispose();
    unregisterWaterBody(state, c.body);
  });

  logger.info(
    `[water] ${entity} applied: waterY=${worldWaterY.toFixed(1)} rimY=${result.rimY.toFixed(1)}`
  );
  return true;
}
```

Note: `WaterAnimSystem` (currently in systems.ts) iterates `SIDECARS`. Update it to iterate `SIDE_CARS` from water-shape.ts instead (rename or re-export). See Step 5.

- [ ] **Step 5: Rewire LakeApplySystem + WaterAnimSystem**

In `VibeGame/src/plugins/water/systems.ts`:
1. Replace the `LakeApplySystem.update` body (lines ~302-443) to build a `LakeBowl` and call `applyWaterShape`. Remove the now-redundant `SIDECARS`/`LakeSideCar`/`sidecars` (replaced by `waterSideCars`). Keep the `lakeQuery`.

```ts
import { LakeBowl } from './lake-bowl';
import { applyWaterShape, waterSideCars } from './water-shape';

export const LakeApplySystem: System = {
  group: 'setup',
  update(state: State) {
    if (state.headless) return;
    for (const eid of lakeQuery(state.world)) {
      if (Lake.applied[eid] === 1) continue;
      const radius = Lake.radius[eid] || 6;
      const depth = Lake.depth[eid] || 1.5;
      const waterOffset = Lake.waterOffset[eid];
      const lx = Transform.posX[eid];
      const lz = Transform.posZ[eid];
      // LakeBowl works in field-local coords; applyWaterShape resolves the
      // world waterY (= data.worldOffset.y + carve waterY) and passes it to
      // toWaterBody, so we don't need the field offset here.
      const bowl = new LakeBowl({ localX: lx, localZ: lz, radius, depth, waterOffset });
      const applied = applyWaterShape(state, eid, bowl, {
        color: hexToInt(Lake.color[eid]),
        opacity: Lake.opacity[eid],
        ripple: Lake.ripple[eid],
        waveHeight:
          Lake.waveHeight[eid] > 0
            ? Lake.waveHeight[eid]
            : Math.min(0.09, Math.max(0.02, radius * 0.006)),
        waveSpeed: Lake.waveSpeed[eid] || 1,
      });
      if (applied) Lake.applied[eid] = 1;
    }
  },
};
```

2. Update `WaterAnimSystem` to use `waterSideCars`:

```ts
export const WaterAnimSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const cars = SIDE_CARS_PLACEHOLDER; // ← replace with the imported map
    // ... iterate and advance uTime
  },
};
```
Since `waterSideCars(state)` returns the live `Map`, change `WaterAnimSystem` to:
```ts
import { waterSideCars } from './water-shape';
export const WaterAnimSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    for (const car of waterSideCars(state).values()) {
      if (car.shader) {
        (car.shader.uniforms.uTime as { value: number }).value = state.time.elapsed;
      }
    }
  },
};
```
Remove the old `SIDECARS`/`LakeSideCar`/`sidecars` definitions from systems.ts (now in water-shape.ts).

- [ ] **Step 6: Run full water + terrain test suite**

Run: `cd VibeGame && bun test tests/unit/water tests/unit/terrain`
Expected: PASS — river-channel test green; lake regression (carve, registry, lake-bowl) green; material test green.

- [ ] **Step 7: Format, typecheck, commit**

```bash
cd VibeGame && bun run format && bun run check && bun run lint
git add VibeGame/src/plugins/water/river-channel.ts VibeGame/src/plugins/water/water-shape.ts VibeGame/src/plugins/water/systems.ts VibeGame/tests/unit/water/river-channel.test.ts
git commit -m "feat(vibegame): RiverChannel + applyWaterShape; rewire LakeApplySystem"
```

---

### Task 8: `<River>` component + recipe + `RiverApplySystem`

Expose rivers to the world XML. Parse the `path` attribute (string of `x,z` pairs), store the flat path in a side-channel, wire a `RiverApplySystem` that builds a `RiverChannel` and calls `applyWaterShape`.

**Files:**
- Modify: `VibeGame/src/plugins/water/components.ts` (add `River`)
- Modify: `VibeGame/src/plugins/water/plugin.ts` (add `riverRecipe`, parser, defaults; register system)
- Modify: `VibeGame/src/plugins/water/systems.ts` (add `RiverApplySystem`)
- Test: `VibeGame/tests/unit/water/river-recipe.test.ts` (new)

**Interfaces:**
- Consumes: `RiverChannel`, `applyWaterShape`, `waterSideCars` (Task 7).
- Produces:
  - `River` component (bitecs): `width, depth, waterOffset, color, opacity, ripple, waveHeight, waveSpeed, waterY, applied`.
  - `getRiverPath(state, entity): number[]` / `setRiverPath(state, entity, path)` side-channel.
  - `riverRecipe` + parser that parses `path="0,0 10,5 20,0"` into a flat `[0,0,10,5,20,0]` and sets `Transform.posX/posZ` to the first point.
  - `RiverApplySystem` (group `'setup'`).

- [ ] **Step 1: Write the failing test**

Create `VibeGame/tests/unit/water/river-recipe.test.ts`:

```ts
import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { TransformsPlugin } from 'vibegame/transforms';
import { WaterPlugin } from '../../../src/plugins/water/plugin';
import { River } from '../../../src/plugins/water/components';
import { getRiverPath } from '../../../src/plugins/water/components';

describe('River recipe', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  it('parses <River> with a path and applies defaults', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(WaterPlugin);
    const xml = '<root><River path="0,0 100,20 200,15"></River></root>';
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);
    expect(entities).toHaveLength(1);
    const eid = entities[0].entity;
    expect(River.width[eid]).toBe(WaterPlugin.config!.defaults!.river.width);
    expect(River.depth[eid]).toBe(WaterPlugin.config!.defaults!.river.depth);
    const path = getRiverPath(state, eid);
    expect(path).toEqual([0, 0, 100, 20, 200, 15]);
  });

  it('sets Transform.pos to the first path point (source)', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(WaterPlugin);
    const xml = '<root><River path="10,5 100,20"></River></root>';
    const parsed = XMLParser.parse(xml);
    const [e] = parseXMLToEntities(state, parsed.root);
    // posX/posZ = first path point (10, 5). (Read via the transform plugin.)
    // The exact assertion depends on the transform component export; verify
    // the path side-channel as the source of truth.
    expect(getRiverPath(state, e.entity)).toEqual([10, 5, 100, 20]);
  });

  it('parses width/depth/color attributes', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(WaterPlugin);
    const xml =
      '<root><River path="0,0 10,0" width="12" depth="3" color="#3a5a7a"></River></root>';
    const parsed = XMLParser.parse(xml);
    const [e] = parseXMLToEntities(state, parsed.root);
    expect(River.width[e.entity]).toBe(12);
    expect(River.depth[e.entity]).toBe(3);
    expect(River.color[e.entity]).toBe(0x3a5a7a);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/water/river-recipe.test.ts`
Expected: FAIL — `River` component / `getRiverPath` / `riverRecipe` not defined.

- [ ] **Step 3: Add the River component + path side-channel**

Append to `VibeGame/src/plugins/water/components.ts`:

```ts
import type { State } from '../../core';

/** `<River>` — a sculpted river channel along a polyline. Shares the water
 *  material and registry with lakes; the path is the lake disc generalised
 *  to a ribbon. */
export const River = {
  width: new Float32Array(MAX_ENTITIES),
  depth: new Float32Array(MAX_ENTITIES),
  waterOffset: new Float32Array(MAX_ENTITIES),
  color: new Uint32Array(MAX_ENTITIES),
  opacity: new Float32Array(MAX_ENTITIES),
  ripple: new Float32Array(MAX_ENTITIES),
  waveHeight: new Float32Array(MAX_ENTITIES),
  waveSpeed: new Float32Array(MAX_ENTITIES),
  waterY: new Float32Array(MAX_ENTITIES),
  applied: new Uint8Array(MAX_ENTITIES),
} as const;

/** Side-channel for river paths (bitecs can't store arrays). Flat [x0,z0,...]. */
const RIVER_PATHS = new WeakMap<State, Map<number, number[]>>();

export function getRiverPath(state: State, entity: number): number[] {
  return RIVER_PATHS.get(state)?.get(entity) ?? [];
}

export function setRiverPath(state: State, entity: number, path: number[]): void {
  let m = RIVER_PATHS.get(state);
  if (!m) {
    m = new Map();
    RIVER_PATHS.set(state, m);
  }
  m.set(entity, path);
}
```

- [ ] **Step 4: Add the recipe, parser, defaults, and RiverApplySystem**

In `VibeGame/src/plugins/water/plugin.ts`:
1. Add `River` import and `riverRecipe`:

```ts
import { Lake, River, setRiverPath } from './components';
import { LakeApplySystem, RiverApplySystem, WaterAnimSystem } from './systems';
import { Transform } from '../transforms/components';

export const riverRecipe: Recipe = {
  name: 'River',
  components: ['transform', 'river'],
  parserAttributes: ['path', 'width', 'depth', 'color', 'opacity', 'ripple', 'wave-height', 'wave-speed'],
};
```

2. Add the parser:

```ts
const riverColorAdapter: Adapter = (entity, value) => {
  let hex = String(value).trim();
  if (hex.startsWith('#')) hex = hex.slice(1);
  if (hex.startsWith('0x') || hex.startsWith('0X')) hex = hex.slice(2);
  const n = parseInt(hex, 16);
  if (!Number.isNaN(n)) River.color[entity] = n >>> 0;
};

const riverParser: Parser = ({ state, entity, element }) => {
  const attrs = element.attributes;
  const pathStr = attrs.path;
  const path: number[] = [];
  if (typeof pathStr === 'string') {
    // "0,0 100,20 200,15" → [0,0,100,20,200,15]
    const points = pathStr.trim().split(/\s+/);
    for (const pt of points) {
      const [xs, zs] = pt.split(',');
      const x = Number(xs);
      const z = Number(zs);
      if (Number.isFinite(x) && Number.isFinite(z)) {
        path.push(x, z);
      }
    }
  }
  if (path.length >= 4) {
    setRiverPath(state, entity, path);
    Transform.posX[entity] = path[0]!;
    Transform.posZ[entity] = path[1]!;
    Transform.dirty[entity] = 1;
  }
  const w = attrs.width;
  if (typeof w === 'number') River.width[entity] = w;
  const d = attrs.depth;
  if (typeof d === 'number') River.depth[entity] = d;
};
```

3. Register in the plugin:

```ts
export const WaterPlugin: Plugin = {
  systems: [
    LakeApplySystem,
    RiverApplySystem,
    WaterAnimSystem,
    WaterInteractionSystem,
    WaterRippleFxSystem,
  ],
  recipes: [lakeRecipe, riverRecipe],
  components: { lake: Lake, river: River },
  config: {
    defaults: {
      lake: { /* ... existing ... */ },
      river: {
        width: 6,
        depth: 1.5,
        waterOffset: 0.3,
        color: 0x3a5a7a,
        opacity: 0.85,
        ripple: 0.6,
        waveHeight: 0.04,
        waveSpeed: 1,
        waterY: 0,
        applied: 0,
      },
    },
    adapters: {
      lake: { color: colorAdapter },
      river: { color: riverColorAdapter },
    },
    parsers: {
      Lake: lakeParser,
      River: riverParser,
    },
  },
};
```

4. In `VibeGame/src/plugins/water/systems.ts`, add `RiverApplySystem`:

```ts
import { defineQuery } from '../../core';
import { River, getRiverPath } from './components';
import { RiverChannel } from './river-channel';
import { applyWaterShape } from './water-shape';

const riverQuery = defineQuery([River, Transform]);

export const RiverApplySystem: System = {
  group: 'setup',
  update(state: State) {
    if (state.headless) return;
    for (const eid of riverQuery(state.world)) {
      if (River.applied[eid] === 1) continue;
      const path = getRiverPath(state, eid);
      if (path.length < 4) continue; // need ≥ 2 points
      const channel = new RiverChannel({
        path,
        width: River.width[eid] || 6,
        depth: River.depth[eid] || 1.5,
        waterOffset: River.waterOffset[eid],
      });
      const applied = applyWaterShape(state, eid, channel, {
        color: River.color[eid] >>> 0,
        opacity: River.opacity[eid],
        ripple: River.ripple[eid],
        waveHeight: River.waveHeight[eid] || 0.04,
        waveSpeed: River.waveSpeed[eid] || 1,
      });
      if (applied) River.applied[eid] = 1;
    }
  },
};
```

(`applyWaterShape` resolves `worldWaterY = data.worldOffset.y + carve waterY` and passes it to `channel.toWaterBody(worldWaterY)`, so the channel doesn't carry a worldY.)

- [ ] **Step 5: Run recipe test + full suite**

Run: `cd VibeGame && bun test tests/unit/water tests/unit/terrain`
Expected: PASS — recipe tests green; regression green.

- [ ] **Step 6: Format, typecheck, commit**

```bash
cd VibeGame && bun run format && bun run check && bun run lint
git add VibeGame/src/plugins/water/components.ts VibeGame/src/plugins/water/plugin.ts VibeGame/src/plugins/water/systems.ts VibeGame/tests/unit/water/river-recipe.test.ts
git commit -m "feat(vibegame): <River> recipe + RiverApplySystem"
```

---

### Task 9: `terrain.json` rivers emitter (pixel→world)

Wire the parsed `rivers` array from `terrain.json` into actual `<River>` entities. Convert pixel coords to world coords. Replaces the dead `spawnWaterEntitiesFromTerrainData`.

**Files:**
- Modify: `VibeGame/src/plugins/terrain/terrain-data-loader.ts` — rewrite `spawnWaterEntitiesFromTerrainData` to emit rivers via `createEntityFromRecipe('River', ...)`.
- Test: `VibeGame/tests/unit/terrain/terrain-data-loader.test.ts` (extend).

**Interfaces:**
- Consumes: `createEntityFromRecipe` (core), `River` component + `setRiverPath` (Task 8), `parseTerrainData` output (existing).
- Produces: `spawnWaterEntitiesFromTerrainData` now creates `<River>` entities from `rivers`.

- [ ] **Step 1: Read the current spawnWaterEntitiesFromTerrainData signature**

Run: `cd VibeGame && sed -n '130,145p' src/plugins/terrain/terrain-data-loader.ts`
Confirm the function signature and the `createEntityFromRecipe` import.

- [ ] **Step 2: Write the failing test (append to terrain-data-loader.test.ts)**

```ts
describe('spawnWaterEntitiesFromTerrainData — rivers', () => {
  it('creates a River entity per river with world-coord path', () => {
    // Minimal: terrain 4 px over 8 m world → pixel→world scale 2 m/px.
    const data = {
      version: '1.0',
      terrain: { size: 4, world_size: 8, max_height: 5 },
      rivers: [
        {
          id: 0,
          source: [0, 0] as [number, number],
          path: [
            [0, 0],
            [2, 2],
            [4, 0],
          ] as Array<[number, number]>,
          length: 10,
        },
      ],
      lakes: [],
      lake_planes: [],
    };
    const state = new State() as import('../../../src/core').State;
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(WaterPlugin);
    const parsed = parseTerrainData(data);
    spawnWaterEntitiesFromTerrainData(state, parsed);
    // Verify a River entity exists with the converted path.
    // world = pixel/size * worldSize - worldSize/2 = px/4*8 - 4 = px*2 - 4.
    // pixel (0,0) → world (-4,-4); pixel (2,2) → world (0,0); pixel (4,0) → world (4,-4).
    const { River, getRiverPath } = require('../../../src/plugins/water/components');
    let found = false;
    for (let e = 0; e < 1000; e++) {
      if (River.applied[e] !== undefined && getRiverPath(state, e).length > 0) {
        found = true;
        const path = getRiverPath(state, e);
        expect(path).toEqual([-4, -4, 0, 0, 4, -4]);
        break;
      }
    }
    expect(found).toBe(true);
  });
});
```

(Add the necessary imports at the top: `TransformsPlugin`, `WaterPlugin`, `State`.)

- [ ] **Step 3: Run test to verify it fails**

Run: `cd VibeGame && bun test tests/unit/terrain/terrain-data-loader.test.ts`
Expected: FAIL — emitter still uses the dead `Water` recipe / doesn't create rivers.

- [ ] **Step 4: Rewrite the emitter**

In `VibeGame/src/plugins/terrain/terrain-data-loader.ts`, replace `spawnWaterEntitiesFromTerrainData`:

```ts
import { setRiverPath } from '../water/components';

export function spawnWaterEntitiesFromTerrainData(
  state: import('../../core').State,
  terrainData: TerrainData
): void {
  const { size, world_size, max_height } = terrainData.terrain;
  // pixel → world: world = (pixel / size) * world_size - world_size/2.
  const toWorld = (px: number): number => (px / size) * world_size - world_size / 2;
  for (const river of terrainData.rivers) {
    if (!river.path || river.path.length < 2) continue;
    const flat: number[] = [];
    for (const [px, pz] of river.path) {
      flat.push(toWorld(px), toWorld(pz));
    }
    const eid = createEntityFromRecipe(state, 'River', {});
    if (eid >= 0) {
      setRiverPath(state, eid, flat);
    }
  }
}
```

(Confirm `createEntityFromRecipe` is already imported — it is, used by the old code.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cd VibeGame && bun test tests/unit/terrain/terrain-data-loader.test.ts`
Expected: PASS — emitter creates a River with the converted path.

- [ ] **Step 6: Format, typecheck, commit**

```bash
cd VibeGame && bun run format && bun run check && bun run lint
git add VibeGame/src/plugins/terrain/terrain-data-loader.ts VibeGame/tests/unit/terrain/terrain-data-loader.test.ts
git commit -m "feat(vibegame): terrain.json rivers emitter (pixel→world coords)"
```

---

### Task 10: Dead code cleanup + full regression + visual validation

Remove the obsolete stubs, run the entire suite, and visually validate in the browser (lake unchanged + a river crossing the map).

**Files:**
- Modify: `VibeGame/src/plugins/terrain/lake-renderer.ts` — delete `createLakeWaterEntities` + `createRiverWaterEntities` (or delete the file if empty).
- Test: full suite + manual browser validation.

- [ ] **Step 1: Remove dead code**

Delete `createLakeWaterEntities` and `createRiverWaterEntities` from `VibeGame/src/plugins/terrain/lake-renderer.ts`. If the file becomes empty, delete it and remove any import of it elsewhere (grep: `grep -rn "lake-renderer" VibeGame/src`).

Run: `cd VibeGame && grep -rn "lake-renderer" src` — if no live imports remain, `rm src/plugins/terrain/lake-renderer.ts`.

- [ ] **Step 2: Run full test suite**

Run: `cd VibeGame && bun test`
Expected: PASS — all unit/integration/e2e green.

- [ ] **Step 3: Typecheck + lint + format check**

Run: `make check-vibegame lint-vibegame fmt-check-vibegame` (from repo root)
Expected: All pass.

- [ ] **Step 4: Visual validation (browser)**

Add a `<River>` to `VibeGame/examples/simple-rpg/index.html` crossing the map, e.g.:

```html
<River path="-80,10 -40,5 0,15 40,5 80,20" width="8" depth="2" color="#3a5a7a"></River>
```

Run the dev server (`cd VibeGame/examples/simple-rpg && bun run dev`), open it, and via Chrome MCP verify:
1. A river channel is carved across the terrain.
2. An animated water ribbon follows the path.
3. Existing `<Lake>` elements render unchanged (no regression).
4. Walking/physics over the river behaves (collider rebuilt).

- [ ] **Step 5: Commit cleanup + example**

```bash
git add -A VibeGame/src/plugins/terrain/lake-renderer.ts VibeGame/examples/simple-rpg/index.html
git commit -m "chore(vibegame): remove dead lake/river renderer stubs; add river example"
```

---

## Summary of behavior change

| Before | After |
|---|---|
| Water = lakes only (disc). | Water = lakes (disc) + rivers (ribbon) via shared `WaterShape`. |
| `WaterBody` is a disc; queries are circular. | `WaterBody` is a `lake \| river` union; queries dispatch on `kind`. |
| Water material computes `t` radially in the fragment shader. | Material reads `t` from the geometry (`aWaterT`); shape-agnostic. |
| `terrain.json` rivers are parsed but ignored. | Rivers spawn `<River>` entities (pixel→world). |
| `LakeApplySystem` holds the whole apply flow. | `applyWaterShape` holds the flow; `LakeApplySystem`/`RiverApplySystem` are thin wrappers. |
| Dead stubs `createLakeWaterEntities`/`createRiverWaterEntities`. | Removed. |
