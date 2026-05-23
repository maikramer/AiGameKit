import { MAX_ENTITIES } from '../../core/ecs/constants';

/** Marks an entity that runs a TS module from XML `script="…"`. */
export const MonoBehaviour = {
  ready: new Uint8Array(MAX_ENTITIES),
  enabled: new Uint8Array(MAX_ENTITIES),
} as const;
