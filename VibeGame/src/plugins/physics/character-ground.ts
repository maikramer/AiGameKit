import type { State } from '../../core';
import { getTerrainHeightAt } from '../terrain/systems';
import { getTerrainContext } from '../terrain/utils';
import { Collider } from './components';

/** Max distance below feet to probe for ground contact (Rapier shape/ray cast). */
export const GROUND_PROBE_DISTANCE = 0.22;
/** Skin width for initial spawn on terrain (heightmap sample). */
export const GROUND_CONTACT_SKIN = 0.012;
/** Allowed gap between feet and sampled terrain to still be considered grounded. */
export const TERRAIN_GROUND_TOLERANCE = 0.12;

export function isFeetTouchingTerrain(
  state: State,
  x: number,
  feetY: number,
  z: number
): boolean {
  if (!hasInitializedTerrain(state)) return false;
  const terrainY = getTerrainHeightAt(state, x, z);
  const gap = feetY - terrainY;
  return gap >= -TERRAIN_GROUND_TOLERANCE && gap <= TERRAIN_GROUND_TOLERANCE;
}

export function hasInitializedTerrain(state: State): boolean {
  for (const [, data] of getTerrainContext(state)) {
    if (data.initialized) return true;
  }
  return false;
}

export function getCharacterFeetY(
  state: State,
  entity: number,
  bodyY: number
): number {
  if (!state.hasComponent(entity, Collider)) return bodyY;
  const offsetY = Collider.posOffsetY[entity] || 0;
  const halfHeight = (Collider.height[entity] || 1) / 2;
  const radius = Collider.radius[entity] || 0.5;
  return bodyY + offsetY - halfHeight - radius;
}

export function getBodyYForFeetAt(
  state: State,
  entity: number,
  feetY: number
): number {
  if (!state.hasComponent(entity, Collider)) return feetY;
  const offsetY = Collider.posOffsetY[entity] || 0;
  const halfHeight = (Collider.height[entity] || 1) / 2;
  const radius = Collider.radius[entity] || 0.5;
  return feetY - offsetY + halfHeight + radius;
}

export function moveToward(
  current: number,
  target: number,
  maxDelta: number
): number {
  const diff = target - current;
  if (Math.abs(diff) <= maxDelta) return target;
  return current + Math.sign(diff) * maxDelta;
}

export const CHARACTER_MOVE_ACCEL = {
  ground: 48,
  air: 14,
  groundDecel: 58,
  airDecel: 10,
} as const;
