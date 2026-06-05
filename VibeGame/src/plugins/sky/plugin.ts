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
      },
    },
  },
};
