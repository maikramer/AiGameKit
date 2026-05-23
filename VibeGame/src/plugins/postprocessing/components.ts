import { MAX_ENTITIES } from '../../core/ecs/constants';

export const Bloom = {
  intensity: new Float32Array(MAX_ENTITIES),
  luminanceThreshold: new Float32Array(MAX_ENTITIES),
  luminanceSmoothing: new Float32Array(MAX_ENTITIES),
  mipmapBlur: new Uint8Array(MAX_ENTITIES),
  radius: new Float32Array(MAX_ENTITIES),
  levels: new Uint8Array(MAX_ENTITIES),
} as const;

export const Dithering = {
  colorBits: new Uint8Array(MAX_ENTITIES),
  intensity: new Float32Array(MAX_ENTITIES),
  grayscale: new Uint8Array(MAX_ENTITIES),
  scale: new Float32Array(MAX_ENTITIES),
  noise: new Float32Array(MAX_ENTITIES),
} as const;

export const Tonemapping = {
  mode: new Uint8Array(MAX_ENTITIES),
  middleGrey: new Float32Array(MAX_ENTITIES),
  whitePoint: new Float32Array(MAX_ENTITIES),
  averageLuminance: new Float32Array(MAX_ENTITIES),
  adaptationRate: new Float32Array(MAX_ENTITIES),
} as const;

export const SMAA = {
  preset: new Uint8Array(MAX_ENTITIES),
} as const;

export const Vignette = {
  darkness: new Float32Array(MAX_ENTITIES),
  offset: new Float32Array(MAX_ENTITIES),
} as const;

export const DepthOfField = {
  focusDistance: new Float32Array(MAX_ENTITIES),
  focalLength: new Float32Array(MAX_ENTITIES),
  bokehScale: new Float32Array(MAX_ENTITIES),
  resolutionScale: new Float32Array(MAX_ENTITIES),
  autoFocus: new Uint8Array(MAX_ENTITIES),
} as const;

export const ChromaticAberration = {
  offsetX: new Float32Array(MAX_ENTITIES),
  offsetY: new Float32Array(MAX_ENTITIES),
  radialModulation: new Uint8Array(MAX_ENTITIES),
  modulationOffset: new Float32Array(MAX_ENTITIES),
} as const;

export const Noise = {
  opacity: new Float32Array(MAX_ENTITIES),
  blendFunction: new Uint8Array(MAX_ENTITIES),
} as const;

export const ScreenSpaceReflection = {
  intensity: new Float32Array(MAX_ENTITIES),
  maxDistance: new Float32Array(MAX_ENTITIES),
} as const;

export const ScreenSpaceAmbientOcclusion = {
  intensity: new Float32Array(MAX_ENTITIES),
  radius: new Float32Array(MAX_ENTITIES),
  luminanceInfluence: new Float32Array(MAX_ENTITIES),
} as const;
