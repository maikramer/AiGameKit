import { defineQuery, type State, type System } from '../../core';
import { getBvhSurfaceHeight } from '../bvh';
import { getTerrainContext, getTerrainHeightAt } from '../terrain';
import { isTerrainDynamicsBlocking } from '../terrain/utils';
import { Transform } from '../transforms/components';
import { Rigidbody, Collider } from '../physics/components';
import { getBodyForEntity } from '../physics/systems';
import {
  getBodyYForFeetAt,
  GROUND_CONTACT_SKIN,
} from '../physics/character-ground';
import { SpawnGateComponent } from './components';

const gateQuery = defineQuery([SpawnGateComponent]);

/** Ray origin above the spawn Y when probing for the surface below. */
const SURFACE_PROBE_MARGIN = 8;
const SURFACE_PROBE_MAX_DROP = 2000;

/**
 * A terrain field is data-ready once it has initialised and any requested
 * heightmap has decoded into the sampler. Before this, the visual surface the
 * player sees does not exist and a snap target cannot be sampled.
 */
function isTerrainDataReady(state: State): boolean {
  const ctx = getTerrainContext(state);
  if (ctx.size === 0) return true;
  for (const [, data] of ctx) {
    if (!data.initialized) return false;
    if (data.heightmapUrl && data.sampler.data === null) return false;
  }
  return true;
}

/**
 * Every terrain field has a Rapier heightfield collider built. This is the
 * second gate: the BVH/heightmap surfaces load before the collision surface,
 * and releasing onto a not-yet-built one-sided heightfield lets gravity
 * tunnel the body through the floor.
 */
function isTerrainCollisionReady(state: State): boolean {
  const ctx = getTerrainContext(state);
  if (ctx.size === 0) return true;
  for (const [, data] of ctx) {
    if (!data.collisionReady) return false;
  }
  return true;
}

function isTerrainGateReady(state: State): boolean {
  // isTerrainDynamicsBlocking already aggregates both conditions; the two
  // helpers above are kept so each gate can be inspected/mocked independently.
  if (isTerrainDynamicsBlocking(state)) return false;
  return isTerrainDataReady(state) && isTerrainCollisionReady(state);
}

/** Surface Y under (x, z), preferring the BVH raycast and falling back to the heightmap sampler. */
function surfaceHeightAt(
  state: State,
  x: number,
  yAbove: number,
  z: number
): number {
  const bvh = getBvhSurfaceHeight(state, x, yAbove, z, SURFACE_PROBE_MAX_DROP);
  if (bvh !== null) return bvh;
  return getTerrainHeightAt(state, x, z);
}

/**
 * Mark `eid` for spawn gating. The entity is frozen at `yFallback` (or its
 * current Transform Y when omitted) and released on the first frame the
 * terrain underneath it is both heightmap-decoded and heightfield-backed.
 */
export function gateEntity(
  state: State,
  eid: number,
  opts?: { yFallback?: number; skinDistance?: number }
): void {
  state.addComponent(eid, SpawnGateComponent);
  SpawnGateComponent.ready[eid] = 0;
  const fallback = opts?.yFallback;
  SpawnGateComponent.yOffset[eid] =
    fallback !== undefined && fallback !== null
      ? fallback
      : Transform.posY[eid];
  SpawnGateComponent.skinDistance[eid] =
    opts?.skinDistance ?? GROUND_CONTACT_SKIN;
}

export const SpawnGateSystem: System = {
  group: 'fixed',
  update(state: State): void {
    const terrainReady = isTerrainGateReady(state);

    for (const eid of gateQuery(state.world)) {
      if (SpawnGateComponent.ready[eid] === 1) continue;

      const x = Transform.posX[eid];
      const z = Transform.posZ[eid];
      const holdY = SpawnGateComponent.yOffset[eid];

      if (!terrainReady) {
        freezeAt(state, eid, x, holdY, z);
        continue;
      }

      const groundY = surfaceHeightAt(
        state,
        x,
        holdY + SURFACE_PROBE_MARGIN,
        z
      );
      const feetY = groundY + SpawnGateComponent.skinDistance[eid];
      const snapY = state.hasComponent(eid, Collider)
        ? getBodyYForFeetAt(state, eid, feetY)
        : feetY;

      Transform.posX[eid] = x;
      Transform.posY[eid] = snapY;
      Transform.posZ[eid] = z;
      Transform.dirty[eid] = 1;

      const body = getBodyForEntity(state, eid);
      if (body) {
        body.setTranslation({ x, y: snapY, z }, true);
        body.wakeUp();
      }

      SpawnGateComponent.ready[eid] = 1;
    }
  },
};

/** Pin the entity at its spawn Y and kill any velocity so gravity cannot build up. */
function freezeAt(
  state: State,
  eid: number,
  x: number,
  y: number,
  z: number
): void {
  Transform.posX[eid] = x;
  Transform.posY[eid] = y;
  Transform.posZ[eid] = z;
  Transform.dirty[eid] = 1;

  if (state.hasComponent(eid, Rigidbody)) {
    Rigidbody.velX[eid] = 0;
    Rigidbody.velY[eid] = 0;
    Rigidbody.velZ[eid] = 0;
  }

  const body = getBodyForEntity(state, eid);
  if (body) {
    body.setTranslation({ x, y, z }, true);
    body.setLinvel({ x: 0, y: 0, z: 0 }, true);
  }
}
