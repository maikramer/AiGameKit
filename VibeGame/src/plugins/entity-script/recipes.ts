import type { Recipe } from '../../core';

/** Standalone or merged child: `<entity-script script="file.ts">` under an `<entity>`. */
export const entityScriptRecipe: Recipe = {
  name: 'entity-script',
  merge: true,
  components: ['transform', 'entityScript'],
};
