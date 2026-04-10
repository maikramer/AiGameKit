import type { Plugin } from '../../core';
import {
  Bloom,
  ChromaticAberration,
  DepthOfField,
  Dithering,
  Noise,
  SMAA,
  ScreenSpaceAmbientOcclusion,
  ScreenSpaceReflection,
  Tonemapping,
  Vignette,
} from './components';
import { PostprocessingSystem, PostprocessingRenderSystem } from './systems';
import { registerBuiltinEffects } from './builtin-effects';

registerBuiltinEffects();

export const PostprocessingPlugin: Plugin = {
  systems: [PostprocessingSystem, PostprocessingRenderSystem],
  components: {
    Bloom,
    ChromaticAberration,
    DepthOfField,
    Dithering,
    Noise,
    SMAA,
    SSAO: ScreenSpaceAmbientOcclusion,
    SSR: ScreenSpaceReflection,
    Tonemapping,
    Vignette,
  },
  config: {
    defaults: {
      bloom: {
        intensity: 1.0,
        luminanceSmoothing: 0.3,
        luminanceThreshold: 1.0,
        mipmapBlur: 1,
        radius: 0.85,
        levels: 8,
      },
      dithering: {
        colorBits: 4,
        intensity: 1.0,
        grayscale: 0,
        scale: 1.0,
        noise: 1.0,
      },
      smaa: {
        preset: 2,
      },
      tonemapping: {
        mode: 7,
        middleGrey: 0.6,
        whitePoint: 4.0,
        averageLuminance: 1.0,
        adaptationRate: 1.0,
      },
      vignette: {
        darkness: 0.5,
        offset: 0.1,
      },
      depthOfField: {
        focusDistance: 10,
        focalLength: 0.05,
        bokehScale: 1,
        resolutionScale: 0.5,
        autoFocus: 1,
      },
      chromaticAberration: {
        offsetX: 0.002,
        offsetY: 0.001,
        radialModulation: 0,
        modulationOffset: 0.15,
      },
      noise: {
        opacity: 0.2,
        blendFunction: 0,
      },
      ssao: {
        intensity: 1.0,
        radius: 0.1825,
        luminanceInfluence: 0.7,
      },
      ssr: {
        intensity: 1.0,
        maxDistance: 10.0,
      },
    },
    enums: {
      smaa: {
        preset: {
          low: 0,
          medium: 1,
          high: 2,
          ultra: 3,
        },
      },
      tonemapping: {
        mode: {
          linear: 0,
          reinhard: 1,
          reinhard2: 2,
          'reinhard2-adaptive': 3,
          uncharted2: 4,
          'optimized-cineon': 5,
          cineon: 6,
          'aces-filmic': 7,
          agx: 8,
          neutral: 9,
        },
      },
      depthOfField: {
        autoFocus: { off: 0, on: 1 },
      },
      chromaticAberration: {
        radialModulation: { off: 0, on: 1 },
      },
      noise: {
        blendFunction: {
          skip: 0,
          normal: 1,
          darken: 2,
          multiply: 3,
          lighten: 4,
          screen: 5,
          overlay: 6,
        },
      },
    },
  },
};
