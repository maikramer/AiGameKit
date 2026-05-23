import { MAX_ENTITIES } from '../../core/ecs/constants';

/** Painel world-space (three-mesh-ui Block). */
export const HudPanel = {
  width: new Float32Array(MAX_ENTITIES),
  height: new Float32Array(MAX_ENTITIES),
  bgR: new Float32Array(MAX_ENTITIES),
  bgG: new Float32Array(MAX_ENTITIES),
  bgB: new Float32Array(MAX_ENTITIES),
  opacity: new Float32Array(MAX_ENTITIES),
  textIndex: new Uint32Array(MAX_ENTITIES),
  built: new Uint8Array(MAX_ENTITIES),
} as const;
