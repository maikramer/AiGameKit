import { MAX_ENTITIES } from '../../core/ecs/constants';

/** 0 fixed, 1 revolute, 2 prismatic, 3 spherical, 4 rope, 5 spring */
export const Joint = {
  bodyA: new Uint32Array(MAX_ENTITIES),
  bodyB: new Uint32Array(MAX_ENTITIES),
  jointType: new Uint8Array(MAX_ENTITIES),
  anchorAX: new Float32Array(MAX_ENTITIES),
  anchorAY: new Float32Array(MAX_ENTITIES),
  anchorAZ: new Float32Array(MAX_ENTITIES),
  anchorBX: new Float32Array(MAX_ENTITIES),
  anchorBY: new Float32Array(MAX_ENTITIES),
  anchorBZ: new Float32Array(MAX_ENTITIES),
  axisX: new Float32Array(MAX_ENTITIES),
  axisY: new Float32Array(MAX_ENTITIES),
  axisZ: new Float32Array(MAX_ENTITIES),
  limitsMin: new Float32Array(MAX_ENTITIES),
  limitsMax: new Float32Array(MAX_ENTITIES),
  motorSpeed: new Float32Array(MAX_ENTITIES),
  motorMaxForce: new Float32Array(MAX_ENTITIES),
  ropeLength: new Float32Array(MAX_ENTITIES),
  springStiffness: new Float32Array(MAX_ENTITIES),
  springDamping: new Float32Array(MAX_ENTITIES),
  created: new Uint8Array(MAX_ENTITIES),
} as const;
