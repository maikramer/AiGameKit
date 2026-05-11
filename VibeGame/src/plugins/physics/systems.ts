import * as RAPIER from '@dimforge/rapier3d-simd-compat';
import { defineQuery, type State, type System } from '../../core';
import { Transform } from '../transforms';
import {
  Rigidbody,
  Collider,
  SetLinearVelocity,
  SetAngularVelocity,
  BodyType,
} from './components';
import { getOrCreateWorld, stepWorld } from './world';
import { createRapierBody, createRapierColliderDesc } from './body';
import { isTerrainDynamicsBlocking } from '../terrain/utils';

const bodyQuery = defineQuery([Rigidbody, Collider, Transform]);
const setLinvelQuery = defineQuery([SetLinearVelocity, Rigidbody]);
const setAngvelQuery = defineQuery([SetAngularVelocity, Rigidbody]);

const stateToBodies = new WeakMap<State, Map<number, RAPIER.RigidBody>>();

function getBodyMap(state: State): Map<number, RAPIER.RigidBody> {
  let m = stateToBodies.get(state);
  if (!m) {
    m = new Map();
    stateToBodies.set(state, m);
  }
  return m;
}

export const PhysicsInitSystem: System = {
  group: 'fixed',
  update: (state) => {
    const world = getOrCreateWorld();
    const bodies = getBodyMap(state);

    for (const entity of bodyQuery(state.world)) {
      if (bodies.has(entity)) continue;

      const bodyDesc = createRapierBody(entity);
      const body = world.createRigidBody(bodyDesc);
      bodies.set(entity, body);

      const colliderDesc = createRapierColliderDesc(entity);
      world.createCollider(colliderDesc, body);

      const t = body.translation();
      Rigidbody.posX[entity] = t.x;
      Rigidbody.posY[entity] = t.y;
      Rigidbody.posZ[entity] = t.z;

      const r = body.rotation();
      Rigidbody.rotX[entity] = r.x;
      Rigidbody.rotY[entity] = r.y;
      Rigidbody.rotZ[entity] = r.z;
      Rigidbody.rotW[entity] = r.w;
    }
  },
};

export const ApplyMovementSystem: System = {
  group: 'fixed',
  after: [PhysicsInitSystem],
  update: (state) => {
    const bodies = getBodyMap(state);

    for (const entity of setLinvelQuery(state.world)) {
      const body = bodies.get(entity);
      if (!body) continue;
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
      state.removeComponent(entity, SetLinearVelocity);
    }

    for (const entity of setAngvelQuery(state.world)) {
      const body = bodies.get(entity);
      if (!body) continue;
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

      const t = body.translation();
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
