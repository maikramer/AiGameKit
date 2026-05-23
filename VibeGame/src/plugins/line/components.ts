import { MAX_ENTITIES } from '../../core/ecs/constants';

export const Line = {
  offsetX: new Float32Array(MAX_ENTITIES),
  offsetY: new Float32Array(MAX_ENTITIES),
  offsetZ: new Float32Array(MAX_ENTITIES),
  color: new Uint32Array(MAX_ENTITIES),
  thickness: new Float32Array(MAX_ENTITIES),
  opacity: new Float32Array(MAX_ENTITIES),
  visible: new Uint8Array(MAX_ENTITIES),
  arrowStart: new Uint8Array(MAX_ENTITIES),
  arrowEnd: new Uint8Array(MAX_ENTITIES),
  arrowSize: new Float32Array(MAX_ENTITIES),
} as const;
