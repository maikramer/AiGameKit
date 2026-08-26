import * as THREE from 'three';
import type { State } from '../../core';
import { FarmGrid } from './components';
import { cellToWorld } from './grid';
import { FarmTileStates, cropDefsFor, type CropDef } from './crops';
import { getFarmGridData, specOf, type FarmGridData } from './store';
import {
  findAvailableInstanceSlot,
  getRenderingContext,
  initializeInstancedMesh,
  releaseInstanceSlot,
  resizeInstancedMesh,
} from '../rendering/utils';

/**
 * Instanced rendering for farm plots — flush-only, O(dirty tiles).
 *
 * Soil: one InstancedMesh sized cols×rows (slot i = tile i, no free list).
 * Crops: one pool per (cropId, stage) pair over a procedural geometry,
 * allocated through the rendering plugin's instance-slot helpers so pool
 * growth and bounds tracking behave like every other instanced system.
 */

interface GridPools {
  soil: THREE.InstancedMesh;
  crops: Map<string, THREE.InstancedMesh>;
  geometries: THREE.BufferGeometry[];
  materials: THREE.Material[];
}

const poolsByState = new WeakMap<State, Map<number, GridPools>>();

function poolsFor(state: State): Map<number, GridPools> {
  let map = poolsByState.get(state);
  if (!map) {
    map = new Map();
    poolsByState.set(state, map);
  }
  return map;
}

const SOIL_DRY = new THREE.Color(0x7a5a34);
const SOIL_WET = new THREE.Color(0x4a3419);

const _matrix = new THREE.Matrix4();
const _identity = new THREE.Quaternion();
const _one = new THREE.Vector3(1, 1, 1);
const _pos = new THREE.Vector3();
const _hidden = new THREE.Matrix4().makeScale(0, 0, 0);

/** Merge simple non-indexed geometries (positions + normals only). */
function mergeGeometries(
  parts: { geom: THREE.BufferGeometry; y: number }[]
): THREE.BufferGeometry {
  let vertexCount = 0;
  const baked = parts.map(({ geom, y }) => {
    const g = geom.index ? geom.toNonIndexed() : geom.clone();
    g.translate(0, y, 0);
    vertexCount += g.getAttribute('position').count;
    return g;
  });
  const position = new Float32Array(vertexCount * 3);
  const normal = new Float32Array(vertexCount * 3);
  let off = 0;
  for (const g of baked) {
    const p = g.getAttribute('position').array as ArrayLike<number>;
    const n = g.getAttribute('normal').array as ArrayLike<number>;
    position.set(p, off);
    normal.set(n, off);
    off += p.length;
    g.dispose();
  }
  const out = new THREE.BufferGeometry();
  out.setAttribute('position', new THREE.BufferAttribute(position, 3));
  out.setAttribute('normal', new THREE.BufferAttribute(normal, 3));
  return out;
}

/**
 * Zero-asset crop mesh: a stem plus a foliage cone, sized by the stage's
 * authored height. The fallback until a pack provides `stageMeshes`.
 */
export function buildProceduralCropGeometry(
  def: CropDef,
  stage: number
): THREE.BufferGeometry {
  const h =
    def.stageHeights[Math.min(stage, def.stageHeights.length - 1)] || 0.2;
  const stemH = h * 0.4;
  const coneH = h * 0.7;
  const stem = new THREE.CylinderGeometry(0.02, 0.03, stemH, 5);
  const cone = new THREE.ConeGeometry(0.14 + 0.08 * h, coneH, 6);
  const geom = mergeGeometries([
    { geom: stem, y: stemH / 2 },
    { geom: cone, y: stemH + coneH / 2 },
  ]);
  stem.dispose();
  cone.dispose();
  return geom;
}

function createPools(state: State, eid: number, data: FarmGridData): GridPools {
  const cellSize = FarmGrid.cellSize[eid] || 1;
  const soilGeom = new THREE.PlaneGeometry(cellSize * 0.96, cellSize * 0.96);
  soilGeom.rotateX(-Math.PI / 2);
  const soilMat = new THREE.MeshStandardMaterial({
    roughness: 0.95,
  });
  const soil = initializeInstancedMesh(soilGeom, soilMat, data.state.length);
  soil.receiveShadow = true;
  soil.castShadow = false;
  getRenderingContext(state).scene.add(soil);
  return {
    soil,
    crops: new Map(),
    geometries: [soilGeom],
    materials: [soilMat],
  };
}

function disposePools(state: State, eid: number, pools: GridPools): void {
  const scene = getRenderingContext(state).scene;
  scene.remove(pools.soil);
  pools.soil.dispose();
  for (const mesh of pools.crops.values()) {
    scene.remove(mesh);
    mesh.dispose();
  }
  for (const g of pools.geometries) g.dispose();
  for (const m of pools.materials) m.dispose();
  pools.crops.clear();
  poolsFor(state).delete(eid);
}

/** Pool for a (cropIdx, stage) pair, created on first use. */
function cropPool(
  state: State,
  pools: GridPools,
  key: string,
  data: FarmGridData,
  cropIdx: number,
  stage: number
): THREE.InstancedMesh | null {
  const existing = pools.crops.get(key);
  if (existing) return existing;
  const def = cropDefsFor(data)[cropIdx];
  if (!def) return null;
  const geom = buildProceduralCropGeometry(def, stage);
  const mat = new THREE.MeshStandardMaterial({
    color: def.color,
    roughness: 0.8,
  });
  const mesh = initializeInstancedMesh(geom, mat, 64);
  pools.crops.set(key, mesh);
  pools.geometries.push(geom);
  pools.materials.push(mat);
  getRenderingContext(state).scene.add(mesh);
  return mesh;
}

/**
 * Push dirty tiles into the instanced pools. Called from FarmRenderSystem;
 * reads tile state, never writes it.
 */
export function flushFarmRender(state: State, eid: number): void {
  const data = getFarmGridData(state, eid);
  if (!data?.ready) return;

  let pools = poolsFor(state).get(eid);
  if (pools && pools.soil.count !== data.state.length) {
    // The grid was resized (e.g. a save from another field size loaded).
    disposePools(state, eid, pools);
    pools = undefined;
  }
  if (!pools) {
    pools = createPools(state, eid, data);
    poolsFor(state).set(eid, pools);
  }

  const spec = specOf(state, eid);
  const y = FarmGrid.baseY[eid] + FarmGrid.surfaceEpsilon[eid];
  let soilTouched = false;
  const cropTouched = new Set<THREE.InstancedMesh>();

  for (const i of data.dirtyList) {
    data.dirty[i] = 0;
    const row = Math.floor(i / data.cols);
    const col = i - row * data.cols;
    const world = cellToWorld(spec, col, row);
    if (!world) continue;

    // --- Soil quad: hidden on grass, tinted dark while wet. ---
    if (data.state[i] === FarmTileStates.Empty) {
      pools.soil.setMatrixAt(i, _hidden);
    } else {
      _pos.set(world.x, y, world.z);
      _matrix.compose(_pos, _identity, _one);
      pools.soil.setMatrixAt(i, _matrix);
      const wet =
        data.wateredToday[i] === 1 &&
        (data.state[i] === FarmTileStates.Tilled ||
          data.state[i] === FarmTileStates.Growing);
      pools.soil.setColorAt(i, wet ? SOIL_WET : SOIL_DRY);
    }
    soilTouched = true;

    // --- Crop: give the old slot back to the pool it came from. ---
    if (data.cropSlot[i] >= 0) {
      const oldKey = `${data.slotCrop[i]}:${data.slotStage[i]}`;
      const oldPool = pools.crops.get(oldKey);
      if (oldPool) {
        releaseInstanceSlot(oldPool, data.cropSlot[i]);
        cropTouched.add(oldPool);
      }
      data.cropSlot[i] = -1;
    }

    const wantsCrop =
      data.state[i] === FarmTileStates.Growing ||
      data.state[i] === FarmTileStates.Ready ||
      data.state[i] === FarmTileStates.Withered;
    const cropIdx = data.cropId[i];
    if (wantsCrop && cropIdx >= 0) {
      // Withered fruit keeps its mature geometry — dead stays visible.
      const stage =
        data.state[i] === FarmTileStates.Withered
          ? Math.max(1, data.stage[i])
          : data.stage[i];
      const key = `${cropIdx}:${stage}`;
      let mesh = cropPool(state, pools, key, data, cropIdx, stage);
      let slot = mesh ? findAvailableInstanceSlot(mesh, _matrix) : null;
      if (mesh && slot === null) {
        const grown = resizeInstancedMesh(
          mesh,
          mesh.geometry,
          mesh.material as THREE.Material,
          getRenderingContext(state).scene
        );
        pools.crops.set(key, grown);
        mesh = grown;
        slot = findAvailableInstanceSlot(grown, _matrix);
      }
      if (mesh && slot !== null) {
        _pos.set(world.x, y, world.z);
        _matrix.compose(_pos, _identity, _one);
        mesh.setMatrixAt(slot, _matrix);
        data.cropSlot[i] = slot;
        data.slotCrop[i] = cropIdx;
        data.slotStage[i] = stage;
        cropTouched.add(mesh);
      }
    }
  }
  data.dirtyList.length = 0;

  if (soilTouched) {
    pools.soil.instanceMatrix.needsUpdate = true;
    if (pools.soil.instanceColor) pools.soil.instanceColor.needsUpdate = true;
  }
  for (const mesh of cropTouched) mesh.instanceMatrix.needsUpdate = true;
}

/** Dispose pools for grids whose entity is gone (world reload). */
export function sweepDeadFarmPools(state: State): void {
  const map = poolsFor(state);
  for (const [eid, pools] of map) {
    if (!state.exists(eid)) disposePools(state, eid, pools);
  }
}
