import * as THREE from 'three';
import {
  computeBoundsTree,
  disposeBoundsTree,
  acceleratedRaycast,
  type MeshBVH,
} from 'three-mesh-bvh';
import type { State } from '../../core';

// Patch THREE prototypes once per process.
let prototypesPatched = false;
function ensurePrototypes(): void {
  if (prototypesPatched) return;
  prototypesPatched = true;
  (
    THREE.BufferGeometry.prototype as unknown as {
      computeBoundsTree: typeof computeBoundsTree;
    }
  ).computeBoundsTree = computeBoundsTree;
  (
    THREE.BufferGeometry.prototype as unknown as {
      disposeBoundsTree: typeof disposeBoundsTree;
    }
  ).disposeBoundsTree = disposeBoundsTree;
  (
    THREE.Mesh.prototype as unknown as {
      raycast: typeof acceleratedRaycast;
    }
  ).raycast = acceleratedRaycast;
}

export interface BvhEntry {
  /** Owning ECS entity, or 0 for engine-owned (e.g. terrain). */
  entity: number;
  /** Wrapper mesh whose geometry holds the BVH. World-space vertices. */
  mesh: THREE.Mesh;
  /** Collision/visibility layer mask (bitfield). */
  layer: number;
  /** Optional source object3D, kept for transform-based rebuilds. */
  source?: THREE.Object3D;
}

export interface BvhContext {
  /** All registered meshes used in queries. */
  entries: Map<string, BvhEntry>;
  /** Per-entity lookup of entry keys (one entity can register multiple meshes). */
  entityKeys: Map<number, string[]>;
}

const stateToContext = new WeakMap<State, BvhContext>();

export function getBvhContext(state: State): BvhContext {
  ensurePrototypes();
  let ctx = stateToContext.get(state);
  if (!ctx) {
    ctx = { entries: new Map(), entityKeys: new Map() };
    stateToContext.set(state, ctx);
  }
  return ctx;
}

/**
 * Register a mesh in the BVH. Geometry must already be transformed to world
 * space (vertices baked) for accurate queries — the mesh's matrixWorld is set
 * to identity. Returns the key used to later unregister.
 */
export function registerBvhMesh(
  state: State,
  key: string,
  geometry: THREE.BufferGeometry,
  options: { entity?: number; layer?: number; source?: THREE.Object3D } = {}
): BvhEntry {
  ensurePrototypes();
  const ctx = getBvhContext(state);

  const existing = ctx.entries.get(key);
  if (existing) {
    unregisterBvhMesh(state, key);
  }

  const geo = geometry;
  const geoWithBvh = geo as THREE.BufferGeometry & {
    computeBoundsTree: () => void;
    boundsTree?: MeshBVH;
  };
  if (!geoWithBvh.boundsTree) {
    geoWithBvh.computeBoundsTree();
  }

  const mesh = new THREE.Mesh(geo);
  mesh.matrixAutoUpdate = false;
  mesh.matrixWorld.identity();

  const entry: BvhEntry = {
    entity: options.entity ?? 0,
    mesh,
    layer: options.layer ?? 0xffff,
    source: options.source,
  };
  ctx.entries.set(key, entry);

  if (entry.entity > 0) {
    let arr = ctx.entityKeys.get(entry.entity);
    if (!arr) {
      arr = [];
      ctx.entityKeys.set(entry.entity, arr);
    }
    arr.push(key);
  }
  return entry;
}

export function unregisterBvhMesh(state: State, key: string): void {
  const ctx = getBvhContext(state);
  const entry = ctx.entries.get(key);
  if (!entry) return;
  const geo = entry.mesh.geometry as THREE.BufferGeometry & {
    disposeBoundsTree?: () => void;
  };
  // Free the BVH acceleration structure (CPU), then the GPU buffer.
  geo.disposeBoundsTree?.();
  geo.dispose();
  ctx.entries.delete(key);
  if (entry.entity > 0) {
    const arr = ctx.entityKeys.get(entry.entity);
    if (arr) {
      const i = arr.indexOf(key);
      if (i >= 0) arr.splice(i, 1);
      if (arr.length === 0) ctx.entityKeys.delete(entry.entity);
    }
  }
}

export function unregisterBvhForEntity(state: State, entity: number): void {
  const ctx = getBvhContext(state);
  const keys = ctx.entityKeys.get(entity);
  if (!keys) return;
  for (const key of [...keys]) {
    unregisterBvhMesh(state, key);
  }
}

/**
 * Dispose every registered BVH mesh geometry (bounds tree + GPU buffer) for
 * `state` and drop the context. Idempotent: no-op when no context exists.
 * Intended for the bvh system's `dispose(state)` hook on State teardown.
 */
export function disposeBvhContext(state: State): void {
  const ctx = stateToContext.get(state);
  if (!ctx) return;
  for (const entry of ctx.entries.values()) {
    const geo = entry.mesh.geometry as THREE.BufferGeometry & {
      disposeBoundsTree?: () => void;
    };
    // Best-effort per item: keep sweeping even if one geometry throws.
    try {
      geo.disposeBoundsTree?.();
    } catch {
      /* ignore */
    }
    try {
      geo.dispose();
    } catch {
      /* ignore */
    }
  }
  ctx.entries.clear();
  ctx.entityKeys.clear();
  stateToContext.delete(state);
}

export interface BvhRaycastHit {
  entity: number;
  layer: number;
  distance: number;
  point: THREE.Vector3;
  normal: THREE.Vector3;
  key: string;
}

const _raycaster = new THREE.Raycaster();
const _hits: THREE.Intersection[] = [];

const _scratchPoint = new THREE.Vector3();
const _scratchNormal = new THREE.Vector3();
const _scratchHit: BvhRaycastHit = {
  entity: 0,
  layer: 0,
  distance: 0,
  point: _scratchPoint,
  normal: _scratchNormal,
  key: '',
};

function writeHit(
  target: BvhRaycastHit,
  entity: number,
  layer: number,
  distance: number,
  point: THREE.Vector3,
  faceNormal: THREE.Vector3 | undefined,
  key: string
): void {
  target.entity = entity;
  target.layer = layer;
  target.distance = distance;
  target.point.copy(point);
  if (faceNormal) {
    target.normal.copy(faceNormal);
  } else {
    target.normal.set(0, 1, 0);
  }
  target.key = key;
}

/**
 * Cast a ray against every registered BVH mesh and return the closest hit.
 * Pass `layerMask` to ignore meshes whose layer has no overlapping bits.
 *
 * Pass `out` to write the result into a caller-owned object (avoids per-call
 * allocation in hot paths like camera collision). If omitted, a shared module
 * scratch is used — callers must consume it before the next `castBvhRay` call.
 */
export function castBvhRay(
  state: State,
  origin: THREE.Vector3,
  direction: THREE.Vector3,
  maxDist: number,
  layerMask = 0xffff,
  out?: BvhRaycastHit
): BvhRaycastHit | null {
  ensurePrototypes();
  const ctx = getBvhContext(state);
  if (ctx.entries.size === 0) return null;

  _raycaster.set(origin, direction);
  _raycaster.near = 0;
  _raycaster.far = maxDist;
  _raycaster.firstHitOnly = true;

  const target = out ?? _scratchHit;
  let haveHit = false;
  let bestDistance = Infinity;
  let bestEntity = 0;
  let bestLayer = 0;
  let bestPoint: THREE.Vector3 | null = null;
  let bestNormal: THREE.Vector3 | undefined = undefined;
  let bestKey = '';

  for (const [key, entry] of ctx.entries) {
    if ((entry.layer & layerMask) === 0) continue;
    _hits.length = 0;
    entry.mesh.raycast(_raycaster, _hits);
    if (_hits.length === 0) continue;
    const hit = _hits[0];
    if (hit.distance < bestDistance) {
      bestDistance = hit.distance;
      bestEntity = entry.entity;
      bestLayer = entry.layer;
      bestPoint = hit.point;
      bestNormal = hit.face?.normal;
      bestKey = key;
      haveHit = true;
      _raycaster.far = hit.distance;
    }
  }

  if (!haveHit) return null;
  writeHit(
    target,
    bestEntity,
    bestLayer,
    bestDistance,
    bestPoint!,
    bestNormal,
    bestKey
  );
  return target;
}

const _downDir = new THREE.Vector3(0, -1, 0);
const _downOrigin = new THREE.Vector3();

/**
 * Returns the surface Y at (worldX, worldZ) by casting a ray straight down
 * from `worldY`. Returns `null` if nothing was hit within `maxDrop` metres.
 */
export function getBvhSurfaceHeight(
  state: State,
  worldX: number,
  worldY: number,
  worldZ: number,
  maxDrop = 2000,
  layerMask = 0xffff
): number | null {
  _downOrigin.set(worldX, worldY, worldZ);
  const hit = castBvhRay(state, _downOrigin, _downDir, maxDrop, layerMask);
  return hit ? hit.point.y : null;
}

export function getBvhStats(state: State): {
  meshCount: number;
  entityCount: number;
} {
  const ctx = getBvhContext(state);
  return {
    meshCount: ctx.entries.size,
    entityCount: ctx.entityKeys.size,
  };
}
