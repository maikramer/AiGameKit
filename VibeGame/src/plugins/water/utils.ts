import type * as RAPIER from '@dimforge/rapier3d-compat';
import type * as THREE from 'three';
import type { State } from '../../core';
import { getTerrainContext } from '../terrain/utils';
import type { PlanarReflection } from './planar-reflection';

export interface WaterEntityData {
  mesh: THREE.Mesh;
  material: THREE.ShaderMaterial;
  reflection: PlanarReflection;
  initialized: boolean;
  worldOffset: { x: number; y: number; z: number };
  physicsBody: RAPIER.RigidBody | null;
  physicsCollider: RAPIER.Collider | null;
  isSubmerged: boolean;
  rippleCenter: THREE.Vector3;
  rippleStrength: number;
  rippleDecay: number;
  underwaterPostProcessActive: boolean;
  audioMuffleHint: boolean;
}

const stateToWaterContext = new WeakMap<State, Map<number, WaterEntityData>>();

export function getWaterContext(state: State): Map<number, WaterEntityData> {
  let context = stateToWaterContext.get(state);
  if (!context) {
    context = new Map();
    stateToWaterContext.set(state, context);
  }
  return context;
}

export function findNearestTerrainHeightmap(
  state: State
): THREE.Texture | null {
  const terrainCtx = getTerrainContext(state);
  for (const [, data] of terrainCtx) {
    const hm = data.terrainLOD.getHeightMap();
    if (hm) return hm;
  }
  return null;
}

export function findNearestTerrainConfig(
  state: State
): { worldSize: number; maxHeight: number } | null {
  const terrainCtx = getTerrainContext(state);
  for (const [, data] of terrainCtx) {
    return data.terrainLOD.getConfig();
  }
  return null;
}
