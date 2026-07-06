import type { Plugin } from '../../core';
import { EquirectSky } from './components';
import { equirectSkyRecipe } from './recipes';
import { equirectSkyParser } from './parser';
import { EquirectSkyLoadSystem } from './systems';

/**
 * Wires the `<EquirectSky>` element to the equirectangular sky/IBL loader
 * ({@link applyEquirectSkyEnvironment}).
 */
export const EquirectSkyPlugin: Plugin = {
  recipes: [equirectSkyRecipe],
  systems: [EquirectSkyLoadSystem],
  components: {
    'equirect-sky': EquirectSky,
  },
  config: {
    parsers: {
      EquirectSky: equirectSkyParser,
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
    },
  },
};
