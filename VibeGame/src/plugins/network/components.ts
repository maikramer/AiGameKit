import { MAX_ENTITIES } from '../../core/ecs/constants';

export const NetworkStatus = {
  connected: new Uint8Array(MAX_ENTITIES),
} as const;

export const Networked = {
  networkId: new Uint32Array(MAX_ENTITIES),
  isOwner: new Uint8Array(MAX_ENTITIES),
  interpolate: new Uint8Array(MAX_ENTITIES),
} as const;

export const NetworkBuffer = {
  prevX: new Float32Array(MAX_ENTITIES),
  prevY: new Float32Array(MAX_ENTITIES),
  prevZ: new Float32Array(MAX_ENTITIES),
  prevRotX: new Float32Array(MAX_ENTITIES),
  prevRotY: new Float32Array(MAX_ENTITIES),
  prevRotZ: new Float32Array(MAX_ENTITIES),
  prevRotW: new Float32Array(MAX_ENTITIES),
  prevScaleX: new Float32Array(MAX_ENTITIES),
  prevScaleY: new Float32Array(MAX_ENTITIES),
  prevScaleZ: new Float32Array(MAX_ENTITIES),
  nextX: new Float32Array(MAX_ENTITIES),
  nextY: new Float32Array(MAX_ENTITIES),
  nextZ: new Float32Array(MAX_ENTITIES),
  nextRotX: new Float32Array(MAX_ENTITIES),
  nextRotY: new Float32Array(MAX_ENTITIES),
  nextRotZ: new Float32Array(MAX_ENTITIES),
  nextRotW: new Float32Array(MAX_ENTITIES),
  nextScaleX: new Float32Array(MAX_ENTITIES),
  nextScaleY: new Float32Array(MAX_ENTITIES),
  nextScaleZ: new Float32Array(MAX_ENTITIES),
} as const;
