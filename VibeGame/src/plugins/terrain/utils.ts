import type * as THREE from 'three';
import type { State } from '../../core';
import type { HeightSampler } from './height-sampler';

/**
 * Per-field terrain state. The ECS components are the source of truth for
 * configuration; this holds derived runtime data (decoded heights, the set of
 * spawned chunk entities, load/collision readiness) keyed by the field entity.
 */
export interface TerrainEntityData {
  sampler: HeightSampler;
  chunks: Set<number>;
  heightmapUrl?: string;
  textureUrl?: string;
  initialized: boolean;
  collisionReady: boolean;
  worldOffset: { x: number; y: number; z: number };
  lastWireframe: number;
  lastShowChunkBorders: number;
  physicsBody: import("@dimforge/rapier3d-compat").RigidBody | null;
  physicsCollider: import("@dimforge/rapier3d-compat").Collider | null;
}

const stateToTerrainContext = new WeakMap<
  State,
  Map<number, TerrainEntityData>
>();

const stateToChunkMeshes = new WeakMap<State, Map<number, THREE.Mesh>>();

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

/** Per-state registry of chunk meshes (derived cache, not source of truth). */
export function getChunkMeshRegistry(state: State): Map<number, THREE.Mesh> {
  let registry = stateToChunkMeshes.get(state);
  if (!registry) {
    registry = new Map();
    stateToChunkMeshes.set(state, registry);
  }
  return registry;
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


