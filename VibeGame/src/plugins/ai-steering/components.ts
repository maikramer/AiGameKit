import { MAX_ENTITIES } from '../../core/ecs/constants';

/** 0 seek, 1 wander, 2 flee */
export const SteeringAgent = {
  behavior: new Uint8Array(MAX_ENTITIES),
  maxSpeed: new Float32Array(MAX_ENTITIES),
  maxForce: new Float32Array(MAX_ENTITIES),
  active: new Uint8Array(MAX_ENTITIES),
} as const;

/** Se `targetEntity` > 0, segue essa entidade; senão usa targetX/Y/Z. */
export const SteeringTarget = {
  targetEntity: new Uint32Array(MAX_ENTITIES),
  targetX: new Float32Array(MAX_ENTITIES),
  targetY: new Float32Array(MAX_ENTITIES),
  targetZ: new Float32Array(MAX_ENTITIES),
} as const;
