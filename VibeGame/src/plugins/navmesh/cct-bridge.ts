import type { State } from '../../core';
import {
  CharacterController,
  CharacterMovement,
  Rigidbody,
} from '../physics/components';
import { Transform } from '../transforms/components';
import { setTransformFacingXZ } from '../transforms/utils';
import { NavMeshAgent } from './components';

/** Push Transform yaw into the kinematic Rigidbody so physics sync cannot clobber it. */
function syncFacingToRigidbody(state: State, eid: number): void {
  if (!state.hasComponent(eid, Rigidbody)) return;
  Rigidbody.eulerX[eid] = Transform.eulerX[eid];
  Rigidbody.eulerY[eid] = Transform.eulerY[eid];
  Rigidbody.eulerZ[eid] = Transform.eulerZ[eid];
  Rigidbody.rotX[eid] = Transform.rotX[eid];
  Rigidbody.rotY[eid] = Transform.rotY[eid];
  Rigidbody.rotZ[eid] = Transform.rotZ[eid];
  Rigidbody.rotW[eid] = Transform.rotW[eid];
  Rigidbody.poseDirty[eid] = 1;
}

function ensureCharacterMovement(state: State, eid: number): void {
  if (!state.hasComponent(eid, CharacterMovement)) {
    state.addComponent(eid, CharacterMovement);
  }
}

/**
 * How far the physics pose may drift from the crowd agent before the agent is
 * teleported back onto it. `agent.teleport` resets the corridor and the move
 * request, so doing it every frame leaves the agent permanently pathless: it
 * never steers, so the CCT never moves, so the drift never closes.
 */
export const CCT_RESYNC_XZ = 0.5;
export const CCT_RESYNC_Y = 1.5;

/** Whether the crowd agent should be teleported back onto the physics pose. */
export function needsCrowdResync(dx: number, dy: number, dz: number): boolean {
  if (dx * dx + dz * dz >= CCT_RESYNC_XZ * CCT_RESYNC_XZ) return true;
  return Math.abs(dy) >= CCT_RESYNC_Y;
}

export interface CrowdAgentPoseSample {
  posX: number;
  posY: number;
  posZ: number;
  velX: number;
  velY: number;
  velZ: number;
}

/**
 * After `crowd.update`: either steer a CCT entity via desiredVel, or write
 * Transform XZ for agents without CharacterController.
 *
 * Returns whether Transform XZ was written (legacy path).
 */
export function applyCrowdAgentToEntity(
  state: State,
  eid: number,
  sample: CrowdAgentPoseSample
): { wroteTransformXZ: boolean } {
  const usesCct = state.hasComponent(eid, CharacterController);

  if (NavMeshAgent.suspended[eid] === 1) {
    if (usesCct) {
      ensureCharacterMovement(state, eid);
      CharacterMovement.desiredVelX[eid] = 0;
      CharacterMovement.desiredVelZ[eid] = 0;
    }
    return { wroteTransformXZ: false };
  }

  const speed = Math.hypot(sample.velX, sample.velZ);

  if (usesCct) {
    ensureCharacterMovement(state, eid);
    CharacterMovement.desiredVelX[eid] = sample.velX;
    CharacterMovement.desiredVelZ[eid] = sample.velZ;
    if (NavMeshAgent.faceVelocity[eid] === 1 && speed > 0.05) {
      setTransformFacingXZ(Transform, eid, sample.velX, sample.velZ);
      Transform.dirty[eid] = 1;
      syncFacingToRigidbody(state, eid);
    }
    return { wroteTransformXZ: false };
  }

  const prevX = Transform.posX[eid];
  const prevZ = Transform.posZ[eid];
  Transform.posX[eid] = sample.posX;
  Transform.posZ[eid] = sample.posZ;

  if (NavMeshAgent.faceVelocity[eid] === 1 && speed > 0.05) {
    setTransformFacingXZ(Transform, eid, sample.velX, sample.velZ);
    syncFacingToRigidbody(state, eid);
  }

  if (
    Math.abs(sample.posX - prevX) > 1e-4 ||
    Math.abs(sample.posZ - prevZ) > 1e-4 ||
    speed > 0.05
  ) {
    Transform.dirty[eid] = 1;
  }

  return { wroteTransformXZ: true };
}
