import type { Plugin } from '../../core';
import { EquirectSky, ProceduralSky } from './components';
import { equirectSkyRecipe, proceduralSkyRecipe } from './recipes';
import { equirectSkyParser, proceduralSkyParser } from './parser';
import { EquirectSkyLoadSystem, ProceduralSkySystem } from './systems';

/**
 * Sky plugin. `<EquirectSky>` loads a panoramic image as background + IBL;
 * `<Sky>` renders a procedural atmospheric sky (scattering, shader clouds,
 * visible sun) whose sun drives the first directional light and whose PMREM
 * becomes the scene's IBL.
 */
export const SkyPlugin: Plugin = {
  recipes: [equirectSkyRecipe, proceduralSkyRecipe],
  systems: [EquirectSkyLoadSystem, ProceduralSkySystem],
  components: {
    'equirect-sky': EquirectSky,
    'procedural-sky': ProceduralSky,
  },
  config: {
    parsers: {
      EquirectSky: equirectSkyParser,
      Sky: proceduralSkyParser,
    },
    defaults: {
      'equirect-sky': {
        rotationDeg: 0,
        setBackground: 1,
        applied: 0,
        // 0 = "use the loader's default" (0.45 env / 1.2 background). A positive
        // value overrides per-entity so scenes can crank IBL for stronger PBR
        // reflections without editing engine code.
        environmentIntensity: 0,
        backgroundIntensity: 0,
      },
      'procedural-sky': {
        turbidity: 2.8,
        rayleigh: 1.6,
        mieCoefficient: 0.004,
        mieDirectionalG: 0.85,
        sunElevation: 35,
        sunAzimuth: 160,
        cloudCoverage: 0.3,
        cloudDensity: 0.35,
        cloudElevation: 0.5,
        environmentIntensity: 0,
        sunIntensity: 0,
        driveLight: 1,
      },
    },
  },
};
