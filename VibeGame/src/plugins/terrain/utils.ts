import type * as RAPIER from '@dimforge/rapier3d-simd';
import type { TerrainLOD } from '@interverse/three-terrain-lod';
import type { State } from '../../core';

export interface TerrainEntityData {
  terrainLOD: TerrainLOD;
  heightmapUrl?: string;
  textureUrl?: string;
  initialized: boolean;
  collisionReady: boolean;
  collisionDispatchStarted: boolean;
  worldOffset: { x: number; y: number; z: number };
  chunkColliders: Map<
    string,
    { body: RAPIER.RigidBody; collider: RAPIER.Collider }
  >;
  lastWireframe: number;
  lastShowChunkBorders: number;
}

const stateToTerrainContext = new WeakMap<
  State,
  Map<number, TerrainEntityData>
>();

const heightmapReloadCallbacks = new WeakMap<State, (() => void)[]>();

export function registerHeightmapReloadCallback(
  state: State,
  cb: () => void
): void {
  let arr = heightmapReloadCallbacks.get(state);
  if (!arr) {
    arr = [];
    heightmapReloadCallbacks.set(state, arr);
  }
  arr.push(cb);
}

export function fireHeightmapReloadCallbacks(state: State): void {
  const arr = heightmapReloadCallbacks.get(state);
  if (arr) {
    for (const cb of arr) cb();
  }
}

export function getTerrainContext(
  state: State
): Map<number, TerrainEntityData> {
  let context = stateToTerrainContext.get(state);
  if (!context) {
    context = new Map();
    stateToTerrainContext.set(state, context);
  }
  return context;
}

export function isTerrainDynamicsBlocking(state: State, eid?: number): boolean {
  const ctx = getTerrainContext(state);
  if (ctx.size === 0) return false;
  if (eid !== undefined) {
    const data = ctx.get(eid);
    return !!data && (!data.initialized || !data.collisionReady);
  }
  for (const [, data] of ctx) {
    if (!data.initialized || !data.collisionReady) return true;
  }
  return false;
}

const heightmapUrls = new WeakMap<State, Map<number, string>>();
const textureUrls = new WeakMap<State, Map<number, string>>();

export function setTerrainHeightmapUrl(
  state: State,
  entity: number,
  url: string
): void {
  let m = heightmapUrls.get(state);
  if (!m) {
    m = new Map();
    heightmapUrls.set(state, m);
  }
  m.set(entity, url.trim());
}

export function getTerrainHeightmapUrl(
  state: State,
  entity: number
): string | undefined {
  return heightmapUrls.get(state)?.get(entity);
}

export function setTerrainTextureUrl(
  state: State,
  entity: number,
  url: string
): void {
  let m = textureUrls.get(state);
  if (!m) {
    m = new Map();
    textureUrls.set(state, m);
  }
  m.set(entity, url.trim());
}

export function getTerrainTextureUrl(
  state: State,
  entity: number
): string | undefined {
  return textureUrls.get(state)?.get(entity);
}

export function terrainHeightsToRapierColumnMajor(
  heights: Float32Array,
  rows: number,
  cols: number
): Float32Array {
  const out = new Float32Array(rows * cols);
  for (let rowZ = 0; rowZ < rows; rowZ++) {
    for (let colX = 0; colX < cols; colX++) {
      out[rowZ + colX * rows] = heights[rowZ * cols + colX];
    }
  }
  return out;
}

