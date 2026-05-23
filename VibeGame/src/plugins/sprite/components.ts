import { MAX_ENTITIES } from '../../core/ecs/constants';

export const Sprite = {
  width: new Float32Array(MAX_ENTITIES),
  height: new Float32Array(MAX_ENTITIES),
  pivotX: new Float32Array(MAX_ENTITIES),
  pivotY: new Float32Array(MAX_ENTITIES),
  opacity: new Float32Array(MAX_ENTITIES),
  colorR: new Float32Array(MAX_ENTITIES),
  colorG: new Float32Array(MAX_ENTITIES),
  colorB: new Float32Array(MAX_ENTITIES),
  flipX: new Uint8Array(MAX_ENTITIES),
  flipY: new Uint8Array(MAX_ENTITIES),
} as const;
