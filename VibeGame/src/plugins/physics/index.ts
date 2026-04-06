import * as RAPIER from '@dimforge/rapier3d-compat';

export {
  ApplyAngularImpulse,
  ApplyForce,
  ApplyImpulse,
  ApplyTorque,
  Body,
  BodyType,
  CharacterController,
  CharacterMovement,
  Collider,
  ColliderShape,
  CollisionEvents,
  InterpolatedTransform,
  KinematicAngularVelocity,
  KinematicMove,
  KinematicRotate,
  PhysicsWorld,
  SetAngularVelocity,
  SetLinearVelocity,
  TouchedEvent,
  TouchEndedEvent,
} from './components';
export { PhysicsPlugin } from './plugin';
export { getPhysicsContext } from './systems';
export { DEFAULT_GRAVITY, initializePhysics } from './utils';
export { RAPIER };
