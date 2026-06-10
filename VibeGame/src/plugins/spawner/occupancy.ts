import type { State } from '../../core';
import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Spawn occupancy registry: every placement (spawn-group instances, instanced
 * vegetation, `place="…"` entities with colliders, explicit
 * `<SpawnExclusion>` zones) registers an XZ disc. Spawn-group sampling
 * rejects candidates whose disc overlaps a registered one, so rocks don't
 * spawn inside trees, trees don't spawn inside the hut, etc.
 *
 * Order-independent by construction: whoever spawns later avoids whoever
 * registered earlier — both spawn paths register and check.
 */

interface SpawnFootprint {
  x: number;
  z: number;
  radius: number;
}

const occupancyByState = new WeakMap<State, SpawnFootprint[]>();

function getFootprints(state: State): SpawnFootprint[] {
  let list = occupancyByState.get(state);
  if (!list) {
    list = [];
    occupancyByState.set(state, list);
  }
  return list;
}

export function registerSpawnFootprint(
  state: State,
  x: number,
  z: number,
  radius: number
): void {
  if (!(radius > 0)) return;
  getFootprints(state).push({ x, z, radius });
}

/** True when a disc at (x, z) does not overlap any registered footprint. */
export function isSpawnAreaFree(
  state: State,
  x: number,
  z: number,
  radius: number
): boolean {
  const list = occupancyByState.get(state);
  if (!list) return true;
  for (const f of list) {
    const dx = f.x - x;
    const dz = f.z - z;
    const minDist = f.radius + radius;
    if (dx * dx + dz * dz < minDist * minDist) return false;
  }
  return true;
}

export function clearSpawnOccupancy(state: State): void {
  occupancyByState.delete(state);
}

/**
 * Explicit no-spawn zone: `<SpawnExclusion at="16 8" radius="7">`.
 * Registered into the occupancy registry by TerrainSpawnSystem before any
 * group samples positions.
 */
export const SpawnExclusion = {
  x: new Float32Array(MAX_ENTITIES),
  z: new Float32Array(MAX_ENTITIES),
  radius: new Float32Array(MAX_ENTITIES),
  registered: new Uint8Array(MAX_ENTITIES),
} as const;
