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
} from './components';
import { getOrCreateWorld, stepWorld } from './world';
import { createRapierBody, createRapierColliderDesc } from './body';
import { isTerrainDynamicsBlocking } from '../terrain/utils';

const bodyQuery = defineQuery([Rigidbody, Collider, Transform]);
const setLinvelQuery = defineQuery([SetLinearVelocity, Rigidbody]);
const setAngvelQuery = defineQuery([SetAngularVelocity, Rigidbody]);
const charMoveQuery = defineQuery([CharacterMovement, Rigidbody]);

const stateToBodies = new WeakMap<State, Map<number, RAPIER.RigidBody>>();
const stateToFailed = new WeakMap<State, Set<number>>();
const stateToGroundCreated = new WeakMap<State, boolean>();

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

function ensureGroundPlane(state: State, world: RAPIER.World): void {
  if (stateToGroundCreated.get(state)) return;
  stateToGroundCreated.set(state, true);
  try {
    const groundDesc = RAPIER.RigidBodyDesc.fixed().setTranslation(0, -1, 0);
    const groundBody = world.createRigidBody(groundDesc);
    const groundCollider = RAPIER.ColliderDesc.cuboid(500, 1, 500);
    groundCollider.setFriction(0.8);
    world.createCollider(groundCollider, groundBody);
    console.log('[physics] safety ground plane created at y=-1 (1000x2x1000)');
  } catch (e) {
    console.error('[physics] failed to create ground plane:', e);
  }
}

export const PhysicsInitSystem: System = {
  group: 'fixed',
  update: (state) => {
    const world = getOrCreateWorld();
    const bodies = getBodyMap(state);
    const failed = getFailedSet(state);
    ensureGroundPlane(state, world);

    for (const entity of bodyQuery(state.world)) {
      if (bodies.has(entity)) continue;
      if (failed.has(entity)) continue;

      const px = Rigidbody.posX[entity] ?? 0;
      const py = Rigidbody.posY[entity] ?? 0;
      const pz = Rigidbody.posZ[entity] ?? 0;
      const mass = Rigidbody.mass[entity] ?? 1;
      const gs = Rigidbody.gravityScale[entity] ?? 1;
      const type = Rigidbody.type[entity] ?? 0;

      if (!isFinite(px) || !isFinite(py) || !isFinite(pz) || !isFinite(mass)) {
        console.warn(`[physics] skipping entity ${entity}: NaN/Inf pos=(${px},${py},${pz}) mass=${mass}`);
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
        world.createCollider(colliderDesc, body);

        console.log(`[physics] body created: entity=${entity} pos=(${px.toFixed(1)}, ${py.toFixed(2)}, ${pz.toFixed(1)}) mass=${mass} type=${type}`);

        const t = body.translation();
        Rigidbody.posX[entity] = t.x;
        Rigidbody.posY[entity] = t.y;
        Rigidbody.posZ[entity] = t.z;

        const r = body.rotation();
        Rigidbody.rotX[entity] = r.x;
        Rigidbody.rotY[entity] = r.y;
        Rigidbody.rotZ[entity] = r.z;
        Rigidbody.rotW[entity] = r.w;

        failed.delete(entity);
      } catch (err) {
        console.error(`[physics] createRigidBody failed entity ${entity}: pos=(${px},${py},${pz}) mass=${mass} type=${type}`, err);
        failed.add(entity);
      }
    }
  },
};

export const ApplyMovementSystem: System = {
  group: 'fixed',
  after: [PhysicsInitSystem],
  update: (state) => {
    const bodies = getBodyMap(state);
    const dt = state.time.fixedDeltaTime;

    // Character movement (player)
    for (const entity of charMoveQuery(state.world)) {
      const body = bodies.get(entity);
      if (!body) continue;
      try {
        const type = Rigidbody.type[entity];
        if (type !== BodyType.Dynamic && type !== 2) continue;

        const dvx = CharacterMovement.desiredVelX[entity] || 0;
        const dvz = CharacterMovement.desiredVelZ[entity] || 0;
        const jumpVel = CharacterMovement.velocityY[entity] || 0;

        // Horizontal movement
        if (dvx !== 0 || dvz !== 0) {
          body.setLinvel(new RAPIER.Vector3(dvx, 0, dvz), true);
        }

        // Jump
        if (jumpVel > 0) {
          const grounded = state.hasComponent(entity, CharacterController)
            ? CharacterController.grounded[entity]
            : 0;
          if (grounded) {
            const mass = body.mass();
            body.applyImpulse(new RAPIER.Vector3(0, jumpVel * (mass || 70), 0), true);
            CharacterMovement.velocityY[entity] = 0;
          }
        }
      } catch (e) { /* skip */ }
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
          new RAPIER.Vector3(SetLinearVelocity.x[entity], SetLinearVelocity.y[entity], SetLinearVelocity.z[entity]),
          true
        );
      } catch (e) { /* skip */ }
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
          new RAPIER.Vector3(SetAngularVelocity.x[entity], SetAngularVelocity.y[entity], SetAngularVelocity.z[entity]),
          true
        );
      } catch (e) { /* skip */ }
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
  },
};

export const PhysicsSyncSystem: System = {
  group: 'simulation',
  update: (state) => {
    const bodies = getBodyMap(state);

    for (const [entity, body] of bodies) {
      if (!state.hasComponent(entity, Rigidbody)) continue;

      try {
        const t = body.translation();
        const prevX = Rigidbody.posX[entity];
        const prevY = Rigidbody.posY[entity];
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

        if (state.hasComponent(entity, CharacterController)) {
          const dy = t.y - prevY;
          const grounded = Math.abs(v.y) < 0.1 && Math.abs(dy) < 0.01;
          CharacterController.grounded[entity] = grounded ? 1 : 0;
          // Track position changes for debugging
          if (Math.abs(dy) > 1 && Math.abs(v.y) > 5) {
            console.log(`[player] falling: y=${t.y.toFixed(2)} vy=${v.y.toFixed(2)} dy=${dy.toFixed(2)}`);
          }
        }

        if (state.hasComponent(entity, Transform)) {
          Transform.posX[entity] = t.x;
          Transform.posY[entity] = t.y;
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

export function getPhysicsContext(state: State): { physicsWorld: RAPIER.World } {
  return { physicsWorld: getOrCreateWorld() };
}

export { PhysicsStepSystem as PhysicsWorldSystem };
export { PhysicsInitSystem as PhysicsInitializationSystem };
export { PhysicsSyncSystem as PhysicsInterpolationSystem };
