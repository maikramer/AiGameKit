import { defineSystem, defineQuery, type State, type System } from '../../core';
import { logger } from '../../core/utils/logger';
import { getBvhSurfaceHeight } from '../bvh';
import {
  getGroundHeight,
  getTerrainContext,
  isTerrainColliderAt,
} from '../terrain';
import { isTerrainDynamicsBlocking } from '../terrain/utils';
import { Transform } from '../transforms/components';
import {
  Rigidbody,
  Collider,
  CharacterController,
  CharacterMovement,
  InterpolatedTransform,
} from '../physics/components';
import { CharacterMovementSystem, getBodyForEntity } from '../physics/systems';
import { teleportEntity, GROUND_SNAP_MAX } from '../physics/utils';
import {
  getBodyYForFeetAt,
  getCharacterFeetY,
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
  return getGroundHeight(state, x, z);
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

export const SpawnGateSystem: System = defineSystem({
  name: 'SpawnGateSystem',
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
});

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

const characterQuery = defineQuery([CharacterController, Rigidbody]);

/**
 * Heightmap terrain has no overhangs, so a character whose feet sit further
 * below the terrain surface than the per-step ground snap can recover is
 * inside solid ground — a state down-only collider casts never escape. Late
 * ground mutations (bridge re-grades, heightmap reloads) can create it after
 * the one-shot spawn gate latched; this re-seats such characters on the
 * surface in a single tick instead of letting them fall forever.
 *
 * Terrain collision is a ring of chunk heightfields around the camera
 * (`PHYSICS_COLLIDER_RADIUS`), not the whole field. Every character outside
 * that ring — in an RPG, most of the roster — has literally nothing under it:
 * the CCT reports airborne, gravity integrates, and the character sinks
 * through the visual surface. Re-seating those is not an anomaly recovery, it
 * is the steady state, and doing it only past `GROUND_SNAP_MAX` produced a
 * 0.35 m sawtooth (fall → snap → fall) plus one warning per creature per
 * ~0.3 s. Off-collider characters are instead *carried* by the height
 * sampler: pinned flush on the surface with a centimetre of slop, silently.
 * A character that is buried while a collider does cover it is the real
 * anomaly the system was written for, and still warns.
 */

/** Vertical slop tolerated before an off-collider character is re-pinned. */
const UNSUPPORTED_PIN_EPSILON = 0.02;
/** Minimum gap between two warnings about the same entity (seconds). */
const RESEAT_WARN_COOLDOWN = 10;
/** Cap on the cooldown book-keeping so recycled eids cannot grow it forever. */
const RESEAT_WARN_MAX_TRACKED = 512;

const reseatWarnedAt = new Map<number, number>();

function warnReseat(eid: number, depth: number, elapsed: number): void {
  const last = reseatWarnedAt.get(eid);
  if (last !== undefined && elapsed - last < RESEAT_WARN_COOLDOWN) return;
  if (reseatWarnedAt.size >= RESEAT_WARN_MAX_TRACKED) reseatWarnedAt.clear();
  reseatWarnedAt.set(eid, elapsed);
  logger.warn(
    `[spawn-gate] re-seated entity ${eid} buried ${depth.toFixed(1)}m under the terrain surface`
  );
}

export const CharacterUnburySystem: System = defineSystem({
  name: 'CharacterUnburySystem',
  group: 'fixed',
  // After gravity integration, so a carried character is put back on the
  // surface in the same step it was pulled off it (no visible dip).
  after: [CharacterMovementSystem],
  update(state: State): void {
    if (getTerrainContext(state).size === 0) return;
    // Pre-decode the flat sampler does not describe the real surface.
    if (!isTerrainDataReady(state)) return;

    for (const eid of characterQuery(state.world)) {
      if (
        state.hasComponent(eid, SpawnGateComponent) &&
        SpawnGateComponent.ready[eid] === 0
      ) {
        continue; // still held by the spawn gate — it owns seating
      }

      const x = Rigidbody.posX[eid];
      const z = Rigidbody.posZ[eid];
      const surfaceY = getGroundHeight(state, x, z);
      const hasCollider = state.hasComponent(eid, Collider);
      const feetY = hasCollider
        ? getCharacterFeetY(state, eid, Rigidbody.posY[eid])
        : Rigidbody.posY[eid];
      const depth = surfaceY - feetY;

      // On or above the surface: the CCT (or free fall) owns the character.
      if (depth <= UNSUPPORTED_PIN_EPSILON) continue;

      // Below it: who is responsible depends on whether physics ground exists
      // here at all. Only ask once the character actually looks low, so the
      // per-chunk lookup stays off the common path.
      const supported = isTerrainColliderAt(state, x, z);
      // Supported and within snap range — `applyCharacterMovement` re-seats it.
      if (supported && depth <= GROUND_SNAP_MAX) continue;

      const skin = state.hasComponent(eid, SpawnGateComponent)
        ? SpawnGateComponent.skinDistance[eid]
        : GROUND_CONTACT_SKIN;
      const feetTarget = surfaceY + skin;
      Rigidbody.posY[eid] = hasCollider
        ? getBodyYForFeetAt(state, eid, feetTarget)
        : feetTarget;
      CharacterMovement.velocityY[eid] = 0;
      CharacterController.grounded[eid] = 1;

      const body = getBodyForEntity(state, eid);
      if (body) {
        teleportEntity(
          eid,
          body,
          state.hasComponent(eid, InterpolatedTransform)
        );
      }

      if (supported) warnReseat(eid, depth, state.time.elapsed);
    }
  },
});
