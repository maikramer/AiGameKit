import { MAX_ENTITIES } from '../../core/ecs/constants';

export const BodyType = {
  Dynamic: 0,
  Fixed: 1,
  KinematicPositionBased: 2,
  KinematicVelocityBased: 3,
} as const;

export const ColliderShape = { Box: 0, Sphere: 1, Capsule: 2 } as const;

export const Rigidbody = {
  type: new Uint8Array(MAX_ENTITIES),
  mass: new Float32Array(MAX_ENTITIES),
  gravityScale: new Float32Array(MAX_ENTITIES),
  lockRotX: new Uint8Array(MAX_ENTITIES),
  lockRotY: new Uint8Array(MAX_ENTITIES),
  lockRotZ: new Uint8Array(MAX_ENTITIES),
  ccd: new Uint8Array(MAX_ENTITIES),
  linearDamping: new Float32Array(MAX_ENTITIES),
  angularDamping: new Float32Array(MAX_ENTITIES),
  posX: new Float32Array(MAX_ENTITIES),
  posY: new Float32Array(MAX_ENTITIES),
  posZ: new Float32Array(MAX_ENTITIES),
  rotX: new Float32Array(MAX_ENTITIES),
  rotY: new Float32Array(MAX_ENTITIES),
  rotZ: new Float32Array(MAX_ENTITIES),
  rotW: new Float32Array(MAX_ENTITIES),
  eulerX: new Float32Array(MAX_ENTITIES),
  eulerY: new Float32Array(MAX_ENTITIES),
  eulerZ: new Float32Array(MAX_ENTITIES),
  velX: new Float32Array(MAX_ENTITIES),
  velY: new Float32Array(MAX_ENTITIES),
  velZ: new Float32Array(MAX_ENTITIES),
  rotVelX: new Float32Array(MAX_ENTITIES),
  rotVelY: new Float32Array(MAX_ENTITIES),
  rotVelZ: new Float32Array(MAX_ENTITIES),
} as const;

export const Collider = {
  shape: new Uint8Array(MAX_ENTITIES),
  sizeX: new Float32Array(MAX_ENTITIES),
  sizeY: new Float32Array(MAX_ENTITIES),
  sizeZ: new Float32Array(MAX_ENTITIES),
  radius: new Float32Array(MAX_ENTITIES),
  height: new Float32Array(MAX_ENTITIES),
  friction: new Float32Array(MAX_ENTITIES),
  restitution: new Float32Array(MAX_ENTITIES),
  density: new Float32Array(MAX_ENTITIES),
  sensor: new Uint8Array(MAX_ENTITIES),
  isSensor: new Uint8Array(MAX_ENTITIES),
  membershipGroups: new Uint16Array(MAX_ENTITIES),
  filterGroups: new Uint16Array(MAX_ENTITIES),
  posOffsetX: new Float32Array(MAX_ENTITIES),
  posOffsetY: new Float32Array(MAX_ENTITIES),
  posOffsetZ: new Float32Array(MAX_ENTITIES),
  rotOffsetX: new Float32Array(MAX_ENTITIES),
  rotOffsetY: new Float32Array(MAX_ENTITIES),
  rotOffsetZ: new Float32Array(MAX_ENTITIES),
  rotOffsetW: new Float32Array(MAX_ENTITIES),
} as const;

export const CollisionEvents = {
  activeEvents: new Uint8Array(MAX_ENTITIES),
} as const;

export const TouchedEvent = {
  other: new Uint32Array(MAX_ENTITIES),
} as const;

export const TouchEndedEvent = {
  other: new Uint32Array(MAX_ENTITIES),
} as const;

export const SetLinearVelocity = {
  x: new Float32Array(MAX_ENTITIES),
  y: new Float32Array(MAX_ENTITIES),
  z: new Float32Array(MAX_ENTITIES),
} as const;

export const SetAngularVelocity = {
  x: new Float32Array(MAX_ENTITIES),
  y: new Float32Array(MAX_ENTITIES),
  z: new Float32Array(MAX_ENTITIES),
} as const;

export const CharacterController = {
  moveX: new Float32Array(MAX_ENTITIES),
  moveY: new Float32Array(MAX_ENTITIES),
  moveZ: new Float32Array(MAX_ENTITIES),
  grounded: new Uint8Array(MAX_ENTITIES),
  platform: new Uint32Array(MAX_ENTITIES),
  platformVelX: new Float32Array(MAX_ENTITIES),
  platformVelZ: new Float32Array(MAX_ENTITIES),
  offset: new Float32Array(MAX_ENTITIES),
  maxSlope: new Float32Array(MAX_ENTITIES),
  upX: new Float32Array(MAX_ENTITIES),
  upY: new Float32Array(MAX_ENTITIES),
  upZ: new Float32Array(MAX_ENTITIES),
  snapDist: new Float32Array(MAX_ENTITIES),
  autoStep: new Uint8Array(MAX_ENTITIES),
  maxStepHeight: new Float32Array(MAX_ENTITIES),
  minStepWidth: new Float32Array(MAX_ENTITIES),
  maxSlide: new Float32Array(MAX_ENTITIES),
} as const;

export const CharacterMovement = {
  desiredVelX: new Float32Array(MAX_ENTITIES),
  desiredVelY: new Float32Array(MAX_ENTITIES),
  desiredVelZ: new Float32Array(MAX_ENTITIES),
  velocityY: new Float32Array(MAX_ENTITIES),
  actualMoveX: new Float32Array(MAX_ENTITIES),
  actualMoveY: new Float32Array(MAX_ENTITIES),
  actualMoveZ: new Float32Array(MAX_ENTITIES),
} as const;

export const InterpolatedTransform = {
  prevPosX: new Float32Array(MAX_ENTITIES),
  prevPosY: new Float32Array(MAX_ENTITIES),
  prevPosZ: new Float32Array(MAX_ENTITIES),
  prevRotX: new Float32Array(MAX_ENTITIES),
  prevRotY: new Float32Array(MAX_ENTITIES),
  prevRotZ: new Float32Array(MAX_ENTITIES),
  prevRotW: new Float32Array(MAX_ENTITIES),
  posX: new Float32Array(MAX_ENTITIES),
  posY: new Float32Array(MAX_ENTITIES),
  posZ: new Float32Array(MAX_ENTITIES),
  rotX: new Float32Array(MAX_ENTITIES),
  rotY: new Float32Array(MAX_ENTITIES),
  rotZ: new Float32Array(MAX_ENTITIES),
  rotW: new Float32Array(MAX_ENTITIES),
} as const;

export const ApplyForce = {
  x: new Float32Array(MAX_ENTITIES),
  y: new Float32Array(MAX_ENTITIES),
  z: new Float32Array(MAX_ENTITIES),
} as const;

export const ApplyImpulse = {
  x: new Float32Array(MAX_ENTITIES),
  y: new Float32Array(MAX_ENTITIES),
  z: new Float32Array(MAX_ENTITIES),
} as const;

export const ApplyAngularImpulse = {
  x: new Float32Array(MAX_ENTITIES),
  y: new Float32Array(MAX_ENTITIES),
  z: new Float32Array(MAX_ENTITIES),
} as const;

export const ApplyTorque = {
  x: new Float32Array(MAX_ENTITIES),
  y: new Float32Array(MAX_ENTITIES),
  z: new Float32Array(MAX_ENTITIES),
} as const;

export const KinematicMove = {
  x: new Float32Array(MAX_ENTITIES),
  y: new Float32Array(MAX_ENTITIES),
  z: new Float32Array(MAX_ENTITIES),
} as const;

export const KinematicRotate = {
  x: new Float32Array(MAX_ENTITIES),
  y: new Float32Array(MAX_ENTITIES),
  z: new Float32Array(MAX_ENTITIES),
  w: new Float32Array(MAX_ENTITIES),
} as const;

export const PhysicsWorld = {
  gravityX: new Float32Array(MAX_ENTITIES),
  gravityY: new Float32Array(MAX_ENTITIES),
  gravityZ: new Float32Array(MAX_ENTITIES),
} as const;
