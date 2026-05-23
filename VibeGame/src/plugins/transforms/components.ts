import { MAX_ENTITIES } from '../../core/ecs/constants';

export const Transform = {
  posX: new Float32Array(MAX_ENTITIES),
  posY: new Float32Array(MAX_ENTITIES),
  posZ: new Float32Array(MAX_ENTITIES),
  rotX: new Float32Array(MAX_ENTITIES),
  rotY: new Float32Array(MAX_ENTITIES),
  rotZ: new Float32Array(MAX_ENTITIES),
  rotW: new Float32Array(MAX_ENTITIES),
  eulerX: new Float32Array(MAX_ENTITIES),
  eulerY: new Float32Array(MAX_ENTITIES),
  eulerZ: new Float32Array(MAX_ENTITIES),
  scaleX: new Float32Array(MAX_ENTITIES),
  scaleY: new Float32Array(MAX_ENTITIES),
  scaleZ: new Float32Array(MAX_ENTITIES),
  dirty: new Uint8Array(MAX_ENTITIES),
} as const;

export const WorldTransform = {
  posX: new Float32Array(MAX_ENTITIES),
  posY: new Float32Array(MAX_ENTITIES),
  posZ: new Float32Array(MAX_ENTITIES),
  rotX: new Float32Array(MAX_ENTITIES),
  rotY: new Float32Array(MAX_ENTITIES),
  rotZ: new Float32Array(MAX_ENTITIES),
  rotW: new Float32Array(MAX_ENTITIES),
  eulerX: new Float32Array(MAX_ENTITIES),
  eulerY: new Float32Array(MAX_ENTITIES),
  eulerZ: new Float32Array(MAX_ENTITIES),
  scaleX: new Float32Array(MAX_ENTITIES),
  scaleY: new Float32Array(MAX_ENTITIES),
  scaleZ: new Float32Array(MAX_ENTITIES),
} as const;
