import { MAX_ENTITIES } from '../../core/ecs/constants';

export const Postprocessing = {
  enabled: new Uint8Array(MAX_ENTITIES),
  bloom: new Uint8Array(MAX_ENTITIES),
  bloomStrength: new Float32Array(MAX_ENTITIES),
  bloomRadius: new Float32Array(MAX_ENTITIES),
  bloomThreshold: new Float32Array(MAX_ENTITIES),
  chromaticAberration: new Uint8Array(MAX_ENTITIES),
  caStrength: new Float32Array(MAX_ENTITIES),
  vignette: new Uint8Array(MAX_ENTITIES),
  vignetteOffset: new Float32Array(MAX_ENTITIES),
  vignetteDarkness: new Float32Array(MAX_ENTITIES),
  aa: new Uint8Array(MAX_ENTITIES),
  toneMapping: new Uint8Array(MAX_ENTITIES),
  toneMappingExposure: new Float32Array(MAX_ENTITIES),
  ssao: new Uint8Array(MAX_ENTITIES),
  ssaoIntensity: new Float32Array(MAX_ENTITIES),
  ssaoRadius: new Float32Array(MAX_ENTITIES),
  depthOfField: new Uint8Array(MAX_ENTITIES),
  dofFocusDistance: new Float32Array(MAX_ENTITIES),
  dofFocusRange: new Float32Array(MAX_ENTITIES),
  dofBokehScale: new Float32Array(MAX_ENTITIES),
  heightFog: new Uint8Array(MAX_ENTITIES),
  fogColor: new Uint32Array(MAX_ENTITIES),
  fogDensity: new Float32Array(MAX_ENTITIES),
  fogHeight: new Float32Array(MAX_ENTITIES),
  fogFalloff: new Float32Array(MAX_ENTITIES),
  fogNoise: new Float32Array(MAX_ENTITIES),
  colorGrading: new Uint8Array(MAX_ENTITIES),
  saturation: new Float32Array(MAX_ENTITIES),
  contrast: new Float32Array(MAX_ENTITIES),
  brightness: new Float32Array(MAX_ENTITIES),
  // Film grain (NoiseEffect): subtle animated noise for cinematic texture.
  filmGrain: new Uint8Array(MAX_ENTITIES),
  filmGrainOpacity: new Float32Array(MAX_ENTITIES),
  // LUT color grading (LUT3DEffect): PNG strip/tile or .cube lookup table.
  // URL is resolved via the recipe's `lut-url` attribute → side-map.
  lut: new Uint8Array(MAX_ENTITIES),
  lutIntensity: new Float32Array(MAX_ENTITIES),
  // God rays (volumetric light scattering). The sun source follows the active
  // directional light's direction; density/decay tune the radial blur.
  godRays: new Uint8Array(MAX_ENTITIES),
  godRaysDensity: new Float32Array(MAX_ENTITIES),
  godRaysDecay: new Float32Array(MAX_ENTITIES),
  godRaysWeight: new Float32Array(MAX_ENTITIES),
  godRaysExposure: new Float32Array(MAX_ENTITIES),
  // Screen-space reflections (SSRPass adapter wrapping three's addon).
  // Resolution scale < 1 trades reflection sharpness for perf.
  ssr: new Uint8Array(MAX_ENTITIES),
  ssrResolutionScale: new Float32Array(MAX_ENTITIES),
  ssrOpacity: new Float32Array(MAX_ENTITIES),
  ssrMaxDistance: new Float32Array(MAX_ENTITIES),
  ssrThickness: new Float32Array(MAX_ENTITIES),
} as const;
