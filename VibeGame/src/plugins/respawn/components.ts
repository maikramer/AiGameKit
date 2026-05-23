import { MAX_ENTITIES } from '../../core/ecs/constants';

export const Respawn = {
  posX: new Float32Array(MAX_ENTITIES),
  posY: new Float32Array(MAX_ENTITIES),
  posZ: new Float32Array(MAX_ENTITIES),
  eulerX: new Float32Array(MAX_ENTITIES),
  eulerY: new Float32Array(MAX_ENTITIES),
  eulerZ: new Float32Array(MAX_ENTITIES),
} as const;
