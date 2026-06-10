export {
  ApplyAngularImpulse,
  ApplyForce,
  ApplyImpulse,
  ApplyTorque,
  BodyType,
  CharacterController,
  CharacterMovement,
  Collider,
  ColliderShape,
  CollisionEvents,
  InterpolatedTransform,
  KinematicMove,
  KinematicRotate,
  PhysicsWorld,
  Rigidbody,
  SetAngularVelocity,
  SetLinearVelocity,
  TouchedEvent,
  TouchEndedEvent,
} from './components';
export { PhysicsPlugin } from './plugin';
export { getRapierWorld } from './systems';
export { DEFAULT_GRAVITY, initializePhysics } from './utils';
export {
  MeshAnchor,
  buildMeshColliderGeometry,
  parseGlbCollisionMesh,
  setColliderMeshUrl,
  getColliderMeshUrl,
} from './mesh-collider';
export type { ColliderMeshData } from './mesh-collider';
