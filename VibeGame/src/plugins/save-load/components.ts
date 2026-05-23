import { MAX_ENTITIES } from '../../core/ecs/constants';

export const Serializable = {
  flag: new Uint8Array(MAX_ENTITIES),
  serializationId: new Uint32Array(MAX_ENTITIES),
} as const;
