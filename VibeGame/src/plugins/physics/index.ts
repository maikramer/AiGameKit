import * as RAPIER from "@dimforge/rapier3d-simd";

export { BodyType, ColliderShape, Rigidbody, Collider } from "./components";
export { PhysicsPlugin } from "./plugin";
export { getBodyForEntity } from "./systems";
export { initPhysics, getWorld, getOrCreateWorld } from "./world";
export { createRapierBody, createRapierColliderDesc } from "./body";
export { RAPIER };
