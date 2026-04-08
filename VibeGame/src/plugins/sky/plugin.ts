import type { Plugin, Adapter } from '../../core';
import { Sky } from './components';
import { skyRecipe } from './recipes';
import { SkySystem, assignSkyUrl } from './systems';

const skyUrlAdapter: Adapter = (entity, value, _state) => {
  Sky.urlIndex[entity] = assignSkyUrl(value);
};

/** Plugin de sky environment — aplica texturas equirectangulares como IBL/background. */
export const SkyPlugin: Plugin = {
  systems: [SkySystem],
  recipes: [skyRecipe],
  components: { sky: Sky },
  config: {
    adapters: {
      sky: { url: skyUrlAdapter },
    },
    defaults: {
      sky: {
        urlIndex: 0,
        rotationDeg: 0,
        setBackground: 1,
        loaded: 0,
      },
    },
  },
};
