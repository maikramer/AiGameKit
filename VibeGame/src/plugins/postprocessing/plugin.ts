import type { Plugin } from '../../core';
import { Postprocessing } from './components';
import { PostprocessingBuildSystem } from './systems';

export const PostprocessingPlugin: Plugin = {
  systems: [PostprocessingBuildSystem],
  components: { postprocessing: Postprocessing },
  config: {
    defaults: {
      postprocessing: {
        enabled: 1,
        bloom: 1,
        bloomStrength: 0.6,
        bloomRadius: 0.4,
        bloomThreshold: 0.85,
        chromaticAberration: 1,
        caStrength: 0.003,
        vignette: 1,
        vignetteOffset: 0.35,
        vignetteDarkness: 0.5,
        aa: 2,
        toneMapping: 1,
        toneMappingExposure: 1.0,
        ssao: 0,
        ssaoIntensity: 1.0,
        ssaoRadius: 1.0,
        depthOfField: 0,
        dofFocusDistance: 0.01,
        dofFocusRange: 0.5,
        dofBokehScale: 3.0,
      },
    },
    enums: {
      postprocessing: {
        aa: { off: 0, fxaa: 1, smaa: 2 },
        toneMapping: { off: 0, agx: 1, aces: 2, neutral: 3, reinhard: 4 },
      },
    },
  },
};
