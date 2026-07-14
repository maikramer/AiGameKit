import type { Recipe } from '../../core';

/** `<EquirectSky url="/assets/sky/sky.png" rotation-deg="0" set-background="true"
 *  environment-intensity="0.7" background-intensity="1.4">` */
export const equirectSkyRecipe: Recipe = {
  name: 'EquirectSky',
  components: ['transform', 'equirect-sky'],
  parserAttributes: [
    'url',
    'rotation-deg',
    'set-background',
    'environment-intensity',
    'background-intensity',
  ],
};
