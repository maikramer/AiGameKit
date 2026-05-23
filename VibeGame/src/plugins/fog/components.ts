import { MAX_ENTITIES } from '../../core/ecs/constants';

export const Fog = {
  mode: new Float32Array(MAX_ENTITIES),
  density: new Float32Array(MAX_ENTITIES),
  near: new Float32Array(MAX_ENTITIES),
  far: new Float32Array(MAX_ENTITIES),
  colorR: new Float32Array(MAX_ENTITIES),
  colorG: new Float32Array(MAX_ENTITIES),
  colorB: new Float32Array(MAX_ENTITIES),
  heightFalloff: new Float32Array(MAX_ENTITIES),
  baseHeight: new Float32Array(MAX_ENTITIES),
  volumetricStrength: new Float32Array(MAX_ENTITIES),
  quality: new Float32Array(MAX_ENTITIES),
  noiseScale: new Float32Array(MAX_ENTITIES),
} as const;
