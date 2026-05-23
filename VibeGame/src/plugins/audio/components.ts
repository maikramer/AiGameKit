import { MAX_ENTITIES } from '../../core/ecs/constants';

export const AudioSource = {
  clipPath: new Uint32Array(MAX_ENTITIES),
  volume: new Float32Array(MAX_ENTITIES),
  loop: new Uint8Array(MAX_ENTITIES),
  pitch: new Float32Array(MAX_ENTITIES),
  spatial: new Uint8Array(MAX_ENTITIES),
  minDistance: new Float32Array(MAX_ENTITIES),
  maxDistance: new Float32Array(MAX_ENTITIES),
  rolloff: new Float32Array(MAX_ENTITIES),
  playing: new Uint8Array(MAX_ENTITIES),
} as const;

export const AudioListener = {
  posX: new Float32Array(MAX_ENTITIES),
  posY: new Float32Array(MAX_ENTITIES),
  posZ: new Float32Array(MAX_ENTITIES),
} as const;
