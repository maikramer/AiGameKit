import type { Plugin, Adapter } from '../../core';
import { Skybox } from './components';
import { skyRecipe } from './recipes';
import { SkySystem, assignSkyUrl } from './systems';

const skyUrlAdapter: Adapter = (entity, value, _state) => {
  Skybox.urlIndex[entity] = assignSkyUrl(value);
};

/** Plugin de sky environment — aplica texturas equirectangulares como IBL/background. */
export const SkyPlugin: Plugin = {
  systems: [SkySystem],
  recipes: [skyRecipe],
  components: { skybox: Skybox },
  config: {
    adapters: {
      skybox: { url: skyUrlAdapter },
    },
    defaults: {
      skybox: {
        urlIndex: 0,
        rotationDeg: 0,
        setBackground: 1,
        loaded: 0,
      },
    },
  },
};
