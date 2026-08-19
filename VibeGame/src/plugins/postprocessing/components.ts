import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

export const Postprocessing = defineComponent({
  enabled: U8,
  bloom: U8,
  bloomStrength: F32,
  bloomRadius: F32,
  bloomThreshold: F32,
  chromaticAberration: U8,
  caStrength: F32,
  vignette: U8,
  vignetteOffset: F32,
  vignetteDarkness: F32,
  aa: U8,
  toneMapping: U8,
  toneMappingExposure: F32,
  ssao: U8,
  ssaoIntensity: F32,
  ssaoRadius: F32,
  depthOfField: U8,
  dofFocusDistance: F32,
  dofFocusRange: F32,
  dofBokehScale: F32,
  heightFog: U8,
  fogColor: U32,
  fogDensity: F32,
  fogHeight: F32,
  fogFalloff: F32,
  fogNoise: F32,
  /** Forward-scattering strength when looking toward the sun through fog. */
  fogSunInfluence: F32,
  /** Horizon haze amount applied to sky pixels (aerial perspective). */
  fogSkyHaze: F32,
  colorGrading: U8,
  saturation: F32,
  contrast: F32,
  brightness: F32,
  // Film grain (NoiseEffect): subtle animated noise for cinematic texture.
  filmGrain: U8,
  filmGrainOpacity: F32,
  // LUT color grading (LUT3DEffect): PNG strip/tile or .cube lookup table.
  // URL is resolved via the recipe's `lut-url` attribute → side-map.
  lut: U8,
  lutIntensity: F32,
  // God rays (volumetric light scattering). The sun source follows the active
  // directional light's direction; density/decay tune the radial blur.
  godRays: U8,
  godRaysDensity: F32,
  godRaysDecay: F32,
  godRaysWeight: F32,
  godRaysExposure: F32,
  // Screen-space reflections (SSRPass adapter wrapping three's addon).
  // Resolution scale < 1 trades reflection sharpness for perf.
  ssr: U8,
  ssrResolutionScale: F32,
  ssrOpacity: F32,
  ssrMaxDistance: F32,
  ssrThickness: F32,
});
