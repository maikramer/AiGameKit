import type { Recipe } from '../../core';

export const resourceNodeRecipe: Recipe = {
  name: 'ResourceNode',
  components: ['resource-node'],
  // `kind` is a string resolved via the config enum by the ResourceNode tag
  // parser; listing it here keeps the generic attribute applier from coercing
  // it through `Number()` (which would map every non-numeric kind to 0).
  parserAttributes: ['kind'],
};
