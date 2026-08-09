import { Transform, Rigidbody, getBodyForEntity } from 'vibegame';
import type { State } from 'vibegame';

/**
 * Teleport an entity: write the Transform AND Rigidbody SOA pose plus zero
 * velocity, then push the Rapier body. `poseDirty` is deliberately NOT set —
 * the engine TeleportationSystem re-applies the SOA pose and rewrites
 * InterpolatedTransform mid-frame.
 *
 * Replaces the hand-rolled copies in the respawn system, the debug `tp`
 * action and the door portals (they were all the same body of code).
 */
export function teleportEntity(
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
  Rigidbody.posX[eid] = x;
  Rigidbody.posY[eid] = y;
  Rigidbody.posZ[eid] = z;
  Rigidbody.velX[eid] = 0;
  Rigidbody.velY[eid] = 0;
  Rigidbody.velZ[eid] = 0;
  const body = getBodyForEntity(state, eid);
  if (body) {
    body.setTranslation({ x, y, z }, true);
    body.setLinvel({ x: 0, y: 0, z: 0 }, true);
    body.wakeUp();
  }
}
