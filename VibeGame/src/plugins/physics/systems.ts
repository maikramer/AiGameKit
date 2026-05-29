import * as RAPIER from '@dimforge/rapier3d-simd-compat';
import { defineQuery, type State, type System } from '../../core';
import { Transform } from '../transforms';
import {
  Rigidbody,
  Collider,
  SetLinearVelocity,
  SetAngularVelocity,
  BodyType,
  CharacterMovement,
  CharacterController,
  CollisionEvents,
  TouchedEvent,
} from './components';
import { getOrCreateWorld, getEventQueue, stepWorld, DEFAULT_GRAVITY } from './world';
import { createRapierBody, createRapierColliderDesc } from './body';
import {
  CHARACTER_MOVE_ACCEL,
  getCharacterFeetY,
  GROUND_PROBE_DISTANCE,
  isFeetTouchingTerrain,
  moveToward,
} from './character-ground';
import { castBvhRay } from '../bvh/utils';
import { isTerrainDynamicsBlocking } from '../terrain/utils';
import * as THREE from 'three';

const bodyQuery = defineQuery([Rigidbody, Collider, Transform]);
const setLinvelQuery = defineQuery([SetLinearVelocity, Rigidbody]);
const setAngvelQuery = defineQuery([SetAngularVelocity, Rigidbody]);
const charMoveQuery = defineQuery([CharacterMovement, Rigidbody]);

const _groundRayOrigin = new RAPIER.Vector3(0, 0, 0);
const _groundRayDir = new RAPIER.Vector3(0, -1, 0);
const _bvhOrigin = new THREE.Vector3();
const _bvhDir = new THREE.Vector3(0, -1, 0);

function isCharacterGrounded(
  state: State,
  world: RAPIER.World,
  x: number,
  y: number,
  z: number,
  entity: number,
  verticalSpeed: number
): boolean {
  if (verticalSpeed > 1.5) return false;

  const feetY = getCharacterFeetY(state, entity, y);

  // 1) Heightmap (cheap CPU sample, no Rapier roundtrip).
  if (isFeetTouchingTerrain(state, x, feetY, z)) {
    return true;
  }

  // 2) Mesh BVH (terrain + static GLTFs). Catches props/floors the heightmap
  //    does not represent.
  _bvhOrigin.set(x, feetY + 0.08, z);
  const bvhHit = castBvhRay(state, _bvhOrigin, _bvhDir, GROUND_PROBE_DISTANCE + 0.08);
  if (bvhHit && bvhHit.distance <= GROUND_PROBE_DISTANCE + 0.08) {
    return true;
  }

  // 3) Rapier ray cast for dynamic colliders (moving platforms, kinematics).
  const c2e = getColliderToEntityMap(state);
  const filter = (collider: RAPIER.Collider) =>
    c2e.get(collider.handle) !== entity;

  const offsetY =
    state.hasComponent(entity, Collider) ? Collider.posOffsetY[entity] || 0 : 0;
  _groundRayOrigin.x = x;
  _groundRayOrigin.y = y + offsetY + 0.05;
  _groundRayOrigin.z = z;
  const maxDist = 0.45;
  const hit = world.castRay(
    new RAPIER.Ray(_groundRayOrigin, _groundRayDir),
    maxDist,
    true,
    undefined,
    undefined,
    undefined,
    undefined,
    filter
  );
  return hit !== null && hit.timeOfImpact <= maxDist;
}

const stateToBodies = new WeakMap<State, Map<number, RAPIER.RigidBody>>();
const stateToFailed = new WeakMap<State, Set<number>>();
const stateToColliderToEntity = new WeakMap<State, Map<number, number>>();

function getBodyMap(state: State): Map<number, RAPIER.RigidBody> {
  let m = stateToBodies.get(state);
  if (!m) {
    m = new Map();
    stateToBodies.set(state, m);
  }
  return m;
}

function getFailedSet(state: State): Set<number> {
  let s = stateToFailed.get(state);
  if (!s) {
    s = new Set();
    stateToFailed.set(state, s);
  }
  return s;
}

function getColliderToEntityMap(state: State): Map<number, number> {
  let m = stateToColliderToEntity.get(state);
  if (!m) {
    m = new Map();
    stateToColliderToEntity.set(state, m);
  }
  return m;
}

export const PhysicsInitSystem: System = {
  group: 'fixed',
  update: (state) => {
    const world = getOrCreateWorld();
    const bodies = getBodyMap(state);
    const failed = getFailedSet(state);

    for (const entity of bodyQuery(state.world)) {
      if (bodies.has(entity)) continue;
      if (failed.has(entity)) continue;

      const px = Rigidbody.posX[entity] ?? 0;
      const py = Rigidbody.posY[entity] ?? 0;
      const pz = Rigidbody.posZ[entity] ?? 0;
      const mass = Rigidbody.mass[entity] ?? 1;
      const type = Rigidbody.type[entity] ?? 0;

      if (!isFinite(px) || !isFinite(py) || !isFinite(pz) || !isFinite(mass)) {
        console.warn(
          `[physics] skipping entity ${entity}: NaN/Inf pos=(${px},${py},${pz}) mass=${mass}`
        );
        failed.add(entity);
        continue;
      }
      if (type === 0 && mass <= 0) {
        Rigidbody.mass[entity] = 1;
      }

      try {
        const bodyDesc = createRapierBody(entity);
        const body = world.createRigidBody(bodyDesc);
        bodies.set(entity, body);

        const colliderDesc = createRapierColliderDesc(entity);
        const collider = world.createCollider(colliderDesc, body);
        getColliderToEntityMap(state).set(collider.handle, entity);

        console.log(
          `[physics] body created: entity=${entity} pos=(${px.toFixed(1)}, ${py.toFixed(2)}, ${pz.toFixed(1)}) mass=${mass} type=${type}`
        );

        const t = body.translation();
        Rigidbody.posX[entity] = t.x;
        Rigidbody.posY[entity] = t.y;
        Rigidbody.posZ[entity] = t.z;

        const r = body.rotation();
        Rigidbody.rotX[entity] = r.x;
        Rigidbody.rotY[entity] = r.y;
        Rigidbody.rotZ[entity] = r.z;
        Rigidbody.rotW[entity] = r.w;

        const vx = Rigidbody.velX[entity];
        const vy = Rigidbody.velY[entity];
        const vz = Rigidbody.velZ[entity];
        if ((vx || vy || vz) && type === BodyType.Dynamic) {
          body.setLinvel(new RAPIER.Vector3(vx, vy, vz), true);
        }

        failed.delete(entity);
      } catch (err) {
        console.error(
          `[physics] createRigidBody failed entity ${entity}: pos=(${px},${py},${pz}) mass=${mass} type=${type}`,
          err
        );
        failed.add(entity);
      }
    }
  },
};

export const ApplyMovementSystem: System = {
  group: 'fixed',
  after: [PhysicsInitSystem],
  update: (state) => {
    if (isTerrainDynamicsBlocking(state)) return;

    const bodies = getBodyMap(state);

    // Character movement (player)
    for (const entity of charMoveQuery(state.world)) {
      const body = bodies.get(entity);
      if (!body) continue;
      try {
        const type = Rigidbody.type[entity];

        const dvx = CharacterMovement.desiredVelX[entity] || 0;
        const dvz = CharacterMovement.desiredVelZ[entity] || 0;
        const jumpVel = CharacterMovement.velocityY[entity] || 0;

        if (type === 2) {
          const t = body.translation();
          const dt = state.time.fixedDeltaTime || 1 / 60;
          let vy = CharacterMovement.velocityY[entity] || 0;

          if (state.hasComponent(entity, CharacterController)) {
            const grounded = CharacterController.grounded[entity] === 1;
            const gravityScale = Rigidbody.gravityScale[entity] ?? 1;

            if (grounded && vy <= 0) {
              vy = 0;
            } else if (gravityScale > 0) {
              vy = Math.max(vy + DEFAULT_GRAVITY * gravityScale * dt, -40);
            }
            CharacterMovement.velocityY[entity] = vy;
          }

          const newY = t.y + vy * dt;
          body.setNextKinematicTranslation({
            x: t.x + dvx * dt,
            y: newY,
            z: t.z + dvz * dt,
          });
          body.setNextKinematicRotation({
            x: Rigidbody.rotX[entity],
            y: Rigidbody.rotY[entity],
            z: Rigidbody.rotZ[entity],
            w: Rigidbody.rotW[entity],
          });
        } else if (type === BodyType.Dynamic) {
          const dt = state.time.fixedDeltaTime || 1 / 60;
          const currentVel = body.linvel();
          const grounded =
            state.hasComponent(entity, CharacterController) &&
            CharacterController.grounded[entity] === 1;

          const hasInput = dvx !== 0 || dvz !== 0;
          let newVx = currentVel.x;
          let newVz = currentVel.z;

          if (hasInput) {
            const accel = grounded
              ? CHARACTER_MOVE_ACCEL.ground
              : CHARACTER_MOVE_ACCEL.air;
            newVx = moveToward(currentVel.x, dvx, accel * dt);
            newVz = moveToward(currentVel.z, dvz, accel * dt);
          } else {
            const decel = grounded
              ? CHARACTER_MOVE_ACCEL.groundDecel
              : CHARACTER_MOVE_ACCEL.airDecel;
            newVx = moveToward(currentVel.x, 0, decel * dt);
            newVz = moveToward(currentVel.z, 0, decel * dt);
          }

          body.setLinvel(new RAPIER.Vector3(newVx, currentVel.y, newVz), true);

          if (jumpVel > 0) {
            const grounded = state.hasComponent(entity, CharacterController)
              ? CharacterController.grounded[entity]
              : 0;
            if (grounded) {
              const mass = body.mass();
              body.applyImpulse(
                new RAPIER.Vector3(0, jumpVel * (mass || 70), 0),
                true
              );
              CharacterMovement.velocityY[entity] = 0;
            }
          }
        }
      } catch (e) {
        /* skip */
      }
    }

    for (const entity of setLinvelQuery(state.world)) {
      const body = bodies.get(entity);
      if (!body) continue;
      try {
        if (Rigidbody.type[entity] !== BodyType.Dynamic) {
          state.removeComponent(entity, SetLinearVelocity);
          continue;
        }
        body.setLinvel(
          new RAPIER.Vector3(
            SetLinearVelocity.x[entity],
            SetLinearVelocity.y[entity],
            SetLinearVelocity.z[entity]
          ),
          true
        );
      } catch (e) {
        /* skip */
      }
      state.removeComponent(entity, SetLinearVelocity);
    }

    for (const entity of setAngvelQuery(state.world)) {
      const body = bodies.get(entity);
      if (!body) continue;
      try {
        if (Rigidbody.type[entity] !== BodyType.Dynamic) {
          state.removeComponent(entity, SetAngularVelocity);
          continue;
        }
        body.setAngvel(
          new RAPIER.Vector3(
            SetAngularVelocity.x[entity],
            SetAngularVelocity.y[entity],
            SetAngularVelocity.z[entity]
          ),
          true
        );
      } catch (e) {
        /* skip */
      }
      state.removeComponent(entity, SetAngularVelocity);
    }
  },
};

export const ApplyJumpSystem: System = {
  group: 'fixed',
  after: [ApplyMovementSystem],
  update: () => {
    // Placeholder: jump handled by player plugin via applyImpulse
  },
};

export const PhysicsStepSystem: System = {
  group: 'fixed',
  after: [ApplyJumpSystem],
  update: (state) => {
    if (isTerrainDynamicsBlocking(state)) return;
    stepWorld();

    const queue = getEventQueue();
    const c2e = getColliderToEntityMap(state);
    if (!queue) return;

    queue.drainCollisionEvents((handle1, handle2, started) => {
      const eid1 = c2e.get(handle1) ?? 0;
      const eid2 = c2e.get(handle2) ?? 0;
      if (!eid1 || !eid2) return;

      if (started) {
        if (state.hasComponent(eid1, CollisionEvents)) {
          if (!state.hasComponent(eid1, TouchedEvent)) {
            state.addComponent(eid1, TouchedEvent);
          }
          TouchedEvent.other[eid1] = eid2;
        }
        if (state.hasComponent(eid2, CollisionEvents)) {
          if (!state.hasComponent(eid2, TouchedEvent)) {
            state.addComponent(eid2, TouchedEvent);
          }
          TouchedEvent.other[eid2] = eid1;
        }
      } else {
        if (state.hasComponent(eid1, TouchedEvent)) {
          state.removeComponent(eid1, TouchedEvent);
        }
        if (state.hasComponent(eid2, TouchedEvent)) {
          state.removeComponent(eid2, TouchedEvent);
        }
      }
    });
  },
};

export const PhysicsSyncSystem: System = {
  group: 'simulation',
  update: (state) => {
    const bodies = getBodyMap(state);
    const world = getOrCreateWorld();

    for (const [entity, body] of bodies) {
      if (!state.hasComponent(entity, Rigidbody)) continue;

      try {
        const t = body.translation();
        const prevX = Rigidbody.posX[entity];
        const prevZ = Rigidbody.posZ[entity];
        Rigidbody.posX[entity] = t.x;
        Rigidbody.posY[entity] = t.y;
        Rigidbody.posZ[entity] = t.z;

        const r = body.rotation();
        Rigidbody.rotX[entity] = r.x;
        Rigidbody.rotY[entity] = r.y;
        Rigidbody.rotZ[entity] = r.z;
        Rigidbody.rotW[entity] = r.w;

        const v = body.linvel();
        Rigidbody.velX[entity] = v.x;
        Rigidbody.velY[entity] = v.y;
        Rigidbody.velZ[entity] = v.z;

        const bodyY = t.y;

        if (state.hasComponent(entity, CharacterMovement)) {
          CharacterMovement.actualMoveX[entity] = t.x - prevX;
          CharacterMovement.actualMoveZ[entity] = t.z - prevZ;
        }

        if (state.hasComponent(entity, CharacterController)) {
          const grounded = isCharacterGrounded(
            state,
            world,
            t.x,
            bodyY,
            t.z,
            entity,
            v.y
          );
          CharacterController.grounded[entity] = grounded ? 1 : 0;
          if (
            grounded &&
            state.hasComponent(entity, CharacterMovement) &&
            CharacterMovement.velocityY[entity] < 0
          ) {
            CharacterMovement.velocityY[entity] = 0;
          }
        }

        if (state.hasComponent(entity, Transform)) {
          Transform.posX[entity] = t.x;
          Transform.posY[entity] = bodyY;
          Transform.posZ[entity] = t.z;
          Transform.rotX[entity] = r.x;
          Transform.rotY[entity] = r.y;
          Transform.rotZ[entity] = r.z;
          Transform.rotW[entity] = r.w;
          Transform.dirty[entity] = 1;
        }
      } catch (err) {
        console.error(`[physics] sync failed for entity ${entity}`, err);
      }
    }
  },
};

export function getBodyForEntity(
  state: State,
  entity: number
): RAPIER.RigidBody | undefined {
  return getBodyMap(state).get(entity);
}

export function getPhysicsContext(_state: State): {
  physicsWorld: RAPIER.World;
  entityToRigidbody: Map<number, RAPIER.RigidBody>;
  colliderToEntity: Map<number, number>;
} {
  return {
    physicsWorld: getOrCreateWorld(),
    entityToRigidbody: getBodyMap(_state),
    colliderToEntity: getColliderToEntityMap(_state),
  };
}

export { PhysicsStepSystem as PhysicsWorldSystem };
export { PhysicsInitSystem as PhysicsInitializationSystem };
export { PhysicsSyncSystem as PhysicsInterpolationSystem };
