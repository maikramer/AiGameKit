import * as RAPIER from '@dimforge/rapier3d-simd';
import { ActiveCollisionTypes, ActiveEvents } from '@dimforge/rapier3d-simd';
import {
  defineQuery,
  NULL_ENTITY,
  TIME_CONSTANTS,
  type State,
} from '../../core';
import {
  eulerToQuaternion,
  quaternionToEuler,
  syncEulerFromQuaternion,
  Transform,
  WorldTransform,
} from '../transforms';
import {
  ApplyAngularImpulse,
  ApplyForce,
  ApplyImpulse,
  ApplyTorque,
  Rigidbody,
  BodyType,
  CharacterController,
  CharacterMovement,
  ColliderShape,
  InterpolatedTransform,
  KinematicAngularVelocity,
  KinematicMove,
  KinematicRotate,
  SetAngularVelocity,
  SetLinearVelocity,
} from './components';

export const DEFAULT_GRAVITY = -60;

interface RapierVec3 {
  x: number;
  y: number;
  z: number;
  free?: () => void;
}

interface RapierRot {
  x: number;
  y: number;
  z: number;
  w: number;
  free?: () => void;
}

export function safeVec3(v: RapierVec3): [number, number, number] {
  const r: [number, number, number] = [v.x, v.y, v.z];
  if (v.free) v.free();
  return r;
}

export function safeQuat(v: RapierRot): [number, number, number, number] {
  const r: [number, number, number, number] = [v.x, v.y, v.z, v.w];
  if (v.free) v.free();
  return r;
}

let rapierEngineInitialized = false;

export async function initializePhysics(): Promise<void> {
  if (!rapierEngineInitialized) {
    await RAPIER.init();
    rapierEngineInitialized = true;
  }
}

const interpolatedTransformQuery = defineQuery([InterpolatedTransform]);

export function createRigidbodyDescriptor(
  type: number,
  position?: RAPIER.Vector3,
  rotation?: RAPIER.Quaternion
): RAPIER.RigidBodyDesc {
  let desc: RAPIER.RigidBodyDesc;

  switch (type) {
    case BodyType.Fixed:
      desc = RAPIER.RigidBodyDesc.fixed();
      break;
    case BodyType.Dynamic:
      desc = RAPIER.RigidBodyDesc.dynamic();
      break;
    case BodyType.KinematicVelocityBased:
      desc = RAPIER.RigidBodyDesc.kinematicVelocityBased();
      break;
    case BodyType.KinematicPositionBased:
      desc = RAPIER.RigidBodyDesc.kinematicPositionBased();
      break;
    default:
      desc = RAPIER.RigidBodyDesc.dynamic();
  }

  if (position) desc.setTranslation(position.x, position.y, position.z);
  if (rotation) desc.setRotation(rotation);

  return desc;
}

export function configureRigidbody(
  body: RAPIER.RigidBody,
  _entity: number,
  type: number,
  mass: number,
  velocity: RAPIER.Vector3,
  angularVelocity: RAPIER.Vector3,
  linearDamping: number,
  angularDamping: number,
  gravityScale: number,
  ccd: number,
  lockRotX: number,
  lockRotY: number,
  lockRotZ: number
): void {
  if (type === BodyType.Dynamic && mass > 0) {
    body.setAdditionalMass(mass, true);
  }

  body.setLinvel(velocity, true);
  body.setAngvel(angularVelocity, true);
  body.setLinearDamping(linearDamping);
  body.setAngularDamping(angularDamping);
  body.setGravityScale(gravityScale, true);
  body.enableCcd(!!ccd);

  if (lockRotX || lockRotY || lockRotZ) {
    body.setEnabledRotations(!lockRotX, !lockRotY, !lockRotZ, true);
  }
}

export function createColliderDescriptor(
  shape: number,
  sizeX: number,
  sizeY: number,
  sizeZ: number,
  radius: number,
  height: number,
  friction: number,
  restitution: number,
  density: number,
  isSensor: number,
  membershipGroups: number,
  filterGroups: number,
  offset: RAPIER.Vector3,
  rotationOffset: RAPIER.Quaternion,
  activeEvents: ActiveEvents = ActiveEvents.NONE
): RAPIER.ColliderDesc {
  let desc: RAPIER.ColliderDesc;

  switch (shape) {
    case ColliderShape.Box:
      desc = RAPIER.ColliderDesc.cuboid(sizeX / 2, sizeY / 2, sizeZ / 2);
      break;
    case ColliderShape.Sphere:
      desc = RAPIER.ColliderDesc.ball(sizeX / 2);
      break;
    case ColliderShape.Capsule:
      desc = RAPIER.ColliderDesc.capsule(height / 2, radius);
      break;
    default:
      desc = RAPIER.ColliderDesc.cuboid(0.5, 0.5, 0.5);
  }

  desc.setFriction(friction);
  desc.setRestitution(restitution);

  if (isSensor) {
    desc.setSensor(true);
    desc.setDensity(0);
  } else {
    desc.setDensity(density);
  }

  const groups = membershipGroups || 0xffff;
  const filter = filterGroups || 0xffff;
  desc.setCollisionGroups((groups & 0xffff) | ((filter & 0xffff) << 16));

  desc.setTranslation(offset.x, offset.y, offset.z);
  desc.setRotation(rotationOffset);

  desc.activeEvents = activeEvents;
  desc.activeCollisionTypes = ActiveCollisionTypes.DEFAULT;

  return desc;
}

export function applyForceToEntity(
  entity: number,
  body: RAPIER.RigidBody,
  state: State
): void {
  if (Rigidbody.type[entity] === BodyType.Dynamic) {
    body.addForce(
      new RAPIER.Vector3(
        ApplyForce.x[entity],
        ApplyForce.y[entity],
        ApplyForce.z[entity]
      ),
      true
    );
  }
  state.removeComponent(entity, ApplyForce);
}

export function applyTorqueToEntity(
  entity: number,
  body: RAPIER.RigidBody,
  state: State
): void {
  if (Rigidbody.type[entity] === BodyType.Dynamic) {
    body.addTorque(
      new RAPIER.Vector3(
        ApplyTorque.x[entity],
        ApplyTorque.y[entity],
        ApplyTorque.z[entity]
      ),
      true
    );
  }
  state.removeComponent(entity, ApplyTorque);
}

export function applyImpulseToEntity(
  entity: number,
  body: RAPIER.RigidBody,
  state: State
): void {
  if (Rigidbody.type[entity] === BodyType.Dynamic) {
    body.applyImpulse(
      new RAPIER.Vector3(
        ApplyImpulse.x[entity],
        ApplyImpulse.y[entity],
        ApplyImpulse.z[entity]
      ),
      true
    );
  }
  state.removeComponent(entity, ApplyImpulse);
}

export function applyAngularImpulseToEntity(
  entity: number,
  body: RAPIER.RigidBody,
  state: State
): void {
  if (Rigidbody.type[entity] === BodyType.Dynamic) {
    body.applyTorqueImpulse(
      new RAPIER.Vector3(
        ApplyAngularImpulse.x[entity],
        ApplyAngularImpulse.y[entity],
        ApplyAngularImpulse.z[entity]
      ),
      true
    );
  }
  state.removeComponent(entity, ApplyAngularImpulse);
}

export function setLinearVelocityForEntity(
  entity: number,
  body: RAPIER.RigidBody,
  state: State
): void {
  const type = Rigidbody.type[entity];

  if (type === BodyType.Dynamic) {
    const [cvx, cvy, cvz] = safeVec3(body.linvel());
    const targetVel = new RAPIER.Vector3(
      SetLinearVelocity.x[entity],
      SetLinearVelocity.y[entity],
      SetLinearVelocity.z[entity]
    );
    const deltaVel = new RAPIER.Vector3(
      targetVel.x - cvx,
      targetVel.y - cvy,
      targetVel.z - cvz
    );
    const mass = body.mass();
    const impulse = new RAPIER.Vector3(
      deltaVel.x * mass,
      deltaVel.y * mass,
      deltaVel.z * mass
    );
    body.applyImpulse(impulse, true);
  } else if (type === BodyType.KinematicVelocityBased) {
    body.setLinvel(
      new RAPIER.Vector3(
        SetLinearVelocity.x[entity],
        SetLinearVelocity.y[entity],
        SetLinearVelocity.z[entity]
      ),
      true
    );
  }

  state.removeComponent(entity, SetLinearVelocity);
}

export function setAngularVelocityForEntity(
  entity: number,
  body: RAPIER.RigidBody,
  state: State
): void {
  const type = Rigidbody.type[entity];

  if (type === BodyType.Dynamic) {
    const [cax, cay, caz] = safeVec3(body.angvel());
    const targetAngVel = new RAPIER.Vector3(
      SetAngularVelocity.x[entity],
      SetAngularVelocity.y[entity],
      SetAngularVelocity.z[entity]
    );
    const deltaAngVel = new RAPIER.Vector3(
      targetAngVel.x - cax,
      targetAngVel.y - cay,
      targetAngVel.z - caz
    );
    const [ix, iy, iz] = safeVec3(body.principalInertia());
    const impulse = new RAPIER.Vector3(
      deltaAngVel.x * ix,
      deltaAngVel.y * iy,
      deltaAngVel.z * iz
    );
    body.applyTorqueImpulse(impulse, true);
  } else if (type === BodyType.KinematicVelocityBased) {
    const angVelX = SetAngularVelocity.x[entity];
    const angVelY = SetAngularVelocity.y[entity];
    const angVelZ = SetAngularVelocity.z[entity];

    body.setAngvel(new RAPIER.Vector3(angVelX, angVelY, angVelZ), true);

    if (!state.hasComponent(entity, KinematicAngularVelocity)) {
      state.addComponent(entity, KinematicAngularVelocity);
    }
    KinematicAngularVelocity.x[entity] = angVelX;
    KinematicAngularVelocity.y[entity] = angVelY;
    KinematicAngularVelocity.z[entity] = angVelZ;
  }

  state.removeComponent(entity, SetAngularVelocity);
}

export function applyKinematicMove(
  entity: number,
  body: RAPIER.RigidBody,
  state: State
): void {
  const type = Rigidbody.type[entity];
  if (type === BodyType.KinematicPositionBased) {
    body.setNextKinematicTranslation(
      new RAPIER.Vector3(
        KinematicMove.x[entity],
        KinematicMove.y[entity],
        KinematicMove.z[entity]
      )
    );
  } else if (type === BodyType.KinematicVelocityBased) {
    const [cpx, cpy, cpz] = safeVec3(body.translation());
    const targetX = KinematicMove.x[entity];
    const targetY = KinematicMove.y[entity];
    const targetZ = KinematicMove.z[entity];
    const dt = TIME_CONSTANTS.FIXED_TIMESTEP;
    body.setLinvel(
      new RAPIER.Vector3(
        (targetX - cpx) / dt,
        (targetY - cpy) / dt,
        (targetZ - cpz) / dt
      ),
      true
    );
  }
  state.removeComponent(entity, KinematicMove);
}

export function applyKinematicRotation(
  entity: number,
  body: RAPIER.RigidBody,
  state: State
): void {
  if (Rigidbody.type[entity] === BodyType.KinematicPositionBased) {
    body.setNextKinematicRotation(
      new RAPIER.Quaternion(
        KinematicRotate.x[entity],
        KinematicRotate.y[entity],
        KinematicRotate.z[entity],
        KinematicRotate.w[entity]
      )
    );
  }
  state.removeComponent(entity, KinematicRotate);
}

export function teleportEntity(entity: number, body: RAPIER.RigidBody): void {
  const [cpx, cpy, cpz] = safeVec3(body.translation());
  const [crx, cry, crz, crw] = safeQuat(body.rotation());

  const hasPositionChange =
    cpx !== Rigidbody.posX[entity] ||
    cpy !== Rigidbody.posY[entity] ||
    cpz !== Rigidbody.posZ[entity];

  const hasRotationChange =
    crx !== Rigidbody.rotX[entity] ||
    cry !== Rigidbody.rotY[entity] ||
    crz !== Rigidbody.rotZ[entity] ||
    crw !== Rigidbody.rotW[entity];

  if (hasPositionChange) {
    body.setTranslation(
      new RAPIER.Vector3(
        Rigidbody.posX[entity],
        Rigidbody.posY[entity],
        Rigidbody.posZ[entity]
      ),
      true
    );

    if (InterpolatedTransform.prevPosX[entity] !== undefined) {
      InterpolatedTransform.prevPosX[entity] = Rigidbody.posX[entity];
      InterpolatedTransform.prevPosY[entity] = Rigidbody.posY[entity];
      InterpolatedTransform.prevPosZ[entity] = Rigidbody.posZ[entity];
      InterpolatedTransform.posX[entity] = Rigidbody.posX[entity];
      InterpolatedTransform.posY[entity] = Rigidbody.posY[entity];
      InterpolatedTransform.posZ[entity] = Rigidbody.posZ[entity];
    }
  }

  if (hasRotationChange) {
    body.setRotation(
      new RAPIER.Quaternion(
        Rigidbody.rotX[entity],
        Rigidbody.rotY[entity],
        Rigidbody.rotZ[entity],
        Rigidbody.rotW[entity]
      ),
      true
    );

    if (InterpolatedTransform.prevRotX[entity] !== undefined) {
      InterpolatedTransform.prevRotX[entity] = Rigidbody.rotX[entity];
      InterpolatedTransform.prevRotY[entity] = Rigidbody.rotY[entity];
      InterpolatedTransform.prevRotZ[entity] = Rigidbody.rotZ[entity];
      InterpolatedTransform.prevRotW[entity] = Rigidbody.rotW[entity];
      InterpolatedTransform.rotX[entity] = Rigidbody.rotX[entity];
      InterpolatedTransform.rotY[entity] = Rigidbody.rotY[entity];
      InterpolatedTransform.rotZ[entity] = Rigidbody.rotZ[entity];
      InterpolatedTransform.rotW[entity] = Rigidbody.rotW[entity];
    }
  }
}

export function detectPlatformContinuous(
  entity: number,
  collider: RAPIER.Collider,
  physicsWorld: RAPIER.World,
  colliderToEntity: Map<number, number>
): number {
  const castDistance = 0.15;
  const [spx, spy, spz] = safeVec3(collider.translation());
  const [srx, sry, srz, srw] = safeQuat(collider.rotation());
  const shapePos = new RAPIER.Vector3(spx, spy, spz);
  const shapeRot = new RAPIER.Quaternion(srx, sry, srz, srw);
  const shapeVel = new RAPIER.Vector3(0, -1, 0);
  const colliderShape = collider.shape;

  const hit = physicsWorld.castShape(
    shapePos,
    shapeRot,
    shapeVel,
    colliderShape,
    castDistance,
    castDistance,
    true,
    RAPIER.QueryFilterFlags.EXCLUDE_SENSORS,
    undefined,
    undefined,
    undefined,
    (otherCollider: RAPIER.Collider) => otherCollider.handle !== collider.handle
  );

  if (hit) {
    const platformColliderHandle = hit.collider.handle;
    const platformEntity = colliderToEntity.get(platformColliderHandle);

    if (platformEntity !== undefined && platformEntity !== entity) {
      const hitNormal = hit.normal1;
      if (hitNormal.y > 0.7) {
        return platformEntity;
      }
    }
  }

  return NULL_ENTITY;
}

export function applyCharacterMovement(
  entity: number,
  controller: RAPIER.KinematicCharacterController,
  collider: RAPIER.Collider,
  body: RAPIER.RigidBody,
  deltaTime: number,
  gravityY: number,
  colliderToEntity: Map<number, number>,
  physicsWorld: RAPIER.World
): void {
  const wasGrounded = CharacterController.grounded[entity] === 1;

  const gravityScale = Rigidbody.gravityScale[entity];
  const effectiveGravity = gravityY * gravityScale;

  if (!wasGrounded) {
    CharacterMovement.velocityY[entity] =
      (CharacterMovement.velocityY[entity] || 0) + effectiveGravity * deltaTime;
  } else if (CharacterMovement.velocityY[entity] < 0) {
    CharacterMovement.velocityY[entity] = 0;
  }

  const totalVelY =
    (CharacterMovement.velocityY[entity] || 0) +
    (CharacterMovement.desiredVelY[entity] || 0);

  let platformVelX = 0;
  let platformVelY = 0;
  let platformVelZ = 0;
  let tangentialVelX = 0;
  let tangentialVelY = 0;
  let tangentialVelZ = 0;

  const platform = CharacterController.platform[entity];
  if (wasGrounded && platform !== NULL_ENTITY) {
    const platformBodyType = Rigidbody.type[platform];
    if (platformBodyType === BodyType.KinematicVelocityBased) {
      platformVelX = Rigidbody.velX[platform] || 0;
      platformVelY = Rigidbody.velY[platform] || 0;
      platformVelZ = Rigidbody.velZ[platform] || 0;
    }

    const angVelX = Rigidbody.rotVelX[platform] || 0;
    const angVelY = Rigidbody.rotVelY[platform] || 0;
    const angVelZ = Rigidbody.rotVelZ[platform] || 0;

    if (angVelX !== 0 || angVelY !== 0 || angVelZ !== 0) {
      const playerPosX = Rigidbody.posX[entity];
      const playerPosY = Rigidbody.posY[entity];
      const playerPosZ = Rigidbody.posZ[entity];
      const platformPosX = Rigidbody.posX[platform];
      const platformPosY = Rigidbody.posY[platform];
      const platformPosZ = Rigidbody.posZ[platform];

      const offsetX = playerPosX - platformPosX;
      const offsetY = playerPosY - platformPosY;
      const offsetZ = playerPosZ - platformPosZ;

      tangentialVelX = angVelY * offsetZ - angVelZ * offsetY;
      tangentialVelY = angVelZ * offsetX - angVelX * offsetZ;
      tangentialVelZ = angVelX * offsetY - angVelY * offsetX;
    }
  }

  CharacterController.platformVelX[entity] = platformVelX + tangentialVelX;
  CharacterController.platformVelY[entity] = platformVelY + tangentialVelY;
  CharacterController.platformVelZ[entity] = platformVelZ + tangentialVelZ;

  const desiredX =
    (CharacterMovement.desiredVelX[entity] + platformVelX + tangentialVelX) *
    deltaTime;
  const desiredY = (totalVelY + platformVelY + tangentialVelY) * deltaTime;
  const desiredZ =
    (CharacterMovement.desiredVelZ[entity] + platformVelZ + tangentialVelZ) *
    deltaTime;

  let fmx = desiredX;
  let fmy = desiredY;
  let fmz = desiredZ;
  let grounded = false;
  let rapierOk = false;

  try {
    const desiredTranslation = new RAPIER.Vector3(desiredX, desiredY, desiredZ);
    controller.computeColliderMovement(
      collider,
      desiredTranslation,
      RAPIER.QueryFilterFlags.EXCLUDE_SENSORS
    );

    const movement = controller.computedMovement();
    fmx = movement.x;
    fmy = movement.y;
    fmz = movement.z;

    const desiredHorizontalSpeed = Math.sqrt(
      CharacterMovement.desiredVelX[entity] ** 2 +
        CharacterMovement.desiredVelZ[entity] ** 2
    );
    const actualHorizontalSpeed = Math.sqrt(
      (fmx / deltaTime) ** 2 + (fmz / deltaTime) ** 2
    );

    if (
      desiredHorizontalSpeed > 0.1 &&
      actualHorizontalSpeed < desiredHorizontalSpeed * 0.1 &&
      CharacterMovement.velocityY[entity] > 0
    ) {
      fmx += -CharacterMovement.desiredVelX[entity] * 0.001;
      fmz += -CharacterMovement.desiredVelZ[entity] * 0.001;
    }

    grounded = controller.computedGrounded();
    rapierOk = true;
  } catch {
    // Fallback: use desired translation directly
  }

  const px = Rigidbody.posX[entity];
  const py = Rigidbody.posY[entity];
  const pz = Rigidbody.posZ[entity];

  if (rapierOk) {
    try {
      body.setNextKinematicTranslation(
        new RAPIER.Vector3(px + fmx, py + fmy, pz + fmz)
      );
    } catch {
      // Rapier RefCell still stuck; position will be off for this frame
    }
  }

  CharacterMovement.actualMoveX[entity] = fmx;
  CharacterMovement.actualMoveY[entity] = fmy;
  CharacterMovement.actualMoveZ[entity] = fmz;

  CharacterController.moveX[entity] = fmx;
  CharacterController.moveY[entity] = fmy;
  CharacterController.moveZ[entity] = fmz;

  CharacterController.grounded[entity] = grounded ? 1 : 0;

  if (grounded) {
    CharacterController.platform[entity] = detectPlatformContinuous(
      entity,
      collider,
      physicsWorld,
      colliderToEntity
    );
  } else {
    CharacterController.platform[entity] = NULL_ENTITY;
  }

  if (grounded && !wasGrounded) {
    CharacterMovement.velocityY[entity] = 0;
  }
}

export function interpolateTransforms(state: State, alpha: number): void {
  for (const entity of interpolatedTransformQuery(state.world)) {
    if (!state.hasComponent(entity, Transform))
      throw new Error(
        `[interpolateTransforms] Entity ${entity} does not have the required components`
      );

    Transform.posX[entity] =
      InterpolatedTransform.prevPosX[entity] * (1 - alpha) +
      InterpolatedTransform.posX[entity] * alpha;
    Transform.posY[entity] =
      InterpolatedTransform.prevPosY[entity] * (1 - alpha) +
      InterpolatedTransform.posY[entity] * alpha;
    Transform.posZ[entity] =
      InterpolatedTransform.prevPosZ[entity] * (1 - alpha) +
      InterpolatedTransform.posZ[entity] * alpha;

    const prevW = InterpolatedTransform.prevRotW[entity];
    const prevX = InterpolatedTransform.prevRotX[entity];
    const prevY = InterpolatedTransform.prevRotY[entity];
    const prevZ = InterpolatedTransform.prevRotZ[entity];

    const currW = InterpolatedTransform.rotW[entity];
    const currX = InterpolatedTransform.rotX[entity];
    const currY = InterpolatedTransform.rotY[entity];
    const currZ = InterpolatedTransform.rotZ[entity];

    let dot = prevW * currW + prevX * currX + prevY * currY + prevZ * currZ;
    const invAlpha = 1 - alpha;

    if (dot < 0) {
      Transform.rotW[entity] = prevW * invAlpha - currW * alpha;
      Transform.rotX[entity] = prevX * invAlpha - currX * alpha;
      Transform.rotY[entity] = prevY * invAlpha - currY * alpha;
      Transform.rotZ[entity] = prevZ * invAlpha - currZ * alpha;
    } else {
      Transform.rotW[entity] = prevW * invAlpha + currW * alpha;
      Transform.rotX[entity] = prevX * invAlpha + currX * alpha;
      Transform.rotY[entity] = prevY * invAlpha + currY * alpha;
      Transform.rotZ[entity] = prevZ * invAlpha + currZ * alpha;
    }

    const norm = Math.sqrt(
      Transform.rotW[entity] * Transform.rotW[entity] +
        Transform.rotX[entity] * Transform.rotX[entity] +
        Transform.rotY[entity] * Transform.rotY[entity] +
        Transform.rotZ[entity] * Transform.rotZ[entity]
    );

    if (norm > 0.001) {
      Transform.rotW[entity] /= norm;
      Transform.rotX[entity] /= norm;
      Transform.rotY[entity] /= norm;
      Transform.rotZ[entity] /= norm;
    }

    syncEulerFromQuaternion(Transform, entity);

    Transform.dirty[entity] = 1;
  }
}

export function syncRigidbodyToECS(
  entity: number,
  body: RAPIER.RigidBody,
  state: State
): void {
  const [px, py, pz] = safeVec3(body.translation());
  const [rx, ry, rz, rw] = safeQuat(body.rotation());
  const [lvx, lvy, lvz] = safeVec3(body.linvel());

  Rigidbody.posX[entity] = px;
  Rigidbody.posY[entity] = py;
  Rigidbody.posZ[entity] = pz;
  Rigidbody.rotX[entity] = rx;
  Rigidbody.rotY[entity] = ry;
  Rigidbody.rotZ[entity] = rz;
  Rigidbody.rotW[entity] = rw;

  const euler = quaternionToEuler(rx, ry, rz, rw);
  Rigidbody.eulerX[entity] = euler.x;
  Rigidbody.eulerY[entity] = euler.y;
  Rigidbody.eulerZ[entity] = euler.z;

  Rigidbody.velX[entity] = lvx;
  Rigidbody.velY[entity] = lvy;
  Rigidbody.velZ[entity] = lvz;

  if (state.hasComponent(entity, KinematicAngularVelocity)) {
    Rigidbody.rotVelX[entity] = KinematicAngularVelocity.x[entity];
    Rigidbody.rotVelY[entity] = KinematicAngularVelocity.y[entity];
    Rigidbody.rotVelZ[entity] = KinematicAngularVelocity.z[entity];
  } else {
    const [avx, avy, avz] = safeVec3(body.angvel());
    Rigidbody.rotVelX[entity] = avx;
    Rigidbody.rotVelY[entity] = avy;
    Rigidbody.rotVelZ[entity] = avz;
  }
}

export function copyRigidbodyToTransforms(entity: number, state: State): void {
  if (
    !state.hasComponent(entity, Transform) ||
    !state.hasComponent(entity, WorldTransform) ||
    !state.hasComponent(entity, InterpolatedTransform)
  )
    throw new Error(
      `[copyRigidbodyToTransforms] Entity ${entity} does not have the required components`
    );

  Transform.posX[entity] = Rigidbody.posX[entity];
  Transform.posY[entity] = Rigidbody.posY[entity];
  Transform.posZ[entity] = Rigidbody.posZ[entity];
  Transform.rotX[entity] = Rigidbody.rotX[entity];
  Transform.rotY[entity] = Rigidbody.rotY[entity];
  Transform.rotZ[entity] = Rigidbody.rotZ[entity];
  Transform.rotW[entity] = Rigidbody.rotW[entity];
  Transform.eulerX[entity] = Rigidbody.eulerX[entity];
  Transform.eulerY[entity] = Rigidbody.eulerY[entity];
  Transform.eulerZ[entity] = Rigidbody.eulerZ[entity];

  WorldTransform.posX[entity] = Rigidbody.posX[entity];
  WorldTransform.posY[entity] = Rigidbody.posY[entity];
  WorldTransform.posZ[entity] = Rigidbody.posZ[entity];
  WorldTransform.rotX[entity] = Rigidbody.rotX[entity];
  WorldTransform.rotY[entity] = Rigidbody.rotY[entity];
  WorldTransform.rotZ[entity] = Rigidbody.rotZ[entity];
  WorldTransform.rotW[entity] = Rigidbody.rotW[entity];
  WorldTransform.eulerX[entity] = Rigidbody.eulerX[entity];
  WorldTransform.eulerY[entity] = Rigidbody.eulerY[entity];
  WorldTransform.eulerZ[entity] = Rigidbody.eulerZ[entity];

  InterpolatedTransform.prevPosX[entity] = InterpolatedTransform.posX[entity];
  InterpolatedTransform.prevPosY[entity] = InterpolatedTransform.posY[entity];
  InterpolatedTransform.prevPosZ[entity] = InterpolatedTransform.posZ[entity];
  InterpolatedTransform.prevRotX[entity] = InterpolatedTransform.rotX[entity];
  InterpolatedTransform.prevRotY[entity] = InterpolatedTransform.rotY[entity];
  InterpolatedTransform.prevRotZ[entity] = InterpolatedTransform.rotZ[entity];
  InterpolatedTransform.prevRotW[entity] = InterpolatedTransform.rotW[entity];

  InterpolatedTransform.posX[entity] = Rigidbody.posX[entity];
  InterpolatedTransform.posY[entity] = Rigidbody.posY[entity];
  InterpolatedTransform.posZ[entity] = Rigidbody.posZ[entity];
  InterpolatedTransform.rotX[entity] = Rigidbody.rotX[entity];
  InterpolatedTransform.rotY[entity] = Rigidbody.rotY[entity];
  InterpolatedTransform.rotZ[entity] = Rigidbody.rotZ[entity];
  InterpolatedTransform.rotW[entity] = Rigidbody.rotW[entity];

  Transform.dirty[entity] = 1;
}

export function syncBodyQuaternionFromEuler(entity: number): void {
  const quat = eulerToQuaternion(
    Rigidbody.eulerX[entity],
    Rigidbody.eulerY[entity],
    Rigidbody.eulerZ[entity]
  );
  Rigidbody.rotX[entity] = quat.x;
  Rigidbody.rotY[entity] = quat.y;
  Rigidbody.rotZ[entity] = quat.z;
  Rigidbody.rotW[entity] = quat.w;
}

export function syncBodyEulerFromQuaternion(entity: number): void {
  const euler = quaternionToEuler(
    Rigidbody.rotX[entity],
    Rigidbody.rotY[entity],
    Rigidbody.rotZ[entity],
    Rigidbody.rotW[entity]
  );
  Rigidbody.eulerX[entity] = euler.x;
  Rigidbody.eulerY[entity] = euler.y;
  Rigidbody.eulerZ[entity] = euler.z;
}
