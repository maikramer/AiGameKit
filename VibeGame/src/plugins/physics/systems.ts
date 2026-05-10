import * as RAPIER from '@dimforge/rapier3d-simd-compat';
import { defineQuery, type State, type System } from '../../core';
import { Transform } from '../transforms';
import { Rigidbody, Collider } from './components';
import { getOrCreateWorld, stepWorld } from './world';
import { createRapierBody, createRapierColliderDesc } from './body';

const bodyQuery = defineQuery([Rigidbody, Collider, Transform]);

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
  update: () => {
    // Placeholder: player plugin sets body.linvel() directly via getBodyForEntity()
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
  update: () => {
    stepWorld();
  },
};

export const PhysicsSyncSystem: System = {
  group: 'fixed',
  after: [PhysicsStepSystem],
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
