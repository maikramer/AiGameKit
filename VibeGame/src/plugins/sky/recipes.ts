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

/** `<Sky sun-elevation="22" sun-azimuth="205" turbidity="2.8" rayleigh="1.8"
 *  cloud-coverage="0.35" environment-intensity="0.7">` — procedural
 *  atmospheric sky whose sun drives the first directional light. */
export const proceduralSkyRecipe: Recipe = {
  name: 'Sky',
  components: ['transform', 'procedural-sky'],
  parserAttributes: [
    'turbidity',
    'rayleigh',
    'mie-coefficient',
    'mie-directional-g',
    'sun-elevation',
    'sun-azimuth',
    'cloud-coverage',
    'cloud-density',
    'cloud-elevation',
    'environment-intensity',
    'sun-intensity',
    'drive-light',
  ],
};
