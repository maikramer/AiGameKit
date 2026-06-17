import * as THREE from 'three';
import type { State, System } from '../../core';
import { defineQuery } from '../../core';
import { loadGltfMaster } from '../../extras/gltf-bridge';
import { getScene } from '../rendering';
import { MainCamera } from '../rendering/components';
import { DistanceCull } from '../rendering/components';
import { Transform, WorldTransform } from '../transforms/components';
import { registerGltfLocalYBounds } from './gltf-bounds-cache';
import { pickLodLevel } from './gltf-lod-level';

/**
 * Auto-instancing for identical static GLBs (`<GLTFLoader instanced="true">`).
 *
 * All entities sharing a URL render through ONE `InstancedMesh` per GLB
 * primitive — one draw call per (geometry, material) for the whole set,
 * instead of a scene-graph clone per entity. This is the project's single
 * instancing path: dense static props (trees, rocks) AND interactive ones
 * (destructible, scripted) all flow through here as real ECS entities, so they
 * keep colliders/scripts/DistanceCull while sharing draw calls.
 *
 * LOD: when `lod1-url` / `lod2-url` are provided, the pool holds up to three
 * InstancedMesh sets (one per level) and each instance is drawn in exactly the
 * level its camera distance selects (others zero-scaled). Single-LOD pools keep
 * the cheap one-level path.
 *
 * Instances are dynamic: entities can be destroyed at any time (destructible
 * props) and their slot is swap-removed; `DistanceCull` collapses the instance
 * to zero scale.
 */

const LEVELS = 3;
const LOD1_DIST = 80;
const LOD2_DIST = 200;

interface PoolPrimitive {
  mesh: THREE.InstancedMesh;
  /** Node transform of the primitive inside the GLB. */
  local: THREE.Matrix4;
}

interface InstanceSlotState {
  entity: number;
  /** Active LOD level (-1 forces a rewrite next frame). */
  level: number;
  // last written source values — rewrite only on change
  x: number;
  y: number;
  z: number;
  ex: number;
  ey: number;
  ez: number;
  sx: number;
  culled: boolean;
}

interface GltfInstancePool {
  url: string;
  /** [lod0, lod1?, lod2?] — lod0 always present. */
  lodUrls: [string, string | undefined, string | undefined];
  /** Primitives per level; null until that level's GLB finishes loading. */
  levels: (PoolPrimitive[] | null)[];
  slots: InstanceSlotState[];
  slotByEntity: Map<number, number>;
  pendingAdds: number[];
  capacity: number;
  loadKicked: boolean;
  boundsDirty: boolean;
  /** LOD thresholds (near = lod0→1, mid = lod1→2). */
  near: number;
  mid: number;
}

const poolsByState = new WeakMap<State, Map<string, GltfInstancePool>>();
const instancedFlagByState = new WeakMap<State, Set<number>>();
/** lod1/lod2 urls captured by the `lod1-url`/`lod2-url` adapters, per entity. */
const instancedLodUrlsByState = new WeakMap<
  State,
  Map<number, [string | undefined, string | undefined]>
>();

const INITIAL_CAPACITY = 16;

export function markGltfInstanced(state: State, entity: number): void {
  let s = instancedFlagByState.get(state);
  if (!s) {
    s = new Set();
    instancedFlagByState.set(state, s);
  }
  s.add(entity);
}

export function isGltfInstanced(state: State, entity: number): boolean {
  return instancedFlagByState.get(state)?.has(entity) ?? false;
}

/** Record an instanced entity's lod1/lod2 URLs (from the GLTFLoader adapters). */
export function setInstancedLodUrl(
  state: State,
  entity: number,
  level: 1 | 2,
  url: string
): void {
  let m = instancedLodUrlsByState.get(state);
  if (!m) {
    m = new Map();
    instancedLodUrlsByState.set(state, m);
  }
  let pair = m.get(entity);
  if (!pair) {
    pair = [undefined, undefined];
    m.set(entity, pair);
  }
  pair[level - 1] = url.trim();
}

export function getInstancedLodUrls(
  state: State,
  entity: number
): [string | undefined, string | undefined] {
  return (
    instancedLodUrlsByState.get(state)?.get(entity) ?? [undefined, undefined]
  );
}

function getPools(state: State): Map<string, GltfInstancePool> {
  let m = poolsByState.get(state);
  if (!m) {
    m = new Map();
    poolsByState.set(state, m);
  }
  return m;
}

const _entityMatrix = new THREE.Matrix4();
const _instanceMatrix = new THREE.Matrix4();
const _pos = new THREE.Vector3();
const _quat = new THREE.Quaternion();
const _euler = new THREE.Euler();
const _scale = new THREE.Vector3();
const ZERO_SCALE_MATRIX = new THREE.Matrix4().makeScale(0, 0, 0);

/** Compose the entity's world matrix (same euler-vs-quat rule as scene sync). */
function composeEntityMatrix(state: State, eid: number): THREE.Matrix4 {
  const useWorld =
    state.hasComponent(eid, WorldTransform) &&
    // WorldTransform is filled by the hierarchy one frame after spawn; a
    // zero scale means "not computed yet" — fall back to the local pose.
    WorldTransform.scaleX[eid] !== 0;
  const T = useWorld ? WorldTransform : Transform;

  _pos.set(T.posX[eid], T.posY[eid], T.posZ[eid]);
  _scale.set(T.scaleX[eid] || 1, T.scaleY[eid] || 1, T.scaleZ[eid] || 1);

  const rx = T.rotX[eid];
  const ry = T.rotY[eid];
  const rz = T.rotZ[eid];
  const rw = T.rotW[eid];
  const quatIdentity =
    Math.abs(rw - 1) < 1e-6 &&
    Math.abs(rx) < 1e-6 &&
    Math.abs(ry) < 1e-6 &&
    Math.abs(rz) < 1e-6;
  if (quatIdentity) {
    _quat.setFromEuler(_euler.set(T.eulerX[eid], T.eulerY[eid], T.eulerZ[eid]));
  } else {
    _quat.set(rx, ry, rz, rw);
  }

  return _entityMatrix.compose(_pos, _quat, _scale);
}

/** Highest loaded level ≤ desired (lod0 always loaded). */
function clampLevel(pool: GltfInstancePool, desired: number): number {
  for (let L = Math.min(desired, LEVELS - 1); L > 0; L--) {
    if (pool.levels[L]) return L;
  }
  return 0;
}

function writeSlotMatrices(
  state: State,
  pool: GltfInstancePool,
  slotIndex: number
): void {
  const slot = pool.slots[slotIndex];
  const active = clampLevel(pool, slot.level < 0 ? 0 : slot.level);

  const entityMatrix = slot.culled
    ? null
    : composeEntityMatrix(state, slot.entity);

  for (let L = 0; L < LEVELS; L++) {
    const prims = pool.levels[L];
    if (!prims) continue;
    const drawHere = entityMatrix !== null && L === active;
    for (const prim of prims) {
      if (drawHere) {
        _instanceMatrix.multiplyMatrices(entityMatrix, prim.local);
        prim.mesh.setMatrixAt(slotIndex, _instanceMatrix);
      } else {
        prim.mesh.setMatrixAt(slotIndex, ZERO_SCALE_MATRIX);
      }
      prim.mesh.instanceMatrix.needsUpdate = true;
    }
  }
  pool.boundsDirty = true;
}

function snapshotSlotSource(slot: InstanceSlotState): void {
  const eid = slot.entity;
  slot.x = Transform.posX[eid];
  slot.y = Transform.posY[eid];
  slot.z = Transform.posZ[eid];
  slot.ex = Transform.eulerX[eid];
  slot.ey = Transform.eulerY[eid];
  slot.ez = Transform.eulerZ[eid];
  slot.sx = Transform.scaleX[eid];
}

function slotSourceChanged(slot: InstanceSlotState): boolean {
  const eid = slot.entity;
  return (
    slot.x !== Transform.posX[eid] ||
    slot.y !== Transform.posY[eid] ||
    slot.z !== Transform.posZ[eid] ||
    slot.ex !== Transform.eulerX[eid] ||
    slot.ey !== Transform.eulerY[eid] ||
    slot.ez !== Transform.eulerZ[eid] ||
    slot.sx !== Transform.scaleX[eid]
  );
}

function syncCounts(pool: GltfInstancePool): void {
  const n = pool.slots.length;
  for (let L = 0; L < LEVELS; L++) {
    const prims = pool.levels[L];
    if (!prims) continue;
    for (const prim of prims) {
      // Clamp to the instanceMatrix buffer capacity so a level whose buffer was
      // allocated before the pool grew never draws more instances than it holds
      // (otherwise WebGL warns "Instance fetch requires N, but attribs only
      // supply M").
      prim.mesh.count = Math.min(n, prim.mesh.instanceMatrix.count);
    }
  }
}

function growPool(pool: GltfInstancePool, scene: THREE.Scene): void {
  const newCapacity = Math.max(INITIAL_CAPACITY, pool.capacity * 2);
  for (let L = 0; L < LEVELS; L++) {
    const prims = pool.levels[L];
    if (!prims) continue;
    pool.levels[L] = prims.map((prim) => {
      const next = new THREE.InstancedMesh(
        prim.mesh.geometry,
        prim.mesh.material,
        newCapacity
      );
      next.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      next.castShadow = prim.mesh.castShadow;
      next.receiveShadow = prim.mesh.receiveShadow;
      next.count = pool.slots.length;
      next.instanceMatrix.array.set(
        prim.mesh.instanceMatrix.array.subarray(0, pool.slots.length * 16)
      );
      next.instanceMatrix.needsUpdate = true;
      scene.add(next);
      scene.remove(prim.mesh);
      prim.mesh.dispose(); // releases the instance buffer; geometry/material shared
      return { mesh: next, local: prim.local };
    });
  }
  pool.capacity = newCapacity;
}

function addSlot(state: State, pool: GltfInstancePool, eid: number): void {
  const scene = getScene(state);
  if (!scene || !pool.levels[0]) return;
  if (pool.slotByEntity.has(eid)) return;

  if (pool.slots.length === pool.capacity) {
    growPool(pool, scene);
  }

  const slotIndex = pool.slots.length;
  const slot: InstanceSlotState = {
    entity: eid,
    level: -1,
    x: NaN,
    y: NaN,
    z: NaN,
    ex: 0,
    ey: 0,
    ez: 0,
    sx: 0,
    culled: false,
  };
  pool.slots.push(slot);
  pool.slotByEntity.set(eid, slotIndex);
  syncCounts(pool);
  snapshotSlotSource(slot);
  slot.level = 0;
  writeSlotMatrices(state, pool, slotIndex);

  state.onDestroy(eid, () => removeSlot(pool, eid));
}

function removeSlot(pool: GltfInstancePool, eid: number): void {
  const slotIndex = pool.slotByEntity.get(eid);
  if (slotIndex === undefined || !pool.levels[0]) {
    pool.slotByEntity.delete(eid);
    return;
  }
  pool.slotByEntity.delete(eid);

  const lastIndex = pool.slots.length - 1;
  const last = pool.slots[lastIndex];
  pool.slots.pop();

  if (slotIndex !== lastIndex) {
    pool.slots[slotIndex] = last;
    pool.slotByEntity.set(last.entity, slotIndex);
    for (let L = 0; L < LEVELS; L++) {
      const prims = pool.levels[L];
      if (!prims) continue;
      for (const prim of prims) {
        prim.mesh.getMatrixAt(lastIndex, _instanceMatrix);
        prim.mesh.setMatrixAt(slotIndex, _instanceMatrix);
      }
    }
  }
  syncCounts(pool);
  for (let L = 0; L < LEVELS; L++) {
    const prims = pool.levels[L];
    if (!prims) continue;
    for (const prim of prims) prim.mesh.instanceMatrix.needsUpdate = true;
  }
  pool.boundsDirty = true;
}

function buildLevelPrimitives(
  state: State,
  pool: GltfInstancePool,
  level: number,
  master: THREE.Group
): void {
  const scene = getScene(state);
  if (!scene) return;

  master.updateMatrixWorld(true);
  const primitives: PoolPrimitive[] = [];
  master.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh !== true) return;
    const instanced = new THREE.InstancedMesh(
      mesh.geometry,
      mesh.material,
      pool.capacity
    );
    instanced.name = `gltf-instances:${pool.lodUrls[level]}`;
    instanced.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    instanced.castShadow = mesh.castShadow;
    instanced.receiveShadow = mesh.receiveShadow;
    instanced.count = pool.slots.length;
    scene.add(instanced);
    primitives.push({ mesh: instanced, local: mesh.matrixWorld.clone() });
  });
  pool.levels[level] = primitives;

  // A freshly built level starts with garbage (identity) matrices for existing
  // slots — zero them, then force the LOD system to re-place the right ones.
  for (let i = 0; i < pool.slots.length; i++) {
    for (const prim of primitives) prim.mesh.setMatrixAt(i, ZERO_SCALE_MATRIX);
    pool.slots[i].level = -1;
  }
  for (const prim of primitives) prim.mesh.instanceMatrix.needsUpdate = true;
}

function kickLoad(state: State, pool: GltfInstancePool): void {
  pool.loadKicked = true;
  // lod0 first: gates the pending adds and finalizes pool.capacity from the
  // full pendingAdds queue. Higher LOD levels are kicked only after lod0 is
  // built so they allocate a buffer matching lod0; if they raced ahead they
  // would build at INITIAL_CAPACITY (16) while slots later exceed it, and
  // syncCounts would draw past the buffer ("Instance fetch requires N...").
  void loadGltfMaster(state, pool.lodUrls[0])
    .then((gltf) => {
      registerGltfLocalYBounds(pool.lodUrls[0], gltf.scene);
      pool.capacity = Math.max(INITIAL_CAPACITY, pool.pendingAdds.length * 2);
      buildLevelPrimitives(state, pool, 0, gltf.scene);
      const adds = pool.pendingAdds;
      pool.pendingAdds = [];
      for (const eid of adds) {
        if (state.exists(eid)) addSlot(state, pool, eid);
      }

      for (let L = 1; L < LEVELS; L++) {
        const lodUrl = pool.lodUrls[L];
        if (!lodUrl) continue;
        const level = L;
        void loadGltfMaster(state, lodUrl)
          .then((gltfLod) => buildLevelPrimitives(state, pool, level, gltfLod.scene))
          .catch((err: unknown) => {
            const msg = err instanceof Error ? err.message : String(err);
            console.warn(`[gltf-instance] lod${level} "${lodUrl}" failed: ${msg}`);
          });
      }
    })
    .catch((err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(
        `[gltf-instance] failed to load "${pool.lodUrls[0]}": ${msg}`
      );
    });
}

/** Route an entity's GLB visual through the shared instance pool for `url`. */
export function addInstancedGltf(
  state: State,
  entity: number,
  url: string,
  lod1?: string,
  lod2?: string
): void {
  const pools = getPools(state);
  let pool = pools.get(url);
  if (!pool) {
    pool = {
      url,
      lodUrls: [url, lod1, lod2],
      levels: new Array(LEVELS).fill(null),
      slots: [],
      slotByEntity: new Map(),
      pendingAdds: [],
      capacity: INITIAL_CAPACITY,
      loadKicked: false,
      boundsDirty: false,
      near: LOD1_DIST,
      mid: LOD2_DIST,
    };
    pools.set(url, pool);
  }

  if (pool.levels[0]) {
    addSlot(state, pool, entity);
    return;
  }

  pool.pendingAdds.push(entity);
  if (!pool.loadKicked) kickLoad(state, pool);
}

function entityDistanceToCamera(
  state: State,
  eid: number,
  cx: number,
  cy: number,
  cz: number
): number {
  const useWorld =
    state.hasComponent(eid, WorldTransform) && WorldTransform.scaleX[eid] !== 0;
  const x = useWorld ? WorldTransform.posX[eid] : Transform.posX[eid];
  const y = useWorld ? WorldTransform.posY[eid] : Transform.posY[eid];
  const z = useWorld ? WorldTransform.posZ[eid] : Transform.posZ[eid];
  const dx = x - cx;
  const dy = y - cy;
  const dz = z - cz;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

const cameraQuery = defineQuery([MainCamera, WorldTransform]);

/**
 * Per-frame slot maintenance: rewrite matrices for entities whose Transform
 * changed, whose DistanceCull state flipped, or whose camera-distance LOD level
 * changed, and refresh bounding spheres (instances participate in frustum
 * culling as one unit).
 */
export const GltfAutoInstanceSystem: System = {
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const pools = poolsByState.get(state);
    if (!pools) return;

    const cams = cameraQuery(state.world);
    let cx = 0;
    let cy = 0;
    let cz = 0;
    if (cams.length > 0) {
      const cam = cams[0];
      cx = WorldTransform.posX[cam];
      cy = WorldTransform.posY[cam];
      cz = WorldTransform.posZ[cam];
    }

    for (const [, pool] of pools) {
      if (!pool.levels[0]) continue;
      const multiLevel = pool.levels[1] != null || pool.levels[2] != null;

      for (let i = 0; i < pool.slots.length; i++) {
        const slot = pool.slots[i];
        const eid = slot.entity;

        const culled =
          state.hasComponent(eid, DistanceCull) &&
          DistanceCull.culled[eid] === 1;

        let level = slot.level;
        if (multiLevel && !culled) {
          const dist = entityDistanceToCamera(state, eid, cx, cy, cz);
          level = clampLevel(
            pool,
            pickLodLevel(
              dist,
              pool.near,
              pool.mid,
              slot.level < 0 ? undefined : slot.level
            )
          );
        }

        const moved = slotSourceChanged(slot);
        if (culled === slot.culled && level === slot.level && !moved) continue;

        slot.culled = culled;
        slot.level = level;
        snapshotSlotSource(slot);
        writeSlotMatrices(state, pool, i);
      }

      if (pool.boundsDirty) {
        pool.boundsDirty = false;
        for (let L = 0; L < LEVELS; L++) {
          const prims = pool.levels[L];
          if (!prims) continue;
          for (const prim of prims) prim.mesh.computeBoundingSphere();
        }
      }
    }
  },

  dispose(state: State) {
    const pools = poolsByState.get(state);
    if (!pools) return;
    const scene = getScene(state);
    for (const [, pool] of pools) {
      for (let L = 0; L < LEVELS; L++) {
        for (const prim of pool.levels[L] ?? []) {
          if (scene) scene.remove(prim.mesh);
          prim.mesh.dispose();
        }
      }
    }
    pools.clear();
    poolsByState.delete(state);
    instancedFlagByState.delete(state);
    instancedLodUrlsByState.delete(state);
  },
};
