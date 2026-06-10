import * as RAPIER from '@dimforge/rapier3d-compat';
import { ActiveCollisionTypes, ActiveEvents } from '@dimforge/rapier3d-compat';
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

let rapierEngineInitialized = false;

export async function initializePhysics(): Promise<void> {
  if (!rapierEngineInitialized) {
    await RAPIER.init();
    rapierEngineInitialized = true;
  }
}

const interpolatedTransformQuery = defineQuery([InterpolatedTransform]);

/** Constant gap kept between the character's feet and the ground when snapped. */
const GROUND_SNAP_SKIN = 0.04;
/** Max distance the feet may be re-seated downward per step (slopes/step-downs). */
const GROUND_SNAP_MAX = 0.35;
const _groundCastDown = { x: 0, y: -1, z: 0 };
const _snapOrigin = { x: 0, y: 0, z: 0 };
const _desiredTranslation = { x: 0, y: 0, z: 0 };
const _newPos = { x: 0, y: 0, z: 0 };

/**
 * Shape-cast of the character's own shape straight down from `pos`, up to
 * {@link GROUND_SNAP_MAX}. Returns the Rapier hit (toi + collider + normal)
 * or null — the hit collider doubles as the platform candidate, saving a
 * second cast.
 */
function groundCast(
  collider: RAPIER.Collider,
  physicsWorld: RAPIER.World,
  pos: { x: number; y: number; z: number }
) {
  return physicsWorld.castShape(
    pos,
    collider.rotation(),
    _groundCastDown,
    collider.shape,
    0,
    GROUND_SNAP_MAX,
    true,
    RAPIER.QueryFilterFlags.EXCLUDE_SENSORS,
    undefined,
    collider
  );
}

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
  activeEvents: ActiveEvents = ActiveEvents.NONE,
  mesh?: { vertices: Float32Array; indices: Uint32Array }
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
    case ColliderShape.TriMesh: {
      if (!mesh) throw new Error('TriMesh collider requires mesh geometry');
      desc = RAPIER.ColliderDesc.trimesh(mesh.vertices, mesh.indices);
      break;
    }
    case ColliderShape.ConvexHull: {
      if (!mesh) throw new Error('ConvexHull collider requires mesh geometry');
      const hull = RAPIER.ColliderDesc.convexHull(mesh.vertices);
      if (!hull) throw new Error('convex hull computation failed');
      desc = hull;
      break;
    }
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

  if (type === BodyType.Dynamic || type === BodyType.KinematicVelocityBased) {
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
    // setAngvel direto: o caminho antigo via applyTorqueImpulse multiplicava
    // principalInertia componente a componente, o que só é correto com o corpo
    // alinhado aos eixos principais de inércia.
    body.setAngvel(
      new RAPIER.Vector3(
        SetAngularVelocity.x[entity],
        SetAngularVelocity.y[entity],
        SetAngularVelocity.z[entity]
      ),
      true
    );
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
  const dt = TIME_CONSTANTS.FIXED_TIMESTEP;
  if (type === BodyType.KinematicPositionBased) {
    const currentPos = body.translation();
    const targetX = KinematicMove.x[entity];
    const targetY = KinematicMove.y[entity];
    const targetZ = KinematicMove.z[entity];

    Rigidbody.velX[entity] = (targetX - currentPos.x) / dt;
    Rigidbody.velY[entity] = (targetY - currentPos.y) / dt;
    Rigidbody.velZ[entity] = (targetZ - currentPos.z) / dt;

    body.setNextKinematicTranslation(
      new RAPIER.Vector3(targetX, targetY, targetZ)
    );
  } else if (type === BodyType.KinematicVelocityBased) {
    const currentPos = body.translation();
    const targetX = KinematicMove.x[entity];
    const targetY = KinematicMove.y[entity];
    const targetZ = KinematicMove.z[entity];

    const velX = (targetX - currentPos.x) / dt;
    const velY = (targetY - currentPos.y) / dt;
    const velZ = (targetZ - currentPos.z) / dt;

    Rigidbody.velX[entity] = velX;
    Rigidbody.velY[entity] = velY;
    Rigidbody.velZ[entity] = velZ;

    body.setLinvel(new RAPIER.Vector3(velX, velY, velZ), true);
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
  const currentPos = body.translation();
  const currentRot = body.rotation();

  // Rapier devolve f64; os stores ECS são Float32Array. Comparar via fround,
  // senão todo corpo em movimento dispara um falso teleporte (setTranslation +
  // wake) a cada step e nenhum corpo consegue dormir.
  const hasPositionChange =
    Math.fround(currentPos.x) !== Rigidbody.posX[entity] ||
    Math.fround(currentPos.y) !== Rigidbody.posY[entity] ||
    Math.fround(currentPos.z) !== Rigidbody.posZ[entity];

  const hasRotationChange =
    Math.fround(currentRot.x) !== Rigidbody.rotX[entity] ||
    Math.fround(currentRot.y) !== Rigidbody.rotY[entity] ||
    Math.fround(currentRot.z) !== Rigidbody.rotZ[entity] ||
    Math.fround(currentRot.w) !== Rigidbody.rotW[entity];

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

    // O sync pós-step pula corpos dormindo, e um corpo fixed teleportado pode
    // nunca acordar — escrever os transforms aqui mantém o visual correto.
    Transform.posX[entity] = Rigidbody.posX[entity];
    Transform.posY[entity] = Rigidbody.posY[entity];
    Transform.posZ[entity] = Rigidbody.posZ[entity];
    WorldTransform.posX[entity] = Rigidbody.posX[entity];
    WorldTransform.posY[entity] = Rigidbody.posY[entity];
    WorldTransform.posZ[entity] = Rigidbody.posZ[entity];
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

    Transform.rotX[entity] = Rigidbody.rotX[entity];
    Transform.rotY[entity] = Rigidbody.rotY[entity];
    Transform.rotZ[entity] = Rigidbody.rotZ[entity];
    Transform.rotW[entity] = Rigidbody.rotW[entity];
    WorldTransform.rotX[entity] = Rigidbody.rotX[entity];
    WorldTransform.rotY[entity] = Rigidbody.rotY[entity];
    WorldTransform.rotZ[entity] = Rigidbody.rotZ[entity];
    WorldTransform.rotW[entity] = Rigidbody.rotW[entity];
  }
}

export function detectPlatformContinuous(
  entity: number,
  collider: RAPIER.Collider,
  physicsWorld: RAPIER.World,
  colliderToEntity: Map<number, number>
): number {
  const castDistance = 0.5;
  const shapePos = collider.translation();
  const shapeRot = collider.rotation();
  const colliderShape = collider.shape;

  const hit = physicsWorld.castShape(
    shapePos,
    shapeRot,
    _groundCastDown,
    colliderShape,
    0,
    castDistance,
    true,
    RAPIER.QueryFilterFlags.EXCLUDE_SENSORS,
    undefined,
    collider
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

  // Airborne: integrate gravity. Grounded (and not actively jumping): no vertical
  // velocity — the explicit ground snap below keeps the feet planted and follows
  // slopes, which is stable frame-to-frame (a velocity "stick" jittered against
  // the collider). A positive velocityY (an active jump) is left untouched.
  if (!wasGrounded) {
    CharacterMovement.velocityY[entity] =
      (CharacterMovement.velocityY[entity] || 0) + effectiveGravity * deltaTime;
  } else if (CharacterMovement.velocityY[entity] <= 0) {
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
    if (
      platformBodyType === BodyType.KinematicVelocityBased ||
      platformBodyType === BodyType.KinematicPositionBased
    ) {
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

  _desiredTranslation.x = CharacterMovement.desiredVelX[entity] * deltaTime;
  _desiredTranslation.y = totalVelY * deltaTime;
  _desiredTranslation.z = CharacterMovement.desiredVelZ[entity] * deltaTime;

  // The CCT automatically excludes the controlled collider itself.
  controller.computeColliderMovement(
    collider,
    _desiredTranslation,
    RAPIER.QueryFilterFlags.EXCLUDE_SENSORS
  );

  const correctedMovement = controller.computedMovement();

  const desiredHorizontalSpeed = Math.sqrt(
    CharacterMovement.desiredVelX[entity] ** 2 +
      CharacterMovement.desiredVelZ[entity] ** 2
  );
  const actualHorizontalSpeed = Math.sqrt(
    (correctedMovement.x / deltaTime) ** 2 +
      (correctedMovement.z / deltaTime) ** 2
  );

  const isStuckAgainstWall =
    desiredHorizontalSpeed > 0.1 &&
    actualHorizontalSpeed < desiredHorizontalSpeed * 0.1;

  let finalX = correctedMovement.x;
  const finalY = correctedMovement.y;
  let finalZ = correctedMovement.z;

  if (isStuckAgainstWall && CharacterMovement.velocityY[entity] > 0) {
    finalX -= CharacterMovement.desiredVelX[entity] * 0.001;
    finalZ -= CharacterMovement.desiredVelZ[entity] * 0.001;
  }

  const currentPos = body.translation();
  _newPos.x = currentPos.x + finalX;
  _newPos.y = currentPos.y + finalY;
  _newPos.z = currentPos.z + finalZ;

  let grounded = controller.computedGrounded() ? 1 : 0;
  let snapHit: ReturnType<typeof groundCast> = null;

  // Explicit ground snap: when not rising, cast the collider straight down from
  // the post-move position and re-seat the feet at a constant skin above the
  // first contact. This makes the resting height identical every frame (no
  // penetration-recovery creep/jitter that shook the camera) and lets the
  // character track up/down slopes and step-downs within GROUND_SNAP_MAX.
  if (CharacterMovement.velocityY[entity] <= 0) {
    // Cast from the collider's projected new centre (it sits at a fixed offset
    // from the body origin, so cast there — not from the body position).
    const cp = collider.translation();
    _snapOrigin.x = cp.x + finalX;
    _snapOrigin.y = cp.y + finalY;
    _snapOrigin.z = cp.z + finalZ;
    snapHit = groundCast(collider, physicsWorld, _snapOrigin);
    if (snapHit) {
      _newPos.y += GROUND_SNAP_SKIN - snapHit.time_of_impact;
      grounded = 1;
      CharacterMovement.velocityY[entity] = 0;
    }
  }

  if (grounded && platform !== NULL_ENTITY) {
    _newPos.x += (platformVelX + tangentialVelX) * deltaTime;
    _newPos.y += (platformVelY + tangentialVelY) * deltaTime;
    _newPos.z += (platformVelZ + tangentialVelZ) * deltaTime;
  }

  body.setNextKinematicTranslation(_newPos);

  CharacterMovement.actualMoveX[entity] = finalX;
  CharacterMovement.actualMoveY[entity] = _newPos.y - currentPos.y;
  CharacterMovement.actualMoveZ[entity] = finalZ;

  CharacterController.moveX[entity] = finalX;
  CharacterController.moveY[entity] = _newPos.y - currentPos.y;
  CharacterController.moveZ[entity] = finalZ;

  CharacterController.grounded[entity] = grounded;

  if (grounded) {
    if (snapHit) {
      // Reuse the snap cast's hit as the platform candidate instead of a
      // second, nearly identical shape-cast.
      const platformEntity = colliderToEntity.get(snapHit.collider.handle);
      CharacterController.platform[entity] =
        platformEntity !== undefined &&
        platformEntity !== entity &&
        snapHit.normal1.y > 0.7
          ? platformEntity
          : NULL_ENTITY;
    } else {
      CharacterController.platform[entity] = detectPlatformContinuous(
        entity,
        collider,
        physicsWorld,
        colliderToEntity
      );
    }
  } else {
    CharacterController.platform[entity] = NULL_ENTITY;
  }

  if (grounded && CharacterMovement.velocityY[entity] < 0) {
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
  }
}

export function syncRigidbodyToECS(
  entity: number,
  body: RAPIER.RigidBody,
  state: State
): void {
  const position = body.translation();
  const rotation = body.rotation();
  const linvel = body.linvel();

  Rigidbody.posX[entity] = position.x;
  Rigidbody.posY[entity] = position.y;
  Rigidbody.posZ[entity] = position.z;
  Rigidbody.rotX[entity] = rotation.x;
  Rigidbody.rotY[entity] = rotation.y;
  Rigidbody.rotZ[entity] = rotation.z;
  Rigidbody.rotW[entity] = rotation.w;

  const euler = quaternionToEuler(
    rotation.x,
    rotation.y,
    rotation.z,
    rotation.w
  );
  Rigidbody.eulerX[entity] = euler.x;
  Rigidbody.eulerY[entity] = euler.y;
  Rigidbody.eulerZ[entity] = euler.z;

  Rigidbody.velX[entity] = linvel.x;
  Rigidbody.velY[entity] = linvel.y;
  Rigidbody.velZ[entity] = linvel.z;

  if (state.hasComponent(entity, KinematicAngularVelocity)) {
    Rigidbody.rotVelX[entity] = KinematicAngularVelocity.x[entity];
    Rigidbody.rotVelY[entity] = KinematicAngularVelocity.y[entity];
    Rigidbody.rotVelZ[entity] = KinematicAngularVelocity.z[entity];
  } else {
    const angvel = body.angvel();
    Rigidbody.rotVelX[entity] = angvel.x;
    Rigidbody.rotVelY[entity] = angvel.y;
    Rigidbody.rotVelZ[entity] = angvel.z;
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
