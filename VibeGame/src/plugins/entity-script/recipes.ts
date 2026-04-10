import type { Recipe } from '../../core';

/** Optional standalone tag: `<entity-script script="file.ts"></entity-script>`. */
export const entityScriptRecipe: Recipe = {
  name: 'entity-script',
  components: ['transform', 'entityScript'],
};
