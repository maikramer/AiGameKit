import { defineQueryLive, type State } from '../../core';
import { Rigidbody, getBodyForEntity } from '../physics';
import { teleportEntity } from '../physics/utils';

const bodyQuery = defineQueryLive([Rigidbody]);

/**
 * Push restored ECS pose and velocities into the Rapier bodies after a chrono
 * seek. ECS is authoritative post-restore; without this the next physics step
 * would drag entities back to their pre-rewind body state.
 */
export function resyncPhysicsAfterSeek(state: State): void {
  if (state.getComponent('rigidbody') === undefined) return;

  for (const eid of bodyQuery(state.world)) {
    const body = getBodyForEntity(state, eid);
    if (!body) continue;
    teleportEntity(eid, body, true);
    body.setLinvel(
      {
        x: Rigidbody.velX[eid],
        y: Rigidbody.velY[eid],
        z: Rigidbody.velZ[eid],
      },
      true
    );
    body.setAngvel(
      {
        x: Rigidbody.rotVelX[eid],
        y: Rigidbody.rotVelY[eid],
        z: Rigidbody.rotVelZ[eid],
      },
      true
    );
  }
}
