import {
  defineComponent,
  F32,
  U16,
  U32,
  U8,
} from '../../core/ecs/component-storage';

export enum BodyType {
  Dynamic = 0,
  Fixed = 1,
  KinematicPositionBased = 2,
  KinematicVelocityBased = 3,
}

export enum ColliderShape {
  Box = 0,
  Sphere = 1,
  Capsule = 2,
  /** Exact triangle mesh from a collision GLB (`mesh-url`); fixed bodies only. */
  TriMesh = 3,
  /** Convex hull of a collision GLB's vertices (`mesh-url`); cheaper, works on dynamic bodies. */
  ConvexHull = 4,
  /** Vertical cylinder (Rapier `ColliderDesc.cylinder`); cheap fixed prop collider. */
  Cylinder = 5,
  /**
   * Placeholder resolvido pelo PrecomputeColliderSystem a partir do manifest
   * de pré-cálculo (`gameassets_handoff.json`) — nunca chega ao Rapier.
   */
  Precompute = 6,
}

export const PhysicsWorld = defineComponent({
  gravityX: F32,
  gravityY: F32,
  gravityZ: F32,
});

export const Rigidbody = defineComponent({
  type: U8,
  mass: F32,
  linearDamping: F32,
  angularDamping: F32,
  gravityScale: F32,
  ccd: U8,
  lockRotX: U8,
  lockRotY: U8,
  lockRotZ: U8,

  posX: F32,
  posY: F32,
  posZ: F32,
  rotX: F32,
  rotY: F32,
  rotZ: F32,
  rotW: F32,
  eulerX: F32,
  eulerY: F32,
  eulerZ: F32,

  velX: F32,
  velY: F32,
  velZ: F32,
  rotVelX: F32,
  rotVelY: F32,
  rotVelZ: F32,
  /** 1 = ECS pose was written; TeleportationSystem pushes to Rapier then clears. */
  poseDirty: U8,
});

export const Collider = defineComponent({
  shape: U8,
  sizeX: F32,
  sizeY: F32,
  sizeZ: F32,
  radius: F32,
  height: F32,
  friction: F32,
  restitution: F32,
  density: F32,
  isSensor: U8,
  membershipGroups: U16,
  filterGroups: U16,
  // TriMesh/ConvexHull shapes: uniform scale applied to the collision mesh
  // vertices, and anchor mode (0 = as authored, 1 = recenter so the mesh
  // AABB's base center sits at the entity origin — this project's GLB pivot
  // convention).
  meshScale: F32,
  meshAnchor: U8,
  posOffsetX: F32,
  posOffsetY: F32,
  posOffsetZ: F32,
  rotOffsetX: F32,
  rotOffsetY: F32,
  rotOffsetZ: F32,
  rotOffsetW: F32,
});

export const CharacterController = defineComponent({
  offset: F32,
  maxSlope: F32,
  maxSlide: F32,
  snapDist: F32,
  autoStep: U8,
  maxStepHeight: F32,
  minStepWidth: F32,
  upX: F32,
  upY: F32,
  upZ: F32,
  moveX: F32,
  moveY: F32,
  moveZ: F32,
  grounded: U8,
  platform: U32,
  platformVelX: F32,
  platformVelY: F32,
  platformVelZ: F32,
});

export const CharacterMovement = defineComponent({
  desiredVelX: F32,
  desiredVelY: F32,
  desiredVelZ: F32,
  velocityY: F32,
  actualMoveX: F32,
  actualMoveY: F32,
  actualMoveZ: F32,
  /**
   * Horizontal movement resistance 0..0.9 (0 = none, so the zeroed default is
   * "no drag"). Written by environment systems (e.g. water submersion) and
   * consumed by applyCharacterMovement as a stride scale of (1 - waterDrag).
   */
  waterDrag: F32,
});

export const InterpolatedTransform = defineComponent({
  prevPosX: F32,
  prevPosY: F32,
  prevPosZ: F32,
  prevRotX: F32,
  prevRotY: F32,
  prevRotZ: F32,
  prevRotW: F32,

  posX: F32,
  posY: F32,
  posZ: F32,
  rotX: F32,
  rotY: F32,
  rotZ: F32,
  rotW: F32,
});

export const CollisionEvents = defineComponent({
  activeEvents: U8,
});

export const TouchedEvent = defineComponent({
  other: U32,
  handle1: U32,
  handle2: U32,
});

export const TouchEndedEvent = defineComponent({
  other: U32,
  handle1: U32,
  handle2: U32,
});

export const ApplyForce = defineComponent({
  x: F32,
  y: F32,
  z: F32,
});

export const ApplyTorque = defineComponent({
  x: F32,
  y: F32,
  z: F32,
});

export const ApplyImpulse = defineComponent({
  x: F32,
  y: F32,
  z: F32,
});

export const ApplyAngularImpulse = defineComponent({
  x: F32,
  y: F32,
  z: F32,
});

export const SetLinearVelocity = defineComponent({
  x: F32,
  y: F32,
  z: F32,
});

export const SetAngularVelocity = defineComponent({
  x: F32,
  y: F32,
  z: F32,
});

export const KinematicMove = defineComponent({
  x: F32,
  y: F32,
  z: F32,
});

export const KinematicRotate = defineComponent({
  x: F32,
  y: F32,
  z: F32,
  w: F32,
});

export const KinematicAngularVelocity = defineComponent({
  x: F32,
  y: F32,
  z: F32,
});
