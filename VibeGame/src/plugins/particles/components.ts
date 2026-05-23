import { MAX_ENTITIES } from '../../core/ecs/constants';

/** Estado do emissor declarativo (preset + parâmetros). */
export const ParticleSystem = {
  preset: new Uint8Array(MAX_ENTITIES),
  rate: new Float32Array(MAX_ENTITIES),
  lifetime: new Float32Array(MAX_ENTITIES),
  size: new Float32Array(MAX_ENTITIES),
  looping: new Uint8Array(MAX_ENTITIES),
  playing: new Uint8Array(MAX_ENTITIES),
  spawned: new Uint8Array(MAX_ENTITIES),
} as const;

export const ParticleBurst = {
  preset: new Uint8Array(MAX_ENTITIES),
  count: new Float32Array(MAX_ENTITIES),
  triggered: new Uint8Array(MAX_ENTITIES),
} as const;

export const ColorOverLife = {
  startR: new Float32Array(MAX_ENTITIES),
  startG: new Float32Array(MAX_ENTITIES),
  startB: new Float32Array(MAX_ENTITIES),
  startA: new Float32Array(MAX_ENTITIES),
  endR: new Float32Array(MAX_ENTITIES),
  endG: new Float32Array(MAX_ENTITIES),
  endB: new Float32Array(MAX_ENTITIES),
  endA: new Float32Array(MAX_ENTITIES),
} as const;

export const SizeOverLife = {
  startSize: new Float32Array(MAX_ENTITIES),
  endSize: new Float32Array(MAX_ENTITIES),
} as const;

export const ParticleTexture = {
  frameWidth: new Float32Array(MAX_ENTITIES),
  frameHeight: new Float32Array(MAX_ENTITIES),
  frames: new Uint8Array(MAX_ENTITIES),
  animationSpeed: new Float32Array(MAX_ENTITIES),
} as const;
