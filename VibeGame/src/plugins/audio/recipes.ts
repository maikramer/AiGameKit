import type { Recipe } from '../../core';

/** Clip 2D (Howler): regista URL e `clipPath` = entidade via adapter `url`. */
export const audioClipRecipe: Recipe = {
  name: 'AudioSource',
  merge: true,
  components: ['audioSource'],
};
