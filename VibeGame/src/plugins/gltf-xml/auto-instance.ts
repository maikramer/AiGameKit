import { logger } from '../../core/utils/logger';
import * as THREE from 'three';
import { InstancedMesh2 } from '@three.ez/instanced-mesh';
import type { State, System } from '../../core';
import { defineSystem, Parent } from '../../core';
import { loadGltfMasterTracked } from '../../extras/gltf-bridge';
import { getSceneGeneration } from '../../extras/scene-generation';
import { getScene, setupCsmMaterial } from '../rendering';
import { maybePatchVegetationWindMaterial } from '../vegetation/wind';
import {
  findSpawnVariation,
  INSTANCE_VARIATION_UNIFORM_SCHEMA,
  maybePatchInstanceVariationMaterial,
} from '../spawn-variation';
import { DistanceCull } from '../rendering/components';
import { getDistanceCullChanges } from '../rendering/cull-changes';
import { BodyType, Rigidbody } from '../physics/components';
import { Transform, WorldTransform } from '../transforms/components';
import { registerGltfLocalYBounds } from './gltf-bounds-cache';

const _instanceColor = new THREE.Color(1, 1, 1);

/**
 * Auto-instancing for identical static GLBs (`<GLTFLoader instanced="true">`).
 *
 * All entities sharing a URL render through ONE `InstancedMesh2` per GLB
 * primitive (@three.ez/instanced-mesh) — one draw call per (geometry,
 * material) for the whole set, instead of a scene-graph clone per entity.
 * This is the project's single instancing path: dense static props (trees,
 * rocks) AND interactive ones (destructible, scripted) all flow through here
 * as real ECS entities, so they keep colliders/scripts/DistanceCull while
 * sharing draw calls.
 *
 * LOD and frustum culling are handled natively by `InstancedMesh2`: when
 * `lod1-url` / `lod2-url` are provided, their geometries are chained onto the
 * lod0 mesh via `addLOD`, and the library selects/culls per instance every
 * frame — no manual zero-scale trick or per-level mesh bookkeeping needed.
 *
 * Instances are dynamic: entities can be destroyed at any time (destructible
 * props) and their slot is freed via `removeInstances`; `DistanceCull` maps to
 * `setVisibilityAt`.
 *
 * A pool with multiple primitives (multi-mesh GLBs) keeps every primitive's
 * `InstancedMesh2` in lockstep: `addInstances`/`removeInstances` are called on
 * all primitives for the same entity in the same order, so the library's
 * internal free-list assigns identical ids across primitives.
 */

const LOD1_DIST = 50;
const LOD2_DIST = 120;

/**
 * Drop `lod1`/`lod2` URLs that alias `url` (or each other).
 *
 * GLB masters share `BufferGeometry`. InstancedMesh2 `addLOD` then reuses the
 * parent LOD object (`geometry ===`), and frustum LOD writes `object.count`
 * per level — last write wins → near-band instances vanish when approaching.
 */
export function normalizeInstancedLodUrls(
  url: string,
  lod1?: string,
  lod2?: string
): [string, string | undefined, string | undefined] {
  const l1 = lod1 && lod1 !== url ? lod1 : undefined;
  const l2 = lod2 && lod2 !== url && lod2 !== l1 ? lod2 : undefined;
  return [url, l1, l2];
}

interface PoolPrimitive {
  mesh: InstancedMesh2;
  /** Node transform of the primitive inside the GLB. */
  local: THREE.Matrix4;
}

interface InstanceSlotState {
  entity: number;
  id: number;
  // last written source values — rewrite only on change
  x: number;
  y: number;
  z: number;
  ex: number;
  ey: number;
  ez: number;
  rx: number;
  ry: number;
  rz: number;
  rw: number;
  sx: number;
  sy: number;
  sz: number;
  useWorld: boolean;
  dynamic: boolean;
  culled: boolean;
}

interface GltfInstancePool {
  url: string;
  /** [lod0, lod1?, lod2?] — lod0 always present. */
  lodUrls: [string, string | undefined, string | undefined];
  /** One InstancedMesh2 per primitive found in the lod0 GLB. */
  primitives: PoolPrimitive[] | null;
  /** How many LOD levels have been chained onto `primitives` so far. */
  lodLevelsBuilt: number;
  slots: InstanceSlotState[];
  slotByEntity: Map<number, number>;
  pendingAdds: number[];
  loadKicked: boolean;
  boundsDirty: boolean;
  /**
   * Round-robin cursor into `slots` for the static rescan. Scanning every slot
   * on the same frame is what turned this system into a 4 ms spike every
   * fourth frame once a circuit's worth of props moved into the pool.
   */
  scanCursor: number;
  /** Slots that must be re-checked every frame (parented / non-fixed body). */
  dynamicSlots: InstanceSlotState[];
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

/**
 * Per-entity LOD threshold overrides for the instanced path (mirrors the
 * lod-threshold-near/mid adapters on the non-instanced GltfLod component).
 * The first entity to spawn a given URL seeds the shared pool's near/mid; a
 * pool already built keeps its initial thresholds (the library bakes them into
 * per-primitive LOD ranges at attach time).
 */
const instancedLodThresholdsByState = new WeakMap<
  State,
  Map<number, { near?: number; mid?: number }>
>();

export function setInstancedLodThreshold(
  state: State,
  entity: number,
  level: 1 | 2,
  value: number
): void {
  let m = instancedLodThresholdsByState.get(state);
  if (!m) {
    m = new Map();
    instancedLodThresholdsByState.set(state, m);
  }
  let entry = m.get(entity);
  if (!entry) {
    entry = {};
    m.set(entity, entry);
  }
  if (level === 1) entry.near = value;
  else entry.mid = value;
}

function consumeInstancedLodThresholds(
  state: State,
  entity: number
): { near: number; mid: number } {
  const entry = instancedLodThresholdsByState.get(state)?.get(entity);
  return {
    near: entry?.near ?? LOD1_DIST,
    mid: entry?.mid ?? LOD2_DIST,
  };
}

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

/** URL of the GLB pool holding this entity's instance slot, if any. */
export function getInstancedPoolUrl(
  state: State,
  entity: number
): string | undefined {
  const pools = poolsByState.get(state);
  if (!pools) return undefined;
  for (const pool of pools.values()) {
    if (pool.slotByEntity.has(entity)) return pool.url;
  }
  return undefined;
}

/** Aggregated GLB auto-instance pool counters for the profiler panel. */
export function getInstancePoolStats(state: State): {
  poolCount: number;
  slotCount: number;
  pendingCount: number;
} {
  const pools = poolsByState.get(state);
  if (!pools || pools.size === 0) {
    return { poolCount: 0, slotCount: 0, pendingCount: 0 };
  }
  let slotCount = 0;
  let pendingCount = 0;
  for (const pool of pools.values()) {
    slotCount += pool.slotByEntity.size;
    pendingCount += pool.pendingAdds.length;
  }
  return { poolCount: pools.size, slotCount, pendingCount };
}

/**
 * Detach the entity's instance slot from its pool (the pooled visual vanishes
 * immediately). Used to "de-instance" a prop that needs a private scene-graph
 * visual (per-entity shader FX, felled-tree animation). Returns the pool URL
 * so the caller can clone the master GLB as the replacement visual.
 */
export function detachInstanceSlot(
  state: State,
  entity: number
): string | undefined {
  const pools = poolsByState.get(state);
  if (!pools) return undefined;
  for (const pool of pools.values()) {
    if (pool.slotByEntity.has(entity)) {
      removeSlot(pool, entity);
      instancedFlagByState.get(state)?.delete(entity);
      return pool.url;
    }
  }
  return undefined;
}

/**
 * World matrix an instance slot renders with (WorldTransform when computed,
 * local Transform otherwise — same rule as the pool writer). Returns a fresh
 * matrix safe to keep.
 */
export function getInstancedEntityMatrix(
  state: State,
  entity: number
): THREE.Matrix4 {
  return composeEntityMatrix(state, entity).clone();
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

function writeSlotMatrix(
  state: State,
  pool: GltfInstancePool,
  slot: InstanceSlotState
): void {
  if (!pool.primitives) return;
  if (slot.culled) return;
  const entityMatrix = composeEntityMatrix(state, slot.entity);
  for (const prim of pool.primitives) {
    _instanceMatrix.multiplyMatrices(entityMatrix, prim.local);
    prim.mesh.setMatrixAt(slot.id, _instanceMatrix);
  }
  writeSlotVariation(state, pool, slot);
  pool.boundsDirty = true;
}

let warnedMissingVarUniforms = false;

/** Apply per-instance colour + brightness/contrast from SpawnVariation. */
function writeSlotVariation(
  state: State,
  pool: GltfInstancePool,
  slot: InstanceSlotState
): void {
  if (!pool.primitives) return;
  const v = findSpawnVariation(state, slot.entity);
  const r = v?.colorR ?? 1;
  const g = v?.colorG ?? 1;
  const b = v?.colorB ?? 1;
  const brightness = v?.brightness ?? 1;
  const contrast = v?.contrast ?? 1;
  _instanceColor.setRGB(r, g, b);
  for (const prim of pool.primitives) {
    prim.mesh.setColorAt(slot.id, _instanceColor);
    try {
      prim.mesh.setUniformAt(slot.id, 'uVarBrightness', brightness);
      prim.mesh.setUniformAt(slot.id, 'uVarContrast', contrast);
    } catch (err) {
      if (!warnedMissingVarUniforms) {
        warnedMissingVarUniforms = true;
        const msg = err instanceof Error ? err.message : String(err);
        logger.warn(
          `[gltf-instance] spawn variation uniforms unavailable on "${pool.url}": ${msg}`
        );
      }
    }
  }
}

function slotUsesWorld(state: State, eid: number): boolean {
  return (
    state.hasComponent(eid, WorldTransform) && WorldTransform.scaleX[eid] !== 0
  );
}

function slotIsDynamic(state: State, eid: number): boolean {
  return (
    state.hasComponent(eid, Parent) ||
    (state.hasComponent(eid, Rigidbody) &&
      Rigidbody.type[eid] !== BodyType.Fixed)
  );
}

function snapshotSlotSource(state: State, slot: InstanceSlotState): void {
  const eid = slot.entity;
  const useWorld = slotUsesWorld(state, eid);
  const source = useWorld ? WorldTransform : Transform;
  slot.x = source.posX[eid];
  slot.y = source.posY[eid];
  slot.z = source.posZ[eid];
  slot.ex = source.eulerX[eid];
  slot.ey = source.eulerY[eid];
  slot.ez = source.eulerZ[eid];
  slot.rx = source.rotX[eid];
  slot.ry = source.rotY[eid];
  slot.rz = source.rotZ[eid];
  slot.rw = source.rotW[eid];
  slot.sx = source.scaleX[eid];
  slot.sy = source.scaleY[eid];
  slot.sz = source.scaleZ[eid];
  slot.useWorld = useWorld;
  slot.dynamic = slotIsDynamic(state, eid);
}

function slotSourceChanged(state: State, slot: InstanceSlotState): boolean {
  const eid = slot.entity;
  const useWorld = slotUsesWorld(state, eid);
  const source = useWorld ? WorldTransform : Transform;
  return (
    slot.useWorld !== useWorld ||
    slot.x !== source.posX[eid] ||
    slot.y !== source.posY[eid] ||
    slot.z !== source.posZ[eid] ||
    slot.ex !== source.eulerX[eid] ||
    slot.ey !== source.eulerY[eid] ||
    slot.ez !== source.eulerZ[eid] ||
    slot.rx !== source.rotX[eid] ||
    slot.ry !== source.rotY[eid] ||
    slot.rz !== source.rotZ[eid] ||
    slot.rw !== source.rotW[eid] ||
    slot.sx !== source.scaleX[eid] ||
    slot.sy !== source.scaleY[eid] ||
    slot.sz !== source.scaleZ[eid]
  );
}

function addSlot(state: State, pool: GltfInstancePool, eid: number): void {
  const scene = getScene(state);
  if (!scene || !pool.primitives || pool.primitives.length === 0) return;
  if (pool.slotByEntity.has(eid)) return;

  let id = -1;
  for (const prim of pool.primitives) {
    prim.mesh.addInstances(1, (instance, index) => {
      if (id === -1) id = index;
      else if (index !== id) {
        // Primitives fell out of lockstep (should not happen if every add/remove
        // is mirrored across all primitives of the pool) — log so it's visible.
        logger.warn(
          `[gltf-instance] primitive id mismatch for "${pool.url}": ${index} vs ${id}`
        );
      }
      instance.visible = true;
    });
  }
  if (id === -1) return;

  const slot: InstanceSlotState = {
    entity: eid,
    id,
    x: NaN,
    y: NaN,
    z: NaN,
    ex: 0,
    ey: 0,
    ez: 0,
    rx: 0,
    ry: 0,
    rz: 0,
    rw: 1,
    sx: 0,
    sy: 0,
    sz: 0,
    useWorld: false,
    dynamic: slotIsDynamic(state, eid),
    culled: false,
  };
  pool.slots.push(slot);
  pool.slotByEntity.set(eid, pool.slots.length - 1);
  snapshotSlotSource(state, slot);
  writeSlotMatrix(state, pool, slot);

  state.onDestroy(eid, () => removeSlot(pool, eid));
}

function removeSlot(pool: GltfInstancePool, eid: number): void {
  const slotIndex = pool.slotByEntity.get(eid);
  if (slotIndex === undefined || !pool.primitives) {
    pool.slotByEntity.delete(eid);
    return;
  }
  pool.slotByEntity.delete(eid);

  const slot = pool.slots[slotIndex];
  const lastIndex = pool.slots.length - 1;
  pool.slots[slotIndex] = pool.slots[lastIndex];
  pool.slots.pop();
  if (slotIndex !== lastIndex) {
    pool.slotByEntity.set(pool.slots[slotIndex].entity, slotIndex);
  }

  for (const prim of pool.primitives) prim.mesh.removeInstances(slot.id);
  pool.boundsDirty = true;
}

function buildLevelPrimitives(
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
    // Must patch *before* handing the material to InstancedMesh2: it saves
    // whatever `onBeforeCompile` is currently on the material and wraps it
    // every frame (onBeforeRender/onAfterRender) to inject its own
    // instancing uniforms — patching CSM in afterward would race that
    // per-frame save/restore and get silently discarded.
    for (const mat of Array.isArray(mesh.material)
      ? mesh.material
      : [mesh.material]) {
      setupCsmMaterial(state, mat);
      maybePatchVegetationWindMaterial(state, pool.lodUrls[0], mat);
      maybePatchInstanceVariationMaterial(mat);
    }
    const instanced = new InstancedMesh2(mesh.geometry, mesh.material, {
      capacity: INITIAL_CAPACITY,
    });
    instanced.name = `gltf-instances:${pool.lodUrls[0]}`;
    instanced.castShadow = mesh.castShadow;
    instanced.receiveShadow = mesh.receiveShadow;
    instanced.setFirstLODDistance(pool.near);
    instanced.initUniformsPerInstance(INSTANCE_VARIATION_UNIFORM_SCHEMA);
    // Force colorsTexture so the first compile includes USE_INSTANCING_COLOR_INDIRECT.
    instanced.setColorAt(0, _instanceColor.setRGB(1, 1, 1));
    scene.add(instanced);
    primitives.push({ mesh: instanced, local: mesh.matrixWorld.clone() });
  });
  pool.primitives = primitives;
  pool.lodLevelsBuilt = 1;
}

function attachLodLevel(
  state: State,
  pool: GltfInstancePool,
  level: 1 | 2,
  group: THREE.Group
): void {
  if (!pool.primitives) return;
  group.updateMatrixWorld(true);
  const meshes: THREE.Mesh[] = [];
  group.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh === true) meshes.push(mesh);
  });

  if (meshes.length !== pool.primitives.length) {
    logger.warn(
      `[gltf-instance] lod${level} "${pool.lodUrls[level]}" has ${meshes.length} primitives, ` +
        `lod0 has ${pool.primitives.length} — extra/missing primitives are skipped`
    );
  }

  const distance = level === 1 ? pool.near : pool.mid;
  const count = Math.min(meshes.length, pool.primitives.length);
  for (let i = 0; i < count; i++) {
    // Same ordering requirement as buildLevelPrimitives: patch before this
    // material is ever handed to InstancedMesh2's per-frame onBeforeCompile
    // wrapping (addLOD stores it exactly the same way as the constructor).
    for (const mat of Array.isArray(meshes[i].material)
      ? (meshes[i].material as THREE.Material[])
      : [meshes[i].material as THREE.Material]) {
      setupCsmMaterial(state, mat);
      maybePatchVegetationWindMaterial(
        state,
        pool.lodUrls[level] ?? pool.lodUrls[0],
        mat
      );
      maybePatchInstanceVariationMaterial(mat);
    }
    pool.primitives[i].mesh.addLOD(
      meshes[i].geometry,
      meshes[i].material,
      distance
    );
  }
  pool.lodLevelsBuilt = Math.max(pool.lodLevelsBuilt, level + 1);
}

function kickLoad(state: State, pool: GltfInstancePool): void {
  pool.loadKicked = true;
  // Captured at kick time: if the scene generation changed by resolve time
  // (scene swap / runtime teardown), the .then handlers bail before adding any
  // InstancedMesh2 to a retired scene — those meshes would never be torn down.
  const gen = getSceneGeneration(state);
  // lod0 is boot-critical (visible near the player); lod1/2 stream after.
  void loadGltfMasterTracked(state, pool.lodUrls[0], 'critical')
    .then((gltf) => {
      if (getSceneGeneration(state) !== gen) return;
      registerGltfLocalYBounds(pool.lodUrls[0], gltf.scene);
      buildLevelPrimitives(state, pool, gltf.scene);
      const adds = pool.pendingAdds;
      pool.pendingAdds = [];
      for (const eid of adds) {
        if (state.exists(eid)) addSlot(state, pool, eid);
      }

      for (const level of [1, 2] as const) {
        const lodUrl = pool.lodUrls[level];
        if (!lodUrl) continue;
        void loadGltfMasterTracked(state, lodUrl, 'background')
          .then((gltfLod) => {
            if (getSceneGeneration(state) !== gen) return;
            attachLodLevel(state, pool, level, gltfLod.scene);
          })
          .catch((err: unknown) => {
            const msg = err instanceof Error ? err.message : String(err);
            logger.warn(
              `[gltf-instance] lod${level} "${lodUrl}" failed: ${msg}`
            );
          });
      }
    })
    .catch((err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      logger.error(
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
    // Seed the pool's LOD thresholds from this entity's overrides (if any);
    // fall back to the engine defaults. Only the first entity to spawn a URL
    // contributes — the pool is shared and LOD thresholds are baked into the
    // primitives at attach time.
    const thresholds = consumeInstancedLodThresholds(state, entity);
    const [lod0, distinctLod1, distinctLod2] = normalizeInstancedLodUrls(
      url,
      lod1,
      lod2
    );
    pool = {
      url: lod0,
      lodUrls: [lod0, distinctLod1, distinctLod2],
      primitives: null,
      lodLevelsBuilt: 0,
      slots: [],
      slotByEntity: new Map(),
      pendingAdds: [],
      loadKicked: false,
      boundsDirty: false,
      scanCursor: 0,
      dynamicSlots: [],
      near: thresholds.near,
      mid: thresholds.mid,
    };
    pools.set(url, pool);
  }

  if (pool.primitives) {
    addSlot(state, pool, entity);
    return;
  }

  pool.pendingAdds.push(entity);
  if (!pool.loadKicked) kickLoad(state, pool);
}

/**
 * Frames between full sweeps of the static slots (spread across the window by
 * the round-robin shard). Every static instance is still re-checked ~8 frames
 * after anything changes; at 320 m cull distances that latency is invisible,
 * and it halves what a circuit's worth of roadside props costs per frame.
 * Slots flagged `dynamic` (parented, or a non-fixed body) are exempt — they
 * are re-checked every frame.
 */
const STATIC_SLOT_SCAN_INTERVAL = 8;

/**
 * Upper bound on static slots re-examined per pool per frame.
 *
 * The sweep only exists to notice an instance that moved without being flagged
 * dynamic (a script nudging a static prop). Cull flips arrive as events now, so
 * the sweep can be slow: a world with 30k instanced plants would otherwise pay
 * thousands of checks a frame for something that almost never happens.
 */
const MAX_STATIC_SCAN_PER_POOL = 32;

/**
 * Per-frame slot maintenance: rewrite matrices for entities whose Transform
 * changed or whose `DistanceCull` state flipped, and refresh bounding spheres.
 * LOD selection and frustum culling per instance are handled internally by
 * `InstancedMesh2` every render — no camera-distance bookkeeping needed here.
 */
export const GltfAutoInstanceSystem: System = defineSystem({
  name: 'GltfAutoInstanceSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const pools = poolsByState.get(state);
    if (!pools) return;

    const cullChanges = getDistanceCullChanges(state);

    for (const [, pool] of pools) {
      if (!pool.primitives) continue;

      const slots = pool.slots;
      const primitives = pool.primitives;

      const processSlot = (slot: InstanceSlotState, rescan: boolean): void => {
        const eid = slot.entity;
        if (rescan) {
          const dynamic = slotIsDynamic(state, eid);
          if (dynamic !== slot.dynamic) {
            slot.dynamic = dynamic;
            const at = pool.dynamicSlots.indexOf(slot);
            if (dynamic && at < 0) pool.dynamicSlots.push(slot);
            else if (!dynamic && at >= 0) pool.dynamicSlots.splice(at, 1);
          }
        }
        const culled =
          state.hasComponent(eid, DistanceCull) &&
          DistanceCull.culled[eid] === 1;

        const moved = slotSourceChanged(state, slot);
        if (culled === slot.culled && !moved) return;

        if (culled !== slot.culled) {
          for (const prim of primitives) {
            prim.mesh.setVisibilityAt(slot.id, !culled);
          }
        }
        slot.culled = culled;
        if (!culled) {
          snapshotSlotSource(state, slot);
          writeSlotMatrix(state, pool, slot);
        }
      };

      // Static slots are re-examined on a rolling shard: every slot is still
      // checked every STATIC_SLOT_SCAN_INTERVAL frames, but only a slice is
      // touched per frame — a circuit's worth of roadside props used to make
      // this a full O(instances) sweep on every fourth frame.
      // Cull flips are edge-triggered: handle exactly the slots that changed.
      for (const eid of cullChanges) {
        const at = pool.slotByEntity.get(eid);
        if (at !== undefined) processSlot(slots[at]!, false);
      }

      if (slots.length > 0) {
        const shard = Math.min(
          Math.ceil(slots.length / STATIC_SLOT_SCAN_INTERVAL),
          MAX_STATIC_SCAN_PER_POOL
        );
        const start = pool.scanCursor % slots.length;
        for (let k = 0; k < shard; k++) {
          processSlot(slots[(start + k) % slots.length]!, true);
        }
        pool.scanCursor = (start + shard) % slots.length;
      }
      // Anything that actually moves is checked every frame.
      for (const slot of pool.dynamicSlots) processSlot(slot, false);

      if (pool.boundsDirty) {
        pool.boundsDirty = false;
        for (const prim of pool.primitives) prim.mesh.computeBoundingSphere();
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
    instancedLodUrlsByState.delete(state);
  },
});
