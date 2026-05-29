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
  (THREE.BufferGeometry.prototype as unknown as {
    computeBoundsTree: typeof computeBoundsTree;
  }).computeBoundsTree = computeBoundsTree;
  (THREE.BufferGeometry.prototype as unknown as {
    disposeBoundsTree: typeof disposeBoundsTree;
  }).disposeBoundsTree = disposeBoundsTree;
  (THREE.Mesh.prototype as unknown as {
    raycast: typeof acceleratedRaycast;
  }).raycast = acceleratedRaycast;
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
  geo.disposeBoundsTree?.();
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
const _normalScratch = new THREE.Vector3();

/**
 * Cast a ray against every registered BVH mesh and return the closest hit.
 * Pass `layerMask` to ignore meshes whose layer has no overlapping bits.
 */
export function castBvhRay(
  state: State,
  origin: THREE.Vector3,
  direction: THREE.Vector3,
  maxDist: number,
  layerMask = 0xffff
): BvhRaycastHit | null {
  ensurePrototypes();
  const ctx = getBvhContext(state);
  if (ctx.entries.size === 0) return null;

  _raycaster.set(origin, direction);
  _raycaster.near = 0;
  _raycaster.far = maxDist;
  _raycaster.firstHitOnly = true;

  let closest: BvhRaycastHit | null = null;

  for (const [key, entry] of ctx.entries) {
    if ((entry.layer & layerMask) === 0) continue;
    _hits.length = 0;
    entry.mesh.raycast(_raycaster, _hits);
    if (_hits.length === 0) continue;
    const hit = _hits[0];
    if (!closest || hit.distance < closest.distance) {
      const normal = hit.face
        ? _normalScratch.copy(hit.face.normal).clone()
        : new THREE.Vector3(0, 1, 0);
      closest = {
        entity: entry.entity,
        layer: entry.layer,
        distance: hit.distance,
        point: hit.point.clone(),
        normal,
        key,
      };
    }
  }

  return closest;
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
