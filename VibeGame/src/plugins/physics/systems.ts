import { logger } from '../../core/utils/logger';
import * as RAPIER from '@dimforge/rapier3d-compat';
import { ActiveEvents } from '@dimforge/rapier3d-compat';
import type { State, System } from '../../core';
import { defineQuery, isPhysicsHeld, TIME_CONSTANTS } from '../../core';
import { Transform, WorldTransform } from '../transforms';
import {
  ApplyAngularImpulse,
  ApplyForce,
  ApplyImpulse,
  ApplyTorque,
  Rigidbody,
  CharacterController,
  CharacterMovement,
  Collider,
  ColliderShape,
  CollisionEvents,
  InterpolatedTransform,
  KinematicMove,
  KinematicRotate,
  PhysicsWorld,
  SetAngularVelocity,
  SetLinearVelocity,
  TouchedEvent,
  TouchEndedEvent,
} from './components';
import {
  buildMeshColliderGeometry,
  colliderMeshFailed,
  getColliderMeshUrl,
  requestColliderMesh,
  type ColliderMeshData,
} from './mesh-collider';
import {
  applyAngularImpulseToEntity,
  applyCharacterMovement,
  applyForceToEntity,
  applyImpulseToEntity,
  applyKinematicMove,
  applyKinematicRotation,
  applyTorqueToEntity,
  configureRigidbody,
  copyRigidbodyToTransforms,
  createColliderDescriptor,
  createRigidbodyDescriptor,
  DEFAULT_GRAVITY,
  interpolateTransforms,
  setAngularVelocityForEntity,
  setLinearVelocityForEntity,
  syncBodyQuaternionFromEuler,
  syncRigidbodyToECS,
  teleportEntity,
} from './utils';

interface PhysicsContext {
  physicsWorld: RAPIER.World | null;
  worldEntity: number | null;
  eventQueue: RAPIER.EventQueue | null;
  entityToRigidbody: Map<number, RAPIER.RigidBody>;
  entityToCollider: Map<number, RAPIER.Collider>;
  entityToCharacterController: Map<number, RAPIER.KinematicCharacterController>;
  colliderToEntity: Map<number, number>;
}

const physicsWorldQuery = defineQuery([PhysicsWorld]);
const bodyQuery = defineQuery([Rigidbody]);
const colliderQuery = defineQuery([Collider]);
const characterControllerQuery = defineQuery([CharacterController]);
const characterMovementQuery = defineQuery([
  CharacterController,
  CharacterMovement,
  Rigidbody,
  Transform,
]);
const applyForceQuery = defineQuery([ApplyForce, Rigidbody]);
const applyTorqueQuery = defineQuery([ApplyTorque, Rigidbody]);
const applyImpulseQuery = defineQuery([ApplyImpulse, Rigidbody]);
const applyAngularImpulseQuery = defineQuery([ApplyAngularImpulse, Rigidbody]);
const setLinearVelocityQuery = defineQuery([SetLinearVelocity, Rigidbody]);
const setAngularVelocityQuery = defineQuery([SetAngularVelocity, Rigidbody]);
const kinematicMoveQuery = defineQuery([KinematicMove, Rigidbody]);
const kinematicRotateQuery = defineQuery([KinematicRotate, Rigidbody]);
const touchedEventQuery = defineQuery([TouchedEvent]);
const touchEndedEventQuery = defineQuery([TouchEndedEvent]);

const stateToPhysicsContext = new WeakMap<State, PhysicsContext>();

function getPhysicsContext(state: State): PhysicsContext {
  let context = stateToPhysicsContext.get(state);
  if (!context) {
    context = {
      physicsWorld: null,
      worldEntity: null,
      eventQueue: null,
      entityToRigidbody: new Map(),
      entityToCollider: new Map(),
      entityToCharacterController: new Map(),
      colliderToEntity: new Map(),
    };
    stateToPhysicsContext.set(state, context);
  }
  return context;
}

export { getPhysicsContext };

export function getRapierWorld(state: State): RAPIER.World | null {
  const context = stateToPhysicsContext.get(state);
  return context?.physicsWorld ?? null;
}

export function getBodyForEntity(
  state: State,
  entity: number
): RAPIER.RigidBody | null {
  const context = stateToPhysicsContext.get(state);
  return context?.entityToRigidbody.get(entity) ?? null;
}

export const PhysicsWorldSystem: System = {
  group: 'fixed',
  first: true,
  update: (state) => {
    const context = getPhysicsContext(state);
    if (context.physicsWorld) return;

    const worldEntities = physicsWorldQuery(state.world);
    if (worldEntities.length === 0) {
      const worldEntity = state.createEntity();
      state.addComponent(worldEntity, PhysicsWorld);
      context.worldEntity = worldEntity;

      PhysicsWorld.gravityX[worldEntity] = 0;
      PhysicsWorld.gravityY[worldEntity] = DEFAULT_GRAVITY;
      PhysicsWorld.gravityZ[worldEntity] = 0;

      const worldRapier = new RAPIER.World(
        new RAPIER.Vector3(
          PhysicsWorld.gravityX[worldEntity],
          PhysicsWorld.gravityY[worldEntity],
          PhysicsWorld.gravityZ[worldEntity]
        )
      );
      worldRapier.timestep = TIME_CONSTANTS.FIXED_TIMESTEP;
      context.physicsWorld = worldRapier;
    }
  },
  dispose: (state) => {
    const context = stateToPhysicsContext.get(state);
    if (context) {
      if (context.eventQueue) {
        context.eventQueue.free();
        context.eventQueue = null;
      }
      if (context.physicsWorld) {
        context.physicsWorld.free();
      }
      context.entityToRigidbody.clear();
      context.entityToCollider.clear();
      context.entityToCharacterController.clear();
      context.colliderToEntity.clear();
      stateToPhysicsContext.delete(state);
    }
  },
};

/**
 * Entities with `place="…"` only reach their world pose after terrain
 * placement resolves; creating the body earlier would leave a stray collider
 * at the pre-placement position.
 */
function isPlacementPending(state: State, entity: number): boolean {
  const placePending = state.getComponent('placePending');
  if (!placePending || !state.hasComponent(entity, placePending)) return false;
  return (placePending as { spawned: Uint8Array }).spawned[entity] === 0;
}

export const PhysicsInitializationSystem: System = {
  group: 'fixed',
  after: [PhysicsWorldSystem],
  update: (state) => {
    const context = getPhysicsContext(state);
    const worldRapier = context.physicsWorld;
    if (!worldRapier) return;

    for (const entity of bodyQuery(state.world)) {
      if (
        !context.entityToRigidbody.has(entity) &&
        !isPlacementPending(state, entity)
      ) {
        createRigidbodyForEntity(entity, worldRapier, state, context);
      }
    }

    for (const entity of colliderQuery(state.world)) {
      if (!context.entityToCollider.has(entity)) {
        createColliderForEntity(entity, worldRapier, state, context);
      }
    }

    for (const entity of characterControllerQuery(state.world)) {
      if (!context.entityToCharacterController.has(entity)) {
        createCharacterControllerForEntity(entity, worldRapier, context);
      }
    }
  },
};

function createRigidbodyForEntity(
  entity: number,
  worldRapier: RAPIER.World,
  state: State,
  context: PhysicsContext
): void {
  const position = new RAPIER.Vector3(
    Rigidbody.posX[entity],
    Rigidbody.posY[entity],
    Rigidbody.posZ[entity]
  );

  const hasEuler =
    Rigidbody.eulerX[entity] !== 0 ||
    Rigidbody.eulerY[entity] !== 0 ||
    Rigidbody.eulerZ[entity] !== 0;
  if (hasEuler) {
    syncBodyQuaternionFromEuler(entity);
  }

  const rotX = Rigidbody.rotX[entity];
  const rotY = Rigidbody.rotY[entity];
  const rotZ = Rigidbody.rotZ[entity];
  const rotW = Rigidbody.rotW[entity];
  const magnitude = Math.sqrt(
    rotX * rotX + rotY * rotY + rotZ * rotZ + rotW * rotW
  );

  if (magnitude < 0.001) {
    throw new Error(
      `Invalid quaternion for Rigidbody entity ${entity}: (${rotX}, ${rotY}, ${rotZ}, ${rotW}). ` +
        `Quaternion magnitude is ${magnitude}. ` +
        `Ensure Rigidbody.rotW is initialized (typically to 1 for identity rotation).`
    );
  }

  const rotation = new RAPIER.Quaternion(rotX, rotY, rotZ, rotW);
  const descriptor = createRigidbodyDescriptor(
    Rigidbody.type[entity],
    position,
    rotation
  );

  const body = worldRapier.createRigidBody(descriptor);

  configureRigidbody(
    body,
    entity,
    Rigidbody.type[entity],
    Rigidbody.mass[entity],
    new RAPIER.Vector3(
      Rigidbody.velX[entity],
      Rigidbody.velY[entity],
      Rigidbody.velZ[entity]
    ),
    new RAPIER.Vector3(
      Rigidbody.rotVelX[entity],
      Rigidbody.rotVelY[entity],
      Rigidbody.rotVelZ[entity]
    ),
    Rigidbody.linearDamping[entity],
    Rigidbody.angularDamping[entity],
    Rigidbody.gravityScale[entity],
    Rigidbody.ccd[entity],
    Rigidbody.lockRotX[entity],
    Rigidbody.lockRotY[entity],
    Rigidbody.lockRotZ[entity]
  );

  context.entityToRigidbody.set(entity, body);

  if (!state.hasComponent(entity, Transform)) {
    state.addComponent(entity, Transform);
    Transform.posX[entity] = Rigidbody.posX[entity];
    Transform.posY[entity] = Rigidbody.posY[entity];
    Transform.posZ[entity] = Rigidbody.posZ[entity];
    Transform.rotX[entity] = Rigidbody.rotX[entity];
    Transform.rotY[entity] = Rigidbody.rotY[entity];
    Transform.rotZ[entity] = Rigidbody.rotZ[entity];
    Transform.rotW[entity] = Rigidbody.rotW[entity];
    Transform.scaleX[entity] = 1;
    Transform.scaleY[entity] = 1;
    Transform.scaleZ[entity] = 1;
  }

  if (!state.hasComponent(entity, WorldTransform)) {
    state.addComponent(entity, WorldTransform);
    WorldTransform.posX[entity] = Rigidbody.posX[entity];
    WorldTransform.posY[entity] = Rigidbody.posY[entity];
    WorldTransform.posZ[entity] = Rigidbody.posZ[entity];
    WorldTransform.rotX[entity] = Rigidbody.rotX[entity];
    WorldTransform.rotY[entity] = Rigidbody.rotY[entity];
    WorldTransform.rotZ[entity] = Rigidbody.rotZ[entity];
    WorldTransform.rotW[entity] = Rigidbody.rotW[entity];
    WorldTransform.scaleX[entity] = 1;
    WorldTransform.scaleY[entity] = 1;
    WorldTransform.scaleZ[entity] = 1;
  }

  if (!state.hasComponent(entity, InterpolatedTransform)) {
    state.addComponent(entity, InterpolatedTransform);
  }

  const pos = body.translation();
  const rot = body.rotation();

  InterpolatedTransform.prevPosX[entity] = pos.x;
  InterpolatedTransform.prevPosY[entity] = pos.y;
  InterpolatedTransform.prevPosZ[entity] = pos.z;
  InterpolatedTransform.posX[entity] = pos.x;
  InterpolatedTransform.posY[entity] = pos.y;
  InterpolatedTransform.posZ[entity] = pos.z;

  InterpolatedTransform.prevRotX[entity] = rot.x;
  InterpolatedTransform.prevRotY[entity] = rot.y;
  InterpolatedTransform.prevRotZ[entity] = rot.z;
  InterpolatedTransform.prevRotW[entity] = rot.w;
  InterpolatedTransform.rotX[entity] = rot.x;
  InterpolatedTransform.rotY[entity] = rot.y;
  InterpolatedTransform.rotZ[entity] = rot.z;
  InterpolatedTransform.rotW[entity] = rot.w;

  // Sync completo já na criação: o PhysicsRapierSyncSystem pula corpos
  // dormindo, então corpos que nascem (e ficam) adormecidos precisam dos
  // transforms corretos desde o primeiro frame.
  syncRigidbodyToECS(entity, body, state);
  copyRigidbodyToTransforms(entity, state);
}

const meshColliderWarned = new Set<number>();
function warnOnce(entity: number, message: string): void {
  if (meshColliderWarned.has(entity)) return;
  meshColliderWarned.add(entity);
  logger.warn(message);
}

function createColliderForEntity(
  entity: number,
  worldRapier: RAPIER.World,
  state: State,
  context: PhysicsContext
): void {
  const body = context.entityToRigidbody.get(entity);
  if (!body || !state.hasComponent(entity, Rigidbody)) {
    return;
  }

  const activeEvents = state.hasComponent(entity, CollisionEvents)
    ? ActiveEvents.COLLISION_EVENTS
    : ActiveEvents.NONE;

  const offset = new RAPIER.Vector3(
    Collider.posOffsetX[entity],
    Collider.posOffsetY[entity],
    Collider.posOffsetZ[entity]
  );

  const rotOffsetX = Collider.rotOffsetX[entity] || 0;
  const rotOffsetY = Collider.rotOffsetY[entity] || 0;
  const rotOffsetZ = Collider.rotOffsetZ[entity] || 0;
  let rotOffsetW = Collider.rotOffsetW[entity];

  const magnitude = Math.sqrt(
    rotOffsetX * rotOffsetX +
      rotOffsetY * rotOffsetY +
      rotOffsetZ * rotOffsetZ +
      rotOffsetW * rotOffsetW
  );

  if (magnitude < 0.001) {
    rotOffsetW = 1;
  }

  const rotationOffset = new RAPIER.Quaternion(
    rotOffsetX,
    rotOffsetY,
    rotOffsetZ,
    rotOffsetW
  );

  let scaleX = 1;
  let scaleY = 1;
  let scaleZ = 1;
  if (state.hasComponent(entity, Transform)) {
    scaleX = Transform.scaleX[entity];
    scaleY = Transform.scaleY[entity];
    scaleZ = Transform.scaleZ[entity];
  }

  const shape = Collider.shape[entity];
  let mesh: ColliderMeshData | undefined;
  if (shape === ColliderShape.TriMesh || shape === ColliderShape.ConvexHull) {
    const url = getColliderMeshUrl(state, entity);
    if (!url) {
      warnOnce(
        entity,
        `[mesh-collider] entity ${entity}: shape trimesh/convex-hull sem "mesh-url"; collider omitido.`
      );
      return;
    }
    if (colliderMeshFailed(url)) return;
    const data = requestColliderMesh(url);
    // GLB still downloading: leave entityToCollider unset so the init system
    // retries next tick.
    if (!data) return;
    mesh = buildMeshColliderGeometry(
      data,
      (Collider.meshScale[entity] || 1) * scaleX,
      Collider.meshAnchor[entity]
    );
  }

  const descriptor = createColliderDescriptor(
    shape,
    Collider.sizeX[entity] * scaleX,
    Collider.sizeY[entity] * scaleY,
    Collider.sizeZ[entity] * scaleZ,
    Collider.radius[entity],
    Collider.height[entity],
    Collider.friction[entity],
    Collider.restitution[entity],
    Collider.density[entity],
    Collider.isSensor[entity],
    Collider.membershipGroups[entity],
    Collider.filterGroups[entity],
    offset,
    rotationOffset,
    activeEvents,
    mesh
  );

  const collider = worldRapier.createCollider(descriptor, body);

  context.entityToCollider.set(entity, collider);
  context.colliderToEntity.set(collider.handle, entity);
}

function createCharacterControllerForEntity(
  entity: number,
  worldRapier: RAPIER.World,
  context: PhysicsContext
): void {
  const controller = worldRapier.createCharacterController(
    CharacterController.offset[entity]
  );
  controller.setMaxSlopeClimbAngle(CharacterController.maxSlope[entity]);
  controller.setMinSlopeSlideAngle(CharacterController.maxSlide[entity]);
  if (CharacterController.snapDist[entity] > 0) {
    controller.enableSnapToGround(CharacterController.snapDist[entity]);
  } else {
    controller.disableSnapToGround();
  }
  controller.enableAutostep(
    CharacterController.maxStepHeight[entity],
    CharacterController.minStepWidth[entity],
    !!CharacterController.autoStep[entity]
  );
  controller.setUp(
    new RAPIER.Vector3(
      CharacterController.upX[entity],
      CharacterController.upY[entity],
      CharacterController.upZ[entity]
    )
  );
  controller.setApplyImpulsesToDynamicBodies(true);
  controller.setCharacterMass(70);
  controller.setSlideEnabled(true);
  context.entityToCharacterController.set(entity, controller);
}

export const PhysicsCleanupSystem: System = {
  group: 'fixed',
  after: [PhysicsInitializationSystem],
  update: (state) => {
    const context = getPhysicsContext(state);
    const worldRapier = context.physicsWorld;
    if (!worldRapier) return;

    for (const [entity, collider] of context.entityToCollider) {
      if (!state.hasComponent(entity, Collider)) {
        worldRapier.removeCollider(collider, true);
        (collider as unknown as { free: () => void }).free();
        context.entityToCollider.delete(entity);
        context.colliderToEntity.delete(collider.handle);
      }
    }

    for (const [entity, body] of context.entityToRigidbody) {
      if (!state.hasComponent(entity, Rigidbody)) {
        worldRapier.removeRigidBody(body);
        (body as unknown as { free: () => void }).free();
        context.entityToRigidbody.delete(entity);
      }
    }

    for (const [entity, controller] of context.entityToCharacterController) {
      if (!state.hasComponent(entity, CharacterController)) {
        worldRapier.removeCharacterController(controller);
        (controller as unknown as { free: () => void }).free();
        context.entityToCharacterController.delete(entity);
      }
    }
  },
};

export const CharacterMovementSystem: System = {
  group: 'fixed',
  after: [PhysicsCleanupSystem],
  update: (state) => {
    const context = getPhysicsContext(state);
    if (!context.physicsWorld || context.worldEntity === null) return;

    const gravityY = PhysicsWorld.gravityY[context.worldEntity];

    const entities = characterMovementQuery(state.world);

    for (const entity of entities) {
      const controller = context.entityToCharacterController.get(entity);
      const collider = context.entityToCollider.get(entity);
      const rigidbody = context.entityToRigidbody.get(entity);

      if (!controller || !collider || !rigidbody) continue;

      applyCharacterMovement(
        entity,
        controller,
        collider,
        rigidbody,
        state.time.fixedDeltaTime,
        gravityY,
        context.colliderToEntity,
        context.physicsWorld
      );
    }
  },
};

export const ApplyInputSystem: System = {
  group: 'fixed',
  after: [CharacterMovementSystem],
  update: (state) => {
    const context = getPhysicsContext(state);

    for (const entity of applyForceQuery(state.world)) {
      const body = context.entityToRigidbody.get(entity);
      if (body) applyForceToEntity(entity, body, state);
    }
    for (const entity of applyTorqueQuery(state.world)) {
      const body = context.entityToRigidbody.get(entity);
      if (body) applyTorqueToEntity(entity, body, state);
    }
    for (const entity of applyImpulseQuery(state.world)) {
      const body = context.entityToRigidbody.get(entity);
      if (body) applyImpulseToEntity(entity, body, state);
    }
    for (const entity of applyAngularImpulseQuery(state.world)) {
      const body = context.entityToRigidbody.get(entity);
      if (body) applyAngularImpulseToEntity(entity, body, state);
    }
  },
};

export const SetVelocitySystem: System = {
  group: 'fixed',
  after: [ApplyInputSystem],
  update: (state) => {
    const context = getPhysicsContext(state);

    for (const entity of setLinearVelocityQuery(state.world)) {
      const body = context.entityToRigidbody.get(entity);
      if (body) {
        setLinearVelocityForEntity(entity, body, state);
      }
    }

    for (const entity of setAngularVelocityQuery(state.world)) {
      const body = context.entityToRigidbody.get(entity);
      if (body) {
        setAngularVelocityForEntity(entity, body, state);
      }
    }
  },
};

export const KinematicMovementSystem: System = {
  group: 'fixed',
  after: [SetVelocitySystem],
  update: (state) => {
    const context = getPhysicsContext(state);

    for (const entity of kinematicMoveQuery(state.world)) {
      const body = context.entityToRigidbody.get(entity);
      if (body) {
        applyKinematicMove(entity, body, state);
      }
    }

    for (const entity of kinematicRotateQuery(state.world)) {
      const body = context.entityToRigidbody.get(entity);
      if (body) {
        applyKinematicRotation(entity, body, state);
      }
    }
  },
};

export const TeleportationSystem: System = {
  group: 'fixed',
  after: [KinematicMovementSystem],
  update: (state) => {
    const context = getPhysicsContext(state);

    for (const entity of bodyQuery(state.world)) {
      const body = context.entityToRigidbody.get(entity);
      if (!body) continue;

      teleportEntity(entity, body);
    }
  },
};

export const PhysicsStepSystem: System = {
  group: 'fixed',
  after: [TeleportationSystem],
  update: (state) => {
    // Hold the simulation while the world is still loading so nothing falls or
    // settles before terrain colliders and assets are in place. Body/collider
    // creation runs in other `fixed` systems, so colliders still build now.
    if (isPhysicsHeld(state)) return;

    const context = getPhysicsContext(state);
    const worldRapier = context.physicsWorld;
    if (!worldRapier) return;

    if (!context.eventQueue) {
      context.eventQueue = new RAPIER.EventQueue(true);
    }
    worldRapier.step(context.eventQueue);
    processCollisionEvents(context.eventQueue, state, context);
  },
};

function processCollisionEvents(
  eventQueue: RAPIER.EventQueue,
  state: State,
  context: PhysicsContext
): void {
  eventQueue.drainCollisionEvents(
    (handle1: number, handle2: number, started: boolean) => {
      const entity1 = context.colliderToEntity.get(handle1);
      const entity2 = context.colliderToEntity.get(handle2);

      if (entity1 === undefined || entity2 === undefined) return;

      if (started) {
        if (state.hasComponent(entity1, CollisionEvents)) {
          state.addComponent(entity1, TouchedEvent);
          TouchedEvent.other[entity1] = entity2;
          TouchedEvent.handle1[entity1] = handle1;
          TouchedEvent.handle2[entity1] = handle2;
        }

        if (state.hasComponent(entity2, CollisionEvents)) {
          state.addComponent(entity2, TouchedEvent);
          TouchedEvent.other[entity2] = entity1;
          TouchedEvent.handle1[entity2] = handle2;
          TouchedEvent.handle2[entity2] = handle1;
        }
      } else {
        if (state.hasComponent(entity1, CollisionEvents)) {
          state.addComponent(entity1, TouchEndedEvent);
          TouchEndedEvent.other[entity1] = entity2;
          TouchEndedEvent.handle1[entity1] = handle1;
          TouchEndedEvent.handle2[entity1] = handle2;
        }

        if (state.hasComponent(entity2, CollisionEvents)) {
          state.addComponent(entity2, TouchEndedEvent);
          TouchEndedEvent.other[entity2] = entity1;
          TouchEndedEvent.handle1[entity2] = handle2;
          TouchEndedEvent.handle2[entity2] = handle1;
        }
      }
    }
  );
}

export const CollisionEventCleanupSystem: System = {
  group: 'setup',
  update: (state) => {
    for (const entity of touchedEventQuery(state.world)) {
      state.removeComponent(entity, TouchedEvent);
    }

    for (const entity of touchEndedEventQuery(state.world)) {
      state.removeComponent(entity, TouchEndedEvent);
    }
  },
};

export const PhysicsRapierSyncSystem: System = {
  group: 'fixed',
  after: [PhysicsStepSystem],
  update: (state) => {
    const context = getPhysicsContext(state);

    for (const [entity, body] of context.entityToRigidbody) {
      // Corpos dormindo não se moveram: pular evita ~5 chamadas WASM +
      // conversão de euler por corpo parado por step. Kinematic ficam de
      // fora do skip (o estado de sleep deles não garante pose imutável).
      if (body.isSleeping() && !body.isKinematic()) continue;
      if (state.hasComponent(entity, Rigidbody)) {
        syncRigidbodyToECS(entity, body, state);
        copyRigidbodyToTransforms(entity, state);
      }
    }
  },
};

export const PhysicsInterpolationSystem: System = {
  group: 'simulation',
  first: true,
  update: (state) => {
    const alpha =
      state.scheduler.getAccumulator() / TIME_CONSTANTS.FIXED_TIMESTEP;
    interpolateTransforms(state, alpha);
  },
};
