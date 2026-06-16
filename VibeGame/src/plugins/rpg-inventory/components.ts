import { MAX_ENTITIES } from '../../core/ecs/constants';

export const InventoryComponent = {
  slots: new Uint32Array(MAX_ENTITIES),
  capacity: new Uint8Array(MAX_ENTITIES),
  version: new Uint32Array(MAX_ENTITIES),
} as const;
