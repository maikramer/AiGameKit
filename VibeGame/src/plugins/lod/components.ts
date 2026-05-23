import { MAX_ENTITIES } from '../../core/ecs/constants';

export const LODGroup = {
  near: new Float32Array(MAX_ENTITIES),
  far: new Float32Array(MAX_ENTITIES),
  currentLevel: new Uint8Array(MAX_ENTITIES),
  nearEntity: new Uint32Array(MAX_ENTITIES),
  farEntity: new Uint32Array(MAX_ENTITIES),
} as const;
