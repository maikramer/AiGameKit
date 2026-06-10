import * as THREE from 'three';
import type { State, System } from '../../core';
import { loadGltfMaster } from '../../extras/gltf-bridge';
import { getScene } from '../rendering';
import { DistanceCull } from '../rendering/components';
import { Transform, WorldTransform } from '../transforms/components';
import { registerGltfLocalYBounds } from './gltf-bounds-cache';

/**
 * Auto-instancing for identical static GLBs (`<GLTFLoader instanced="true">`).
 *
 * All entities sharing a URL render through ONE `InstancedMesh` per GLB
 * primitive — one draw call per (geometry, material) for the whole set,
 * instead of a scene-graph clone per entity. Instances are dynamic: entities
 * can be destroyed at any time (destructible props) and their slot is
 * swap-removed; `DistanceCull` collapses the instance to zero scale.
 *
 * Not for LOD roots (`lod-urls` — use instanced SpawnGroups/vegetation),
 * physics-fitted `GLTFDynamic`, or animated models.
 */

interface PoolPrimitive {
  mesh: THREE.InstancedMesh;
  /** Node transform of the primitive inside the GLB. */
  local: THREE.Matrix4;
}

interface InstanceSlotState {
  entity: number;
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
  /** null until the master GLB finishes loading. */
  primitives: PoolPrimitive[] | null;
  slots: InstanceSlotState[];
  slotByEntity: Map<number, number>;
  pendingAdds: number[];
  capacity: number;
  loadKicked: boolean;
  boundsDirty: boolean;
}

const poolsByState = new WeakMap<State, Map<string, GltfInstancePool>>();
const instancedFlagByState = new WeakMap<State, Set<number>>();

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

function writeSlotMatrices(
  state: State,
  pool: GltfInstancePool,
  slotIndex: number
): void {
  const slot = pool.slots[slotIndex];
  const eid = slot.entity;

  if (slot.culled) {
    for (const prim of pool.primitives!) {
      prim.mesh.setMatrixAt(slotIndex, ZERO_SCALE_MATRIX);
      prim.mesh.instanceMatrix.needsUpdate = true;
    }
    return;
  }

  const entityMatrix = composeEntityMatrix(state, eid);
  for (const prim of pool.primitives!) {
    _instanceMatrix.multiplyMatrices(entityMatrix, prim.local);
    prim.mesh.setMatrixAt(slotIndex, _instanceMatrix);
    prim.mesh.instanceMatrix.needsUpdate = true;
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

function growPool(pool: GltfInstancePool, scene: THREE.Scene): void {
  const newCapacity = Math.max(INITIAL_CAPACITY, pool.capacity * 2);
  pool.primitives = pool.primitives!.map((prim) => {
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
  pool.capacity = newCapacity;
}

function addSlot(state: State, pool: GltfInstancePool, eid: number): void {
  const scene = getScene(state);
  if (!scene || !pool.primitives) return;
  if (pool.slotByEntity.has(eid)) return;

  if (pool.slots.length === pool.capacity) {
    growPool(pool, scene);
  }

  const slotIndex = pool.slots.length;
  const slot: InstanceSlotState = {
    entity: eid,
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
  for (const prim of pool.primitives) {
    prim.mesh.count = pool.slots.length;
  }
  snapshotSlotSource(slot);
  writeSlotMatrices(state, pool, slotIndex);

  state.onDestroy(eid, () => removeSlot(pool, eid));
}

function removeSlot(pool: GltfInstancePool, eid: number): void {
  const slotIndex = pool.slotByEntity.get(eid);
  if (slotIndex === undefined || !pool.primitives) {
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
    for (const prim of pool.primitives) {
      prim.mesh.getMatrixAt(lastIndex, _instanceMatrix);
      prim.mesh.setMatrixAt(slotIndex, _instanceMatrix);
    }
  }
  for (const prim of pool.primitives) {
    prim.mesh.count = pool.slots.length;
    prim.mesh.instanceMatrix.needsUpdate = true;
  }
  pool.boundsDirty = true;
}

function buildPrimitives(
  state: State,
  pool: GltfInstancePool,
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
    instanced.name = `gltf-instances:${pool.url}`;
    instanced.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    instanced.castShadow = mesh.castShadow;
    instanced.receiveShadow = mesh.receiveShadow;
    instanced.count = 0;
    scene.add(instanced);
    primitives.push({ mesh: instanced, local: mesh.matrixWorld.clone() });
  });
  pool.primitives = primitives;
}

/** Route an entity's GLB visual through the shared instance pool for `url`. */
export function addInstancedGltf(
  state: State,
  entity: number,
  url: string
): void {
  const pools = getPools(state);
  let pool = pools.get(url);
  if (!pool) {
    pool = {
      url,
      primitives: null,
      slots: [],
      slotByEntity: new Map(),
      pendingAdds: [],
      capacity: INITIAL_CAPACITY,
      loadKicked: false,
      boundsDirty: false,
    };
    pools.set(url, pool);
  }

  if (pool.primitives) {
    addSlot(state, pool, entity);
    return;
  }

  pool.pendingAdds.push(entity);
  if (!pool.loadKicked) {
    pool.loadKicked = true;
    void loadGltfMaster(state, url)
      .then((gltf) => {
        registerGltfLocalYBounds(url, gltf.scene);
        pool.capacity = Math.max(INITIAL_CAPACITY, pool.pendingAdds.length * 2);
        buildPrimitives(state, pool, gltf.scene);
        const adds = pool.pendingAdds;
        pool.pendingAdds = [];
        for (const eid of adds) {
          if (state.exists(eid)) addSlot(state, pool, eid);
        }
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`[gltf-instance] failed to load "${url}": ${msg}`);
      });
  }
}

/**
 * Per-frame slot maintenance: rewrite matrices for entities whose Transform
 * changed or whose DistanceCull state flipped, and refresh bounding spheres
 * (instances participate in frustum culling as one unit).
 */
export const GltfAutoInstanceSystem: System = {
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const pools = poolsByState.get(state);
    if (!pools) return;

    for (const [, pool] of pools) {
      if (!pool.primitives) continue;

      for (let i = 0; i < pool.slots.length; i++) {
        const slot = pool.slots[i];
        const eid = slot.entity;

        const culled =
          state.hasComponent(eid, DistanceCull) &&
          DistanceCull.culled[eid] === 1;
        const moved = slotSourceChanged(slot);
        if (culled === slot.culled && !moved) continue;

        slot.culled = culled;
        snapshotSlotSource(slot);
        writeSlotMatrices(state, pool, i);
      }

      if (pool.boundsDirty) {
        pool.boundsDirty = false;
        for (const prim of pool.primitives) {
          prim.mesh.computeBoundingSphere();
        }
      }
    }
  },

  dispose(state: State) {
    const pools = poolsByState.get(state);
    if (!pools) return;
    const scene = getScene(state);
    for (const [, pool] of pools) {
      for (const prim of pool.primitives ?? []) {
        if (scene) scene.remove(prim.mesh);
        prim.mesh.dispose();
      }
    }
    pools.clear();
    poolsByState.delete(state);
    instancedFlagByState.delete(state);
  },
};
