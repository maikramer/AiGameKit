import type { Recipe } from '../../core';

/** `<EquirectSky url="/assets/sky/sky.png" rotation-deg="0" set-background="true">` */
export const equirectSkyRecipe: Recipe = {
  name: 'EquirectSky',
  components: ['equirect-sky'],
  parserAttributes: ['url', 'rotation-deg', 'set-background'],
};
